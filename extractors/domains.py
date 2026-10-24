"""Live domain resolver — survives source domain rotations.

These sites rotate domains without notice (``animedekho.app`` → ``animedekho.tv``;
``toonanime.tv`` → ``ww547.toonanime.tv``). Hardcoding one host means the bot
dies on every move and someone has to ship a code change.

This module resolves the *live* origin for a source at runtime:

1. an explicit operator override wins (``<KEY>_BASE_URL`` env var)
2. otherwise probe the known candidates, **follow redirects**, and adopt the
   final origin
3. park/junk hosts (expired-domains, ``ww<digits>.``, sedo, bodis …) are
   rejected, so a squatted mirror is never selected
4. the verdict is cached (successes long, failures short) so we don't probe on
   every call

Only the origin (``scheme://host``) is resolved; paths stay with the callers.
"""

from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: Known candidate origins per source key, best-first.
KNOWN_ALTERNATES: dict[str, list[str]] = {
    "animedekho": ["https://animedekho.tv", "https://animedekho.app"],
    "toonflix": ["https://toonflix.in"],
    "animedrive": ["https://animedrive.cc", "https://animedrive.me"],
    "toonanime": [
        "https://toonanime.biz",
        "https://toonanime.tv",
        "https://toonanime.cc",
        "https://toonanimes.com",
    ],
    "toono": ["https://toono.app"],
    "rareanimes": ["https://www.rareanimes.mov", "https://rareanimes.mov"],
    "deadtoons": ["https://deadtoons.sbs"],
    "toonworld4all": ["https://toonworld4all.me"],
    "animedubhindi": ["https://www.animedubhindi.link"],
    "4khdhub": ["https://4khdhub.one"],
}

#: Hosts/paths that mean "this mirror is parked/squatted" — never select them.
_PARKED_HINTS = (
    "expireddomains", "sedo", "bodis", "parking", "/lander", "afternic",
    "dan.com", "hugedomains", "?subid1=",
)
_PARKED_HOST_RE = re.compile(r"^ww\d+\.", re.I)

_OK_TTL = 3600.0      # a resolved origin is good for an hour
_FAIL_TTL = 120.0     # remember a dead candidate briefly
_TIMEOUT = 10.0

#: key -> (expiry_ts, origin)
_cache: dict[str, tuple[float, str]] = {}


def _norm_origin(url: str) -> str:
    """Return ``scheme://host`` for a URL (drops path/query/trailing slash)."""
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


def _is_parked(final_url: str, body: str = "") -> bool:
    host = urlparse(final_url or "").netloc or ""
    if _PARKED_HOST_RE.match(host):
        return True
    hay = (host + "/" + urlparse(final_url or "").path).lower() + " " + (body or "").lower()[:4000]
    return any(h in hay for h in _PARKED_HINTS)


def _env_override(key: str) -> str:
    return _norm_origin(os.environ.get(f"{key.upper()}_BASE_URL", ""))


def _cached(key: str) -> str | None:
    ent = _cache.get(key)
    if ent and ent[0] > time.time():
        return ent[1]
    return None


def _store(key: str, origin: str, ttl: float) -> None:
    _cache[key] = (time.time() + ttl, origin)


def reset_cache() -> None:
    """Drop cached verdicts (used by tests / after a manual override change)."""
    _cache.clear()


def resolve_base_sync(key: str, candidates: list[str] | None = None, *, validate=None) -> str:
    """Blocking resolver (for extractors that run in a thread executor).

    Returns the live origin, or the first candidate as a last resort so callers
    never get an empty string.
    """
    cands = candidates or KNOWN_ALTERNATES.get(key) or []
    override = _env_override(key)
    if override:
        return override
    hit = _cached(key)
    if hit:
        return hit

    import requests
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    for cand in cands:
        origin = _norm_origin(cand)
        if not origin:
            continue
        try:
            r = requests.get(origin, timeout=_TIMEOUT, allow_redirects=True,
                             headers={"User-Agent": ua})
            final = _norm_origin(r.url) or origin
            if r.status_code == 200 and not _is_parked(r.url, r.text):
                if validate is not None and not validate(r.text, r.url):
                    log.debug("domains: %s candidate %s failed validation", key, origin)
                    continue
                if final != origin:
                    log.info("domains: %s moved %s → %s", key, origin, final)
                _store(key, final, _OK_TTL)
                return final
            log.debug("domains: %s candidate %s rejected (status=%s parked=%s)",
                      key, origin, r.status_code, _is_parked(r.url, r.text))
        except Exception as e:
            log.debug("domains: %s candidate %s probe failed: %s", key, origin, e)
    if cands:
        _store(key, _norm_origin(cands[0]), _FAIL_TTL)
        log.warning("domains: %s — no live candidate, falling back to %s", key, cands[0])
        return _norm_origin(cands[0])
    return ""


async def resolve_base_async(key: str, candidates: list[str] | None = None, *, validate=None) -> str:
    """Async resolver (for aiohttp-based callers)."""
    cands = candidates or KNOWN_ALTERNATES.get(key) or []
    override = _env_override(key)
    if override:
        return override
    hit = _cached(key)
    if hit:
        return hit

    import aiohttp
    to = aiohttp.ClientTimeout(total=_TIMEOUT)
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    try:
        async with aiohttp.ClientSession(timeout=to) as sess:
            for cand in cands:
                origin = _norm_origin(cand)
                if not origin:
                    continue
                try:
                    async with sess.get(origin, headers={"User-Agent": ua},
                                        allow_redirects=True) as r:
                        body = await r.text(errors="ignore")
                        final = _norm_origin(str(r.url)) or origin
                        if r.status == 200 and not _is_parked(str(r.url), body):
                            if validate is not None and not validate(body, str(r.url)):
                                continue
                            if final != origin:
                                log.info("domains: %s moved %s → %s", key, origin, final)
                            _store(key, final, _OK_TTL)
                            return final
                except Exception as e:
                    log.debug("domains: %s candidate %s probe failed: %s", key, origin, e)
    except Exception as e:
        log.debug("domains: %s aiohttp session failed: %s", key, e)
    if cands:
        _store(key, _norm_origin(cands[0]), _FAIL_TTL)
        log.warning("domains: %s — no live candidate, falling back to %s", key, cands[0])
        return _norm_origin(cands[0])
    return ""


def base_url_sync(key: str, default: str, candidates: list[str] | None = None) -> str:
    """Convenience: live origin for *key*, with *default* as ultimate fallback."""
    try:
        resolved = resolve_base_sync(key, candidates)
        return resolved or default
    except Exception as e:
        log.debug("domains: %s resolution failed (%s), using default %s", key, e, default)
        return default


def live_base(obj, key: str, candidates: list[str] | None = None,
              default: str = "", attr: str = "_base_url") -> str:
    """Refresh ``obj.<attr>`` with the live origin for *key* and return it.

    Call this at the top of an extractor's network methods. It is cheap after
    the first call (the resolver caches for an hour) and never raises — on any
    problem the current value is kept, so behaviour only ever improves.
    """
    try:
        cur = getattr(obj, attr, "") or default
        resolved = base_url_sync(key, cur, candidates)
        if resolved and resolved != cur:
            setattr(obj, attr, resolved)
            log.info("%s: live base domain → %s", key, resolved)
        return getattr(obj, attr, cur)
    except Exception as e:
        log.debug("domains: live_base(%s) failed: %s", key, e)
        return getattr(obj, attr, default)
