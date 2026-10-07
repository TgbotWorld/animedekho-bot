"""DeadToons extractor — fallback source for Hindi & Multi-Audio anime."""

from __future__ import annotations
import asyncio
import logging
import re
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import cloudscraper

from utils.anilist import is_valid_poster_url

log = logging.getLogger(__name__)


def _get_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


class DeadToonsExtractor:
    """Extracts Multi-Audio & Hindi anime series from DeadToons (deadtoons.sbs)."""

    def __init__(self):
        self._base_url = "https://deadtoons.sbs"

    async def search(self, query: str) -> list[dict]:
        """Search DeadToons for anime series or movies."""
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
        url = f"{self._base_url}/search?q={quote_plus(search_query)}"

        try:
            r = s.get(url, timeout=12)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            seen = set()

            # Issue #34: prefer the headings. The raw anchor walk returned card
            # chips ("Movie7.90", "Completed") as titles, so every result from
            # this source looked like junk.
            heading_anchors = soup.select("h2 a[href], h3 a[href]")
            anchors = heading_anchors or soup.find_all("a", href=True)

            for a in anchors:
                href = a["href"]
                if "/posts/" not in href or href in seen:
                    continue
                seen.add(href)
                full_url = href if href.startswith("http") else f"{self._base_url}{href}"
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 3 or title.lower() in ("completed", "ongoing", "movie"):
                    continue
                if not heading_anchors and (
                    len(title) < 12
                    or re.match(r"(?i)^(completed|ongoing|movie|episode|sub|dub|hd|4k)\b", title)
                    or re.search(r"\d+\.\d{1,2}\b", title)
                ):
                    # status/rating chip on a card, not a title
                    continue

                poster = ""
                img = a.find("img") or (a.parent.find("img") if a.parent else None)
                if img:
                    p_url = img.get("src") or img.get("data-src", "")
                    if is_valid_poster_url(p_url):
                        poster = p_url

                results.append({
                    "title": title,
                    "url": full_url,
                    "poster": poster,
                    "source": "DeadToons",
                })

            return results
        except Exception as e:
            log.warning("DeadToons search failed for '%s': %s", query, e)
            return []

    async def resolve_episode(
        self,
        anime_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
    ) -> dict | None:
        """Resolve an episode stream/download link from DeadToons."""
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

        # V3 #5: confident title match (no single-keyword guess).
        from utils.anime_match import is_confident_match, matches_season
        target_post = None
        for res in search_results:
            t = res.get("title", "")
            href = res.get("url", "")
            if not is_confident_match(anime_title, t, href):
                continue
            if (
                f"season {season}" in t.lower()
                or f"season {season:02d}" in t.lower()
                or f"s{season}" in t.lower()
                or f"s{season:02d}" in t.lower()
                or (season == 1 and "season" not in t.lower() and "s0" not in t.lower() and "s1" not in t.lower())
            ):
                if not matches_season(t, href, season):
                    continue
                target_post = res
                break

        if not target_post:
            log.info("DeadToons: No verified season %d post for '%s'", season, anime_title)
            return None

        post_url = target_post["url"]
        try:
            r = s.get(post_url, timeout=12)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")

            # Look for episode download link (e.g., /episode/.../{season}x{episode})
            pattern = re.compile(rf"/{season}x0*{episode}\b", re.I)
            ep_href = None
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if pattern.search(href) or f"episode/{season}x{episode}" in href.lower():
                    ep_href = href
                    break

            if not ep_href:
                return None

            # Issue #35: an /episode/ link is an HTML page, not media. The old
            # code returned it as the stream URL, so the downloader fetched a
            # web page and presented it as a download. Follow the page and
            # extract the real media link; if the page is blocked (403/CF) or
            # carries no media, return None so the next source gets a chance.
            ep_url = ep_href if ep_href.startswith("http") else f"{self._base_url}{ep_href}"
            r_ep = s.get(ep_url, timeout=12)
            if r_ep.status_code != 200:
                log.warning("DeadToons: episode page %s returned HTTP %d", ep_url, r_ep.status_code)
                return None
            ep_ctype = (r_ep.headers.get("Content-Type") or "").lower()
            if "text/html" not in ep_ctype:
                # Served media directly instead of a page.
                return {
                    "url": ep_url,
                    "quality": "Unknown",
                    "requested_quality": quality_pref,
                    "detected_quality": "Unknown",
                    "verified_quality": "Unknown",
                    "source": "DeadToons",
                    "poster": target_post.get("poster"),
                }
            ep_soup = BeautifulSoup(r_ep.text, "html.parser")
            media_url = None
            for a in ep_soup.find_all("a", href=True):
                h = a["href"]
                if any(ext in h.lower() for ext in (".mp4", ".mkv", ".webm", ".m3u8")):
                    media_url = h
                    break
            if not media_url:
                for fr in ep_soup.find_all(["iframe", "source"], src=True):
                    src = fr["src"]
                    if any(ext in src.lower() for ext in (".mp4", ".mkv", ".webm", ".m3u8")):
                        media_url = src
                        break
            if not media_url:
                log.warning("DeadToons: no extractable media link on episode page %s", ep_url)
                return None
            if not media_url.startswith("http"):
                media_url = f"{self._base_url}{media_url}"
            return {
                "url": media_url,
                "quality": "Unknown",
                "requested_quality": quality_pref,
                "detected_quality": "Unknown",
                "verified_quality": "Unknown",
                "source": "DeadToons",
                "poster": target_post.get("poster"),
            }
        except Exception as e:
            log.warning("DeadToons resolve error for %s: %s", post_url, e)

        return None


deadtoons = DeadToonsExtractor()
