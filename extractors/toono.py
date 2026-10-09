"""Toono extractor — sister source with Toronites theme for high-speed streaming."""

from __future__ import annotations
import asyncio
import logging
import re
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import cloudscraper

from utils.anilist import is_valid_poster_url
from extractors.resolver import resolve_player_url

log = logging.getLogger(__name__)


def _get_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


class ToonoExtractor:
    """Extracts anime series and episodes from TOONo (toono.app)."""

    def __init__(self):
        self._base_url = "https://toono.app"

    async def search(self, query: str) -> list[dict]:
        """Search TOONo catalog for anime."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_search, query)

    def _sync_search(self, query: str) -> list[dict]:
        s = _get_scraper()
        clean = re.sub(
            r"(?i)\s*(season\s*\d+|s\d+|hindi|dubbed|multi-audio|tamil|telugu).*$",
            "",
            query,
        ).strip()
        search_query = clean or query
        url = f"{self._base_url}/?s={quote_plus(search_query)}"

        try:
            r = s.get(url, timeout=12)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            seen = set()

            for art in soup.find_all("article"):
                h = art.find(["h1", "h2", "h3", "h4", "h5", "header"])
                a = art.find("a", href=True)
                if not a:
                    continue
                href = a["href"]
                if ("/series/" not in href and "/movies/" not in href and "/movie/" not in href) or href in seen:
                    continue
                seen.add(href)
                raw_title = h.get_text(strip=True) if h else a.get_text(strip=True)
                title = re.sub(r"\d{4}$", "", raw_title).strip()
                if not title or title.lower() in ("watch series", "series", "watch movies", "movies"):
                    continue

                poster = ""
                img = art.find("img")
                if img:
                    p_url = img.get("src") or img.get("data-src", "")
                    if is_valid_poster_url(p_url):
                        poster = p_url

                results.append({
                    "title": title,
                    "url": href,
                    "poster": poster,
                    "source": "TOONo",
                })

            if not results:
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if ("/series/" not in href and "/movie/" not in href) or href in seen:
                        continue
                    seen.add(href)
                    title = a.get_text(strip=True)
                    if not title or title.lower() in ("watch series", "series", "watch movies", "movies"):
                        continue

                    poster = ""
                    img = a.find("img")
                    if img:
                        p_url = img.get("src") or img.get("data-src", "")
                        if is_valid_poster_url(p_url):
                            poster = p_url

                    results.append({
                        "title": title,
                        "url": href,
                        "poster": poster,
                        "source": "TOONo",
                    })

            return results
        except Exception as e:
            log.warning("TOONo search failed for '%s': %s", query, e)
            return []

    async def get_recent(self, page: int = 1) -> list[dict]:
        """Fetch recently released anime from TOONo."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_get_recent, page)

    def _sync_get_recent(self, page: int = 1) -> list[dict]:
        s = _get_scraper()
        url = f"{self._base_url}/series/page/{page}/" if page > 1 else f"{self._base_url}/series/"
        try:
            r = s.get(url, timeout=12)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            seen = set()
            for art in soup.find_all("article"):
                a = art.find("a", href=True)
                if not a:
                    continue
                href = a["href"]
                if ("/series/" not in href and "/movies/" not in href and "/movie/" not in href) or href in seen:
                    continue
                seen.add(href)
                title_el = art.find(["h1", "h2", "h3", "h4", "h5", "header"])
                title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
                if not title:
                    continue
                img = art.find("img")
                poster = ""
                if img:
                    p_url = img.get("src") or img.get("data-src", "")
                    if is_valid_poster_url(p_url):
                        poster = p_url
                results.append({
                    "title": title,
                    "url": href,
                    "poster": poster,
                    "source": "TOONo",
                })
            return results
        except Exception as e:
            log.warning("TOONo get_recent failed (page %d): %s", page, e)
            return []


    async def resolve_episode(
        self,
        anime_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
    ) -> dict | None:
        """Resolve direct stream/download link from TOONo."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._sync_resolve, anime_title, season, episode, quality_pref
        )

    def _sync_resolve(
        self,
        anime_title: str,
        season: int,
        episode: int,
        quality_pref: str,
    ) -> dict | None:
        s = _get_scraper()
        search_results = self._sync_search(anime_title)
        if not search_results:
            return None

        # V3 #5: never blindly take search_results[0] — rank by confident
        # title match + strict season agreement, reject on weak match.
        from utils.anime_match import is_confident_match, matches_season
        ranked = [
            r for r in search_results
            if is_confident_match(anime_title, r.get("title", ""), r.get("url", ""))
            and matches_season(r.get("title", ""), r.get("url", ""), season)
        ]
        if not ranked:
            log.info("TOONo: no confident S%d match for '%s' — rejecting", season, anime_title)
            return None
        target_series = ranked[0]
        series_url = target_series["url"]

        try:
            r = s.get(series_url, timeout=12)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")

            # Look for episode link: /episode/slug-1x1/ or /epi/slug-1x1/
            ep_url = None
            pat = re.compile(rf"[-_]{season}x0*{episode}/?", re.I)
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if pat.search(href) and ("/episode/" in href or "/epi/" in href):
                    ep_url = href
                    break

            if not ep_url:
                return None

            # Fetch episode page
            r_ep = s.get(ep_url, timeout=12)
            if r_ep.status_code != 200:
                return None
            soup_ep = BeautifulSoup(r_ep.text, "html.parser")

            # Look for iframe (e.g. ?trembed=1&trid=... or player URL)
            for iframe in soup_ep.find_all("iframe"):
                src = iframe.get("src", "")
                if "trembed" in src or "trid" in src:
                    # Fetch embed iframe with referer
                    r_ifr = s.get(src, headers={"Referer": ep_url}, timeout=10)
                    if r_ifr.status_code == 200:
                        soup_ifr = BeautifulSoup(r_ifr.text, "html.parser")
                        inner_ifr = soup_ifr.find("iframe")
                        if inner_ifr and inner_ifr.get("src"):
                            player_url = inner_ifr["src"]
                            return {
                                "url": player_url,
                                "quality": "Unknown",
                                "requested_quality": quality_pref,
                                "detected_quality": "Unknown",
                                "verified_quality": "Unknown",
                                "source": "TOONo",
                                "poster": target_series.get("poster"),
                            }
                elif "embed" in src or "player" in src:
                    return {
                        "url": src,
                        "quality": "Unknown",
                        "requested_quality": quality_pref,
                        "detected_quality": "Unknown",
                        "verified_quality": "Unknown",
                        "source": "TOONo",
                        "poster": target_series.get("poster"),
                    }
        except Exception as e:
            log.warning("TOONo resolve error: %s", e)

        return None


toono = ToonoExtractor()
