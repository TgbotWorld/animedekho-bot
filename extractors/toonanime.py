"""ToonAnime extractor — fallback source for Hindi & Multi-Audio anime series & movies."""

from __future__ import annotations
import asyncio
import logging
import re
import time
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import cloudscraper

from utils.anilist import is_valid_poster_url
from extractors.resolver import resolve_player_url
from extractors.shortener import is_shortener, detect_and_bypass, is_valid_media_destination

log = logging.getLogger(__name__)


def _get_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


class ToonAnimeExtractor:
    """Extracts Multi-Audio & Hindi anime series from ToonAnime."""

    # Issue #34: every mirror currently answers with a domain-parking JS
    # challenge, and each probe cost 10s (challenge follow included) — 22s of
    # dead weight on every search. Remember a dead mirror for 10 minutes so
    # the *next* round skips it outright.
    _MIRROR_DEAD_TTL = 600.0

    def __init__(self):
        self._base_urls = [
            "https://toonanime.cc",
            "https://toonanime.biz",
            "https://toonanime.tv",
            "https://toonanimes.com",
        ]
        self._base_url = self._base_urls[0]
        self._mirror_dead: dict[str, float] = {}

    def _mark_mirror_dead(self, base: str) -> None:
        try:
            self._mirror_dead[base] = time.time() + self._MIRROR_DEAD_TTL
            log.info("ToonAnime %s marked dead for %d min", base, int(self._MIRROR_DEAD_TTL // 60))
        except Exception:
            pass

    async def search(self, query: str) -> list[dict]:
        """Search ToonAnime for anime series or movies."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_search, query)

    def _is_dead_challenge_page(self, text: str, final_url: str = "") -> bool:
        """Issue #27: all known ToonAnime mirrors currently return a JS
        challenge → domain-parking page (ww*.toonanime.*). Detect it fast so
        MultiSource moves on to live providers instead of hanging."""
        t = (text or "").lower()
        if "window.location.replace" in t and len(text or "") < 2000:
            return True
        parking_hints = ("ww547.", "ww80.", "?tkn=", "parking", "domain is parked")
        if any(h in (text or "") + final_url for h in parking_hints):
            return True
        # Parking template: dark 600px centered page titled exactly "Toonanime"
        if len(text or "") < 40000 and "<title>Toonanime</title>" in (text or "") and "max-width: 600px" in t:
            return True
        return False

    def _sync_search(self, query: str) -> list[dict]:
        clean = re.sub(
            r"(?i)\s*(season\s*\d+|s\d+|hindi|dubbed|multi-audio|tamil|telugu).*$",
            "",
            query,
        ).strip()
        search_query = clean or query

        now = time.time()

        def _probe(base: str) -> list[dict]:
            """One mirror → its results, or [] when it is parked/unreachable."""
            url = f"{base}/?s={quote_plus(search_query)}"
            try:
                ms = _get_scraper()
                r = ms.get(url, timeout=10)
                # Check for JS challenge redirect
                m = re.search(r"window\.location\.replace\('([^']+)'\)", r.text)
                if m:
                    try:
                        r = ms.get(m.group(1), headers={"Referer": url}, timeout=10)
                    except Exception as ce:
                        log.debug("ToonAnime challenge follow failed on %s: %s", base, ce)
                        self._mark_mirror_dead(base)
                        return []

                if r.status_code != 200 or len(r.text) < 1000:
                    return []
                if self._is_dead_challenge_page(r.text, str(getattr(r, "url", ""))):
                    log.info("ToonAnime %s is parked/challenged — skipping mirror", base)
                    self._mark_mirror_dead(base)
                    return []

                soup = BeautifulSoup(r.text, "html.parser")
                results = []
                seen = set()

                for art in soup.find_all(["article", "div"], class_=lambda c: c and any(k in str(c) for k in ("post", "item", "result", "anime", "film"))):
                    a = art.find("a", href=True)
                    if not a:
                        continue
                    href = a["href"]
                    if href in seen:
                        continue
                    seen.add(href)
                    full_url = href if href.startswith("http") else f"{base}{href}"

                    h = art.find(["h1", "h2", "h3", "h4", "h5", "header", "span"])
                    title = h.get_text(strip=True) if h else a.get_text(strip=True)
                    title = re.sub(r"\d{4}$", "", title).strip()
                    if not title or len(title) < 3 or title.lower() in ("watch series", "series", "movies", "menu", "home"):
                        continue

                    poster = ""
                    img = art.find("img")
                    if img:
                        p_url = img.get("src") or img.get("data-src", "")
                        if is_valid_poster_url(p_url):
                            poster = p_url

                    results.append({
                        "title": title,
                        "url": full_url,
                        "poster": poster,
                        "source": "ToonAnime",
                    })

                return results

            except Exception as e:
                log.debug("ToonAnime search attempt on %s failed: %s", base, e)
                return []

        live = [b for b in self._base_urls if self._mirror_dead.get(b, 0.0) <= now]
        if not live:
            # Every mirror is inside its dead window — record it so the source
            # bench can take this extractor out of the rotation completely
            # instead of re-probing a parked domain on every request.
            try:
                from extractors import reliability as _rel
                _rel.note_source_failure("ToonAnime", "all mirrors parked")
            except Exception:
                pass
            return []

        # Issue #34: probe the mirrors *together*. Taken one after another the
        # parked mirrors cost ~17s on the first round.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(live))) as pool:
            found = list(pool.map(_probe, live))
        for base, results in zip(live, found):
            if results:
                self._base_url = base
                return results

        return []

    async def resolve_episode(
        self,
        anime_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
    ) -> dict | None:
        """Resolve direct stream/download link from ToonAnime."""
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

        # V3 #5: confident match only — rank + require confident overlap
        # and strict season agreement (no first-result guess).
        from utils.anime_match import is_confident_match, matches_season

        def _score(title: str) -> int:
            q_toks = set(re.sub(r"[^a-z0-9 ]", " ", anime_title.lower()).split())
            t_toks = set(re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split())
            stop = {"season", "hindi", "dubbed", "multi", "audio", "the", "a", "an"}
            q_toks -= stop
            t_toks -= stop
            overlap = len(q_toks & t_toks)
            bonus = 2 if str(season) in (title or "") else 0
            return overlap * 10 + bonus - abs(len(t_toks) - len(q_toks))

        ranked = sorted(search_results, key=lambda r: _score(r.get("title", "")), reverse=True)
        # Reject clearly wrong anime (no token overlap at all).
        best = ranked[0]
        if _score(best.get("title", "")) <= 0 or not is_confident_match(
            anime_title, best.get("title", ""), best.get("url", "")
        ):
            log.info("ToonAnime: no confident series match for '%s' — rejecting", anime_title)
            return None
        if not matches_season(best.get("title", ""), best.get("url", ""), season):
            log.info("ToonAnime: season %d mismatch for '%s' — rejecting", season, anime_title)
            return None
        target_series = best
        series_url = target_series["url"]

        try:
            r = s.get(series_url, timeout=12)
            m = re.search(r"window\.location\.replace\('([^']+)'\)", r.text)
            if m:
                r = s.get(m.group(1), headers={"Referer": series_url}, timeout=10)

            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")

            # Look for episode matching season and episode
            ep_url = None
            pat = re.compile(rf"[-_]{season}x0*{episode}\b|episode[-_]0*{episode}\b|ep[-_]0*{episode}\b", re.I)
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if pat.search(href) or pat.search(a.get_text()):
                    ep_url = href if href.startswith("http") else f"{self._base_url}{href}"
                    break

            if not ep_url:
                # If single movie page, current page might be the watch page
                if "movie" in anime_title.lower() or "film" in anime_title.lower():
                    ep_url = series_url
                else:
                    return None

            r_ep = s.get(ep_url, timeout=12)
            if r_ep.status_code != 200:
                return None
            if self._is_dead_challenge_page(r_ep.text, str(getattr(r_ep, "url", ""))):
                log.info("ToonAnime episode page is parked/challenged — skipping")
                return None
            soup_ep = BeautifulSoup(r_ep.text, "html.parser")

            # V2 #16: quality-aware link selection (not first iframe/link).
            # Collect all candidates with surrounding text, score by requested
            # quality tokens, return the ACTUAL detected quality.
            def _detect_quality(url: str, label: str) -> str:
                blob = f"{url} {label}".lower()
                for q in ("2160p", "2160", "4k", "uhd"):
                    if q in blob:
                        return "4K"
                for q in ("1080p", "1080"):
                    if q in blob:
                        return "1080p"
                for q in ("720p", "720"):
                    if q in blob:
                        return "720p"
                for q in ("480p", "480"):
                    if q in blob:
                        return "480p"
                for q in ("360p", "360"):
                    if q in blob:
                        return "360p"
                return ""

            # V3 #2 + V3 #4: exact requested quality only; unknown stays
            # "Unknown" with explicit requested/detected/verified fields.
            # Non-exact candidates are discarded (next source, not fallback).
            from utils.anime_match import normalize_quality, qualities_match
            pref_norm = normalize_quality(quality_pref)
            scored: list[tuple[int, str, str]] = []

            for iframe in soup_ep.find_all("iframe"):
                src = (iframe.get("src") or "").strip()
                if not src or src.startswith("about:") or len(src) < 10:
                    continue
                parent_txt = (iframe.parent.get_text(" ", strip=True) if iframe.parent else "")[:200]
                det = _detect_quality(src, parent_txt) or "Unknown"
                if not qualities_match(quality_pref, det):
                    continue
                score = 10
                # Prefer http(s) embeds over relative/js stubs
                if src.startswith("http"):
                    score += 2
                scored.append((score, src, det))

            for a in soup_ep.find_all("a", href=True):
                href = (a["href"] or "").strip()
                if not href.startswith("http"):
                    continue
                low = href.lower()
                if not any(x in low for x in ("drive.google", "mega.nz", "mediafire", "streamwish", "hubcloud", ".mp4", ".mkv", ".m3u8")):
                    continue
                label = a.get_text(" ", strip=True)[:200]
                det = _detect_quality(href, label) or "Unknown"
                if not qualities_match(quality_pref, det):
                    continue
                score = 10
                # Direct files outrank generic embeds on ties
                if any(x in low for x in (".mp4", ".mkv", ".m3u8", "drive.google")):
                    score += 1
                scored.append((score, href, det))

            if not scored:
                log.info("ToonAnime: no exact %s link for '%s' S%dE%d — rejecting (V3 #2)", quality_pref, anime_title, season, episode)
                return None
            scored.sort(key=lambda t: t[0], reverse=True)
            best_score, best_url, best_q = scored[0]
            log.info("ToonAnime: %d exact candidate(s), picked %s [%s] score=%d for '%s' S%dE%d",
                     len(scored), best_url[:80], best_q, best_score, anime_title, season, episode)
            return {
                "url": best_url,
                "quality": best_q,
                "requested_quality": quality_pref,
                "detected_quality": best_q,
                "verified_quality": best_q,
                "source": "ToonAnime",
                "poster": target_series.get("poster"),
            }
        except Exception as e:
            log.warning("ToonAnime resolve error for '%s' S%dE%d: %s", anime_title, season, episode, e)

        return None


toonanime = ToonAnimeExtractor()
