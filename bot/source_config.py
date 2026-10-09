"""Runtime-configurable default download source (owner command: /source).

The default source decides which resolver is tried FIRST when a user
requests a download. It is persisted in the DB ``config`` collection under
the ``default_source`` key, so changing it takes effect immediately for
every worker without a restart.

Default is ``AnimeDekho`` (the official catalog API). Every other known
extractor can be promoted with ``/source <name>`` and the original order
can be restored with ``/source animedekho``.
"""

from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)

DEFAULT_SOURCE = "AnimeDekho"

# (name, one-line description) — displayed by /source. Keep names in sync
# with MultiSourceManager.sources (extractors/multisource.py).
SOURCE_CATALOG: list[tuple[str, str]] = [
    ("AnimeDekho", "Official catalog API — server links straight from the source"),
    ("AnimeDubHindi", "adhlinks episode pages → HubCloud / Google storage"),
    ("ToonWorld4All", "TW4ALL episode pages → Filepress / GDFlix / MEGA"),
    ("RareAnimes", "RareAnimes player embeds"),
    ("DeadToons", "DeadToons Hindi-dub catalog"),
    ("TOONo", "TOONo episode pages"),
    ("ToonAnime", "ToonAnime mirrors"),
    ("AnimeDrive", "AnimeDrive link pages → HubCloud storage"),
    ("ToonFlix", "ToonFlix seasons → GDrive worker proxy"),
]

# Sources supported for live recent releases browsing (/browser_source)
BROWSE_SOURCES: list[tuple[str, str, str]] = [
    ("AnimeDekho", "Official Catalog & API", "🍿"),
    ("DeadToons", "Hindi & Multi-Audio Direct Streams", "💀"),
    ("ToonFlix", "High Speed & 4K Streams", "⚡"),
    ("AnimeDrive", "Direct & HubCloud Streams", "🚗"),
    ("AnimeDubHindi", "Multi-Audio Dubs & DDL", "🎙️"),
    ("ToonWorld4All", "TW4All Catalog Releases", "🌍"),
    ("TOONo", "Anime Series Collection", "🎭"),
]


# Loose input → canonical name (case/spacing/symbol tolerant + aliases).
_ALIASES = {
    "animedekho": "AnimeDekho",
    "animedekhoapi": "AnimeDekho",
    "adk": "AnimeDekho",
    "api": "AnimeDekho",
    "animedubhindi": "AnimeDubHindi",
    "adh": "AnimeDubHindi",
    "toonworld4all": "ToonWorld4All",
    "tw4a": "ToonWorld4All",
    "toonworld": "ToonWorld4All",
    "rareanimes": "RareAnimes",
    "rare": "RareAnimes",
    "deadtoons": "DeadToons",
    "toono": "TOONo",
    "toonanime": "ToonAnime",
    "animedrive": "AnimeDrive",
    "ad": "AnimeDrive",
    "toonflix": "ToonFlix",
}

_CACHE_TTL = 30.0
_cache: dict = {"value": None, "ts": 0.0}


def _key(name: str) -> str:
    """Normalize a source name for tolerant matching."""
    return re.sub(r"[\s_\-./]+", "", str(name or "")).casefold()


def known_source_names() -> list[str]:
    return [name for name, _ in SOURCE_CATALOG]


def normalize_source(name: str) -> str | None:
    """Return the canonical source name for a user-provided string, or None."""
    raw = str(name or "").strip().strip("@,;:")
    if not raw:
        return None

    # Tolerate pasted URLs, extra words and @handles: try the full string
    # first ("anime drive" → AnimeDrive), then a URL host / first token.
    candidates = [raw]
    if raw.lower().startswith(("http://", "https://")):
        host = raw.split("//", 1)[1].split("/", 1)[0].split("?")[0]
        candidates.append(host.split(".")[0])
    else:
        candidates.append(raw.split()[0].split("?")[0].strip("@,;:"))

    for cand in candidates:
        k = _key(cand)
        if k in _ALIASES:
            return _ALIASES[k]
        for canonical in known_source_names():
            if _key(canonical) == k:
                return canonical
    return None


async def get_default_source() -> str:
    """Current default source (cached 30s; falls back to DEFAULT_SOURCE)."""
    now = time.monotonic()
    if _cache["value"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["value"]
    value = DEFAULT_SOURCE
    try:
        from bot.database import db
        if db:
            stored = await db.get_config("default_source", DEFAULT_SOURCE)
            value = normalize_source(stored) or DEFAULT_SOURCE
    except Exception as e:
        log.debug("get_default_source read failed: %s", e)
    _cache["value"] = value
    _cache["ts"] = now
    return value


async def set_default_source(name: str) -> str:
    """Persist the default source. Raises ValueError for unknown names."""
    canonical = normalize_source(name)
    if not canonical:
        raise ValueError(
            "Unknown source. Use /source to list the available sources."
        )
    from bot.database import db
    if not db:
        raise RuntimeError("Database not initialized")
    await db.set_config("default_source", canonical)
    _cache["value"] = canonical
    _cache["ts"] = time.monotonic()
    log.info("Default source changed to '%s'", canonical)
    return canonical


def is_source(name: str, canonical: str) -> bool:
    return _key(name) == _key(canonical)
