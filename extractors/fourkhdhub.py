"""4KHDHub extractor — high-bitrate, multi-audio anime (movies & series).

Site: https://4khdhub.one  (only the /category/anime section is used)

The site's *anime* section is not a 2160p source: it carries high-bitrate
**1080p** releases (BluRay REMUX, x265/HEVC, AV1 and H.264 WEB-DL variants)
with multi-audio tracks (Hindi + English + Japanese) and dual-audio packs —
i.e. the quality tiers the other sources rarely expose. The site as a whole is
a general release index (Hollywood, Bollywood, K-Drama …); per the operator's
instruction the bot consumes *only* the Anime section, filtered here and at
registration time.

Flow:

1. ``GET /?s=<query>`` returns ``.movie-card`` tiles (title + href + poster).
   Results carry ``.movie-card-format`` tags; a tile is kept only when it looks
   like anime (a ``Anime``/``Animation`` tag, or the anime category marker).
2. A series page exposes per-quality blocks under ``.episode-download-item``
   (filename carries ``SxxEyy`` + resolution + size). Each block's download
   links point at an obfuscated **mediator** (``greenmotors.club/?id=…``).
3. The mediator is an ad-gate: after a "verify" click it opens the real
   **HubCloud** drive URL. We clear it with the shared Playwright browser
   (``extractors.browser_gate``) — the same non-headless/Xvfb machinery used
   for the AnimeDekho DGT gate.
4. ``animedrive._resolve_hubcloud`` turns the HubCloud page into a direct
   stream URL.

Everything degrades gracefully: if Playwright/Xvfb is unavailable the mediator
step returns ``None`` and the source is simply skipped, exactly like the other
providers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote_plus

import cloudscraper
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


def _get_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


#: Tile must show at least one of these to count as anime.
_ANIME_TAGS = {"anime", "animation"}
#: Hard reject — these are the non-anime sections the bot must ignore.
_NON_ANIME_TAGS = {
    "hollywood", "bollywood", "k-drama", "kdrama", "korean drama", "web series",
    "hindi dubbed movie", "tamil", "telugu", "punjabi",
}

_SEASON_EP_RE = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
_QUALITY_RE = re.compile(r"(?i)\b(2160p|1440p|1080p|720p|480p|4k|uhd)\b")
_SIZE_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(GB|MB)")


class FourKHDHubExtractor:
    """Anime-only extractor for 4khdhub.one (high-bitrate 1080p / multi-audio)."""

    def __init__(self):
        self._base_url = "https://4khdhub.one"
        self._anime_path = "/category/anime/"

    def _get_scraper(self) -> cloudscraper.CloudScraper:
        return _get_scraper()

    # ── helpers ──────────────────────────────────────────────────────────

    def _is_anime_card(self, card) -> bool:
        """True when a ``.movie-card`` tile is anime (and not an excluded genre)."""
        tags = {
            t.get_text(" ", strip=True).strip().lower()
            for t in card.select(".movie-card-format")
        }
        if tags & _NON_ANIME_TAGS and not (tags & _ANIME_TAGS):
            return False
        if tags & _ANIME_TAGS:
            return True
        # Fallback: the card links into an anime-only category URL.
        for a in card.find_all("a", href=True):
            if "/category/anime" in a["href"] or a["href"].startswith("/anime"):
                return True
        return False

    def _parse_cards(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[dict] = []
        seen: set[str] = set()
        for card in soup.select(".movie-card"):
            href = card.get("href")
            if not href:
                a = card.find("a", href=True)
                href = a["href"] if a else None
            if not href or href in seen:
                continue
            if not self._is_anime_card(card):
                continue
            seen.add(href)
            title_el = card.select_one(".movie-card-title") or card.find(["h2", "h3", "h4"])
            img = card.find("img")
            raw_p = (img.get("src") or img.get("data-src") or "") if img else ""
            try:
                from utils.anilist import is_valid_poster_url
                poster = raw_p if is_valid_poster_url(raw_p) else ""
            except Exception:
                poster = raw_p
            out.append({
                "title": (title_el.get_text(" ", strip=True) if title_el else "").strip(),
                "url": href if href.startswith("http") else f"{self._base_url}{href}",
                "poster": poster,
            })
        return out

    # ── search / recent ──────────────────────────────────────────────────

    async def search(self, query: str) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_search, query)

    def _sync_search(self, query: str) -> list[dict]:
        from extractors import domains
        domains.live_base(self, "4khdhub")
        s = _get_scraper()
        clean = re.sub(
            r"(?i)\s*(season\s*\d+|s\d+|hindi|dubbed|multi-audio|tamil|telugu|uncensored|episodes).*$",
            "", query,
        ).strip()
        for q in dict.fromkeys([clean or query, query]):
            try:
                r = s.get(f"{self._base_url}/?s={quote_plus(q)}", timeout=20)
                if r.status_code != 200:
                    continue
                results = self._parse_cards(r.text)
                if results:
                    for item in results:
                        item["source"] = "4KHDHub"
                    return results
            except Exception as e:
                log.warning("4KHDHub search failed for '%s': %s", q, e)
        return []

    async def get_recent(self, page: int = 1) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_get_recent, page)

    def _sync_get_recent(self, page: int = 1) -> list[dict]:
        from extractors import domains
        domains.live_base(self, "4khdhub")
        s = _get_scraper()
        url = f"{self._base_url}{self._anime_path}"
        if page > 1:
            url += f"?catx=anime&pagex={page}"
        try:
            r = s.get(url, timeout=20)
            if r.status_code != 200:
                return []
            results = self._parse_cards(r.text)
            for item in results:
                item["source"] = "4KHDHub"
            return results
        except Exception as e:
            log.warning("4KHDHub get_recent failed (page %d): %s", page, e)
            return []

    # ── episode resolution ───────────────────────────────────────────────

    async def resolve_episode(
        self,
        anime_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
    ) -> dict | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._sync_resolve, anime_title, season, episode, quality_pref
        )

    def _pick_mediator(self, soup_page, season: int, episode: int,
                       quality_pref: str) -> tuple[str | None, str, float | None]:
        """Choose the best mediator link for (season, episode, quality).

        Returns ``(mediator_url, detected_quality, size_mb)``.
        """
        from utils.anime_match import qualities_match, parse_size_mb
        want  = (quality_pref or "").lower()
        is_4k = want in ("4k", "2160p", "2160", "uhd")

        best = (None, "", None, -1)
        for item in soup_page.select(".episode-download-item"):
            title_el = item.select_one(".episode-file-title")
            fname = title_el.get_text(" ", strip=True) if title_el else ""
            if not fname:
                continue
            m = _SEASON_EP_RE.search(fname)
            if m:
                if int(m.group(1)) != season or int(m.group(2)) != episode:
                    continue
            else:
                # Pack file (complete season) — accept only for episode 1.
                if episode != 1:
                    continue
            qm = _QUALITY_RE.search(fname)
            q = qm.group(1) if qm else ""
            if q.lower() in ("4k", "uhd", "2160p"):
                q = "4K"
            size = parse_size_mb(fname)
            if size is None:
                info = item.select_one(".episode-file-info")
                if info:
                    sm = _SIZE_RE.search(info.get_text(" ", strip=True))
                    if sm:
                        try:
                            size = float(sm.group(1)) * (1024 if sm.group(2).upper() == "GB" else 1)
                        except Exception:
                            size = None
            # Score: exact quality match wins, then larger size.
            score = 0
            if is_4k:
                score = 1000 if q == "4K" else -1
            else:
                if qualities_match(quality_pref, q, size=size):
                    score = 1000
                elif q:
                    score = 10
            if score < 0:
                continue
            link = None
            for a in item.find_all("a", href=True):
                if "hubcloud" in a.get_text(" ", strip=True).lower() or "hubcloud" in a["href"].lower():
                    link = a["href"]
                    break
            if link is None:
                a = item.find("a", href=True)
                link = a["href"] if a else None
            if not link:
                continue
            composite = score * 10000 + (size or 0)
            if composite > best[3]:
                best = (link, q, size, composite)
        return best[0], best[1], best[2]

    def _sync_resolve(self, anime_title: str, season: int, episode: int,
                      quality_pref: str) -> dict | None:
        from extractors import domains
        domains.live_base(self, "4khdhub")
        s = _get_scraper()

        results = self._sync_search(anime_title)
        if not results:
            return None

        from utils.anime_match import is_confident_match
        page_url = None
        poster = None
        for res in results:
            if not is_confident_match(anime_title, res.get("title", ""), res.get("url", "")):
                continue
            # 4KHDHub lists every season of a series on one page, so the season
            # is resolved from the per-file SxxEyy tag, not the page title.
            page_url = res.get("url")
            poster = res.get("poster")
            break
        if not page_url:
            log.info("4KHDHub: no confident S%d match for '%s'", season, anime_title)
            return None

        try:
            r = s.get(page_url, timeout=25)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log.warning("4KHDHub: series page fetch failed: %s", e)
            return None

        mediator, quality, size = self._pick_mediator(soup, season, episode, quality_pref)
        if not mediator:
            log.info("4KHDHub: no link for S%dE%d on %s", season, episode, page_url)
            return None

        final_url = self._unlock_mediator(mediator)
        if not final_url:
            log.info("4KHDHub: mediator unlock failed (%s)", mediator[:70])
            return None

        stream = None
        if any(x in final_url.lower() for x in ("hubcloud", "gamerxyt")):
            try:
                from extractors.animedrive import animedrive
                stream = animedrive._resolve_hubcloud(_get_scraper(), final_url)
            except Exception as e:
                log.warning("4KHDHub: hubcloud resolve failed: %s", e)
        else:
            stream = final_url

        if not stream:
            return None
        return {
            "url": stream,
            "quality": quality or "",
            "requested_quality": quality_pref,
            "detected_quality": quality or "",
            "verified_quality": quality or "",
            "size_mb": size,
            "server": "4KHDHub",
            "referer": "https://4khdhub.one/",
            "poster": poster,
        }

    def _unlock_mediator(self, mediator_url: str) -> str | None:
        """Clear the ad-gate mediator and return the real HubCloud (or stream) URL.

        Runs the shared Playwright browser synchronously (we are already inside
        a thread executor). Returns ``None`` when the browser is unavailable.
        """
        try:
            from extractors.browser_gate import browser_available
        except Exception:
            return None
        if not browser_available():
            log.info("4KHDHub: browser unavailable — cannot clear mediator gate")
            return None

        try:
            from extractors.browser_gate import unlock_mediator_sync
            return unlock_mediator_sync(mediator_url, referer="https://4khdhub.one/")
        except Exception as e:
            log.warning("4KHDHub: mediator unlock error: %s", e)
            return None


fourkhdhub = FourKHDHubExtractor()
