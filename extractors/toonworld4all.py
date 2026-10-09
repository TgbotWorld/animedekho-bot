"""ToonWorld4All extractor — fallback source for high quality anime & multi-audio series."""

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


class ToonWorld4AllExtractor:
    """Extracts anime and cartoons from ToonWorld4All (toonworld4all.me)."""

    def __init__(self):
        self._base_url = "https://toonworld4all.me"

    async def search(self, query: str) -> list[dict]:
        """Search ToonWorld4All catalog."""
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
                a = art.find("a", href=True)
                title_el = art.find(["h2", "h3", "h4", "h1"])
                if not a or not title_el:
                    continue
                href = a["href"]
                if href in seen or "how-to-download" in href or "anime-shows-list" in href:
                    continue
                seen.add(href)
                title = title_el.get_text(strip=True)

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
                    "source": "ToonWorld4All",
                })

            return results
        except Exception as e:
            log.warning("ToonWorld4All search failed for '%s': %s", query, e)
            return []

    async def get_recent(self, page: int = 1) -> list[dict]:
        """Fetch recently released anime from ToonWorld4All."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_get_recent, page)

    def _sync_get_recent(self, page: int = 1) -> list[dict]:
        s = _get_scraper()
        url = f"{self._base_url}/page/{page}/" if page > 1 else self._base_url
        try:
            r = s.get(url, timeout=12)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            seen = set()
            for art in soup.find_all("article"):
                a = art.find("a", href=True)
                title_el = art.find(["h2", "h3", "h4", "h1"])
                if not a or not title_el:
                    continue
                href = a["href"]
                if href in seen or "how-to-download" in href or "anime-shows-list" in href:
                    continue
                seen.add(href)
                title = title_el.get_text(strip=True)
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
                    "source": "ToonWorld4All",
                })
            return results
        except Exception as e:
            log.warning("ToonWorld4All get_recent failed (page %d): %s", page, e)
            return []


    async def resolve_episode(
        self,
        anime_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
    ) -> dict | None:
        """Resolve episode stream/download link from ToonWorld4All."""
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
            log.info("ToonWorld4All: No verified season %d post for '%s'", season, anime_title)
            return None

        post_url = target_post["url"]
        try:
            r = s.get(post_url, timeout=12)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            content = soup.find("div", class_=lambda c: c and ("entry-content" in c or "post-content" in c))
            if not content:
                content = soup

            # Match episode in archive link or redirect link
            ep_patterns = [
                re.compile(rf"-{season}x0*{episode}\b", re.I),
                re.compile(rf"episode.*{season}x0*{episode}\b", re.I),
                re.compile(rf"(?:ep|episode)\s*0*{episode}\b", re.I),
            ]

            for a in content.find_all("a", href=True):
                href = a["href"]
                txt = a.get_text(" ", strip=True).lower()
                for pat in ep_patterns:
                    if pat.search(href) or pat.search(txt):
                        resolved_url = href
                        # Unpack archive redirect page if present
                        if "archive.toonworld4all" in href or "redirect" in href:
                            try:
                                from urllib.parse import urljoin
                                r_arch = s.get(href, headers={"Referer": post_url}, timeout=10)
                                if r_arch.status_code == 200:
                                    soup_arch = BeautifulSoup(r_arch.text, "html.parser")
                                    for a_arch in soup_arch.find_all("a", href=True):
                                        if "redirect" in a_arch["href"]:
                                            redir_full = urljoin(href, a_arch["href"])
                                            r_red = s.get(redir_full, headers={"Referer": href}, timeout=10)
                                            m_props = re.search(r'window\.__PROPS__\s*=\s*(\{.*?\});', r_red.text)
                                            if m_props:
                                                import json
                                                props = json.loads(m_props.group(1))
                                                link_info = props.get("link") or {}
                                                dom = link_info.get("domain", "")
                                                hid = link_info.get("hidden", "")
                                                if dom and hid:
                                                    root_dl = dom.rstrip("/") + "/" + hid
                                                    resolved_url = root_dl.replace("/video/", "/drive/")
                                                    log.info("ToonWorld4All: Extracted root download URL: %s", resolved_url)
                                                    break
                                                elif props.get("destination"):
                                                    resolved_url = props["destination"]
                                                    break
                            except Exception as arch_err:
                                log.warning("ToonWorld4All archive unpack note: %s", arch_err)

                        return {
                            "url": resolved_url,
                            "quality": "Unknown",
                            "requested_quality": quality_pref,
                            "detected_quality": "Unknown",
                            "verified_quality": "Unknown",
                            "source": "ToonWorld4All",
                            "poster": target_post.get("poster"),
                        }
        except Exception as e:
            log.warning("ToonWorld4All resolve error for %s: %s", post_url, e)

        return None


toonworld4all = ToonWorld4AllExtractor()
