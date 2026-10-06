"""Main Channel Library System — one album message per series in the Telegram channel."""

from __future__ import annotations
import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime, timezone

import aiohttp
from bot.telegram import Client, enums
from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.database import Database
from bot.emojis import get_emoji

log = logging.getLogger(__name__)


async def _download_poster(url: str) -> str | None:
    """Download poster image to a temp file, return path or None."""
    from utils.anilist import is_valid_poster_url
    if not url or not is_valid_poster_url(url):
        return None
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    log.warning("Poster download failed: HTTP %d for %s", resp.status, url[:80])
                    return None
                ct = resp.content_type or ""
                ext = ".jpg"
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"
                data = await resp.read()
                if len(data) < 1500:
                    log.warning("Poster too small (%d bytes), skipping", len(data))
                    return None
                tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=tempfile.gettempdir())
                tmp.write(data)
                tmp.close()
                return tmp.name
    except Exception as e:
        log.warning("Poster download error: %s", e)
        return None


CAPTION_LIMIT = 1024

# Per-series lock to prevent race conditions
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    if len(_locks) > 500:
        oldest = list(_locks.keys())[:200]
        for k in oldest:
            if not _locks[k].locked():
                _locks.pop(k, None)
    return _locks[key]


class LibraryManager:
    def __init__(self, client: Client, db: Database, main_channel: int, bot_username: str):
        self.client = client
        self.db = db
        self.channel = main_channel
        self.bot_username = bot_username

    def _target_channel(self, mapping: dict | None) -> int:
        """V3 #14: album create/edit/update/delete always target
        mapping.channel_id when mapped, else the main channel.

        mapping.channel_id → target_channel_id → album op (same target).
        """
        try:
            cid = (mapping or {}).get("channel_id")
            if cid:
                return int(cid)
        except Exception:
            pass
        return self.channel

    def _bot_username_for_quality(self, quality: str) -> str:
        """V3 #15: per-quality worker bot username (delegates to the shared
        module-level helper the issue #33 link gate also uses)."""
        return bot_username_for_quality(quality, self.bot_username)

    async def save_to_library(
        self,
        series_slug: str,
        series_title: str,
        quality: str,
        episode_key: str,
        file_id: str,
        file_unique_id: str,
        poster_url: str | None = None,
        is_movie: bool = False,
    ):
        """
        Save a downloaded file and update/create the single series album
        message in the main channel.

        One message per series — always updated, never duplicated.
        Sends a reply notification on new episodes (Issue #12).
        """
        if not self.channel:
            # V3 #14: a mapped channel can still receive album posts even
            # when the main channel is unset — check the mapping first.
            try:
                _map = await self.db.get_channel_mapping(series_slug, is_movie=is_movie)
            except Exception:
                _map = None
            if not (_map or {}).get("channel_id"):
                log.warning("save_to_library skipped for '%s': no main channel and no mapping", series_slug)
                return

        async with _get_lock(series_slug):
            await self._save_locked(
                series_slug, series_title, quality, episode_key,
                file_id, file_unique_id, poster_url, is_movie,
            )

    async def _send_episode_update_notification(
        self,
        target_message_id: int,
        series_title: str,
        series_slug: str,
        episode_key: str,
        quality: str = "",
    ):
        """
        Send reply notification to existing library post on new episode (Issue #12):
        • S2 | Episode 04 | #Added ✓
        Start The Bot And Get Linke Here
        (No inline buttons, text hyperlink only).
        Also posts to ongoing_channel if configured.
        """
        try:
            check_emoji = get_emoji("check", "✓")

            m = re.search(r"S(\d+)E(\d+)", episode_key, re.IGNORECASE)
            if m:
                s_part = f"S{int(m.group(1))}"
                e_part = f"Episode {int(m.group(2)):02d}"
            else:
                m_ep = re.search(r"E(?:pisode)?[\s_-]*(\d+)", episode_key, re.IGNORECASE)
                if m_ep:
                    s_part = "S1"
                    e_part = f"Episode {int(m_ep.group(1)):02d}"
                else:
                    s_part = "S1"
                    e_part = episode_key

            q_param = quality or "720p"
            from utils.helpers import encode_file_param
            sec_param = encode_file_param(f"get_{series_slug}_{q_param}_{episode_key}")
            file_link = f"https://t.me/{self._bot_username_for_quality(q_param)}?start={sec_param}"

            reply_text = (
                f"<b>{series_title}</b>\n"
                f"<b>──────────────────────</b>\n\n"
                f"<blockquote>• {s_part} | {e_part} | #Added {check_emoji}</blockquote>"
            )

            # Build action buttons for notification
            mapping = await self.db.get_channel_mapping(series_slug, title=series_title) if self.db else None
            target_channel_id = self._target_channel(mapping)  # V3 #14
            if mapping and mapping.get("channel_id"):
                sec_join = encode_file_param(f"join_{series_slug}")
                join_link = f"https://t.me/{self._bot_username_for_quality(q_param)}?start={sec_join}"
                notif_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Open Channel", url=join_link)]])
            else:
                sec_join = encode_file_param(f"get_{series_slug}_{q_param}_{episode_key}")
                join_link = f"https://t.me/{self._bot_username_for_quality(q_param)}?start={sec_join}"
                notif_markup = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Get Episode", url=join_link)]])

            # Send as reply in the mapped anime channel (V3 #14), not main.
            if target_message_id and target_channel_id:
                try:
                    await self.client.send_message(
                        chat_id=target_channel_id,
                        text=reply_text,
                        reply_to_message_id=target_message_id,
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=notif_markup,
                    )
                except Exception as e:
                    log.warning("Failed sending episode reply notification: %s", e)

            # Optional Ongoing Channel posting (Issue #12)
            from config import Config
            ongoing_ch = getattr(Config, "ONGOING_CHANNEL", None)
            if not ongoing_ch and self.db:
                ongoing_ch = await self.db.get_ongoing_channel()

            if ongoing_ch:
                try:
                    await self.client.send_message(
                        chat_id=ongoing_ch,
                        text=reply_text,
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=notif_markup,
                    )
                    log.info("Posted episode update to ongoing channel: %s", ongoing_ch)
                except Exception as e:
                    log.warning("Failed posting to ongoing channel %s: %s", ongoing_ch, e)
                    log.warning("Failed posting to ongoing channel %s: %s", ongoing_ch, e)

        except Exception as e:
            log.warning("Failed in _send_episode_update_notification: %s", e)

    async def _save_locked(
        self,
        series_slug: str,
        series_title: str,
        quality: str,
        episode_key: str,
        file_id: str,
        file_unique_id: str,
        poster_url: str | None,
        is_movie: bool,
    ):
        now = datetime.now(timezone.utc).isoformat()

        # Resolve authoritative AniList poster
        from utils.anilist import resolve_best_poster
        poster_url = await resolve_best_poster(series_title, poster_url, is_movie=is_movie)

        # Save file mapping
        await self.db.files.update_one(
            {
                "series_slug": series_slug,
                "quality": quality,
                "episode_key": episode_key,
            },
            {"$set": {
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "series_slug": series_slug,
                "series_title": series_title,
                "episode_key": episode_key,
                "quality": quality,
                "updated_at": now,
            }},
            upsert=True,
        )

        # Get ALL files for this series (all episodes, all qualities)
        cursor = self.db.files.find({"series_slug": series_slug})
        all_files = await cursor.to_list(length=None)

        # Build episode/quality map
        episodes: dict[str, set[str]] = {}
        all_qualities: set[str] = set()
        for f in all_files:
            ep = f["episode_key"]
            q = f["quality"]
            episodes.setdefault(ep, set()).add(q)
            all_qualities.add(q)

        # Sort episodes
        sorted_eps = sorted(episodes.keys(), key=_ep_sort_key)
        sorted_qualities = _sort_qualities(all_qualities)

        # Get channel mapping (with movie parent anime routing), album mode, and post style
        mapping = await self.db.get_channel_mapping(series_slug, is_movie=is_movie, title=series_title)
        target_channel_id = self._target_channel(mapping)  # V3 #14: mapped anime channel wins
        if not mapping or not mapping.get("channel_id"):
            log.info("Main channel post for '%s': private channel not mapped, posting with direct deep-links", series_slug)
            from config import Config
            owner_id = getattr(Config, "OWNER_ID", None)
            if owner_id and self.client:
                try:
                    await self.client.send_message(
                        chat_id=owner_id,
                        text=(
                            f"⚠️ <b>Private anime channel is not mapped. Please map it using /mapchannel.</b>\n\n"
                            f"📺 <b>Anime:</b> {series_title}\n"
                            f"🏷️ <b>Slug:</b> <code>{series_slug}</code>\n"
                            f"📁 <b>Episode:</b> {episode_key} ({quality})\n\n"
                            f"<i>(Main channel album posted with direct bot deep-links)</i>"
                        ),
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception as we:
                    log.warning("Failed sending unmapped channel warning to owner: %s", we)

        album_mode = await self.db.get_config("album_mode", default="channel")
        post_style = await self.db.get_post_style()

        # Build caption & buttons
        caption = self._format_album_caption(
            series_title, sorted_eps, sorted_qualities, is_movie, poster_url,
            channel_mapping=mapping, series_slug=series_slug, post_style=post_style,
        )
        markup = self._build_album_buttons(
            series_slug, sorted_eps, sorted_qualities, is_movie, channel_mapping=mapping, album_mode=album_mode,
            **await self._album_gate_kwargs(sorted_eps, is_movie),
        )

        # Check if album message already exists for this series
        entry = await self.db.library.find_one({"series_slug": series_slug, "type": "album"})
        target_msg_id = None

        if entry and entry.get("message_id"):
            msg_id = entry["message_id"]
            target_msg_id = msg_id
            if not entry.get("has_poster") and poster_url:
                try:
                    await self.client.delete_messages(target_channel_id, msg_id)
                except Exception:
                    pass
                entry = None
            else:
                try:
                    if entry.get("has_poster"):
                        await self.client.edit_message_caption(
                            chat_id=target_channel_id,
                            message_id=msg_id,
                            caption=caption[:CAPTION_LIMIT],
                            parse_mode=enums.ParseMode.HTML,
                            reply_markup=markup,
                        )
                    else:
                        await self.client.edit_message_text(
                            chat_id=target_channel_id,
                            message_id=msg_id,
                            text=caption[:CAPTION_LIMIT],
                            parse_mode=enums.ParseMode.HTML,
                            disable_web_page_preview=True,
                            reply_markup=markup,
                        )
                    # Update DB entry
                    await self.db.library.update_one(
                        {"_id": entry["_id"]},
                        {"$set": {
                            "series_title": series_title,
                            "episode_count": len(sorted_eps),
                            "qualities": sorted_qualities,
                            "updated_at": now,
                            "poster_url": poster_url or entry.get("poster_url"),
                        }},
                    )
                    log.info("Updated album for %s: %d episodes, qualities: %s",
                             series_slug, len(sorted_eps), sorted_qualities)

                    # Send reply update notification for new episode
                    if episode_key and not is_movie:
                        await self._send_episode_update_notification(
                            target_message_id=msg_id,
                            series_title=series_title,
                            series_slug=series_slug,
                            episode_key=episode_key,
                            quality=quality,
                        )
                    return
                except Exception as e:
                    log.warning("Failed to update album message %d, recreating: %s", msg_id, e)
                    try:
                        await self.client.delete_messages(target_channel_id, msg_id)
                    except Exception:
                        pass

        # Create new album message
        try:
            has_poster = False
            poster_path = None
            if poster_url:
                poster_path = await _download_poster(poster_url)

            # Fallback to configured default thumbnail (Issue #9)
            if not poster_path:
                from config import Config
                def_thumb = (
                    getattr(Config, "DEFAULT_MOVIE_THUMB", None)
                    if is_movie
                    else getattr(Config, "DEFAULT_ANIME_THUMB", None)
                )
                if def_thumb:
                    poster_path = await _download_poster(def_thumb)

            if poster_path:
                display_photo = poster_path
                temp_card_path = None
                try:
                    from bot.thumbnail import generate_thumbnail
                    auto_thumb_on = await self.db.get_auto_thumb() if self.db else True
                    if auto_thumb_on or post_style == "modern":
                        # Issue #33: one streaming-card style serves both
                        # movies and series (legacy templates retired).
                        template_choice = await self.db.get_thumb_template() if self.db else "streaming"
                        gen_card = generate_thumbnail(
                            title=series_title,
                            quality="HD",
                            season=1,
                            episode=len(sorted_eps) or 1,
                            poster_path=poster_path,
                            template=template_choice,
                            is_movie=is_movie,
                        )
                        if gen_card and os.path.exists(gen_card):
                            display_photo = gen_card
                            temp_card_path = gen_card
                except Exception as gte:
                    log.debug("Modern card generation for channel album skipped: %s", gte)

                try:
                    msg = await self.client.send_photo(
                        chat_id=target_channel_id,
                        photo=display_photo,
                        caption=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                    )
                    has_poster = True
                except Exception as e:
                    log.warning("Failed to send poster photo, sending text: %s", e)
                    msg = await self.client.send_message(
                        chat_id=target_channel_id,
                        text=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=markup,
                    )
                finally:
                    if temp_card_path and os.path.exists(temp_card_path):
                        try: os.remove(temp_card_path)
                        except Exception: pass
                    try:
                        os.remove(poster_path)
                    except Exception:
                        pass
            else:
                msg = await self.client.send_message(
                    chat_id=target_channel_id,
                    text=caption[:CAPTION_LIMIT],
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=markup,
                )

            target_msg_id = msg.id

            # Upsert album entry (one per series)
            await self.db.library.update_one(
                {"series_slug": series_slug, "type": "album"},
                {"$set": {
                    "series_slug": series_slug,
                    "series_title": series_title,
                    "type": "album",
                    "message_id": msg.id,
                    "has_poster": has_poster,
                    "poster_url": poster_url,
                    "episode_count": len(sorted_eps),
                    "qualities": sorted_qualities,
                    "updated_at": now,
                }},
                upsert=True,
            )
            log.info("Created album for %s: %d episodes", series_slug, len(sorted_eps))

            # Send reply update notification for new episode
            if target_msg_id and episode_key and not is_movie:
                await self._send_episode_update_notification(
                    target_message_id=target_msg_id,
                    series_title=series_title,
                    series_slug=series_slug,
                    episode_key=episode_key,
                    quality=quality,
                )
        except Exception as e:
            log.error("Failed to create album message: %s", e)

    async def update_album_for_series(
        self,
        series_slug: str,
        series_title: str,
        poster_url: str | None = None,
        is_movie: bool = False,
        new_episode_key: str | None = None,
        quality: str | None = None,
    ):
        """
        Refresh or create the single album post for a series in the main channel,
        reflecting all current files and sending update notification if an episode is new.
        Addresses Issue #12.
        """
        if not self.channel:
            try:
                _map = await self.db.get_channel_mapping(series_slug, is_movie=is_movie, title=series_title)
            except Exception:
                _map = None
            if not (_map or {}).get("channel_id"):
                log.warning("update_album skipped for '%s': no main channel and no mapping", series_slug)
                return

        async with _get_lock(series_slug):
            cursor = self.db.files.find({"series_slug": series_slug})
            all_files = await cursor.to_list(length=None)

            episodes: dict[str, set[str]] = {}
            all_qualities: set[str] = set()
            for f in all_files:
                ep = f.get("episode_key", "")
                q = f.get("quality", "")
                if ep and q:
                    episodes.setdefault(ep, set()).add(q)
                    all_qualities.add(q)

            sorted_eps = sorted(episodes.keys(), key=_ep_sort_key)
            sorted_qualities = _sort_qualities(all_qualities)

            from utils.anilist import resolve_best_poster
            poster_url = await resolve_best_poster(series_title, poster_url, is_movie=is_movie)

            mapping = await self.db.get_channel_mapping(series_slug, is_movie=is_movie, title=series_title)
            target_channel_id = self._target_channel(mapping)  # V3 #14
            if not target_channel_id:
                # Unmapped series falls back to the main channel album —
                # skipping here meant main-channel albums never updated.
                log.warning("Skipping album update for '%s': no target channel resolved", series_slug)
                return

            album_mode = await self.db.get_config("album_mode", default="channel")
            post_style = await self.db.get_post_style()

            caption = self._format_album_caption(
                series_title, sorted_eps, sorted_qualities, is_movie, poster_url,
                channel_mapping=mapping, series_slug=series_slug, post_style=post_style,
            )
            markup = self._build_album_buttons(
                series_slug, sorted_eps, sorted_qualities, is_movie, channel_mapping=mapping, album_mode=album_mode,
                **await self._album_gate_kwargs(sorted_eps, is_movie),
            )

            now = datetime.now(timezone.utc).isoformat()
            entry = await self.db.library.find_one({"series_slug": series_slug, "type": "album"})
            target_msg_id = None

            if entry and entry.get("message_id"):
                msg_id = entry["message_id"]
                target_msg_id = msg_id
                try:
                    if entry.get("has_poster"):
                        await self.client.edit_message_caption(
                            chat_id=target_channel_id,
                            message_id=msg_id,
                            caption=caption[:CAPTION_LIMIT],
                            parse_mode=enums.ParseMode.HTML,
                            reply_markup=markup,
                        )
                    else:
                        poster_path = None
                        if poster_url:
                            poster_path = await _download_poster(poster_url)
                        if poster_path:
                            try:
                                from bot.telegram.types import InputMediaPhoto
                                await self.client.edit_message_media(
                                    chat_id=target_channel_id,
                                    message_id=msg_id,
                                    media=InputMediaPhoto(poster_path, caption=caption[:CAPTION_LIMIT], parse_mode=enums.ParseMode.HTML),
                                    reply_markup=markup,
                                )
                                entry["has_poster"] = True
                            finally:
                                try:
                                    os.remove(poster_path)
                                except Exception:
                                    pass
                        else:
                            await self.client.edit_message_text(
                                chat_id=target_channel_id,
                                message_id=msg_id,
                                text=caption[:CAPTION_LIMIT],
                                parse_mode=enums.ParseMode.HTML,
                                disable_web_page_preview=True,
                                reply_markup=markup,
                            )
                    await self.db.library.update_one(
                        {"_id": entry["_id"]},
                        {"$set": {
                            "series_title": series_title,
                            "episode_count": len(sorted_eps),
                            "qualities": sorted_qualities,
                            "updated_at": now,
                            "poster_url": poster_url or entry.get("poster_url"),
                            "has_poster": bool(entry.get("has_poster", False)),
                        }},
                    )
                except Exception as e:
                    log.warning("Failed to edit existing album %d: %s", msg_id, e)
            else:
                has_poster = False
                poster_path = None
                if poster_url:
                    poster_path = await _download_poster(poster_url)
                if not poster_path:
                    from config import Config
                    def_thumb = (
                        getattr(Config, "DEFAULT_MOVIE_THUMB", None)
                        if is_movie
                        else getattr(Config, "DEFAULT_ANIME_THUMB", None)
                    )
                    if def_thumb:
                        poster_path = await _download_poster(def_thumb)

                try:
                    if poster_path:
                        try:
                            msg = await self.client.send_photo(
                                chat_id=target_channel_id,
                                photo=poster_path,
                                caption=caption[:CAPTION_LIMIT],
                                parse_mode=enums.ParseMode.HTML,
                                reply_markup=markup,
                            )
                            has_poster = True
                        finally:
                            try:
                                os.remove(poster_path)
                            except Exception:
                                pass
                    else:
                        msg = await self.client.send_message(
                            chat_id=target_channel_id,
                            text=caption[:CAPTION_LIMIT],
                            parse_mode=enums.ParseMode.HTML,
                            disable_web_page_preview=True,
                            reply_markup=markup,
                        )

                    target_msg_id = msg.id
                    await self.db.library.update_one(
                        {"series_slug": series_slug, "type": "album"},
                        {"$set": {
                            "series_slug": series_slug,
                            "series_title": series_title,
                            "type": "album",
                            "message_id": msg.id,
                            "has_poster": has_poster,
                            "poster_url": poster_url,
                            "episode_count": len(sorted_eps),
                            "qualities": sorted_qualities,
                            "updated_at": now,
                        }},
                        upsert=True,
                    )
                except Exception as e:
                    log.error("Failed creating album in update_album_for_series: %s", e)

            if target_msg_id and new_episode_key and not is_movie:
                await self._send_episode_update_notification(
                    target_message_id=target_msg_id,
                    series_title=series_title,
                    series_slug=series_slug,
                    episode_key=new_episode_key,
                    quality=quality or (sorted_qualities[0] if sorted_qualities else "720p"),
                )

    def _format_album_caption(
        self,
        title: str,
        episodes: list[str],
        qualities: list[str],
        is_movie: bool,
        poster_url: str | None,
        channel_mapping: dict | None = None,
        series_slug: str = "",
        post_style: str = "classic",
    ) -> str:
        """
        Format clean channel post caption per Issue #20 Bug 33:
        <b><blockquote>• Episodes,- | S0
        • Audio track,-  Dub | #Official 
        • Quality - 
        ━━━━━━━━━━━━━━━━━━━━━━━━━</blockquote></b> 
        <b>➥ @Channel</b>
        """
        # Extract season and episodes info
        s_num = 1
        ep_numbers = []
        for ep in episodes:
            m = re.match(r"S(\d+)E(\d+)", ep, re.IGNORECASE)
            if m:
                s_num = int(m.group(1))
                ep_numbers.append(int(m.group(2)))

        if is_movie:
            ep_part = "01 | Movie"
        elif ep_numbers:
            ep_numbers.sort()
            if len(ep_numbers) > 1:
                ep_part = f"{ep_numbers[0]:02d}-{ep_numbers[-1]:02d} | S{s_num:02d}"
            else:
                ep_part = f"{ep_numbers[0]:02d} | S{s_num:02d}"
        else:
            ep_part = f"{len(episodes) or 1:02d} | S{s_num:02d}"

        # Audio track
        audio_name = (channel_mapping.get("language") if channel_mapping else "") or "Hindi"
        audio_display = f"{audio_name.capitalize()} Dub"

        # Quality display
        quality_display = ", ".join(qualities) if qualities else "480p, 720p, 1080p"

        # Channel handle / branding
        from config import Config
        chan_handle = getattr(Config, "MAIN_CHANNEL_LINK", "") or f"@{self.bot_username}"
        if chan_handle.startswith("https://t.me/"):
            chan_tag = "@" + chan_handle.removeprefix("https://t.me/").lstrip("+")
        elif chan_handle.startswith("@"):
            chan_tag = chan_handle
        else:
            chan_tag = f"@{self.bot_username}"

        import html as htmlmod
        if post_style == "modern":
            caption = (
                f"🎬 <b>{htmlmod.escape(title)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>✦ Season:</b> S{s_num:02d}\n"
                f"<b>✦ Episodes:</b> {ep_part}\n"
                f"<b>✦ Audio:</b> {audio_display}\n"
                f"<b>✦ Qualities:</b> {quality_display}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>➥ {chan_tag}</b>"
            )
        else:
            caption = (
                f"<b><blockquote>• Episodes,- {ep_part}\n"
                f"• Audio track,- {audio_display} | #Official\n"
                f"• Quality - {quality_display}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━</blockquote></b>\n"
                f"<b>➥ {chan_tag}</b>"
            )
        return caption

    async def _album_gate_kwargs(self, episodes, is_movie: bool = False) -> dict:
        """Issue #33: resolve link-gate state + season for the album post.

        Returns kwargs for :meth:`_build_album_buttons`. Any failure means
        ``gated=False`` — a broken gate check must never stop a post going out.
        """
        try:
            from bot.linkgate import is_enabled
            gated = bool(await is_enabled())
        except Exception as e:
            log.debug("Link gate check failed (posting ungated): %s", e)
            gated = False
        season = 1
        if not is_movie:
            for ep in episodes or []:
                m = re.match(r"S(\d+)E(\d+)", str(ep), re.IGNORECASE)
                if m:
                    season = max(1, int(m.group(1)))
                    break
        return {"gated": gated, "season": season}

    def _build_album_buttons(
        self,
        series_slug: str,
        episodes: list[str],
        qualities: list[str],
        is_movie: bool,
        channel_mapping: dict | None = None,
        album_mode: str = "channel",
        gated: bool = False,
        season: int = 1,
    ) -> InlineKeyboardMarkup:
        """
        Build inline buttons for library album post per Issue #20 (Points 31 & 33).
        Only available qualities as buttons linking directly to secure deep-links:
        [ 480p ] [ 720p ] [ 1080p ]
        V3 #15: each quality deep-link targets its active worker bot
        (ChildBotManager.get_bot_for_quality), not the main bot.

        Issue #33 (issue): when the owner has switched the link gate on
        (`/linkgate <channel>`), the post carries a single ⬇ DOWNLOAD button
        instead — the quality rows are only revealed once the bot has checked
        the user against the gate channel. Gate off ⇒ byte-identical buttons
        to before, so nothing changes for existing deployments.
        """
        if gated:
            from bot.linkgate import encode_gate_param
            from config import Config
            uname = str(getattr(Config, "BOT_USERNAME", "") or "").lstrip("@") or self.bot_username
            return InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬇️ DOWNLOAD",
                    url=f"https://t.me/{uname}?start={encode_gate_param(series_slug, season)}",
                )
            ]])

        markup = build_quality_buttons(
            qualities, series_slug, is_movie=is_movie, bot_username=self.bot_username,
        )
        return markup if markup.inline_keyboard else InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Open Bot", url=f"https://t.me/{self.bot_username}?start=start")]
        ])

    async def get_file(self, series_slug: str, quality: str, episode_key: str) -> str | None:
        """Get file_id for a specific episode."""
        entry = await self.db.files.find_one({
            "series_slug": series_slug,
            "quality": quality,
            "episode_key": episode_key,
        })
        return entry.get("file_id") if entry else None

    async def delete_album(self, series_slug: str):
        """Delete the album message for a series from the channel (V3 #14: mapped channel)."""
        entry = await self.db.library.find_one({"series_slug": series_slug, "type": "album"})
        if entry and entry.get("message_id"):
            try:
                _map = await self.db.get_channel_mapping(series_slug) if self.db else None
                _tgt = self._target_channel(_map)
                await self.client.delete_messages(_tgt, entry["message_id"])
            except Exception as e:
                log.warning("Could not delete album message: %s", e)
        await self.db.library.delete_many({"series_slug": series_slug})

    async def refresh_all_albums(self) -> int:
        """
        Re-generate buttons for all existing series albums in the channel.
        Updates all channel posts to use newly assigned child worker bots!
        """
        # V3 #14: per-album targets resolve via mapping even when the main
        # channel is unset — don't bail out before the loop.
        cursor = self.db.library.find({"type": "album"})
        albums = await cursor.to_list(length=None)
        refreshed = 0

        for a in albums:
            slug = a.get("series_slug")
            msg_id = a.get("message_id")
            if not slug or not msg_id:
                continue

            try:
                all_files = await self.db.files.find({"series_slug": slug}).to_list(length=None)
                if not all_files:
                    continue

                episodes: dict[str, set[str]] = {}
                all_qualities: set[str] = set()
                for f in all_files:
                    ep = f.get("episode_key", "")
                    q = f.get("quality", "")
                    if ep and q:
                        episodes.setdefault(ep, set()).add(q)
                        all_qualities.add(q)

                sorted_eps = sorted(episodes.keys(), key=_ep_sort_key)
                sorted_qualities = _sort_qualities(all_qualities)
                is_movie = bool(a.get("is_movie", False) or "movie" in slug.lower())
                mapping = await self.db.get_channel_mapping(slug, is_movie=is_movie, title=a.get("series_title", ""))
                target_channel_id = self._target_channel(mapping)  # V3 #14
                if not target_channel_id:
                    continue  # nothing to target (no main channel, no mapping)
                album_mode = await self.db.get_config("album_mode", default="channel")
                post_style = await self.db.get_post_style()

                from utils.anilist import resolve_best_poster
                series_title = a.get("series_title", slug)
                poster_url = await resolve_best_poster(series_title, a.get("poster_url"), is_movie=is_movie)

                markup = self._build_album_buttons(
                    slug, sorted_eps, sorted_qualities, is_movie,
                    channel_mapping=mapping, album_mode=album_mode,
                    **await self._album_gate_kwargs(sorted_eps, is_movie),
                )
                caption = self._format_album_caption(
                    series_title, sorted_eps, sorted_qualities, is_movie, poster_url,
                    channel_mapping=mapping, series_slug=slug, post_style=post_style,
                )

                if a.get("has_poster"):
                    await self.client.edit_message_caption(
                        chat_id=target_channel_id,
                        message_id=msg_id,
                        caption=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                    )
                else:
                    await self.client.edit_message_text(
                        chat_id=target_channel_id,
                        message_id=msg_id,
                        text=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=markup,
                    )
                refreshed += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                log.warning("Failed to refresh album %s (msg %d): %s", slug, msg_id, e)

        return refreshed


