"""Callback query router — handles all inline button presses."""

from __future__ import annotations
import asyncio
import logging
import os
import re

from bot.telegram import Client, enums
from bot.telegram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from api.client import api
from api.models import Quality, VideoServer
from bot import keyboards as kb
from bot.auth import require_approved
import bot.logger
from bot.downloader import (
    download_and_upload, make_episode_filename, make_movie_filename,
    sanitize_filename, download_job_manager,
)
from utils.helpers import esc, truncate, short_slug, extract_series_slug, slug_to_title

log = logging.getLogger(__name__)


async def callback_router(client: Client, query: CallbackQuery):
    data = query.data

    user = query.from_user
    user_id = user.id if user else 0
    from bot.database import db
    if db and user_id and await db.is_banned(user_id):
        await query.answer("⛔ You are banned from using this bot.", show_alert=True)
        return

    # Real Download Cancel Button handler (Issues #22 & #23)
    if data and data.startswith("cendl:"):
        job_id = data.split(":", 1)[1]
        is_admin = await db.is_admin(user_id) if db and user_id else False
        ok, msg = await download_job_manager.cancel_job(job_id, user_id, is_admin)
        try:
            await query.answer(msg, show_alert=not ok)
        except Exception:
            pass
        return

    try:
        await query.answer()
    except Exception:
        pass

    try:
        if data == "m:main":
            await _send_text(query, "🎌 <b>AnimeDekho Bot</b>\n\nChoose an option:", kb.main_menu())

        elif data == "m:genres":
            await _handle_genres(query)

        elif data.startswith("rp:"):
            await _handle_series_listing(query, int(data.split(":")[1]))

        elif data.startswith("mp:"):
            await _handle_movies_listing(query, int(data.split(":")[1]))

        elif data.startswith("sr:"):
            await _handle_series_detail(client, query, data[3:])

        elif data.startswith("mr:"):
            await _handle_movie_detail(client, query, data[3:])

        elif data.startswith("se:"):
            parts = data.split(":")
            await _handle_season(query, slug=parts[1], season=int(parts[2]))

        elif data.startswith("ep:"):
            await _handle_episode(query, data[3:])

        elif data.startswith("dl:"):
            # dl:quality:ep_slug — auto server selection
            parts = data.split(":", 2)
            await _handle_download(
                client, query,
                quality_pref=parts[1],
                ep_slug=parts[2],
            )

        elif data.startswith("mdl:"):
            # mdl:quality:movie_slug — auto server selection
            parts = data.split(":", 2)
            await _handle_movie_download(
                client, query,
                quality_pref=parts[1],
                movie_slug=parts[2],
            )

        elif data.startswith("bat:"):
            # bat:series_slug:season — show quality picker
            parts = data.split(":", 2)
            await _handle_batch_picker(query, slug=parts[1], season=int(parts[2]))

        elif data.startswith("bq:"):
            # bq:series_slug:season:quality — execute batch download
            parts = data.split(":", 3)
            await _handle_batch_download(
                client, query,
                slug=parts[1],
                season=int(parts[2]),
                quality_pref=parts[3],
            )

        elif data.startswith("ct:"):
            parts = data.split(":")
            await _handle_category(query, cat_slug=parts[1], page=int(parts[2]))

    except Exception as e:
        log.exception("Callback error for %s", data)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_error(f"callback:{data}", str(e))
        await _safe_edit(query, f"⚠️ Error: {esc(str(e)[:200])}\n\nTry /start")


# ── Handler implementations ───────────────────────────────────────

