"""DeadToons extractor — high-accuracy source for Hindi & Multi-Audio anime with cloud stream unlocks."""

from __future__ import annotations
import asyncio
import logging
import re
import time
from urllib.parse import quote_plus, urljoin, urlparse
from bs4 import BeautifulSoup
import cloudscraper
import requests

from utils.anilist import is_valid_poster_url
from utils.anime_match import is_confident_match, matches_season, normalize_quality, parse_size_mb, is_4k_satisfying

log = logging.getLogger(__name__)


def _get_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


class DeadToonsExtractor:
    """Extracts Multi-Audio & Hindi anime series from DeadToons (deadtoons.sbs & api.deadbase.host)."""

    def __init__(self):
        self._base_url = "https://deadtoons.sbs"
        self._api_base = "https://api.deadbase.host/api/v1/public"

    def _get_scraper(self) -> cloudscraper.CloudScraper:
        return _get_scraper()

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

    async def get_recent(self, page: int = 1) -> list[dict]:
        """Fetch recently released anime from DeadToons."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_get_recent, page)

    def _sync_get_recent(self, page: int = 1) -> list[dict]:
        s = _get_scraper()
        url = f"{self._base_url}/?page={page}" if page > 1 else self._base_url
        try:
            r = s.get(url, timeout=12)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            seen = set()
            for li in soup.select("li"):
                h = li.select_one("h2 a[href], h3 a[href]")
                if not h:
                    continue
                href = h.get("href", "")
                if "/posts/" not in href or href in seen:
                    continue
                seen.add(href)
                title = h.get_text(" ", strip=True)
                if not title or len(title) < 3:
                    continue
                img = li.find("img")
                poster = ""
                if img:
                    p_url = img.get("src") or img.get("data-src", "")
                    if is_valid_poster_url(p_url):
                        poster = p_url
                full_url = href if href.startswith("http") else f"{self._base_url}{href}"
                results.append({
                    "title": title,
                    "url": full_url,
                    "poster": poster,
                    "source": "DeadToons",
                })
            return results
        except Exception as e:
            log.warning("DeadToons get_recent failed (page %d): %s", page, e)
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
        # Score candidates: exact title-token overlap wins over spin-offs
        # (e.g. "One Piece" must beat "LEGO One Piece"), and explicit
        # season matches beat movie/spin-off posts.
        from utils.anime_match import normalize_tokens
        q_toks = normalize_tokens(anime_title)
        candidates = []
        for res in search_results:
            t = res.get("title", "")
            href = res.get("url", "")
            if not is_confident_match(anime_title, t, href):
                continue
            if not matches_season(t, href, season):
                continue

            is_explicit_season = (
                f"season {season}" in t.lower()
                or f"season {season:02d}" in t.lower()
                or f"s{season}" in t.lower()
                or f"s{season:02d}" in t.lower()
            )
            is_movie_post = any(k in t.lower() for k in ("movie", "reawakening", "hdcam", "theater", "the movie"))

            # Title-similarity: shared significant tokens minus extra
            # distinguishing tokens in the candidate ("lego", "wano", ...).
            c_toks = normalize_tokens(f"{t} {href}")
            overlap = len(q_toks & c_toks) if q_toks else 0
            extra = len(c_toks - q_toks) if q_toks else 0

            score = overlap * 10 - extra
            if is_explicit_season:
                score += 100
            elif season == 1:
                score += 20

            if is_movie_post:
                score -= 50

            candidates.append((score, res))

        if not candidates:
            log.info("DeadToons: No verified season %d post for '%s'", season, anime_title)
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)

        # Try each candidate in score order: the top hit may be a
        # lookalike whose API slug 404s while the exact match succeeds.
        for _, target_post in candidates:
            post_url = target_post["url"]
            try:
                r = s.get(post_url, timeout=12)
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")

                # Try modern DeadToons API resolution first
                api_res = self._resolve_via_api(s, soup, target_post, season, episode, quality_pref)
                if api_res:
                    return api_res

                # Fallback legacy page scraping (for backward compatibility & offline mock tests)
                pattern = re.compile(rf"/{season}x0*{episode}\b", re.I)
                ep_href = None
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if pattern.search(href) or f"episode/{season}x{episode}" in href.lower():
                        ep_href = href
                        break

                if not ep_href:
                    continue

                ep_url = ep_href if ep_href.startswith("http") else f"{self._base_url}{ep_href}"
                r_ep = s.get(ep_url, timeout=12)
                if r_ep.status_code != 200:
                    log.warning("DeadToons: episode page %s returned HTTP %d", ep_url, r_ep.status_code)
                    continue
                ep_ctype = (r_ep.headers.get("Content-Type") or "").lower()
                if "text/html" not in ep_ctype:
                    # Served media directly instead of a page.
                    norm_q = normalize_quality(quality_pref) or "Unknown"
                    return {
                        "url": ep_url,
                        "quality": norm_q,
                        "requested_quality": quality_pref,
                        "detected_quality": norm_q,
                        "verified_quality": norm_q,
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
                    continue
                if not media_url.startswith("http"):
                    media_url = f"{self._base_url}{media_url}"
                norm_q = normalize_quality(quality_pref) or "Unknown"
                return {
                    "url": media_url,
                    "quality": norm_q,
                    "requested_quality": quality_pref,
                    "detected_quality": norm_q,
                    "verified_quality": norm_q,
                    "source": "DeadToons",
                    "poster": target_post.get("poster"),
                }
            except Exception as e:
                log.warning("DeadToons resolve error for %s: %s", post_url, e)
                continue

        return None

    def _resolve_via_api(
        self,
        s: cloudscraper.CloudScraper,
        soup: BeautifulSoup,
        target_post: dict,
        season: int,
        episode: int,
        quality_pref: str,
    ) -> dict | None:
        """Resolve episode or movie through the DeadToons public API with fast shortlink unlock."""
        try:
            slug = None
            is_movie = False

            # 0. Check if target_post URL itself is an episode link: /episode/<slug>/<season>x<episode>
            post_url = target_post.get("url", "")
            m_ep_url = re.search(r"/episode/([^/?#]+)(?:/(\d+)x(\d+))?", post_url)
            if m_ep_url:
                slug = m_ep_url.group(1)
                if m_ep_url.group(2) and (not season or season == 1):
                    try:
                        season = int(m_ep_url.group(2))
                    except Exception:
                        pass
                if m_ep_url.group(3) and (not episode or episode == 1):
                    try:
                        episode = int(m_ep_url.group(3))
                    except Exception:
                        pass

            m_mov_url = re.search(r"/movie/([^/?#]+)", post_url)
            if not slug and m_mov_url:
                slug = m_mov_url.group(1)
                is_movie = True

            # Check title tag in soup (e.g. <title>tomb-raider-king · 1x1 | Deadtoons</title>)
            if not slug and soup and soup.title:
                m_title = re.search(r"([^·\s]+)\s*·\s*(\d+)x(\d+)", soup.title.get_text())
                if m_title:
                    slug = m_title.group(1).strip()
                    if not season or season == 1:
                        try:
                            season = int(m_title.group(2))
                        except Exception:
                            pass
                    if not episode or episode == 1:
                        try:
                            episode = int(m_title.group(3))
                        except Exception:
                            pass

            # 1. Search post HTML for archive episode link: /episode/{slug}/{season}x{episode}
            if not slug:
                ep_pattern = re.compile(rf"/episode/([^/?#]+)/{season}x0*{episode}\b", re.I)
                for a in soup.find_all("a", href=True):
                    h = a["href"]
                    m = ep_pattern.search(h)
                    if m:
                        slug = m.group(1)
                        break


            # 2. Check for movie link: /movie/{slug}
            if not slug and (season == 1 and episode == 1):
                movie_pattern = re.compile(r"/movie/([^/?#]+)", re.I)
                for a in soup.find_all("a", href=True):
                    h = a["href"]
                    m = movie_pattern.search(h)
                    if m:
                        slug = m.group(1)
                        is_movie = True
                        break

            # 3. If slug not matched from exact episode link, extract from any /episode/ link on post
            if not slug:
                for a in soup.find_all("a", href=True):
                    h = a["href"]
                    m = re.search(r"/episode/([^/?#]+)/\d+x\d+", h)
                    if m:
                        slug = m.group(1)
                        break

            if not slug:
                return None

            # 4. Fetch JSON data from public API
            if is_movie:
                api_url = f"{self._api_base}/anime/{slug}"
            else:
                api_url = f"{self._api_base}/anime/{slug}/season/{season}/episode/{episode}"

            resp = s.get(api_url, timeout=12)
            if resp.status_code != 200:
                log.debug("DeadToons API %s returned %d", api_url, resp.status_code)
                return None

            data = resp.json()
            links = data.get("links", [])
            if not links:
                log.debug("DeadToons API %s has no links", api_url)
                return None

            # 5. Quality selection
            chosen_link = self._select_best_quality_link(links, quality_pref)
            if not chosen_link:
                log.debug("DeadToons: No suitable quality found matching '%s'", quality_pref)
                return None

            servers = chosen_link.get("servers", [])
            if not servers:
                return None

            # 6. Server prioritization: HubCloud -> Pixeldrain -> FilePress (skip Telegram bot)
            def server_weight(srv: dict) -> int:
                name = (srv.get("name") or "").lower()
                if "hubcloud" in name:
                    return 0
                if "pixeldrain" in name:
                    return 1
                if "filepress" in name or "fpgo" in name:
                    return 2
                if "gdflix" in name:
                    return 3
                return 10

            viable_servers = [srv for srv in servers if (srv.get("name") or "").lower() != "telegram"]
            viable_servers.sort(key=server_weight)

            from extractors.animedrive import animedrive

            # 7. Unlock server link
            for srv in viable_servers:
                lsid = srv.get("link_server_id")
                if not lsid:
                    continue

                unlocked_url = self._unlock_server(s, lsid)
                if not unlocked_url:
                    continue

                media_url = None
                low_unlocked = unlocked_url.lower()

                # If HubCloud, resolve to direct Google CDN stream
                if "hubcloud" in low_unlocked or "gamerxyt" in low_unlocked:
                    media_url = animedrive._resolve_hubcloud(s, unlocked_url)
                elif "pixeldrain.com/u/" in low_unlocked:
                    fid = unlocked_url.split("/u/")[-1].split("?")[0].strip("/")
                    media_url = f"https://pixeldrain.com/api/file/{fid}"
                else:
                    media_url = unlocked_url

                if not media_url:
                    continue

                raw_q = chosen_link.get("quality", "")
                norm_q = self._normalize_dt_quality(raw_q) or self._normalize_dt_quality(quality_pref) or "Unknown"

                # Poster from API or target post
                poster_url = target_post.get("poster")
                if not poster_url and isinstance(data.get("img"), dict):
                    poster_url = data["img"].get("high") or data["img"].get("mid") or data["img"].get("low")
                elif not poster_url and isinstance(data.get("poster"), dict):
                    poster_url = data["poster"].get("high") or data["poster"].get("mid")

                return {
                    "url": media_url,
                    "quality": norm_q,
                    "requested_quality": quality_pref,
                    "detected_quality": norm_q,
                    "verified_quality": norm_q,
                    "source": "DeadToons",
                    "poster": poster_url,
                    "size": chosen_link.get("size", ""),
                    "title": data.get("episode_name") or target_post.get("title", ""),
                    "server": f"DeadToons ({srv.get('name', 'Cloud')})",
                }

        except Exception as e:
            log.warning("DeadToons API resolution failed: %s", e)

        return None

    @staticmethod
    def _normalize_dt_quality(q: str) -> str:
        s = (q or "").lower()
        if any(x in s for x in ("4k", "2160", "uhd")):
            return "4K"
        if "1080" in s:
            return "1080p"
        if "720" in s:
            return "720p"
        if "480" in s:
            return "480p"
        if "360" in s:
            return "360p"
        return normalize_quality(q) or "Unknown"

    @staticmethod
    def _is_720p_x265_10bit(raw_q: str) -> bool:
        """True if string represents 720p x265 / HEVC 10-bit."""
        s = re.sub(r"[\s_\-.]+", "", str(raw_q or "")).lower()
        return "720" in s and ("x265" in s or "hevc" in s) and "10bit" in s

    @staticmethod
    def _is_720p_x265(raw_q: str) -> bool:
        """True if string represents 720p x265 / HEVC."""
        s = re.sub(r"[\s_\-.]+", "", str(raw_q or "")).lower()
        return "720" in s and ("x265" in s or "hevc" in s)

    def _select_best_quality_link(self, links: list[dict], quality_pref: str) -> dict | None:
        """Select quality link matching quality_pref with 4K size eligibility logic.

        DEADTOONS RULE: When 720p is requested or evaluated, ALWAYS choose 720p x265 10bit
        over 720p x264.
        """
        want_norm = self._normalize_dt_quality(quality_pref)
        is_4k_req = want_norm == "4K"
        is_720_req = want_norm == "720p" or "720" in (quality_pref or "").lower()

        # If 720p is requested, check if 720p x265 10bit exists and choose it with top priority
        if is_720_req:
            # 1. Absolute first choice: 720p x265 10bit
            for l in links:
                raw_q = l.get("quality", "")
                if self._is_720p_x265_10bit(raw_q):
                    return l

            # 2. Second choice: 720p x265 (HEVC)
            for l in links:
                raw_q = l.get("quality", "")
                if self._is_720p_x265(raw_q):
                    return l

            # 3. Third choice: any 720p 10bit
            for l in links:
                raw_q = l.get("quality", "")
                raw_low = raw_q.lower()
                if "720" in raw_low and "10bit" in raw_low:
                    return l

            # 4. Fallback: standard 720p (e.g. 720p x264)
            for l in links:
                raw_q = l.get("quality", "")
                if self._normalize_dt_quality(raw_q) == "720p":
                    return l

        scored: list[tuple[int, dict]] = []
        for l in links:
            raw_q = l.get("quality", "")
            raw_low = raw_q.lower()
            size_str = l.get("size", "")
            size_mb = parse_size_mb(size_str)
            norm_q = self._normalize_dt_quality(raw_q)

            is_x265 = "x265" in raw_low or "hevc" in raw_low
            is_10bit = "10bit" in raw_low or "10b" in raw_low

            if is_4k_req:
                if is_4k_satisfying(raw_q, size_mb):
                    # Prefer native 4K, then high bitrate 1080p HQ x265
                    score = 100 if norm_q == "4K" else 80
                    scored.append((score, l))
            else:
                if want_norm == "auto":
                    # In auto mode, prioritize high efficiency 1080p x265, then 720p x265 10bit
                    if norm_q == "1080p":
                        score = 150 if is_x265 else 120
                    elif norm_q == "720p":
                        score = 110 if (is_x265 and is_10bit) else (90 if is_x265 else 60)
                    elif norm_q == "480p":
                        score = 40
                    else:
                        score = 30
                    scored.append((score, l))
                elif norm_q == want_norm:
                    score = 100
                    if is_x265 and is_10bit:
                        score = 140
                    elif is_x265:
                        score = 120
                    elif is_10bit:
                        score = 110
                    scored.append((score, l))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]

        # If strict match didn't yield and request wasn't 4K, return first available link,
        # but prefer x265 / 10bit over x264
        if not is_4k_req and links:
            def fallback_key(link_item):
                rq = (link_item.get("quality", "") or "").lower()
                if "720" in rq and ("x265" in rq or "hevc" in rq) and "10bit" in rq:
                    return 0
                if "1080" in rq and "x265" in rq:
                    return 1
                if "x265" in rq:
                    return 2
                return 10
            sorted_links = sorted(links, key=fallback_key)
            return sorted_links[0]

        return None


    def _unlock_server(self, s: cloudscraper.CloudScraper, link_server_id: int) -> str | None:
        """Unlock a DeadToons server mirror via fast shortener bypass (Shrinkme / MrProBlogger)."""
        try:
            # 1. Check if server link is already unlocked
            u = s.get(f"{self._api_base}/unlock/{link_server_id}", timeout=10)
            if u.status_code == 200:
                udata = u.json()
                if udata.get("unlocked"):
                    claim = s.post(f"{self._api_base}/unlock/{link_server_id}/claim", timeout=10)
                    if claim.status_code == 200:
                        return claim.json().get("url")

            # 2. Trigger shortener start (shortener_id: 1 is Shrinkme)
            st = s.post(
                f"{self._api_base}/unlock/{link_server_id}/start",
                json={"shortener_id": 1},
                timeout=12,
            )
            if st.status_code != 200:
                log.warning("DeadToons unlock start failed (status %d): %s", st.status_code, st.text)
                return None

            st_data = st.json()
            redirect_url = st_data.get("redirect_url")
            if not redirect_url:
                log.warning("DeadToons unlock start missing redirect_url: %s", st_data)
                return None

            # 3. Bypass shortener to obtain unlock callback URL
            callback_url = self._bypass_shrinkme_shortlink(redirect_url)
            if not callback_url:
                log.warning("DeadToons: failed to bypass shortener URL %s", redirect_url)
                return None

            token = urlparse(callback_url).path.split("/")[-1]
            if not token:
                log.warning("DeadToons: could not extract token from %s", callback_url)
                return None

            # 4. Exchange callback token with DeadToons backend for target URL
            cb = s.post(f"{self._api_base}/unlock/callback/{token}", timeout=15)
            if cb.status_code != 200:
                log.warning("DeadToons callback exchange failed (status %d): %s", cb.status_code, cb.text)
                return None

            dest_url = cb.json().get("url")
            if dest_url:
                log.info("DeadToons server %d successfully unlocked: %s", link_server_id, dest_url[:80])
                return dest_url

        except Exception as e:
            log.warning("DeadToons _unlock_server error for %d: %s", link_server_id, e)

        return None

    def _bypass_shrinkme_shortlink(self, short_url: str) -> str | None:
        """Fast HTTP bypass for Shrinkme / MrProBlogger without browser overhead."""
        alias = urlparse(short_url).path.strip("/")
        if not alias:
            return None

        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        try:
            mr_url = f"https://en.mrproblogger.com/{alias}"
            r = sess.get(mr_url, headers={"Referer": "https://themezon.net/"}, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            form = soup.select_one("form#go-link")

            # Fallback to ThemeZon hop if direct MrProBlogger form is missing
            if not form:
                try:
                    hop = sess.post(
                        "https://themezon.net/?redirect_to=random",
                        data={"newwpsafelink": alias},
                        headers={"Referer": "https://themezon.net/", "Origin": "https://themezon.net"},
                        timeout=12,
                        allow_redirects=False,
                    )
                    next_url = hop.headers.get("Location")
                    if next_url:
                        r = sess.get(mr_url, headers={"Referer": next_url}, timeout=15)
                        soup = BeautifulSoup(r.text, "html.parser")
                        form = soup.select_one("form#go-link")
                except Exception as he:
                    log.debug("ThemeZon hop attempt failed: %s", he)

            if not form:
                log.warning("DeadToons: Shrinkme go-link form not found for %s", alias)
                return None

            hidden = {inp.get("name"): inp.get("value", "") for inp in form.select("input[name]")}
            action = urljoin(r.url, form.get("action") or "/links/go")

            # Extract counter timer if available, default to 12s
            counter = 12
            m = re.search(r"counter_value[\"']?\s*:\s*[\"']?(\d+)", r.text)
            if m:
                try:
                    counter = max(4, min(int(m.group(1)), 15))
                except Exception:
                    pass

            time.sleep(counter)

            go_resp = sess.post(
                action,
                data=hidden,
                headers={
                    "Referer": r.url,
                    "Origin": f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                timeout=20,
            )

            if go_resp.status_code == 200:
                data = go_resp.json()
                cb_url = data.get("url")
                if cb_url:
                    return cb_url

        except Exception as e:
            log.warning("DeadToons _bypass_shrinkme_shortlink failed for %s: %s", short_url, e)

        return None


deadtoons = DeadToonsExtractor()
