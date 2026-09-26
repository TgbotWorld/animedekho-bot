"""AniList GraphQL metadata and high-resolution poster resolver for anime titles."""

from __future__ import annotations
import asyncio
import logging
import re
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

ANILIST_API_URL = "https://graphql.anilist.co"

# In-memory LRU-like caches
_poster_cache: dict[str, str | None] = {}
_meta_cache: dict[str, dict[str, Any] | None] = {}
_CACHE_MAX_SIZE = 1000

# Substrings indicating invalid or junk scraped posters (website banners, logos, placeholders)
BAD_POSTER_SUBSTRINGS = (
    "banner-", "banner.", "-banner", "_banner", "/banner",
    "site-logo", "logo-", "-logo", "logo.", "default-",
    "placeholder", "no-image", "no_image", "avatar", "gravatar",
    "favicon", "blank.", "1x1.", "pixel.", "transparent.",
    "default_poster", "dummy", "loading.", "animedekho-logo",
)

BAD_POSTER_EXTENSIONS = (".svg", ".gif", ".ico")

GRAPHQL_POSTER_QUERY = """
query ($search: String) {
  Media (search: $search, type: ANIME, sort: [POPULARITY_DESC, SEARCH_MATCH]) {
    id
    title {
      romaji
      english
      native
    }
    coverImage {
      extraLarge
      large
      medium
    }
    bannerImage
    format
  }
}
"""

GRAPHQL_META_QUERY = """
query ($search: String) {
  Media (search: $search, type: ANIME, sort: [POPULARITY_DESC, SEARCH_MATCH]) {
    id
    title {
      romaji
      english
      native
    }
    coverImage {
      extraLarge
      large
      medium
    }
    bannerImage
    format
    status
    episodes
    genres
    description(asHtml: false)
    averageScore
    popularity
  }
}
"""