def _ep_sort_key(key: str):
    m = re.match(r"S(\d+)E(\d+)", key, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    if key.lower() == "movie":
        return (0, 0)
    return (999, 0)


def _sort_qualities(qualities: set[str]) -> list[str]:
    order = {"360p": 1, "480p": 2, "720p": 3, "1080p": 4, "auto": 5}
    return sorted(qualities, key=lambda q: order.get(q, 99))


def bot_username_for_quality(quality: str, fallback_username: str = "") -> str:
    """V3 #15: worker bot for a quality, else the main bot (never silent).

    Module-level so the issue #33 link gate can build the same buttons as the
    channel post without needing a LibraryManager instance.
    """
    q = (quality or "").strip() or "auto"
    try:
        from bot.child_bots import child_bot_manager
        if child_bot_manager:
            worker = child_bot_manager.get_bot_for_quality(q)
            if worker:
                return worker.lstrip("@")
    except Exception as e:
        log.debug("Worker lookup failed for quality %s: %s", q, e)
    try:
        from config import Config
        allow_fallback = bool(getattr(Config, "QUALITY_BUTTON_FALLBACK_TO_MAIN", True))
    except Exception:
        allow_fallback = True
    if not allow_fallback:
        log.warning("V3 #15: no active worker for quality %s and fallback disabled — using main bot",
                    q)
    else:
        log.warning("V3 #15: no active worker for quality %s — explicit fallback to main bot @%s",
                    q, fallback_username)
    return fallback_username


def build_quality_buttons(
    qualities: list[str],
    series_slug: str,
    is_movie: bool = False,
    bot_username: str = "",
    per_row: int = 3,
) -> InlineKeyboardMarkup:
    """Quality deep-links for a series — shared by the channel post and the
    issue #33 link gate so both render byte-identical buttons."""
    from utils.helpers import encode_file_param
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for q in qualities:
        ep_key = "movie" if is_movie else "all"
        sec_param = encode_file_param(f"get_{series_slug}_{q}_{ep_key}")
        deep_link = f"https://t.me/{bot_username_for_quality(q, bot_username)}?start={sec_param}"
        row.append(InlineKeyboardButton(q, url=deep_link))
        if len(row) >= per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def qualities_for_series(series_slug: str) -> list[str]:
    """Distinct qualities stored for a series, in display order (issue #33)."""
    from bot.database import db
    if not db:
        return []
    try:
        docs = await db.files.find({"series_slug": series_slug}).to_list(length=None)
    except Exception as e:
        log.warning("Quality lookup failed for %s: %s", series_slug, e)
        return []
    found = {str(d.get("quality", "")).strip() for d in docs if d.get("quality")}
    found.discard("")
    return _sort_qualities(found)


# Singleton
library_manager: LibraryManager | None = None