async def _send_photo_with_fallback(
    client: Client,
    chat_id: int,
    photo_url: str | None,
    caption: str,
    reply_markup=None,
    is_movie: bool = False,
    query: CallbackQuery | None = None,
):
    """
    Robust photo sender with multi-tier fallback (Issue #9):
    1. Try sending the primary photo_url (AniList / scraped poster).
    2. If primary fails or is missing, try DEFAULT_MOVIE_THUMB or DEFAULT_ANIME_THUMB from config.
    3. If default thumb also fails or is missing, fall back cleanly to text message.
    Never crashes or displays an unhandled error to the user!
    """
    from config import Config
    fallback_url = getattr(Config, "DEFAULT_MOVIE_THUMB" if is_movie else "DEFAULT_ANIME_THUMB", "")

    async def _try_delete():
        if query and query.message:
            try:
                await query.message.delete()
            except Exception:
                pass

    # 1. Try primary photo
    if photo_url:
        try:
            await _try_delete()
            await client.send_photo(
                chat_id=chat_id,
                photo=photo_url,
                caption=caption[:1024],
                parse_mode=enums.ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return
        except Exception as e:
            log.warning("Primary photo send failed (%s), trying fallback thumb: %s", str(photo_url)[:60], e)

    # 2. Try default fallback photo
    if fallback_url and fallback_url != photo_url:
        try:
            await _try_delete()
            await client.send_photo(
                chat_id=chat_id,
                photo=fallback_url,
                caption=caption[:1024],
                parse_mode=enums.ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return
        except Exception as e:
            log.warning("Fallback photo send failed (%s): %s", str(fallback_url)[:60], e)

    # 3. Clean text fallback (zero error)
    if query:
        await _send_text(query, caption, reply_markup)
    else:
        await client.send_message(
            chat_id=chat_id,
            text=caption[:4096],
            parse_mode=enums.ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


async def _handle_series_listing(q: CallbackQuery, page: int):
    result = await api.get_recent_series(page)
    if not result.items:
        await _send_text(q, "No series found.")
        return
    markup = kb.listing_page(result.items, page, result.max_page, "rp", "sr", "📺")
    await _send_text(q, f"📺 <b>Recent Series</b> — Page {page}/{result.max_page}", markup)


async def _handle_movies_listing(q: CallbackQuery, page: int):
    result = await api.get_recent_movies(page)
    if not result.items:
        await _send_text(q, "No movies found.")
        return
    markup = kb.listing_page(result.items, page, result.max_page, "mp", "mr", "🎬")
    await _send_text(q, f"🎬 <b>Movies</b> — Page {page}/{result.max_page}", markup)


async def _handle_series_detail(client: Client, q: CallbackQuery, slug: str):
    # V2 #11-#13: preserve selected-source context. If this slug came from a
    # fallback provider (registry hit), route straight to that provider's
    # series instead of forcing AnimeDekho first (which caused wrong routing).
    from extractors.multisource import multi_source_manager
    reg_source = multi_source_manager.get_source_for_slug(slug)
    series = None
    if reg_source and reg_source != "AnimeDekho":
        try:
            log.info("Source-context hit: slug '%s' belongs to %s, using fallback series directly", slug, reg_source)
            series = await multi_source_manager.get_fallback_series(slug)
        except Exception as e:
            log.warning("Fallback series failed for '%s' (%s), trying AnimeDekho...", slug, e)
            series = None
    if series is None:
        try:
            series = await api.get_series(slug)
        except Exception as e:
            log.warning("api.get_series failed for '%s' (%s), trying multi_source_manager...", slug, e)
            series = await multi_source_manager.get_fallback_series(slug)

    if not series:
        await _send_text(q, "⚠️ Could not load series details. Please try another anime.")
        return

    # V2 #12: admin/user DM always shows which website/source this came from.
    src_tag = getattr(series, "source", "") or reg_source or "AnimeDekho"
    text = f"📺 <b>{esc(series.title)}</b>\n"
    text += f"🔗 <i>Source: {esc(src_tag)}</i>\n\n"
    if series.genres:
        text += f"🏷 {', '.join(series.genres[:6])}\n"
    if series.description:
        text += f"\n{esc(truncate(series.description, 350))}\n"

    if not series.seasons:
        text += "\n⚠️ No episodes found on this page."
        markup = kb.main_menu()
    else:
        text += f"\n📂 <b>{series.season_count} Season(s)</b> · {series.total_episodes} episodes\nSelect a season:"
        markup = kb.season_picker(series)

    # Cache poster for later use in library
    if series.poster:
        _poster_cache[series.slug] = series.poster

    await _send_photo_with_fallback(
        client=client,
        chat_id=q.message.chat.id,
        photo_url=series.poster,
        caption=text,
        reply_markup=markup,
        is_movie=False,
        query=q,
    )


async def _handle_movie_detail(client: Client, q: CallbackQuery, slug: str):
    movie = None
    try:
        movie = await api.get_movie(slug)
    except Exception as e:
        log.warning("api.get_movie failed for '%s' (%s), using fallback movie skeleton...", slug, e)
        from api.models import Movie
        from utils.helpers import slug_to_title
        title = slug_to_title(slug)
        movie = Movie(title=title, slug=slug, url="", description="Available via fallback network.", servers=[])

    # Skeleton: show default quality buttons instantly, resolve on download
    from config.settings import settings
    default_qualities = set(settings.site.default_qualities)

    text = f"🎬 <b>{esc(movie.title)}</b>\n\n"
    if movie.genres:
        text += f"🏷 {', '.join(movie.genres[:6])}\n"
    if movie.description:
        text += f"\n{esc(truncate(movie.description, 350))}\n"
    text += "\n📊 <b>Select quality to download:</b>"

    # Store raw servers — will be resolved on download
    if movie.poster:
        _poster_cache[slug] = movie.poster
    _store_servers(q.message.chat.id, f"movie:{slug}", movie.servers, movie.title, poster_url=movie.poster or "")

    markup = kb.quality_picker(default_qualities, slug, "mp:1", is_movie=True)

    await _send_photo_with_fallback(
        client=client,
        chat_id=q.message.chat.id,
        photo_url=movie.poster,
        caption=text,
        reply_markup=markup,
        is_movie=True,
        query=q,
    )


async def _handle_season(q: CallbackQuery, slug: str, season: int):
    series = None
    try:
        series = await api.get_series(slug)
    except Exception as e:
        log.warning("api.get_series failed for '%s' (%s) in _handle_season, trying fallback...", slug, e)
        from extractors.multisource import multi_source_manager
        series = await multi_source_manager.get_fallback_series(slug)

    if not series:
        await _safe_edit(q, f"⚠️ Season {season} not found.")
        return

    s = series.seasons.get(season)
    if not s:
        await _safe_edit(q, f"⚠️ Season {season} not found.")
        return

    text = (
        f"📺 <b>{esc(series.title)}</b>\n"
        f"📂 Season {season} — {s.episode_count} episode(s)\n\n"
        f"Select an episode:"
    )
    markup = kb.episode_picker(slug, season, s.episodes)

    try:
        await q.edit_message_caption(caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        await _safe_edit(q, text, markup)


async def _handle_episode(q: CallbackQuery, ep_slug: str):
    """Show episode with skeleton quality buttons instantly — no server resolution."""
    try:
        episode = await api.get_episode(ep_slug)
    except Exception as e:
        log.warning("api.get_episode failed for '%s': %s", ep_slug, e)
        m = re.match(r".*-(\d+)x(\d+)", ep_slug)
        s_num = int(m.group(1)) if m else 1
        e_num = int(m.group(2)) if m else 1
        series_slug = extract_series_slug(ep_slug)
        series_name = slug_to_title(series_slug) if series_slug else "Episode"
        title = f"{series_name} S{s_num}E{e_num:02d}"
        from api.models import Episode
        episode = Episode(number=e_num, slug=ep_slug, season=s_num, title=title, servers=[])

    # Show default quality buttons immediately (like YouTube skeleton)
    from config.settings import settings
    default_qualities = set(settings.site.default_qualities)  # ["480p", "720p", "1080p"]

    text = f"▶️ <b>{esc(episode.title)}</b>\n\n"
    text += "📊 <b>Select quality to download:</b>"

    series_slug = extract_series_slug(ep_slug)
    back_cb = f"sr:{short_slug(series_slug)}" if series_slug else "rp:1"

    # Store raw (unresolved) servers — will be resolved on download
    _store_servers(q.message.chat.id, ep_slug, episode.servers, episode.title)

    markup = kb.quality_picker(default_qualities, ep_slug, back_cb, is_movie=False)

    try:
        await q.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


def _cache_record_is_dead(err: str) -> bool:
    """True only when a cached file_id is provably gone from Telegram.

    Type mismatches (video file_id sent as document) and flood/network
    errors must NEVER purge a valid library record (issue #30: the old
    document-only path deleted every valid video cache record).
    """
    e = (err or "").lower()
    return any(k in e for k in (
        "media_empty", "file_reference", "file_migrate",
        "media_invalid", "message_invalid", "file_is_gone",
    ))


class _CachedSendError(Exception):
    """Both media-type send attempts failed; carries BOTH error strings so
    dead-file detection can see MEDIA_EMPTY/FILE_REFERENCE from either."""

    def __init__(self, video_err, doc_err):
        self.video_err = str(video_err)
        self.doc_err = str(doc_err)
        super().__init__(f"video: {self.video_err} | doc: {self.doc_err}")


async def _send_cached_any(send_video, send_doc):
    """Deliver a cached file_id trying BOTH media types.

    Video file_ids cannot be sent via reply_document (client-side
    "Expected DOCUMENT, got VIDEO file id" error) and vice versa — try
    video first (uploads are usually video), fall back to document. The
    caller decides record purging via _cache_record_is_dead(str(err)).
    """
    video_err = None
    try:
        return await send_video()
    except Exception as e:
        video_err = e
    try:
        return await send_doc()
    except Exception as e_doc:
        raise _CachedSendError(video_err, e_doc) from e_doc


async def _real_series_title(series_slug: str, fallback: str = "") -> str:
    """Resolve the REAL series title for captions/filenames/persistence.

    slug_to_title() rebuilds the title from the URL slug and mangles names
    like "Ranma ½" (slug ranma-1-2) into "Ranma 1 2" — issue #30 caption.
    Authority order: AniList/animedekho API → stored DB record → slug guess.
    """
    if not series_slug:
        return fallback
    try:
        _sd = await api.get_series(series_slug)
        _t = getattr(_sd, "title", None) if _sd else None
        if _t and len(str(_t).strip()) >= 3:
            return str(_t).strip()
    except Exception:
        pass
    try:
        from bot.database import db as _db
        if _db:
            _doc = await _db.files.find_one({"series_slug": series_slug})
            _t = (_doc or {}).get("series_title")
            if _t and len(str(_t).strip()) >= 3:
                return str(_t).strip()
    except Exception:
        pass
    return fallback or slug_to_title(series_slug)


async def _handle_download(client: Client, q: CallbackQuery, quality_pref: str, ep_slug: str):
    """Handle single episode download — resolves servers and falls back across multi-source chain if needed."""
    chat_id = q.message.chat.id
    user = q.from_user
    user_id = user.id if user else 0

    # Immediate feedback
    try:
        await q.answer("⏳ Finding video quality...", show_alert=False)
    except Exception:
        pass

    # Get stored server data (may be raw/unresolved from skeleton episode view)
    data = _get_servers(chat_id, ep_slug)
    if not data:
        try:
            episode = await api.get_episode(ep_slug)
            _store_servers(chat_id, ep_slug, episode.servers, episode.title)
            data = _get_servers(chat_id, ep_slug)
        except Exception as e:
            log.warning("get_episode failed in _handle_download for %s: %s", ep_slug, e)

    title = data.get("title", ep_slug) if data else ep_slug
    raw_servers = data.get("servers", []) if data else []

    # Extract season/episode info for filename and ToonFlix lookup
    import re
    m = re.match(r".*-(\d+)x(\d+)", ep_slug)
    season = int(m.group(1)) if m else 1
    ep_num = int(m.group(2)) if m else 1
    series_slug = extract_series_slug(ep_slug) or ""
    series_title = await _real_series_title(series_slug, title)
    episode_key = f"S{season:02d}E{ep_num:02d}" if season and ep_num else ""

    # ── Immediate Cache Check: Skip all resolution if already downloaded ──
    from bot.database import db
    if db and series_slug and episode_key:
        cached_doc = await db.find_cached_file(series_slug or series_title, episode_key, quality_pref)
        if cached_doc and cached_doc.get("file_id"):
            cached_q = cached_doc.get("quality", quality_pref)
            disp_title = f"{series_title or cached_doc.get('series_title', 'Anime')} {episode_key}"
            filename = make_episode_filename(series_title or cached_doc.get("series_title", "Anime"), season, ep_num, cached_q)
            try:
                _cap = f"📦 <b>{esc(disp_title)}</b> [{cached_q}]\n<i>⚡ From library — instant delivery!</i>"
                await _send_cached_any(
                    lambda: q.message.reply_video(video=cached_doc["file_id"], caption=_cap, parse_mode=enums.ParseMode.HTML),
                    lambda: q.message.reply_document(document=cached_doc["file_id"], file_name=filename, caption=_cap, parse_mode=enums.ParseMode.HTML),
                )
                return
            except Exception as e:
                log.warning("Cached file delivery failed (both media types): %s", e)
                if _cache_record_is_dead(str(e)):
                    await db.files.delete_one({"_id": cached_doc["_id"]})

    # Send immediate progress status message with cancel button (Issues #22 & #23)
    job_id = download_job_manager.create_job(user_id, title)
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{job_id}")]])
    progress_msg = await q.message.reply_text(
        f"⏳ <b>Finding video quality...</b>\n\n📺 <b>{esc(title)}</b> [{quality_pref}]\n<i>Searching fastest servers...</i>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=cancel_markup,
    )
    job = download_job_manager.get_job(job_id)
    if job:
        job.progress_msg = progress_msg

    if download_job_manager.is_job_cancelled(job_id):
        return

    # ── Source tier ordering — owner-configurable via /source ─────────────
    # The default source (default: AnimeDekho, persisted in DB config)
    # resolves FIRST; the other tier is the automatic fallback. Inside the
    # MultiSource tier, /source also promotes the chosen extractor
    # (resolve_episode_stream applies it when no button preference exists).
    candidates: list[tuple[VideoServer, Quality]] = []
    has_exact = False
    is_4k = quality_pref.lower() in ("4k", "2160p", "2160")
    found_match = False
    # V2 #14: rich diagnostics for the failure card.
    diag_steps: list[str] = []
    from bot.source_config import get_default_source, is_source
    default_src = await get_default_source()
    ad_is_default = is_source(default_src, "AnimeDekho")
    diag_steps.append(f"Default source: {default_src}")

    def _is_4k_satisfying(q_str: str) -> bool:
        q = q_str.lower()
        return any(k in q for k in ("4k", "2160", "uhd"))

    async def _step_multisource() -> None:
        """Tier: direct-file extractors via the Multi-Source manager."""
        nonlocal candidates, has_exact, found_match
        try:
            from extractors.multisource import multi_source_manager
            # V2 #13: keep the user's selected-button source first; the
            # global /source default is applied inside resolve_episode_stream.
            preferred_src = multi_source_manager.get_source_for_slug(series_slug) if series_slug else None
            log.info("Trying direct sources for '%s' S%dE%d [%s] (preferred=%s, default=%s)", series_title, season, ep_num, quality_pref, preferred_src, default_src)
            ms_res = await multi_source_manager.resolve_episode_stream(
                series_title=series_title,
                season=season,
                episode=ep_num,
                quality_pref=quality_pref,
                series_slug=series_slug,
                preferred_source=preferred_src,
            )
            if ms_res and ms_res.get("url"):
                ms_q = ms_res.get("quality", quality_pref).lower()
                ms_srv = VideoServer(
                    name=ms_res.get("source", "MultiSource"),
                    server_id=0,
                    player_url=ms_res["url"],
                    qualities=[Quality(resolution=ms_res.get("quality", quality_pref), url=ms_res["url"])],
                )
                if series_slug and not _poster_cache.get(series_slug) and ms_res.get("poster"):
                    from utils.anilist import resolve_best_poster
                    res_p = await resolve_best_poster(series_title, ms_res.get("poster"))
                    if res_p:
                        _poster_cache[series_slug] = res_p
                if (is_4k and _is_4k_satisfying(ms_q)) or (not is_4k and ms_q == quality_pref.lower()):
                    candidates.insert(0, (ms_srv, ms_srv.qualities[0]))
                    has_exact = True
                    found_match = True
                    log.info("%s provided exact/4K stream [%s] for '%s' S%dE%d", ms_res.get("source"), ms_res.get("quality"), series_title, season, ep_num)
                    diag_steps.append(f"{ms_res.get('source')}: exact {ms_res.get('quality')} ✓")
                else:
                    candidates.append((ms_srv, ms_srv.qualities[0]))
                    diag_steps.append(f"{ms_res.get('source')}: {ms_res.get('quality')} (non-exact)")
            else:
                diag_steps.append("MultiSource: no result")
        except Exception as e:
            log.warning("Multi-source manager resolution error: %s", e)
            diag_steps.append(f"MultiSource: error {str(e)[:80]}")

    async def _step_animedekho() -> None:
        """Tier: AnimeDekho API server links (default source)."""
        nonlocal candidates, has_exact
        try:
            resolved = await _lazy_resolve_servers(raw_servers, quality_pref) if raw_servers else []
            if resolved:
                _store_servers(chat_id, ep_slug, resolved, title)
            ad_cands = _find_quality_candidates(resolved or raw_servers, quality_pref) if (resolved or raw_servers) else []
            if ad_cands:
                ad_has_exact = any(q.resolution.lower() == quality_pref.lower() for _, q in ad_cands)
                if ad_has_exact and not has_exact:
                    # No other exact yet — AnimeDekho exact becomes primary.
                    candidates = ad_cands + candidates
                    has_exact = True
                    diag_steps.append(f"AnimeDekho: exact {quality_pref} ✓")
                else:
                    # Other exact already leads — AnimeDekho stays behind it.
                    candidates.extend(ad_cands)
                    if not has_exact:
                        has_exact = any(q.resolution.lower() == quality_pref.lower() for _, q in candidates)
                    diag_steps.append(f"AnimeDekho: {len(ad_cands)} candidate(s)")
            else:
                diag_steps.append("AnimeDekho: no candidates" if raw_servers else "AnimeDekho: no servers (403/empty)")
        except Exception as e:
            log.warning("AnimeDekho fallback resolution error: %s", e)
            diag_steps.append(f"AnimeDekho: error {str(e)[:80]}")

    def _needs_next_tier() -> bool:
        return (not candidates) or (not has_exact) or (is_4k and not found_match)

    if ad_is_default:
        # /source animedekho (default): the API tier resolves first; the
        # direct scrapers only run when it misses quality or is unreachable.
        await _step_animedekho()
        if _needs_next_tier():
            await _step_multisource()
    else:
        # V2 #15 baseline: direct-file sources first, AnimeDekho last.
        await _step_multisource()
        if _needs_next_tier():
            await _step_animedekho()

    # Step 3: AnimeDrive — skip when MultiSource already delivered that provider.
    if (not candidates or not has_exact or (is_4k and not found_match)) and not any(
        "animedrive" in (s.name or "").lower() for s, _ in candidates
    ):
        try:
            from extractors.animedrive import animedrive
            log.info("Checking AnimeDrive fallback for '%s' S%dE%d [%s]", series_title, season, ep_num, quality_pref)
            ad_res = await animedrive.resolve_episode(series_title, season=season, episode=ep_num, quality_pref=quality_pref)
            if ad_res and ad_res.get("url"):
                ad_q = ad_res.get("quality", "").lower()
                ad_srv = VideoServer(
                    name="AnimeDrive",
                    server_id=0,
                    player_url=ad_res["url"],
                    qualities=[Quality(resolution=ad_res["quality"], url=ad_res["url"])],
                )
                if series_slug and not _poster_cache.get(series_slug):
                    from utils.anilist import resolve_best_poster
                    res_p = await resolve_best_poster(series_title, ad_res.get("poster"))
                    if res_p:
                        _poster_cache[series_slug] = res_p
                if ((is_4k and _is_4k_satisfying(ad_q)) or (not is_4k and ad_q == quality_pref.lower())) and not has_exact:
                    candidates.insert(0, (ad_srv, ad_srv.qualities[0]))
                    has_exact = True
                    found_match = True
                    log.info("AnimeDrive provided exact/4K stream [%s] for '%s' S%dE%d", ad_res["quality"], series_title, season, ep_num)
                    diag_steps.append(f"AnimeDrive: exact {ad_res.get('quality')} ✓")
                else:
                    candidates.append((ad_srv, ad_srv.qualities[0]))
                    diag_steps.append(f"AnimeDrive: {ad_res.get('quality')} (appended)")
            else:
                diag_steps.append("AnimeDrive: no result")
        except Exception as e:
            log.warning("AnimeDrive resolution error: %s", e)
            diag_steps.append(f"AnimeDrive: error {str(e)[:80]}")

    # Step 4: ToonFlix — same direct-first respect.
    if (not candidates or not has_exact or (is_4k and not found_match)) and not any(
        "toonflix" in (s.name or "").lower() for s, _ in candidates
    ):
        try:
            from extractors.toonflix import toonflix
            log.info("Checking ToonFlix fallback for '%s' S%dE%d [%s]", series_title, season, ep_num, quality_pref)
            tf_res = await toonflix.resolve_episode(series_title, season=season, episode=ep_num, quality_pref=quality_pref)
            if tf_res and tf_res.get("url"):
                if series_slug and not _poster_cache.get(series_slug):
                    from utils.anilist import resolve_best_poster
                    res_p = await resolve_best_poster(series_title, tf_res.get("poster"))
                    if res_p:
                        _poster_cache[series_slug] = res_p
                tf_q = tf_res.get("quality", "").lower()
                tf_srv = VideoServer(
                    name="ToonFlix",
                    server_id=0,
                    player_url=tf_res["url"],
                    qualities=[Quality(resolution=tf_res["quality"], url=tf_res["url"])],
                )
                if ((is_4k and _is_4k_satisfying(tf_q)) or (not is_4k and tf_q == quality_pref.lower())) and not has_exact:
                    candidates.insert(0, (tf_srv, tf_srv.qualities[0]))
                    has_exact = True
                    found_match = True
                    log.info("ToonFlix provided exact/4K %s stream candidate", tf_res["quality"])
                    diag_steps.append(f"ToonFlix: exact {tf_res.get('quality')} ✓")
                else:
                    candidates.append((tf_srv, tf_srv.qualities[0]))
                    diag_steps.append(f"ToonFlix: {tf_res.get('quality')} (appended)")
            else:
                diag_steps.append("ToonFlix: no result")
        except Exception as e:
            log.warning("ToonFlix resolution error: %s", e)
            diag_steps.append(f"ToonFlix: error {str(e)[:80]}")

    if not candidates:
        if not download_job_manager.is_job_cancelled(job_id):
            # V2 #14: detailed diagnostic instead of generic failure.
            diag_txt = " • ".join(diag_steps) if diag_steps else "no sources attempted"
            await progress_msg.edit_text(
                f"⚠️ <b>No downloadable URL found.</b>\n"
                f"┌ 📺 {esc(title)} [{esc(quality_pref)}]\n"
                f"├ 🔍 Stages: {esc(diag_txt[:400])}\n"
                f"└ 💡 Try another quality/episode or /bypass the source URL.",
                parse_mode=enums.ParseMode.HTML,
            )
        download_job_manager.remove_job(job_id)
        return

    if download_job_manager.is_job_cancelled(job_id):
        return

    primary_server, primary_quality = candidates[0]

    # Warn user if quality doesn't match what they requested
    if primary_quality.resolution != quality_pref and primary_quality.resolution != "auto":
        log.info("Quality fallback: requested %s, got %s", quality_pref, primary_quality.resolution)

    filename = make_episode_filename(series_title, season, ep_num, primary_quality.resolution)
    episode_key = f"S{season}E{ep_num:02d}" if season and ep_num else ""

    # ── Duplicate check: send cached file if already downloaded ──
    from bot.database import db
    if db and series_slug and episode_key:
        cached_fid = await db.get_cached_file(series_slug, primary_quality.resolution, episode_key)
        if cached_fid:
            try:
                _cap = f"📦 <b>{esc(title)}</b> [{primary_quality.resolution}]\n<i>⚡ From library — instant delivery!</i>"
                await _send_cached_any(
                    lambda: q.message.reply_video(video=cached_fid, caption=_cap, parse_mode=enums.ParseMode.HTML),
                    lambda: q.message.reply_document(document=cached_fid, file_name=filename, caption=_cap, parse_mode=enums.ParseMode.HTML),
                )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
                download_job_manager.remove_job(job_id)
                return
            except Exception as e:
                log.warning("Cached file delivery failed (both media types), re-downloading: %s", e)
                if _cache_record_is_dead(str(e)):
                    await db.files.delete_one({
                        "series_slug": series_slug,
                        "quality": primary_quality.resolution,
                        "episode_key": episode_key,
                    })

    # Get poster from cache — if not cached, fetch from API
    poster_url = _poster_cache.get(series_slug, "") if series_slug else ""
    if not poster_url and series_slug:
        try:
            series_data = await api.get_series(series_slug)
            if series_data and series_data.poster:
                poster_url = series_data.poster
                _poster_cache[series_slug] = poster_url
        except Exception:
            pass

    # Log
    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_download_start(
            user.id, user.username or str(user.id), title, primary_quality.resolution
        )

    # Send progress message
    quality_label = primary_quality.resolution
    if is_4k and primary_quality.resolution.lower() not in ("4k", "2160p", "2160"):
        quality_label = f"4K [{primary_quality.resolution}]"
    elif primary_quality.resolution != quality_pref and primary_quality.resolution != "auto":
        quality_label = f"{primary_quality.resolution} (requested {quality_pref})"

    try:
        await progress_msg.edit_text(
            f"📥 <b>Starting download:</b> {esc(title)} [{quality_label}]",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=cancel_markup,
        )
    except Exception:
        pass

    asyncio.create_task(
        _do_download(
            client, chat_id, candidates, filename, title, progress_msg, user,
            series_slug=series_slug or "",
            episode_key=episode_key,
            poster_url=poster_url,
            job_id=job_id,
        )
    )


async def _handle_movie_download(client: Client, q: CallbackQuery, quality_pref: str, movie_slug: str):
    """Handle movie download — resolves servers with multi-server fallback."""
    chat_id = q.message.chat.id
    user = q.from_user

    data = _get_servers(chat_id, f"movie:{movie_slug}")
    if not data:
        movie = await api.get_movie(movie_slug)
        _store_servers(chat_id, f"movie:{movie_slug}", movie.servers, movie.title)
        data = _get_servers(chat_id, f"movie:{movie_slug}")

    if not data or not data["servers"]:
        await _safe_edit(q, "⚠️ No servers found. Please go back and try again.")
        return

    title = data.get("title", slug_to_title(movie_slug))
    poster_url = (data.get("poster_url") if data else "") or _poster_cache.get(movie_slug, "")

    # ── Immediate Cache Check: Avoid scraping if movie already downloaded ──
    from bot.database import db
    if db:
        cached_doc = await db.find_cached_file(movie_slug, "movie", quality_pref)
        if cached_doc and cached_doc.get("file_id"):
            cached_q = cached_doc.get("quality", quality_pref)
            filename = make_movie_filename(title, cached_q)
            try:
                _cap = f"📦 <b>{esc(title)}</b> [{cached_q}]\n<i>⚡ From library — instant delivery!</i>"
                await _send_cached_any(
                    lambda: q.message.reply_video(video=cached_doc["file_id"], caption=_cap, parse_mode=enums.ParseMode.HTML),
                    lambda: q.message.reply_document(document=cached_doc["file_id"], file_name=filename, caption=_cap, parse_mode=enums.ParseMode.HTML),
                )
                return
            except Exception as e:
                log.warning("Cached movie delivery failed (both media types): %s", e)
                if _cache_record_is_dead(str(e)):
                    await db.files.delete_one({"_id": cached_doc["_id"]})

    user_id = user.id if user else 0
    job_id = download_job_manager.create_job(user_id, title)
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{job_id}")]])

    progress_msg = await q.message.reply_text(
        f"⏳ <b>Finding video quality...</b>\n\n🎬 <b>{esc(title)}</b> [{quality_pref}]\n<i>Searching fastest servers...</i>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=cancel_markup,
    )
    job = download_job_manager.get_job(job_id)
    if job:
        job.progress_msg = progress_msg

    if download_job_manager.is_job_cancelled(job_id):
        return

    raw_servers = data["servers"]

    resolved = await _lazy_resolve_servers(raw_servers, quality_pref)
    if resolved:
        _store_servers(chat_id, f"movie:{movie_slug}", resolved, title, poster_url=poster_url)

    candidates = _find_quality_candidates(resolved or raw_servers, quality_pref)

    # Quality fallback logic for movies (V2 #15: direct first, AnimeDekho last):
    # Direct-file MultiSource → AnimeDrive (DEFAULT for 4K) → ToonFlix.
    # AnimeDekho candidates above are the fallback tier, not primary.
    has_exact = any(q.resolution.lower() == quality_pref.lower() for _, q in candidates)
    is_4k = quality_pref.lower() in ("4k", "2160p", "2160")
    from bot.source_config import get_default_source, is_source
    ad_default = is_source(await get_default_source(), "AnimeDekho")

    def _is_4k_satisfying(q_str: str) -> bool:
        q = q_str.lower()
        return any(k in q for k in ("4k", "2160", "uhd"))

    # Step 0 FIRST (movies had no direct-source step at all): MultiSource.
    try:
        from extractors.multisource import multi_source_manager
        from extractors.multisource import multi_source_manager as _msm
        _pref = _msm.get_source_for_slug(movie_slug)
        log.info("V2#15 movies: trying direct sources first for '%s' [%s] (preferred=%s)", title, quality_pref, _pref)
        ms_res = await multi_source_manager.resolve_episode_stream(
            series_title=title, season=1, episode=1,
            quality_pref=quality_pref, series_slug=movie_slug,
            preferred_source=_pref,
        )
        if ms_res and ms_res.get("url"):
            ms_q = ms_res.get("quality", quality_pref).lower()
            ms_srv = VideoServer(
                name=ms_res.get("source", "MultiSource"),
                server_id=0,
                player_url=ms_res["url"],
                qualities=[Quality(resolution=ms_res.get("quality", quality_pref), url=ms_res["url"])],
            )
            if not poster_url and ms_res.get("poster"):
                from utils.anilist import resolve_best_poster
                res_p = await resolve_best_poster(title, ms_res.get("poster"))
                if res_p:
                    poster_url = res_p
                    _poster_cache[movie_slug] = poster_url
            if (is_4k and _is_4k_satisfying(ms_q)) or (not is_4k and ms_q == quality_pref.lower()):
                if ad_default and has_exact:
                    # /source default (AnimeDekho) already leads candidates —
                    # the direct stream joins as fallback tier.
                    candidates.append((ms_srv, ms_srv.qualities[0]))
                else:
                    # Direct exact outranks the fallback exact (direct-first).
                    candidates.insert(0, (ms_srv, ms_srv.qualities[0]))
                has_exact = True
            else:
                candidates.append((ms_srv, ms_srv.qualities[0]))
    except Exception as e:
        log.warning("MultiSource movie resolution error: %s", e)

    if not has_exact or is_4k:
        found_4k = any(
            _is_4k_satisfying(q.resolution) for _, q in candidates
        ) if is_4k else False

        # Step 1: Secondary - AnimeDrive (DEFAULT for 4K)
        try:
            from extractors.animedrive import animedrive
            log.info("Checking AnimeDrive for movie '%s' [%s]", title, quality_pref)
            ad_res = await animedrive.resolve_episode(title, season=1, episode=1, quality_pref=quality_pref)
            if ad_res and ad_res.get("url"):
                if not poster_url:
                    from utils.anilist import resolve_best_poster
                    res_p = await resolve_best_poster(title, ad_res.get("poster"))
                    if res_p:
                        poster_url = res_p
                        _poster_cache[movie_slug] = poster_url
                ad_q = ad_res.get("quality", "").lower()
                ad_srv = VideoServer(
                    name="AnimeDrive",
                    server_id=0,
                    player_url=ad_res["url"],
                    qualities=[Quality(resolution=ad_res["quality"], url=ad_res["url"])],
                )
                if is_4k and _is_4k_satisfying(ad_q):
                    # Exact 4K or enhanced 1080p HQ tier found on AnimeDrive! AnimeDrive is default for 4K
                    candidates.insert(0, (ad_srv, ad_srv.qualities[0]))
                    has_exact = True
                    found_4k = True
                    log.info("AnimeDrive provided 4K-tier movie stream [%s] for '%s'", ad_res["quality"], title)
                elif not is_4k and ad_q == quality_pref.lower():
                    candidates.insert(0, (ad_srv, ad_srv.qualities[0]))
                    has_exact = True
                    log.info("Added AnimeDrive movie candidate [%s]", ad_res["quality"])
                else:
                    candidates.append((ad_srv, ad_srv.qualities[0]))
        except Exception as e:
            log.warning("AnimeDrive movie resolution error: %s", e)

        # Step 2: Tertiary - ToonFlix (if exact quality or 4K not found on AnimeDrive)
        if not has_exact or (is_4k and not found_4k):
            try:
                from extractors.toonflix import toonflix
                log.info("Checking ToonFlix fallback for movie '%s' [%s]", title, quality_pref)
                tf_res = await toonflix.resolve_episode(title, season=1, episode=1, quality_pref=quality_pref)
                if tf_res and tf_res.get("url"):
                    if not poster_url:
                        from utils.anilist import resolve_best_poster
                        res_p = await resolve_best_poster(title, tf_res.get("poster"))
                        if res_p:
                            poster_url = res_p
                            _poster_cache[movie_slug] = poster_url
                    tf_q = tf_res.get("quality", "").lower()
                    tf_srv = VideoServer(
                        name="ToonFlix",
                        server_id=0,
                        player_url=tf_res["url"],
                        qualities=[Quality(resolution=tf_res["quality"], url=tf_res["url"])],
                    )
                    if (is_4k and _is_4k_satisfying(tf_q)) or (not is_4k and tf_q == quality_pref.lower()):
                        candidates.insert(0, (tf_srv, tf_srv.qualities[0]))
                        has_exact = True
                        found_4k = True
                        log.info("ToonFlix provided 4K-tier %s movie candidate", tf_res["quality"])
                    else:
                        candidates.append((tf_srv, tf_srv.qualities[0]))
            except Exception as e:
                log.warning("ToonFlix movie resolution error: %s", e)

    if not candidates:
        if not download_job_manager.is_job_cancelled(job_id):
            await progress_msg.edit_text("⚠️ No downloadable URL found on any server.")
        download_job_manager.remove_job(job_id)
        return

    if download_job_manager.is_job_cancelled(job_id):
        return

    primary_server, primary_quality = candidates[0]
    filename = make_movie_filename(title, primary_quality.resolution)

    # ── Duplicate check for movies ──
    from bot.database import db
    if db:
        cached_fid = await db.get_cached_file(movie_slug, primary_quality.resolution, "movie")
        if cached_fid:
            try:
                _cap = f"📦 <b>{esc(title)}</b> [{primary_quality.resolution}]\n<i>⚡ From library — instant delivery!</i>"
                await _send_cached_any(
                    lambda: q.message.reply_video(video=cached_fid, caption=_cap, parse_mode=enums.ParseMode.HTML),
                    lambda: q.message.reply_document(document=cached_fid, file_name=filename, caption=_cap, parse_mode=enums.ParseMode.HTML),
                )
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
                download_job_manager.remove_job(job_id)
                return
            except Exception as e:
                log.warning("Cached movie file delivery failed (both media types): %s", e)
                if _cache_record_is_dead(str(e)):
                    await db.files.delete_one({
                        "series_slug": movie_slug,
                        "quality": primary_quality.resolution,
                        "episode_key": "movie",
                    })

    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_download_start(
            user.id, user.username or str(user.id), title, primary_quality.resolution
        )

    quality_label = primary_quality.resolution
    if is_4k and primary_quality.resolution.lower() not in ("4k", "2160p", "2160"):
        quality_label = f"4K [{primary_quality.resolution}]"
    elif primary_quality.resolution != quality_pref and primary_quality.resolution != "auto":
        quality_label = f"{primary_quality.resolution} (requested {quality_pref})"

    try:
        await progress_msg.edit_text(
            f"📥 <b>Starting download:</b> {esc(title)} [{quality_label}]",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=cancel_markup,
        )
    except Exception:
        pass

    asyncio.create_task(
        _do_download(
            client, chat_id, candidates, filename, title, progress_msg, user,
            series_slug=movie_slug,
            episode_key="movie",
            poster_url=poster_url,
            is_movie=True,
            job_id=job_id,
        )
    )


async def _handle_batch_picker(q: CallbackQuery, slug: str, season: int):
    """Show quality picker for batch download."""
    text = (
        f"📦 <b>Batch Download</b>\n"
        f"📺 {esc(slug_to_title(slug))} — Season {season}\n\n"
        f"Select quality for all episodes:"
    )
    markup = kb.batch_quality_picker(slug, season)
    await _safe_edit(q, text, markup)


async def _handle_batch_download(client: Client, q: CallbackQuery, slug: str, season: int, quality_pref: str):
    """Execute batch download for an entire season (V3 #6 batch lifecycle)."""
    from bot.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup
    chat_id = q.message.chat.id
    user = q.from_user

    # Get series info
    series = await api.get_series(slug)
    s = series.seasons.get(season)
    if not s or not s.episodes:
        await _safe_edit(q, f"⚠️ No episodes found for Season {season}.")
        return

    total = len(s.episodes)

    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_batch_start(
            user.id, user.username or str(user.id),
            series.title, season, total
        )

    # V3 #6: parent Batch Job + persistent cancel control. One cancel stops
    # this exact batch (episode children included), never other users' jobs.
    batch_id = download_job_manager.create_batch_job(
        user.id, f"{series.title} S{season} [{quality_pref}]", total)
    cancel_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{batch_id}")]])
    progress_msg = await q.message.reply_text(
        f"📦 <b>Batch Download Starting</b>\n"
        f"📺 {esc(series.title)} — Season {season}\n"
        f"📂 {total} episodes · Quality: {quality_pref}\n\n"
        f"⏳ Resolving episodes...",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=cancel_markup,
    )
    download_job_manager.get_job(batch_id).progress_msg = progress_msg

    # Run batch in background
    task = asyncio.create_task(
        _do_batch_download(
            client, chat_id, series, season, s.episodes,
            quality_pref, progress_msg, user, batch_id, cancel_markup,
        )
    )
    download_job_manager.attach_task(batch_id, task)


async def _resolve_destination_channel(series_slug: str, series_title: str = "", poster_url: str = "", language: str = "") -> int | None:
    """Check if series has a mapped channel (including language routes) or auto-create one if enabled."""
    from bot.database import db
    if not db or not series_slug:
        return None
    try:
        if not language and series_title:
            t_low = series_title.lower()
            for l_cand in ("hindi", "tamil", "telugu", "multi", "english"):
                if l_cand in t_low:
                    language = l_cand
                    break

        mapping = await db.get_channel_mapping(series_slug, language=language)
        if not mapping:
            auto_chan = await db.get_config("auto_channel_creation", default=False)
            from bot.userbot import userbot_manager
            if auto_chan and userbot_manager and userbot_manager.is_active:
                from utils.anilist import resolve_best_poster
                resolved_poster = await resolve_best_poster(
                    series_title or series_slug,
                    poster_url or _poster_cache.get(series_slug, "")
                )
                mapping = await userbot_manager.create_anime_channel(
                    series_title=series_title or series_slug,
                    series_slug=series_slug,
                    poster_url=resolved_poster,
                )
        if mapping and mapping.get("channel_id"):
            return mapping["channel_id"]
    except Exception as e:
        log.warning("Failed resolving destination channel for %s: %s", series_slug, e)
    return None


def _ms_refresh(series_title: str, season: int, episode: int, quality_pref: str, series_slug: str = ""):
    """Build a stale-URL refresher closure for MultiSource results."""
    async def _go():
        try:
            from extractors.multisource import multi_source_manager as _msm
            r = await _msm.resolve_episode_stream(
                series_title=series_title, season=season, episode=episode,
                quality_pref=quality_pref, series_slug=series_slug,
            )
            return r["url"] if r and r.get("url") else None
        except Exception:
            return None
    return _go


def _extractor_refresh(kind: str, series_title: str, season: int, episode: int, quality_pref: str):
    """Build a stale-URL refresher closure for AnimeDrive/ToonFlix results."""
    async def _go():
        try:
            if kind == "animedrive":
                from extractors.animedrive import animedrive as _ad
                r = await _ad.resolve_episode(series_title, season=season, episode=episode, quality_pref=quality_pref)
            else:
                from extractors.toonflix import toonflix as _tf
                r = await _tf.resolve_episode(series_title, season=season, episode=episode, quality_pref=quality_pref)
            return r["url"] if r and r.get("url") else None
        except Exception:
            return None
    return _go


async def _do_batch_download(client: Client, chat_id, series, season, episodes, quality_pref, progress_msg, user,
                       batch_id: str | None = None, cancel_markup=None):
    """Execute batch download sequentially with multi-server fallback.

    V3 #6: persistent cancel button on every progress edit; per-episode
    child jobs linked to the parent batch; batch cancel stops the exact
    batch immediately.
    """
    total = len(episodes)
    completed = 0
    skipped = 0  # Episodes served from cache
    sent_messages = [progress_msg]  # Track all messages for auto-delete
    cancelled = False

    # Check or auto-create dedicated series channel if userbot/mapping enabled
    dest_channel_id = await _resolve_destination_channel(series.slug, series.title, series.poster or "")

    def _batch_cancelled() -> bool:
        return bool(batch_id) and download_job_manager.batch_is_cancelled(batch_id)

    for i, ep in enumerate(episodes, 1):
        if _batch_cancelled():
            cancelled = True
            break
        try:
            cache_info = f"\n⚡ {skipped} from library" if skipped > 0 else ""
            # V3 #6: every progress edit keeps the persistent cancel button.
            await progress_msg.edit_text(
                f"📦 <b>Batch Downloading...</b> — {esc(series.title)} S{season}\n\n"
                f"📥 Episode {ep.number:02d} → Downloading ({i}/{total})\n"
                f"✅ {completed} completed{cache_info}",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=cancel_markup,
            )
        except Exception:
            pass

        try:
            # ── Check cache first — skip already downloaded episodes ──
            ep_key = f"S{season}E{ep.number:02d}"
            ep_job_id: str | None = None
            from bot.database import db
            if db:
                cached_fid = await db.get_cached_file(series.slug, quality_pref, ep_key)
                if cached_fid:
                    try:
                        _cap = f"📦 <b>{esc(series.title)} {ep_key}</b> [{quality_pref}]\n<i>⚡ From library — instant!</i>"
                        await _send_cached_any(
                            lambda: client.send_video(chat_id, video=cached_fid, caption=_cap, parse_mode=enums.ParseMode.HTML),
                            lambda: client.send_document(
                                chat_id,
                                document=cached_fid,
                                file_name=make_episode_filename(series.title, season, ep.number, quality_pref),
                                caption=_cap,
                                parse_mode=enums.ParseMode.HTML,
                            ),
                        )
                        completed += 1
                        skipped += 1
                        continue
                    except Exception as e:
                        log.warning("Batch cached delivery failed (both media types): %s", e)
                        if _cache_record_is_dead(str(e)):
                            await db.files.delete_one({
                                "series_slug": series.slug,
                                "quality": quality_pref,
                                "episode_key": ep_key,
                            })

            is_4k = quality_pref.lower() in ("4k", "2160p", "2160")
            success = False
            sent_msg = None
            chosen_q = Quality(resolution=quality_pref, url="")

            # Create a per-episode progress message + child job (V3 #6).
            ep_quality_label = f"4K Tier" if is_4k else quality_pref
            ep_msg = await client.send_message(
                chat_id,
                f"📥 <b>Downloading:</b> S{season}E{ep.number} [{ep_quality_label}]",
                parse_mode=enums.ParseMode.HTML,
            )
            sent_messages.append(ep_msg)
            ep_job_id = download_job_manager.create_episode_job(
                batch_id, user.id, f"{series.title} S{season}E{ep.number} [{quality_pref}]", ep_msg)
            if _batch_cancelled():
                download_job_manager.remove_job(ep_job_id)
                cancelled = True
                break

            if is_4k:
                # 4K Batch: AnimeDrive is default for 4K and enhanced 1080p HQ tiers
                try:
                    from extractors.animedrive import animedrive
                    ad_res = await animedrive.resolve_episode(series.title, season=season, episode=ep.number, quality_pref="4K")
                    if ad_res and ad_res.get("url"):
                        chosen_q = Quality(resolution=ad_res.get("quality", "4K"), url=ad_res["url"])
                        filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                        success, sent_msg = await download_and_upload(
                            chat_id, ad_res["url"], chosen_q.resolution,
                            filename,
                            f"{series.title} S{season}E{ep.number}",
                            ep_msg, client,
                                                        refresh_url=_extractor_refresh("animedrive", series.title, season, ep.number, "4K"),
                            referer=ad_res.get("referer", "https://hubcloud.ist/"),
                            poster_url=series.poster or ad_res.get("poster", ""),
                            destination_channel_id=dest_channel_id,
                        )
                except Exception as e:
                    log.warning("Batch AnimeDrive 4K error for ep %s: %s", ep.slug, e)

                if not success:
                    # Tertiary for 4K: ToonFlix
                    try:
                        from extractors.toonflix import toonflix
                        tf_res = await toonflix.resolve_episode(series.title, season=season, episode=ep.number, quality_pref="4K")
                        if tf_res and tf_res.get("url"):
                            chosen_q = Quality(resolution=tf_res.get("quality", "4K"), url=tf_res["url"])
                            filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                            success, sent_msg = await download_and_upload(
                                chat_id, tf_res["url"], chosen_q.resolution,
                                filename,
                                f"{series.title} S{season}E{ep.number}",
                                ep_msg, client,
                                                            refresh_url=_extractor_refresh("toonflix", series.title, season, ep.number, "4K"),
                                referer=tf_res.get("referer", "https://drive.toonflix.in/"),
                                poster_url=series.poster or tf_res.get("poster", ""),
                                destination_channel_id=dest_channel_id,
                            )
                    except Exception as e:
                        log.warning("Batch ToonFlix 4K error for ep %s: %s", ep.slug, e)

                if not success:
                    # Fallback: AnimeDekho top ranked server tier
                    episode_data = await api.get_episode(ep.slug)
                    resolved = await _lazy_resolve_servers(episode_data.servers, quality_pref)
                    candidates = _find_quality_candidates(resolved or [], quality_pref)
                    if candidates:
                        for attempt, (srv, quality) in enumerate(candidates, 1):
                            chosen_q = quality
                            filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                            success, sent_msg = await download_and_upload(
                                chat_id, quality.master_url or quality.url, quality.resolution, filename,
                                f"{series.title} S{season}E{ep.number}",
                                ep_msg, client, variant_url=quality.url,
                                poster_url=series.poster or "",
                                destination_channel_id=dest_channel_id,
                            )
                            if success:
                                break
            else:
                # Source-tier order for batch downloads — owner-configurable
                # via /source (default: AnimeDekho resolves first, direct
                # scrapers remain the automatic fallback).
                from bot.source_config import get_default_source, is_source
                _def_src = await get_default_source()

                async def _batch_multisource() -> None:
                    """Fallback tier: direct-file MultiSource Manager."""
                    nonlocal success, sent_msg, chosen_q, filename
                    try:
                        from extractors.multisource import multi_source_manager
                        ms0 = await multi_source_manager.resolve_episode_stream(
                            series_title=series.title,
                            season=season,
                            episode=ep.number,
                            quality_pref=quality_pref,
                            series_slug=series.slug,
                        )
                        if ms0 and ms0.get("url"):
                            chosen_q = Quality(resolution=ms0.get("quality", quality_pref), url=ms0["url"])
                            filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                            success, sent_msg = await download_and_upload(
                                chat_id, ms0["url"], chosen_q.resolution,
                                filename,
                                f"{series.title} S{season}E{ep.number}",
                                ep_msg, client,
                                refresh_url=_ms_refresh(series.title, season, ep.number, chosen_q.resolution, series.slug),
                                alternates=ms0.get("alternates") or [],
                                poster_url=series.poster or ms0.get("poster", ""),
                                destination_channel_id=dest_channel_id,
                                series_slug=series.slug,
                            )
                    except Exception as e:
                        log.warning("Batch Multi-Source first-try failed for ep %s: %s", ep.slug, e)

                async def _batch_animedekho() -> None:
                    """Default tier: AnimeDekho API server links."""
                    nonlocal success, sent_msg, chosen_q, filename, candidates
                    try:
                        episode_data = await api.get_episode(ep.slug)
                        resolved = await _lazy_resolve_servers(episode_data.servers, quality_pref)
                        candidates = _find_quality_candidates(resolved or [], quality_pref) if resolved else []

                        if candidates:
                            chosen_q = candidates[0][1]
                            filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                            for attempt, (srv, quality) in enumerate(candidates, 1):
                                chosen_q = quality
                                success, sent_msg = await download_and_upload(
                                    chat_id, quality.master_url or quality.url, quality.resolution, filename,
                                    f"{series.title} S{season}E{ep.number}",
                                    ep_msg, client, variant_url=quality.url,
                                    poster_url=series.poster or "",
                                    destination_channel_id=dest_channel_id,
                                )
                                if success:
                                    break
                    except Exception as e:
                        log.warning("Batch AnimeDekho fallback failed for ep %s: %s", ep.slug, e)

                if is_source(_def_src, "AnimeDekho"):
                    await _batch_animedekho()
                    if not success:
                        await _batch_multisource()
                else:
                    await _batch_multisource()
                    if not success:
                        await _batch_animedekho()

                # Fallback 2: Secondary - AnimeDrive
                if not success:
                    try:
                        from extractors.animedrive import animedrive
                        ad_res = await animedrive.resolve_episode(series.title, season=season, episode=ep.number, quality_pref=quality_pref)
                        if ad_res and ad_res.get("url"):
                            chosen_q = Quality(resolution=ad_res.get("quality", quality_pref), url=ad_res["url"])
                            filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                            success, sent_msg = await download_and_upload(
                                chat_id, ad_res["url"], chosen_q.resolution,
                                filename,
                                f"{series.title} S{season}E{ep.number}",
                                ep_msg, client,
                                                            refresh_url=_extractor_refresh("animedrive", series.title, season, ep.number, chosen_q.resolution),
                                referer=ad_res.get("referer", "https://hubcloud.ist/"),
                                poster_url=series.poster or ad_res.get("poster", ""),
                                destination_channel_id=dest_channel_id,
                            )
                    except Exception as e:
                        log.warning("Batch AnimeDrive fallback failed for ep %s: %s", ep.slug, e)

                # Fallback 2: Tertiary - ToonFlix
                if not success:
                    try:
                        from extractors.toonflix import toonflix
                        tf_res = await toonflix.resolve_episode(series.title, season=season, episode=ep.number, quality_pref=quality_pref)
                        if tf_res and tf_res.get("url"):
                            chosen_q = Quality(resolution=tf_res.get("quality", quality_pref), url=tf_res["url"])
                            filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                            success, sent_msg = await download_and_upload(
                                chat_id, tf_res["url"], chosen_q.resolution,
                                filename,
                                f"{series.title} S{season}E{ep.number}",
                                ep_msg, client,
                                                            refresh_url=_extractor_refresh("toonflix", series.title, season, ep.number, chosen_q.resolution),
                                referer=tf_res.get("referer", "https://drive.toonflix.in/"),
                                poster_url=series.poster or tf_res.get("poster", ""),
                                destination_channel_id=dest_channel_id,
                                series_slug=series.slug,
                            )
                    except Exception as e:
                        log.warning("Batch ToonFlix fallback failed for ep %s: %s", ep.slug, e)

                # Fallback 3: Multi-Source Manager (AnimeDubHindi, ToonWorld4All, RareAnimes, DeadToons, TOONo)
                if not success:
                    try:
                        from extractors.multisource import multi_source_manager
                        ms_res = await multi_source_manager.resolve_episode_stream(
                            series_title=series.title,
                            season=season,
                            episode=ep.number,
                            quality_pref=quality_pref,
                            series_slug=series.slug,
                        )
                        if ms_res and ms_res.get("url"):
                            chosen_q = Quality(resolution=ms_res.get("quality", quality_pref), url=ms_res["url"])
                            filename = make_episode_filename(series.title, season, ep.number, chosen_q.resolution)
                            success, sent_msg = await download_and_upload(
                                chat_id, ms_res["url"], chosen_q.resolution,
                                filename,
                                f"{series.title} S{season}E{ep.number}",
                                ep_msg, client,
                                                            refresh_url=_ms_refresh(series.title, season, ep.number, chosen_q.resolution, series.slug),
                                alternates=ms_res.get("alternates") or [],
                                poster_url=series.poster or ms_res.get("poster", ""),
                                destination_channel_id=dest_channel_id,
                                series_slug=series.slug,
                            )
                    except Exception as e:
                        log.warning("Batch Multi-Source fallback failed for ep %s: %s", ep.slug, e)

            if success:
                completed += 1
                if bot.logger.bot_logger:
                    sent_bytes = 0
                    if sent_msg:
                        sent_bytes = (sent_msg.video.file_size if sent_msg.video else (sent_msg.document.file_size if sent_msg.document else 0)) or 0
                    sent_mb = sent_bytes / (1024 * 1024)
                    await bot.logger.bot_logger.log_download_complete(
                        f"{series.title} S{season}E{ep.number}",
                        chosen_q.resolution, sent_mb
                    )

                # Save to library
                if sent_msg:
                    file_id = None
                    file_unique_id = None
                    if sent_msg.video:
                        file_id = sent_msg.video.file_id
                        file_unique_id = sent_msg.video.file_unique_id
                    elif sent_msg.document:
                        file_id = sent_msg.document.file_id
                        file_unique_id = sent_msg.document.file_unique_id

                    if file_id and file_unique_id:
                        from bot.library import library_manager
                        if library_manager:
                            try:
                                ep_key = f"S{season}E{ep.number:02d}"
                                await library_manager.save_to_library(
                                    series_slug=series.slug,
                                    series_title=series.title,
                                    quality=chosen_q.resolution,
                                    episode_key=ep_key,
                                    file_id=file_id,
                                    file_unique_id=file_unique_id,
                                    poster_url=series.poster,
                                )
                            except Exception as le:
                                log.warning("Library save failed in batch: %s", le)

                        from bot.database import db
                        if db:
                            try:
                                await db.save_file(
                                    series_slug=series.slug,
                                    series_title=series.title,
                                    quality=chosen_q.resolution,
                                    episode_key=f"S{season}E{ep.number:02d}",
                                    file_id=file_id,
                                    file_unique_id=file_unique_id,
                                    storage_channel_id=sent_msg.chat.id,
                                    storage_message_id=sent_msg.id,
                                )
                                await db.log_download(
                                    user_id=user.id,
                                    series_slug=series.slug,
                                    episode=f"S{season}E{ep.number:02d}",
                                    quality=chosen_q.resolution,
                                    file_id=file_id,
                                )
                            except Exception:
                                pass
            else:
                if bot.logger.bot_logger:
                    await bot.logger.bot_logger.log_download_error(
                        f"{series.title} S{season}E{ep.number}",
                        "Download/upload failed on all candidate servers"
                    )

        except Exception as e:
            log.exception("Batch download error for ep %s", ep.slug)
            if bot.logger.bot_logger:
                await bot.logger.bot_logger.log_download_error(
                    f"{series.title} S{season}E{ep.number}", str(e)
                )
        finally:
            try:
                if ep_job_id:
                    download_job_manager.remove_job(ep_job_id)
            except Exception:
                pass

        if _batch_cancelled():
            cancelled = True
            break
        # Small delay between episodes to avoid rate limits
        await asyncio.sleep(2)

    # Final summary (V3 #6: terminal states carry NO cancel button).
    if cancelled or _batch_cancelled():
        try:
            await progress_msg.edit_text(
                f"🛑 <b>Batch Cancelled</b>\n"
                f"┌ 📺 {esc(series.title)} — Season {season}\n"
                f"├ ✅ {completed}/{total} delivered before cancel\n"
                f"└ 🗑️ This message will auto-delete in 12h",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        if batch_id:
            download_job_manager.remove_job(batch_id)
        if bot.logger.bot_logger:
            try:
                await bot.logger.bot_logger.log_batch_complete(f"{series.title} (CANCELLED)", season, completed, total)
            except Exception:
                pass
        asyncio.create_task(_auto_delete_messages(client, chat_id, sent_messages, 43200))
        return
    cache_info = f"\n⚡ {skipped} served from library (instant)" if skipped > 0 else ""
    downloaded = completed - skipped
    try:
        await progress_msg.edit_text(
            f"{'✅' if completed == total else '⚠️'} <b>Batch Complete!</b>\n"
            f"┌ 📺 {esc(series.title)} — Season {season}\n"
            f"├ ✅ {completed}/{total} episodes delivered\n"
            f"{'├ 📥 ' + str(downloaded) + ' freshly downloaded' + chr(10) if downloaded > 0 else ''}"
            f"{'├ ⚡ ' + str(skipped) + ' from library (instant)' + chr(10) if skipped > 0 else ''}"
            f"└ 🗑️ This message will auto-delete in 12h",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass
    if batch_id:
        download_job_manager.remove_job(batch_id)

    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_batch_complete(series.title, season, completed, total)

    # Auto-delete all bot messages after 12 hours (43200 seconds)
    asyncio.create_task(_auto_delete_messages(client, chat_id, sent_messages, 43200))


async def _do_download(client: Client, chat_id, candidates: list[tuple[VideoServer, Quality]],
                       filename, title, progress_msg, user,
                       series_slug="", episode_key="", poster_url=None, is_movie=False,
                       job_id: str | None = None):
    """Background task for single download with multi-server candidate fallback."""
    try:
        if job_id:
            download_job_manager.attach_task(job_id, asyncio.current_task())

        if download_job_manager.is_job_cancelled(job_id):
            return

        success = False
        sent_msg = None
        chosen_quality = candidates[0][1]

        lookup_title = await _real_series_title(series_slug, title)
        dest_channel_id = await _resolve_destination_channel(series_slug, lookup_title, poster_url or "")

        # Parse S/E once — needed for stale-URL refresh closures below.
        import re as _re
        _s_num, _e_num = 1, 1
        if episode_key:
            _m = _re.match(r"S(\d+)E(\d+)", episode_key, _re.I)
            if _m:
                _s_num, _e_num = int(_m.group(1)), int(_m.group(2))

        attempted_sources: list[str] = []
        for attempt, (srv, quality) in enumerate(candidates, 1):
            if download_job_manager.is_job_cancelled(job_id):
                return
            chosen_quality = quality
            attempted_sources.append(srv.name)
            if attempt > 1:
                try:
                    c_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{job_id}")]]) if job_id else None
                    await progress_msg.edit_text(
                        f"🔄 <b>Trying server {attempt}/{len(candidates)}:</b> {esc(srv.name)}\n{esc(title)} [{quality.resolution}]",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=c_kb,
                    )
                except Exception:
                    pass

            log.info("Downloading %s via %s [%s]", title, srv.name, quality.resolution)
            ref = "https://hubcloud.ist/" if "AnimeDrive" in srv.name else ("https://drive.toonflix.in/" if "ToonFlix" in srv.name else "")
            # Stale-URL preflight/refresh: re-resolve from this candidate's
            # own source so a dead signed link gets exactly one fresh retry.
            if "AnimeDrive" in (srv.name or ""):
                _refresh = _extractor_refresh("animedrive", lookup_title, _s_num, _e_num, quality.resolution)
            elif "ToonFlix" in (srv.name or ""):
                _refresh = _extractor_refresh("toonflix", lookup_title, _s_num, _e_num, quality.resolution)
            else:
                _refresh = _ms_refresh(lookup_title, _s_num, _e_num, quality.resolution, series_slug)
            success, sent_msg = await download_and_upload(
                chat_id, quality.master_url or quality.url, quality.resolution, filename, title, progress_msg, client,
                variant_url=quality.url,
                referer=ref,
                poster_url=poster_url or "",
                destination_channel_id=dest_channel_id,
                series_slug=series_slug,
                is_movie=is_movie,
                job_id=job_id,
                refresh_url=_refresh,
            )
            if success:
                break

        if not success and not download_job_manager.is_job_cancelled(job_id):
            import re
            s_num, ep_num = 1, 1
            if episode_key:
                ep_m = re.match(r"S(\d+)E(\d+)", episode_key, re.I)
                if ep_m:
                    s_num = int(ep_m.group(1))
                    ep_num = int(ep_m.group(2))

            # Step 1: Multi-source scrapers fallback (AnimeDubHindi, ToonAnime, ToonWorld4All, RareAnimes, DeadToons, TOONo)
            if not is_movie and not any(s.name in ("AnimeDubHindi", "ToonAnime", "ToonWorld4All", "RareAnimes", "DeadToons", "TOONo", "MultiSource") for s, _ in candidates):
                try:
                    from extractors.multisource import multi_source_manager
                    attempted_sources.append("MultiSource")
                    c_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{job_id}")]]) if job_id else None
                    await progress_msg.edit_text(
                        f"🔄 <b>Primary servers failed, checking multi-source scrapers...</b>\n{esc(title)} [{chosen_quality.resolution}]",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=c_kb,
                    )
                    ms_res = await multi_source_manager.resolve_episode_stream(
                        series_title=lookup_title,
                        season=s_num,
                        episode=ep_num,
                        quality_pref=chosen_quality.resolution,
                        series_slug=series_slug,
                    )
                    if ms_res and ms_res.get("url"):
                        if not poster_url:
                            from utils.anilist import resolve_best_poster
                            res_p = await resolve_best_poster(lookup_title, ms_res.get("poster"))
                            if res_p:
                                poster_url = res_p
                                if series_slug:
                                    _poster_cache[series_slug] = poster_url
                        success, sent_msg = await download_and_upload(
                            chat_id, ms_res["url"], ms_res.get("quality", chosen_quality.resolution), filename, title, progress_msg, client,
                                                        refresh_url=_ms_refresh(lookup_title, s_num, ep_num, chosen_quality.resolution, series_slug),
                            alternates=ms_res.get("alternates") or [],
                            poster_url=poster_url or "",
                            destination_channel_id=dest_channel_id,
                            series_slug=series_slug,
                            is_movie=is_movie,
                            job_id=job_id,
                        )
                        if success:
                            from api.models import Quality
                            chosen_quality = Quality(resolution=ms_res.get("quality", chosen_quality.resolution), url=ms_res["url"])
                except Exception as e:
                    log.warning("Multi-source fallback in _do_download failed: %s", e)

            # Step 2: Fallback to AnimeDrive (if not already tried)
            if not success and not download_job_manager.is_job_cancelled(job_id) and not any("AnimeDrive" in s.name for s, _ in candidates):
                try:
                    from extractors.animedrive import animedrive
                    attempted_sources.append("AnimeDrive")
                    c_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{job_id}")]]) if job_id else None
                    await progress_msg.edit_text(
                        f"🔄 <b>AnimeDekho servers failed, trying AnimeDrive fallback...</b>\n{esc(title)} [{chosen_quality.resolution}]",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=c_kb,
                    )
                    ad_res = await animedrive.resolve_episode(lookup_title, season=s_num, episode=ep_num, quality_pref=chosen_quality.resolution)
                    if ad_res and ad_res.get("url"):
                        if not poster_url:
                            from utils.anilist import resolve_best_poster
                            res_p = await resolve_best_poster(lookup_title, ad_res.get("poster"))
                            if res_p:
                                poster_url = res_p
                                if series_slug:
                                    _poster_cache[series_slug] = poster_url
                        success, sent_msg = await download_and_upload(
                            chat_id, ad_res["url"], ad_res["quality"], filename, title, progress_msg, client,
                                                        refresh_url=_extractor_refresh("animedrive", lookup_title, s_num, ep_num, chosen_quality.resolution),
                            referer=ad_res.get("referer", "https://hubcloud.ist/"),
                            poster_url=poster_url or "",
                            destination_channel_id=dest_channel_id,
                            series_slug=series_slug,
                            is_movie=is_movie,
                            job_id=job_id,
                        )
                        if success:
                            from api.models import Quality
                            chosen_quality = Quality(resolution=ad_res["quality"], url=ad_res["url"])
                except Exception as e:
                    log.warning("AnimeDrive fallback in _do_download failed: %s", e)

            # Step 3: Fallback to ToonFlix (if not already tried)
            if not success and not download_job_manager.is_job_cancelled(job_id) and not any("ToonFlix" in s.name for s, _ in candidates):
                try:
                    from extractors.toonflix import toonflix
                    attempted_sources.append("ToonFlix")
                    c_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{job_id}")]]) if job_id else None
                    await progress_msg.edit_text(
                        f"🔄 <b>Trying ToonFlix fallback...</b>\n{esc(title)} [{chosen_quality.resolution}]",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=c_kb,
                    )
                    tf_res = await toonflix.resolve_episode(lookup_title, season=s_num, episode=ep_num, quality_pref=chosen_quality.resolution)
                    if tf_res and tf_res.get("url"):
                        if not poster_url:
                            from utils.anilist import resolve_best_poster
                            res_p = await resolve_best_poster(lookup_title, tf_res.get("poster"))
                            if res_p:
                                poster_url = res_p
                                if series_slug:
                                    _poster_cache[series_slug] = poster_url
                        success, sent_msg = await download_and_upload(
                            chat_id, tf_res["url"], tf_res["quality"], filename, title, progress_msg, client,
                                                        refresh_url=_extractor_refresh("toonflix", lookup_title, s_num, ep_num, chosen_quality.resolution),
                            referer=tf_res.get("referer", "https://drive.toonflix.in/"),
                            poster_url=poster_url or "",
                            destination_channel_id=dest_channel_id,
                            series_slug=series_slug,
                            is_movie=is_movie,
                            job_id=job_id,
                        )
                        if success:
                            from api.models import Quality
                            chosen_quality = Quality(resolution=tf_res["quality"], url=tf_res["url"])
                except Exception as e:
                    log.warning("ToonFlix fallback in _do_download failed: %s", e)

        if success and bot.logger.bot_logger:
            sent_bytes = 0
            if sent_msg:
                sent_bytes = (sent_msg.video.file_size if sent_msg.video else (sent_msg.document.file_size if sent_msg.document else 0)) or 0
            sent_mb = sent_bytes / (1024 * 1024)
            await bot.logger.bot_logger.log_download_complete(title, chosen_quality.resolution, sent_mb)

        # Save to library if upload succeeded
        if success and sent_msg:
            if not series_slug:
                import re
                series_slug = re.sub(r'[^a-zA-Z0-9]+', '-', title).strip('-').lower() or "series"
            file_id = None
            file_unique_id = None
            if sent_msg.video:
                file_id = sent_msg.video.file_id
                file_unique_id = sent_msg.video.file_unique_id
            elif sent_msg.document:
                file_id = sent_msg.document.file_id
                file_unique_id = sent_msg.document.file_unique_id

            if file_id and file_unique_id:
                from bot.library import library_manager
                if library_manager:
                    try:
                        await library_manager.save_to_library(
                            series_slug=series_slug,
                            series_title=await _real_series_title(series_slug, title),
                            quality=chosen_quality.resolution,
                            episode_key=episode_key or "movie",
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            poster_url=poster_url,
                            is_movie=is_movie,
                        )
                    except Exception as le:
                        log.warning("Library save failed: %s", le)

                # Save file to DB (for duplicate prevention + library links)
                from bot.database import db
                if db:
                    try:
                        await db.save_file(
                            series_slug=series_slug,
                            series_title=await _real_series_title(series_slug, title),
                            quality=chosen_quality.resolution,
                            episode_key=episode_key or "movie",
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            storage_channel_id=sent_msg.chat.id,
                            storage_message_id=sent_msg.id,
                        )
                        await db.log_download(
                            user_id=user.id,
                            series_slug=series_slug,
                            episode=episode_key or "movie",
                            quality=chosen_quality.resolution,
                            file_id=file_id,
                        )
                    except Exception:
                        pass

        elif not success and not download_job_manager.is_job_cancelled(job_id):
            if bot.logger.bot_logger:
                await bot.logger.bot_logger.log_download_error(title, "Download/upload failed on all servers")
            try:
                attempted_txt = ", ".join(dict.fromkeys(attempted_sources)) if attempted_sources else "all available servers"
                # V3 #9 + V3 #10: full provider/source diagnostics — never a
                # bare failure. Includes requested quality, resolver +
                # fallback stages, and the exact failure reason (403 etc).
                req_q = getattr(chosen_quality, "resolution", "?")
                n_cands = len(candidates) if "candidates" in dir() else 0
                try:
                    from bot.database import db as _db
                    _errs = await _db.get_recent_download_errors(limit=1) if _db else []
                    last_stage = (_errs[0].get("error", "") if _errs else "")[:160]
                except Exception:
                    last_stage = ""
                detail = f"├ 🎬 Requested: {esc(str(req_q))} · Candidates: {n_cands}\n"
                detail += f"├ 📡 Provider/Source: {esc(attempted_txt)}\n"
                if last_stage:
                    detail += f"├ 🧩 Resolver stage: {esc(last_stage)}\n"
                detail += f"├ 🔁 Fallback stage: AnimeDekho → MultiSource → AnimeDrive → ToonFlix\n"
                is_403 = "403" in last_stage or "forbidden" in last_stage.lower()
                if is_403:
                    detail += f"├ ⚠️ Failure reason: AnimeDekho 403 Forbidden — direct sources tried first, all fallbacks exhausted\n"
                else:
                    detail += f"├ ⚠️ Failure reason: exact {esc(str(req_q))} stream unavailable on attempted sources\n"
                await progress_msg.edit_text(
                    f"❌ <b>Download Failed</b>\n"
                    f"┌ 📺 {esc(title)}\n"
                    f"{detail}"
                    f"└ 💔 Try another quality/episode or /bypass the source URL.",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass

        # Auto-delete progress + file messages after 12h
        to_delete = [progress_msg]
        if sent_msg:
            to_delete.append(sent_msg)
        asyncio.create_task(_auto_delete_messages(client, chat_id, to_delete, 43200))

    except Exception as e:
        log.exception("Download task error for %s", title)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_download_error(title, str(e))
        try:
            if not download_job_manager.is_job_cancelled(job_id):
                await progress_msg.edit_text(
                    f"❌ <b>Error:</b> {esc(title)}\n{esc(str(e)[:200])}",
                    parse_mode=enums.ParseMode.HTML,
                )
        except Exception:
            pass
    finally:
        download_job_manager.remove_job(job_id)


async def _handle_genres(q: CallbackQuery):
    cats = await api.get_categories()
    if not cats:
        await _send_text(q, "⚠️ Could not load genres.")
        return
    await _send_text(q, "📂 <b>Browse by Genre</b>", kb.genre_list(cats))


async def _handle_category(q: CallbackQuery, cat_slug: str, page: int):
    result = await api.get_category_items(cat_slug, page)
    if not result.items:
        await _send_text(q, "No items found in this category.")
        return
    title = slug_to_title(cat_slug)
    markup = kb.category_page(result.items, cat_slug, page, result.max_page)
    await _send_text(q, f"📂 <b>{esc(title)}</b> — Page {page}", markup)


# ── Quality / Server helpers ─────────────────────────────────────────


def _sort_qualities(qualities: set[str]) -> list[str]:
    """Sort quality strings like 360p, 480p, 720p, 1080p, 4K (V2 #20)."""
    order = {
        "360p": 1, "480p": 2, "720p": 3, "1080p": 4,
        "1080p hq": 5, "4k": 6, "2160p": 6, "2160": 6, "uhd": 6,
        "auto": 7,
    }
    return sorted(qualities, key=lambda q: order.get(str(q).lower(), 99))


async def _lazy_resolve_servers(servers: list, quality_pref: str = "") -> list:
    """Resolve servers lazily in priority order."""
    from config.settings import settings
    preferred = settings.site.preferred_servers

    def _server_priority(srv):
        name_lower = srv.name.lower()
        for i, pref in enumerate(preferred):
            if pref.lower() in name_lower:
                return i
        return len(preferred)

    sorted_servers = sorted(servers, key=_server_priority)

    # Need to resolve: first get player URLs if not already done
    needs_resolve = [s for s in sorted_servers if not s.player_url and not s.direct_url]
    if needs_resolve:
        resolved_list = await api.resolve_all_servers(servers)
        sorted_servers = sorted(resolved_list, key=_server_priority)

    # Extract streams from servers in priority order
    await _populate_server_qualities(sorted_servers, quality_pref)
    return sorted_servers


async def _populate_server_qualities(servers: list, quality_pref: str = ""):
    """Extract stream URLs and qualities from servers in priority order."""
    from extractors.resolver import resolve_player_url
    from config.settings import settings
    preferred = settings.site.preferred_servers

    def _server_priority(srv):
        name_lower = srv.name.lower()
        for i, pref in enumerate(preferred):
            if pref.lower() in name_lower:
                return i
        return len(preferred)

    sorted_servers = sorted(servers, key=_server_priority)
    resolved_count = 0

    for srv in sorted_servers:
        if srv.qualities:
            resolved_count += 1
            if quality_pref and any(q.resolution == quality_pref for q in srv.qualities):
                return
            if resolved_count >= 3:
                return
            continue

        url = srv.player_url or srv.direct_url
        if not url:
            continue

        try:
            result = await resolve_player_url(url)
            if result:
                if result.get("qualities"):
                    srv.qualities = result["qualities"]
                    log.info("✅ %s: %d qualities (%s)",
                             srv.name, len(srv.qualities),
                             ", ".join(q.resolution for q in srv.qualities))
                    resolved_count += 1
                    if quality_pref and any(q.resolution == quality_pref for q in srv.qualities):
                        return
                    if resolved_count >= 3:
                        return
                elif result.get("url"):
                    vtype = result.get("type", "mp4")
                    srv.direct_url = result["url"]
                    srv.video_type = vtype
                    res_name = "auto"
                    name_l = srv.name.lower()
                    if "720p" in name_l:
                        res_name = "720p"
                    elif "480p" in name_l:
                        res_name = "480p"
                    elif "1080p" in name_l or name_l == "vidsrc":
                        res_name = "1080p"
                    srv.qualities = [Quality(
                        resolution=res_name,
                        url=result["url"],
                        label=f"{res_name} ({vtype.upper()})"
                    )]
                    log.info("✅ %s: resolved quality %s", srv.name, res_name)
                    resolved_count += 1
                    if quality_pref and res_name == quality_pref:
                        return
                    if resolved_count >= 3:
                        return
        except Exception as e:
            log.debug("Resolver failed for %s: %s", srv.name, e)

        log.info("❌ %s: failed, trying next...", srv.name)


def _pick_quality(srv, quality_idx: int):
    """Pick a quality from a server by index, with fallback."""
    if srv.qualities and quality_idx < len(srv.qualities):
        return srv.qualities[quality_idx]
    if srv.qualities:
        return srv.qualities[0]
    if srv.direct_url:
        return Quality(resolution="auto", url=srv.direct_url)
    return None


def _find_quality_candidates(servers: list, quality_pref: str) -> list[tuple[VideoServer, Quality]]:
    """V3 #2: strict exact-quality only (no closest/auto fallback).

    Requested quality must match exactly (case-insensitive; 4K≡2160p≡UHD).
    ``auto`` keeps its legacy first-available behaviour. Anything else with
    no exact match returns [] so the caller tries the next source or fails
    instead of silently delivering 720p for a 1080p request.
    """
    from config.settings import settings
    preferred = settings.site.preferred_servers

    def _server_priority(srv):
        name_lower = srv.name.lower()
        for i, pref in enumerate(preferred):
            if pref.lower() in name_lower:
                return i
        return len(preferred)

    sorted_servers = sorted(servers, key=_server_priority)
    candidates: list[tuple[VideoServer, Quality]] = []

    if quality_pref == "auto":
        for srv in sorted_servers:
            if srv.qualities:
                candidates.append((srv, srv.qualities[0]))
            elif srv.direct_url:
                candidates.append((srv, Quality(resolution="auto", url=srv.direct_url)))
        return candidates

    def _norm(q: str) -> str:
        s = (q or "").strip().lower()
        if s in ("4k", "2160p", "2160", "uhd"):
            return "4k"
        return s

    want = _norm(quality_pref)

    for srv in sorted_servers:
        for q in srv.qualities:
            if _norm(q.resolution) == want:
                candidates.append((srv, q))
                break
        if not srv.qualities and srv.direct_url and want == "auto":
            candidates.append((srv, Quality(resolution="auto", url=srv.direct_url)))

    return candidates


def _find_quality_match(servers: list, quality_pref: str) -> Quality | None:
    """Find the single best quality match across servers."""
    candidates = _find_quality_candidates(servers, quality_pref)
    return candidates[0][1] if candidates else None


# ── Server data cache (in-memory, per chat) ──────────────────────────

_server_cache: dict[str, dict] = {}

# ── Poster cache (series_slug -> poster_url) ──────────────────────────

_poster_cache: dict[str, str] = {}


def _store_servers(chat_id: int, key: str, servers: list, title: str = "", poster_url: str = ""):
    """Store resolved servers for download callbacks."""
    cache_key = f"{chat_id}:{key}"
    _server_cache[cache_key] = {
        "servers": servers,
        "title": title,
        "poster_url": poster_url,
    }
    # Keep cache bounded
    if len(_server_cache) > 200:
        # Remove oldest entries
        keys = list(_server_cache.keys())
        for k in keys[:50]:
            _server_cache.pop(k, None)


def _get_servers(chat_id: int, key: str) -> dict | None:
    cache_key = f"{chat_id}:{key}"
    return _server_cache.get(cache_key)


# ── Utilities ─────────────────────────────────────────────────────

async def _send_text(q: CallbackQuery, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def _safe_edit(q: CallbackQuery, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        await q.message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def _auto_delete_messages(client: Client, chat_id: int, messages: list, delay_seconds: int = 43200):
    """Auto-delete a list of bot messages after delay (default 12h)."""
    await asyncio.sleep(delay_seconds)
    for msg in messages:
        try:
            await client.delete_messages(chat_id, msg.id)
        except Exception:
            pass