def is_valid_poster_url(url: str | None) -> bool:
    """Return True if url appears to be a legitimate, renderable anime poster image."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    lower = u.lower()
    if any(bad in lower for bad in BAD_POSTER_SUBSTRINGS):
        return False
    if any(lower.endswith(ext) for ext in BAD_POSTER_EXTENSIONS):
        return False
    return True


def clean_anime_title(title_or_slug: str) -> list[str]:
    """
    Generate progressive search candidate titles from most specific to base title.
    Handles anime titles, episode names, and URL slugs.
    """
    raw = (title_or_slug or "").strip()
    if not raw:
        return []

    # If it is a slug, convert hyphens to spaces and strip episode counters
    if "-" in raw and " " not in raw:
        raw = re.sub(r"-\d+x\d+$", "", raw)
        raw = raw.replace("-", " ")

    # 1. Remove website prefixes / suffixes
    t = raw
    for sep in ("–", "|", " - AnimeDekho", " - Watch", " - AnimeDrive", " - ToonFlix", "::", " - "):
        t = t.split(sep)[0].strip()

    # 2. Normalize S01 / S1 to Season 1
    t = re.sub(r"\b[Ss]0*(\d+)\b", r"Season \1", t)

    candidates: list[str] = []

    # 3. Strip standard bracket/parentheses tags
    tag_pattern = re.compile(
        r"\s*[\(\[\{]\s*(?:hindi|english|japanese|jap|dual|multi|audio|dub|dubbed|sub|subbed|1080p|720p|480p|360p|web-dl|hd|fhd|hevc|x264|x265|10bit|episodes?\s*[\d\-]+.*|complete|ongoing|censored|uncensored|cr|nf)[^\)\]\}]*[\)\]\}]",
        re.IGNORECASE
    )
    cleaned = tag_pattern.sub("", t).strip()

    # 4. Strip loose tags not in brackets
    loose_tag_pattern = re.compile(
        r"\b(?:hindi(?:\s*dub(?:bed)?)?|english(?:\s*dub(?:bed)?)?|multi\s*audio|dual\s*audio|uncensored|censored|web-dl|hevc|1080p|720p|480p|360p|fhd|hd)\b",
        re.IGNORECASE
    )
    cleaned = loose_tag_pattern.sub("", cleaned).strip()
    cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if cleaned and cleaned not in candidates:
        candidates.append(cleaned)

    # 5. Candidate without any remaining parentheses/brackets
    no_paren = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", cleaned).strip()
    no_paren = re.sub(r"^[^\w]+|[^\w]+$", "", no_paren).strip()
    no_paren = re.sub(r"\s+", " ", no_paren)
    if no_paren and no_paren not in candidates:
        candidates.append(no_paren)

    # 6. Candidate for base title (stripping Season X, Part X, Cour X)
    base = re.sub(r"\s*(?:Season\s*\d+|Part\s*\d+|Cour\s*\d+).*$", "", no_paren, flags=re.IGNORECASE).strip()
    base = re.sub(r"^[^\w]+|[^\w]+$", "", base).strip()
    base = re.sub(r"\s+", " ", base)
    if base and base not in candidates:
        candidates.append(base)

    return candidates


def _evict_cache_if_needed(cache: dict):
    if len(cache) > _CACHE_MAX_SIZE:
        keys_to_remove = list(cache.keys())[:200]
        for k in keys_to_remove:
            cache.pop(k, None)


async def get_anilist_poster(title_or_slug: str) -> str | None:
    """
    Fetch the official extraLarge/large anime cover poster from AniList.
    Uses in-memory caching to minimize external API hits.
    """
    if not title_or_slug or not title_or_slug.strip():
        return None

    cache_key = title_or_slug.strip().lower()
    if cache_key in _poster_cache:
        return _poster_cache[cache_key]

    candidates = clean_anime_title(title_or_slug)
    if not candidates:
        _poster_cache[cache_key] = None
        return None

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "AnimeDekhoBot/2.0 (Telegram Bot)",
    }

    poster_url: str | None = None
    timeout = aiohttp.ClientTimeout(total=8)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for cand in candidates:
                payload = {
                    "query": GRAPHQL_POSTER_QUERY,
                    "variables": {"search": cand},
                }
                try:
                    async with session.post(ANILIST_API_URL, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            media = data.get("data", {}).get("Media")
                            if media and media.get("coverImage"):
                                cimg = media["coverImage"]
                                found = cimg.get("extraLarge") or cimg.get("large") or cimg.get("medium")
                                if found and is_valid_poster_url(found):
                                    poster_url = found
                                    matched_title = (media.get("title") or {}).get("english") or (media.get("title") or {}).get("romaji")
                                    log.info("AniList: Matched '%s' via '%s' -> %s", title_or_slug, cand, matched_title)
                                    break
                        elif resp.status == 404:
                            # Try next candidate
                            continue
                        elif resp.status == 429:
                            log.warning("AniList rate limit hit (429), pausing searches")
                            break
                        else:
                            log.debug("AniList returned HTTP %d for '%s'", resp.status, cand)
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    log.debug("AniList request failed for '%s': %s", cand, err)
                    continue
    except Exception as e:
        log.warning("AniList query error for '%s': %s", title_or_slug, e)

    _evict_cache_if_needed(_poster_cache)
    _poster_cache[cache_key] = poster_url
    return poster_url


async def get_anilist_metadata(title_or_slug: str) -> dict[str, Any] | None:
    """Fetch rich anime metadata from AniList (titles, description, banner, cover, episodes, score)."""
    if not title_or_slug or not title_or_slug.strip():
        return None

    cache_key = title_or_slug.strip().lower()
    if cache_key in _meta_cache:
        return _meta_cache[cache_key]

    candidates = clean_anime_title(title_or_slug)
    if not candidates:
        _meta_cache[cache_key] = None
        return None

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "AnimeDekhoBot/2.0 (Telegram Bot)",
    }

    meta_result: dict[str, Any] | None = None
    timeout = aiohttp.ClientTimeout(total=8)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for cand in candidates:
                payload = {
                    "query": GRAPHQL_META_QUERY,
                    "variables": {"search": cand},
                }
                try:
                    async with session.post(ANILIST_API_URL, json=payload, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            media = data.get("data", {}).get("Media")
                            if media:
                                meta_result = media
                                break
                        elif resp.status == 404:
                            continue
                        elif resp.status == 429:
                            break
                except Exception:
                    continue
    except Exception as e:
        log.warning("AniList metadata error for '%s': %s", title_or_slug, e)

    _evict_cache_if_needed(_meta_cache)
    _meta_cache[cache_key] = meta_result
    return meta_result


async def resolve_best_poster(
    title: str,
    scraped_poster: str | None = None,
    allow_network: bool = True,
    is_movie: bool = False,
    fallback_default: bool = True,
) -> str | None:
    """
    Resolve the highest quality, authoritative poster for an anime:
    1. Query AniList for official high-resolution coverImage (Primary Source).
    2. Fall back to scraped_poster if AniList has no match or errors,
       filtering out generic banners/logos/placeholders.
    3. Fall back to DEFAULT_MOVIE_THUMB or DEFAULT_ANIME_THUMB (from config.py)
       if fallback_default is True and neither AniList nor scraped poster is valid.
    """
    # Step 1: Check AniList if network allowed and title present (Primary Source)
    if allow_network and title:
        try:
            anilist_poster = await get_anilist_poster(title)
            if anilist_poster and is_valid_poster_url(anilist_poster):
                return anilist_poster
        except Exception as e:
            log.warning("AniList poster resolution error for '%s': %s", title, e)

    # Step 2: Fall back to scraped poster if it passes validation
    if scraped_poster and is_valid_poster_url(scraped_poster):
        return scraped_poster

    # Step 3: Fall back to configured default thumbnails (Issue #9)
    if fallback_default:
        try:
            from config import Config
            def_thumb = (
                getattr(Config, "DEFAULT_MOVIE_THUMB", None)
                if is_movie
                else getattr(Config, "DEFAULT_ANIME_THUMB", None)
            )
            if def_thumb and is_valid_poster_url(def_thumb):
                return def_thumb
        except Exception as e:
            log.debug("Default thumbnail config lookup failed: %s", e)

    return None
