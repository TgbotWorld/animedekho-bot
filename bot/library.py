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
        """
        if not self.channel:
            return

        async with _get_lock(series_slug):
            await self._save_locked(
                series_slug, series_title, quality, episode_key,
                file_id, file_unique_id, poster_url, is_movie,
            )

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
        poster_url = await resolve_best_poster(series_title, poster_url)

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
        episodes: dict[str, set[str]] = {}  # ep_key -> set of qualities
        all_qualities: set[str] = set()
        for f in all_files:
            ep = f["episode_key"]
            q = f["quality"]
            episodes.setdefault(ep, set()).add(q)
            all_qualities.add(q)

        # Sort episodes
        sorted_eps = sorted(episodes.keys(), key=_ep_sort_key)
        sorted_qualities = _sort_qualities(all_qualities)

        # Get channel mapping, album mode, and post style
        mapping = await self.db.get_channel_mapping(series_slug)
        album_mode = await self.db.get_config("album_mode", default="channel")
        post_style = await self.db.get_post_style()

        # Build caption
        caption = self._format_album_caption(
            series_title, sorted_eps, sorted_qualities, is_movie, poster_url,
            channel_mapping=mapping, series_slug=series_slug, post_style=post_style,
        )

        # Build buttons
        markup = self._build_album_buttons(
            series_slug, sorted_eps, sorted_qualities, is_movie, channel_mapping=mapping, album_mode=album_mode,
        )

        # Check if album message already exists for this series
        entry = await self.db.library.find_one({"series_slug": series_slug, "type": "album"})

        if entry and entry.get("message_id"):
            msg_id = entry["message_id"]
            if not entry.get("has_poster") and poster_url:
                # Upgrade text-only album to photo album by deleting old text message
                try:
                    await self.client.delete_messages(self.channel, msg_id)
                except Exception:
                    pass
            else:
                try:
                    if entry.get("has_poster"):
                        await self.client.edit_message_caption(
                            chat_id=self.channel,
                            message_id=msg_id,
                            caption=caption[:CAPTION_LIMIT],
                            parse_mode=enums.ParseMode.HTML,
                            reply_markup=markup,
                        )
                    else:
                        await self.client.edit_message_text(
                            chat_id=self.channel,
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
                    return
                except Exception as e:
                    log.warning("Failed to update album message %d, recreating: %s", msg_id, e)
                    # Delete old message if possible
                    try:
                        await self.client.delete_messages(self.channel, msg_id)
                    except Exception:
                        pass

        # Create new album message
        try:
            has_poster = False
            poster_path = None
            if poster_url:
                poster_path = await _download_poster(poster_url)

            if poster_path:
                try:
                    msg = await self.client.send_photo(
                        chat_id=self.channel,
                        photo=poster_path,
                        caption=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                    )
                    has_poster = True
                except Exception as e:
                    log.warning("Failed to send poster photo, sending text: %s", e)
                    msg = await self.client.send_message(
                        chat_id=self.channel,
                        text=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=markup,
                    )
                finally:
                    try:
                        os.remove(poster_path)
                    except Exception:
                        pass
            else:
                if poster_url:
                    log.warning("Poster URL exists but download failed: %s", poster_url[:100])
                msg = await self.client.send_message(
                    chat_id=self.channel,
                    text=caption[:CAPTION_LIMIT],
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=markup,
                )

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
        except Exception as e:
            log.error("Failed to create album message: %s", e)

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
        import html as htmlmod
        title_esc = htmlmod.escape(title)
        audio = "Multi Audio (Japanese, English & Hindi)"
        quality_str = " | ".join(qualities)

        slug = series_slug or (channel_mapping.get("series_slug", "") if channel_mapping else "")
        channel_line = ""
        join_deep = ""
        if channel_mapping and slug:
            join_deep = f"https://t.me/{self.bot_username}?start=join_{slug}"
            channel_line = f"➥ 📢 Cʜᴀɴɴᴇʟ:- <a href='{join_deep}'>Join Series Channel</a>\n"

        if post_style == "modern":
            ep_type = "Movie" if is_movie else "Series"
            season_str = "01"
            if not is_movie:
                for ep in episodes:
                    m = re.match(r"S(\d+)E(\d+)", ep, re.IGNORECASE)
                    if m:
                        season_str = f"{int(m.group(1)):02d}"
                        break
            genres_str = "Action, Drama, Fantasy, Anime"
            duration_str = "~2 hrs" if is_movie else "24 min/ep"
            branding = f"@{self.bot_username}"
            channel_entry = f"📢 <b>Channel:</b> <a href='{join_deep}'>Join Series Channel</a>\n" if join_deep else ""

            return (
                f"<b>{title_esc}</b> ❞\n\n"
                f"┌ <b>TYPE:</b> {ep_type}\n"
                f"📁 <b>DURATION:</b> {duration_str}\n"
                f"🌀 <b>Rating:</b> 80%\n"
                f"📋 <b>STATUS:</b> RELEASING\n"
                f"⭕ <b>EPISODES:</b> {len(episodes)}\n"
                f"❦ <b>SEASON:</b> {season_str}\n"
                f"♡ <b>GENRES:</b> {genres_str}\n"
                f"└───────────────\n"
                f"{channel_entry}"
                f"➥ <b>{branding}</b>"
            )

        if is_movie:
            ep_info = "🎬 Movie"
        else:
            # Group episodes by season
            seasons: dict[int, list[int]] = {}
            for ep in episodes:
                m = re.match(r"S(\d+)E(\d+)", ep, re.IGNORECASE)
                if m:
                    s, e = int(m.group(1)), int(m.group(2))
                    seasons.setdefault(s, []).append(e)

            ep_lines = []
            for s_num in sorted(seasons.keys()):
                eps = sorted(seasons[s_num])
                if len(eps) <= 3:
                    ep_str = ", ".join(str(e) for e in eps)
                else:
                    ep_str = f"{eps[0]}-{eps[-1]}"
                ep_lines.append(f"Season {s_num}: Episode {ep_str}")

            ep_info = "\n".join(f"➥ {line}" for line in ep_lines) if ep_lines else f"➥ {len(episodes)} episode(s)"

        caption = (
            f"◆ {title_esc} ◆ ❞\n"
            f"⟐━━━━━━━━━━━━━━━━━⟐\n"
            f"{ep_info}\n"
            f"➥ Qᴜᴀʟɪᴛʏ:- {quality_str}\n"
            f"➥ Aᴜᴅɪᴏ:- {audio}\n"
            f"{channel_line}"
            f"➥ Tᴏᴛᴀʟ:- {len(episodes)} {'file' if len(episodes) == 1 else 'files'}\n"
            f"⟐━━━━━━━━━━━━━━━━━⟐\n"
            f"⟲ Pᴏᴡᴇʀᴇᴅ ʙʏ:- @{self.bot_username}"
        )
        return caption

    def _build_album_buttons(
        self,
        series_slug: str,
        episodes: list[str],
        qualities: list[str],
        is_movie: bool,
        channel_mapping: dict | None = None,
        album_mode: str = "channel",
    ) -> InlineKeyboardMarkup:
        from bot.child_bots import child_bot_manager
        buttons = []

        channel_btn = None
        slug_for_join = series_slug or (channel_mapping.get("series_slug", "") if channel_mapping else "")
        if channel_mapping and slug_for_join:
            join_deep = f"https://t.me/{self.bot_username}?start=join_{slug_for_join}"
            channel_btn = InlineKeyboardButton(
                "📢 Watch / Episodes Channel",
                url=join_deep,
            )

        # If channel is mapped and album_mode is "channel" (channel-only)
        if channel_btn and album_mode == "channel":
            buttons.append([channel_btn])
            first_q = qualities[0] if qualities else "1080p"
            target_bot = self.bot_username
            if child_bot_manager:
                assigned = child_bot_manager.get_bot_for_quality(first_q)
                if assigned:
                    target_bot = assigned
            if is_movie:
                deep = f"https://t.me/{target_bot}?start=get_{series_slug}_{first_q}_movie"
            else:
                deep = f"https://t.me/{target_bot}?start=get_{series_slug}_{first_q}_all"
            buttons.append([InlineKeyboardButton("📥 Download via Bot", url=deep)])
            return InlineKeyboardMarkup(buttons)

        if is_movie:
            # One row per quality for movies
            row = []
            for q in qualities:
                target_bot = self.bot_username
                if child_bot_manager:
                    assigned = child_bot_manager.get_bot_for_quality(q)
                    if assigned:
                        target_bot = assigned
                deep = f"https://t.me/{target_bot}?start=get_{series_slug}_{q}_movie"
                row.append(InlineKeyboardButton(f"📥 {q}", url=deep))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
        else:
            # "Get All" button per quality
            for q in qualities:
                target_bot = self.bot_username
                if child_bot_manager:
                    assigned = child_bot_manager.get_bot_for_quality(q)
                    if assigned:
                        target_bot = assigned
                deep = f"https://t.me/{target_bot}?start=get_{series_slug}_{q}_all"
                buttons.append([InlineKeyboardButton(f"📥 Get All Episodes [{q}]", url=deep)])

        if channel_btn and album_mode == "both":
            buttons.insert(0, [channel_btn])

        return InlineKeyboardMarkup(buttons)

    async def get_file(self, series_slug: str, quality: str, episode_key: str) -> str | None:
        """Get file_id for a specific episode."""
        entry = await self.db.files.find_one({
            "series_slug": series_slug,
            "quality": quality,
            "episode_key": episode_key,
        })
        return entry.get("file_id") if entry else None

    async def delete_album(self, series_slug: str):
        """Delete the album message for a series from the channel."""
        entry = await self.db.library.find_one({"series_slug": series_slug, "type": "album"})
        if entry and entry.get("message_id"):
            try:
                await self.client.delete_messages(self.channel, entry["message_id"])
            except Exception as e:
                log.warning("Could not delete album message: %s", e)
        await self.db.library.delete_many({"series_slug": series_slug})

    async def refresh_all_albums(self) -> int:
        """
        Re-generate buttons for all existing series albums in the channel.
        Updates all channel posts to use newly assigned child worker bots!
        """
        if not self.channel:
            return 0

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
                mapping = await self.db.get_channel_mapping(slug)
                album_mode = await self.db.get_config("album_mode", default="channel")
                post_style = await self.db.get_post_style()

                from utils.anilist import resolve_best_poster
                series_title = a.get("series_title", slug)
                poster_url = await resolve_best_poster(series_title, a.get("poster_url"))

                markup = self._build_album_buttons(
                    slug, sorted_eps, sorted_qualities, is_movie,
                    channel_mapping=mapping, album_mode=album_mode,
                )
                caption = self._format_album_caption(
                    series_title, sorted_eps, sorted_qualities, is_movie, poster_url,
                    channel_mapping=mapping, series_slug=slug, post_style=post_style,
                )

                if a.get("has_poster"):
                    await self.client.edit_message_caption(
                        chat_id=self.channel,
                        message_id=msg_id,
                        caption=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                    )
                else:
                    await self.client.edit_message_text(
                        chat_id=self.channel,
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


# Singleton
library_manager: LibraryManager | None = None
