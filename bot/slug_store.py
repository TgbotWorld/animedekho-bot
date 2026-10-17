"""Robust slug tokenization and persistence for Telegram callback_data limits (64 bytes).

Ensures that anime series and episode slugs of any length never get truncated,
preserving the season number, episode number, and full slug identifier.
"""

from __future__ import annotations
import asyncio
import hashlib
import logging
import re

log = logging.getLogger(__name__)

# Fast in-memory cache for instant synchronous lookups
_TOKEN_TO_SLUG: dict[str, str] = {}
_SLUG_TO_TOKEN: dict[str, str] = {}
_MAX_CACHE = 10000


def _cache_put(token: str, slug: str) -> None:
    if not token or not slug:
        return
    if len(_TOKEN_TO_SLUG) >= _MAX_CACHE:
        # Evict oldest 10%
        keys = list(_TOKEN_TO_SLUG.keys())[:1000]
        for k in keys:
            old_slug = _TOKEN_TO_SLUG.pop(k, None)
            if old_slug:
                _SLUG_TO_TOKEN.pop(old_slug, None)
    _TOKEN_TO_SLUG[token] = slug
    _SLUG_TO_TOKEN[slug] = token

    # Fire background persistence to MongoDB if loop is running
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_slug(token, slug))
    except (RuntimeError, Exception):
        pass


async def _persist_slug(token: str, slug: str) -> None:
    try:
        from bot.database import db
        if db and hasattr(db, "save_cached_slug"):
            await db.save_cached_slug(token, slug)
    except Exception:
        pass


def pack_episode_slug(ep_slug: str, season: int = 1, ep_num: int = 1) -> str:
    """Pack an episode slug into a safe token that fits Telegram's 64-byte callback limit.

    Format: e_{season}x{ep_num}_{hash8} (e.g. e_2x6_8f93a1b2).
    Always retains season and episode number explicitly in the token!
    """
    if not ep_slug:
        return f"e_{season}x{ep_num}_none"

    # If it's already a packed token, return it
    if ep_slug.startswith("e_") and "x" in ep_slug:
        return ep_slug

    # Extract season and episode if not provided or default
    m = re.search(r"(\d+)x(\d+)", ep_slug)
    if m:
        season = int(m.group(1))
        ep_num = int(m.group(2))
    else:
        m2 = re.search(r"[sS](\d+)[eE](\d+)", ep_slug)
        if m2:
            season = int(m2.group(1))
            ep_num = int(m2.group(2))

    # To be 100% safe against 64-byte overflow (callbacks like dl:480p+720p+1080p:slug add ~20-25 bytes),
    # any slug longer than 28 chars gets tokenized.
    if len(ep_slug) <= 28 and re.search(r"-\d+x\d+$", ep_slug):
        _cache_put(ep_slug, ep_slug)
        return ep_slug

    cached = _SLUG_TO_TOKEN.get(ep_slug)
    if cached:
        return cached

    h = hashlib.sha256(ep_slug.encode("utf-8")).hexdigest()[:8]
    token = f"e_{season}x{ep_num}_{h}"
    _cache_put(token, ep_slug)
    return token


def pack_series_slug(series_slug: str) -> str:
    """Pack a series slug into a safe token (<= 15 chars) if too long."""
    if not series_slug:
        return ""
    if series_slug.startswith("s_") and len(series_slug) == 12:
        return series_slug
    if len(series_slug) <= 28:
        _cache_put(series_slug, series_slug)
        return series_slug

    cached = _SLUG_TO_TOKEN.get(series_slug)
    if cached:
        return cached

    h = hashlib.sha256(series_slug.encode("utf-8")).hexdigest()[:10]
    token = f"s_{h}"
    _cache_put(token, series_slug)
    return token


def pack_slug(slug: str, max_len: int = 28) -> str:
    """Generic slug packer for movies, series, or episodes."""
    if not slug:
        return ""
    if len(slug) <= max_len:
        _cache_put(slug, slug)
        return slug

    cached = _SLUG_TO_TOKEN.get(slug)
    if cached:
        return cached

    m = re.search(r"(\d+)x(\d+)", slug)
    if m:
        return pack_episode_slug(slug, int(m.group(1)), int(m.group(2)))

    h = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:10]
    token = f"t_{h}"
    _cache_put(token, slug)
    return token


def resolve_slug(token: str) -> str:
    """Synchronously resolve a token back to its original full slug."""
    if not token:
        return ""
    return _TOKEN_TO_SLUG.get(token, token)


async def resolve_slug_async(token: str) -> str:
    """Asynchronously resolve a token, checking MongoDB if not in memory."""
    if not token:
        return ""
    if token in _TOKEN_TO_SLUG:
        return _TOKEN_TO_SLUG[token]

    try:
        from bot.database import db
        if db and hasattr(db, "get_cached_slug"):
            found = await db.get_cached_slug(token)
            if found:
                _cache_put(token, found)
                return found
    except Exception:
        pass

    return token


def parse_season_episode(payload: str) -> tuple[int, int]:
    """Reliably parse season and episode number from any slug, token, or title.

    Handles:
    - 'e_2x6_8f93a1b2' -> (2, 6)
    - 'solo-leveling-2x6' -> (2, 6)
    - 'solo-leveling-s02e06' -> (2, 6)
    - 'solo-leveling-season-2-episode-6' -> (2, 6)
    - '2x6' -> (2, 6)
    """
    if not payload:
        return 1, 1

    resolved = _TOKEN_TO_SLUG.get(payload, "")
    target = f"{resolved} {payload}" if resolved else payload

    # 1. Look for explicit e_{s}x{e}_ token pattern
    m_tok = re.search(r"e_(\d+)x(\d+)_", target)
    if m_tok:
        return int(m_tok.group(1)), int(m_tok.group(2))

    # 2. Look for -{s}x{e} or {s}x{e}
    m = re.search(r"(?:^|[-_ \t])(\d+)[xX](\d+)(?:[-_ \t]|$)", target)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 3. Look for S{s}E{e}
    m2 = re.search(r"[sS](\d+)\s*[eE](\d+)", target)
    if m2:
        return int(m2.group(1)), int(m2.group(2))

    # 4. Look for season-(\d+)...episode-(\d+)
    m3 = re.search(r"season[-_\s]*(\d+).*?episode[-_\s]*(\d+)", target, re.IGNORECASE)
    if m3:
        return int(m3.group(1)), int(m3.group(2))

    # 5. Look for episode-(\d+)
    m4 = re.search(r"episode[-_\s]*(\d+)", target, re.IGNORECASE)
    if m4:
        return 1, int(m4.group(1))

    # 6. Fallback regex
    m5 = re.search(r"(\d+)x(\d+)", target)
    if m5:
        return int(m5.group(1)), int(m5.group(2))

    return 1, 1
