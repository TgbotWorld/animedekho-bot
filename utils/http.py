"""
Async HTTP client with caching, retries, and Cloudflare bypass.

Uses aiohttp for general requests and cloudscraper for Cloudflare-protected
sites (like animedekho.app).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import aiohttp
import cloudscraper

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Domains that need cloudscraper (Cloudflare-protected)
# V2 #7: animedekho.tv is the live domain (.app 301-redirects to it).
# V3 #9/#11: bypass-site hosts are Cloudflare-fronted and return 403 to
# plain aiohttp from datacenter IPs — route them via cloudscraper (which
# succeeds from here) instead of reporting honest fetch failures.
_CLOUDFLARE_DOMAINS = (
    "animedekho.tv",
    "animedekho.app",
    "animedrive.me",
    "link.animedrive.me",
    "hubcloud.ist",
    "toonflix.in",
    "drive.toonflix.in",
    "files.toonflix.in",
    "animedubhindi.link",
    "adhlinks.com",
    "toonworld4all.me",
    "archive.toonworld4all.me",
    "toonanime.",
    "rareanimes.",
    "deadtoons.",
    "toono.app",
)


# ── Cache entry ────────────────────────────────────────────────────────


@dataclass
class _CacheEntry:
    data: Any
    expires: float


# ── HTTP client ────────────────────────────────────────────────────────


class HTTPClient:
    """
    Async HTTP client with:
    - In-memory response cache (respects ``ttl`` kwarg)
    - Automatic retries with exponential backoff
    - Cloudflare bypass via cloudscraper for protected domains
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._cloudscraper: cloudscraper.CloudScraper | None = None
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create the underlying aiohttp session, closing any previous session."""
        async with self._lock:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = aiohttp.ClientSession(
                headers=DEFAULT_HEADERS,
                timeout=DEFAULT_TIMEOUT,
            )
            if self._cloudscraper is None:
                self._cloudscraper = cloudscraper.create_scraper()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Get or recreate an active aiohttp session thread-safely."""
        async with self._lock:
            if self._session is None or self._session.closed:
                if self._session and not self._session.closed:
                    await self._session.close()
                self._session = aiohttp.ClientSession(
                    headers=DEFAULT_HEADERS,
                    timeout=DEFAULT_TIMEOUT,
                )
            if self._cloudscraper is None:
                self._cloudscraper = cloudscraper.create_scraper()
            return self._session

    async def close(self) -> None:
        """Close the aiohttp session and cleanup resources."""
        async with self._lock:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None
            self._cloudscraper = None

    # ── Internal helpers ───────────────────────────────────────────────

    def _cache_key(self, method: str, url: str, **kwargs) -> str:
        """Build a deterministic cache key."""
        parts = [method.upper(), url]
        if "headers" in kwargs:
            parts.append(json.dumps(kwargs["headers"], sort_keys=True))
        if "data" in kwargs:
            parts.append(json.dumps(kwargs["data"], sort_keys=True))
        return "|".join(parts)

    def _is_cloudflare_domain(self, url: str) -> bool:
        """Check if URL belongs to a Cloudflare-protected domain."""
        return any(domain in url for domain in _CLOUDFLARE_DOMAINS)

    def _get_cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry and time.time() < entry.expires:
            return entry.data
        if entry:
            del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any, ttl: int) -> None:
        if ttl > 0:
            self._cache[key] = _CacheEntry(data=data, expires=time.time() + ttl)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        ttl: int = 300,
        no_cache: bool = False,
        max_retries: int = 3,
        **kwargs,
    ) -> str:
        """
        Make an HTTP request with caching and retries.
        Uses cloudscraper for Cloudflare-protected domains.
        """
        cache_key = self._cache_key(method, url, headers=headers, data=data)

        if not no_cache and ttl > 0:
            cached = self._get_cached(cache_key)
            if cached is not None:
                log.debug("Cache hit: %s %s", method, url)
                return cached

        # Use cloudscraper for Cloudflare-protected domains
        if self._is_cloudflare_domain(url):
            return await self._cloudscraper_request(
                method, url, headers=headers, data=data,
                ttl=ttl, no_cache=no_cache, cache_key=cache_key,
            )

        # Use aiohttp for normal requests
        return await self._aiohttp_request(
            method, url, headers=headers, data=data,
            ttl=ttl, no_cache=no_cache, max_retries=max_retries,
            cache_key=cache_key, **kwargs,
        )

    async def _cloudscraper_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        ttl: int = 300,
        no_cache: bool = False,
        cache_key: str = "",
    ) -> str:
        """Make a request using cloudscraper (runs in thread pool).
        
        Falls back to aiohttp if cloudscraper fails with 403/429.
        """
        loop = asyncio.get_event_loop()

        def _do_request():
            merged_headers = dict(DEFAULT_HEADERS)
            if headers:
                merged_headers.update(headers)

            # Recreate scraper if needed (fresh challenge cookies)
            if self._cloudscraper is None:
                self._cloudscraper = cloudscraper.create_scraper()

            if method.upper() == "POST":
                resp = self._cloudscraper.post(
                    url, data=data, headers=merged_headers, timeout=30
                )
            else:
                resp = self._cloudscraper.get(
                    url, headers=merged_headers, timeout=30
                )
            resp.raise_for_status()
            return resp.text

        last_error = None
        for attempt in range(3):
            try:
                text = await loop.run_in_executor(None, _do_request)
                if not no_cache and ttl > 0 and cache_key:
                    self._set_cached(cache_key, text, ttl)
                return text
            except Exception as e:
                last_error = e
                status = getattr(getattr(e, 'response', None), 'status_code', 0)
                log.warning(
                    "Cloudscraper request failed (attempt %d/3): %s %s — %s (status=%s)",
                    attempt + 1, method, url, e, status,
                )
                # Recreate scraper on 403/429 to get fresh challenge
                if status in (403, 429):
                    self._cloudscraper = cloudscraper.create_scraper()
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)

        # Fallback to aiohttp if cloudscraper completely fails
        log.warning("Cloudscraper failed for %s, trying aiohttp fallback", url)
        try:
            return await self._aiohttp_request(
                method, url, headers=headers, data=data,
                ttl=ttl, no_cache=no_cache, max_retries=2,
                cache_key=cache_key,
            )
        except Exception:
            pass

        raise last_error or Exception(f"Cloudscraper request failed: {url}")

    async def _aiohttp_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        ttl: int = 300,
        no_cache: bool = False,
        max_retries: int = 3,
        cache_key: str = "",
        **kwargs,
    ) -> str:
        """Make a request using aiohttp."""
        merged_headers = dict(DEFAULT_HEADERS)
        if headers:
            merged_headers.update(headers)

        last_error = None
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()

                if method.upper() == "POST":
                    async with session.post(
                        url, data=data, headers=merged_headers, **kwargs
                    ) as resp:
                        resp.raise_for_status()
                        text = await resp.text()
                else:
                    async with session.get(
                        url, headers=merged_headers, **kwargs
                    ) as resp:
                        resp.raise_for_status()
                        text = await resp.text()

                if not no_cache and ttl > 0 and cache_key:
                    self._set_cached(cache_key, text, ttl)
                return text

            except aiohttp.ClientResponseError as e:
                last_error = e
                if e.status in (429, 500, 502, 503, 504):
                    wait = 2 ** attempt
                    log.warning(
                        "HTTP %d (attempt %d/%d), retrying in %ds: %s",
                        e.status, attempt + 1, max_retries, wait, url,
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
            except aiohttp.ClientError as e:
                last_error = e
                wait = 2 ** attempt
                log.warning(
                    "Network error (attempt %d/%d), retrying in %ds: %s — %s",
                    attempt + 1, max_retries, wait, url, e,
                )
                await asyncio.sleep(wait)

        raise last_error or Exception(f"Request failed after {max_retries} retries: {url}")

    # ── Public API ─────────────────────────────────────────────────────

    async def get(self, url: str, *, headers: dict | None = None, ttl: int = 300, **kwargs) -> str:
        """GET request — cached by default."""
        return await self._request("GET", url, headers=headers, ttl=ttl, **kwargs)

    async def get_no_cache(self, url: str, *, headers: dict | None = None, **kwargs) -> str:
        """GET request — bypass cache."""
        return await self._request("GET", url, headers=headers, ttl=0, no_cache=True, **kwargs)

    async def get_text_no_cache(self, url: str, *, headers: dict | None = None, **kwargs) -> str:
        """Alias for get_no_cache."""
        return await self.get_no_cache(url, headers=headers, **kwargs)

    async def get_json(self, url: str, *, headers: dict | None = None, ttl: int = 300, **kwargs) -> Any:
        """GET request and parse JSON."""
        text = await self.get(url, headers=headers, ttl=ttl, **kwargs)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            snippet = (text or "")[:120].replace("\n", " ")
            raise ValueError(
                f"Non-JSON response from {url} ({e}); body: {snippet!r}"
            ) from e

    async def post(self, url: str, *, data: dict | None = None, headers: dict | None = None, ttl: int = 0, **kwargs) -> str:
        """POST request — not cached by default."""
        return await self._request("POST", url, data=data, headers=headers, ttl=ttl, **kwargs)

    async def post_no_cache(self, url: str, *, data: dict | None = None, headers: dict | None = None, **kwargs) -> str:
        """POST request — bypass cache."""
        return await self._request("POST", url, data=data, headers=headers, ttl=0, no_cache=True, **kwargs)

    async def get_with_redirects(
        self, url: str, *, headers: dict | None = None, **kwargs
    ) -> tuple[str, str]:
        """
        GET request that follows redirects.
        Returns (final_url, response_text).
        """
        if self._is_cloudflare_domain(url):
            loop = asyncio.get_event_loop()

            def _do_get():
                # Lazy init: this path previously used a possibly-None
                # _cloudscraper and crashed with AttributeError.
                if self._cloudscraper is None:
                    import cloudscraper as _cs
                    self._cloudscraper = _cs.create_scraper()
                merged_headers = dict(DEFAULT_HEADERS)
                if headers:
                    merged_headers.update(headers)
                resp = self._cloudscraper.get(
                    url, headers=merged_headers, timeout=30, allow_redirects=True
                )
                resp.raise_for_status()
                return resp.url, resp.text

            return await loop.run_in_executor(None, _do_get)

        # Use aiohttp
        merged_headers = dict(DEFAULT_HEADERS)
        if headers:
            merged_headers.update(headers)

        session = await self._ensure_session()

        async with session.get(
            url, headers=merged_headers, allow_redirects=True, **kwargs
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()
            final_url = str(resp.url)
            return final_url, text

    async def post_follow_redirects(
        self, url: str, *, data: dict | None = None, headers: dict | None = None, **kwargs
    ) -> tuple[str, str]:
        """
        POST request that follows redirects.
        Returns (response_text, final_url).
        """
        if self._is_cloudflare_domain(url):
            # Use cloudscraper (follows redirects automatically)
            loop = asyncio.get_event_loop()

            def _do_post():
                # Lazy init (same None-crash as get_with_redirects).
                if self._cloudscraper is None:
                    import cloudscraper as _cs
                    self._cloudscraper = _cs.create_scraper()
                merged_headers = dict(DEFAULT_HEADERS)
                if headers:
                    merged_headers.update(headers)
                resp = self._cloudscraper.post(
                    url, data=data, headers=merged_headers, timeout=30, allow_redirects=True
                )
                resp.raise_for_status()
                return resp.text, resp.url

            text, final_url = await loop.run_in_executor(None, _do_post)
            return text, final_url

        # Use aiohttp
        merged_headers = dict(DEFAULT_HEADERS)
        if headers:
            merged_headers.update(headers)

        session = await self._ensure_session()

        async with session.post(
            url, data=data, headers=merged_headers, allow_redirects=True, **kwargs
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()
            final_url = str(resp.url)
            return text, final_url


# ── Singleton ──────────────────────────────────────────────────────────

http_client = HTTPClient()
