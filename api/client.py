"""High-level async API client for AnimeDekho."""

from __future__ import annotations
import json
import logging
import re
from urllib.parse import quote

from config.settings import settings
from utils.http import http_client
from .models import (
    SearchResult, Series, Movie, Episode, Season, Category,
    PaginatedResult, VideoServer,
)
from .parser import (
    parse_nonce, parse_search_html, parse_listing_page,
    parse_series_detail, parse_episode_page, parse_movie_page,
    parse_categories, parse_categories_html,
    RE_SERIES_URL, RE_MOVIE_URL,
)

log = logging.getLogger(__name__)
cfg = settings.site


class AnimeDekhoAPI:
    """All site interactions go through here."""

    def __init__(self):
        self._nonce: str | None = None

    # ── Nonce management ──────────────────────────────────────────

    async def _ensure_nonce(self, force_refresh: bool = False) -> str:
        if self._nonce and not force_refresh:
            return self._nonce
        self._nonce = None
        urls_to_try = [
            f"{cfg.base_url}/home/",
            f"{cfg.base_url}{cfg.series_path}/",
            f"{cfg.base_url}{cfg.movies_path}/",
            f"{cfg.base_url}/",
        ]
        last_error = None
        for url in urls_to_try:
            try:
                if force_refresh:
                    html = await http_client.get_no_cache(url)
                else:
                    html = await http_client.get(url, ttl=300)
                nonce = parse_nonce(html)
                if nonce:
                    self._nonce = nonce
                    log.info("Nonce acquired from %s: %s...", url, self._nonce[:6])
                    return self._nonce
            except Exception as e:
                last_error = e
                log.warning("Nonce fetch failed for %s: %s", url, e)
                continue
        raise RuntimeError(f"Failed to extract nonce from site: {last_error}")

    def _invalidate_nonce(self):
        self._nonce = None

    # ── Search ────────────────────────────────────────────────────

    async def _search_animedekho(self, query: str, page: int = 1) -> list[SearchResult]:
        """Internal search against AnimeDekho AJAX and GET ?s= endpoints."""
        # Strategy 1: Modern AnimeDekho DGT REST API search endpoint (fast & reliable JSON)
        try:
            from urllib.parse import quote_plus
            queries_to_try = [query]
            clean_q = re.sub(
                r"(?i)\s*(?:season\s*\d+|s\d+|part\s*\d+|cour\s*\d+|hindi|dubbed|subbed|multi-audio|tamil|telugu).*$",
                "",
                query,
            ).strip()
            if clean_q and clean_q.lower() != query.lower():
                queries_to_try.append(clean_q)

            for q_term in queries_to_try:
                suggest_url = f"{cfg.base_url}/wp-json/dgt/v1/suggest?q={quote_plus(q_term)}"
                resp = await http_client.get(suggest_url, ttl=settings.cache.search_ttl)
                if resp:
                    data = json.loads(resp)
                    items = data.get("items", [])
                    if items:
                        results: list[SearchResult] = []
                        for it in items:
                            item_url = it.get("url", "")
                            title = it.get("title", "")
                            poster = it.get("poster", "")
                            c_type = "movie" if it.get("type") == "movie" else "series"
                            m = (RE_MOVIE_URL if c_type == "movie" else RE_SERIES_URL).search(item_url)
                            slug = m.group(1) if m else item_url.rstrip("/").split("/")[-1]
                            results.append(SearchResult(
                                title=title,
                                slug=slug,
                                url=item_url,
                                content_type=c_type,
                                poster=poster,
                                source="AnimeDekho",
                            ))
                        if results:
                            log.info("AnimeDekho DGT REST search returned %d result(s) for '%s' (term: '%s')",
                                     len(results), query, q_term)
                            return results
        except Exception as e:
            log.warning("AnimeDekho DGT REST search failed for '%s': %s", query, e)

        # Strategy 2: Legacy AJAX search with nonce
        for attempt in range(2):
            try:
                nonce = await self._ensure_nonce(force_refresh=(attempt > 0))
                vars_data = json.dumps({
                    "_wpsearch": nonce,
                    "search": query,
                    "page": page,
                })
                resp = await http_client.post(
                    cfg.ajax_url,
                    data={"action": "action_search", "vars": vars_data},
                    ttl=settings.cache.search_ttl if attempt == 0 else 0,
                )
                if resp:
                    try:
                        data = json.loads(resp)
                    except (json.JSONDecodeError, AttributeError):
                        data = None

                    if isinstance(data, dict):
                        if data.get("status") == 400 or "error" in data:
                            log.warning("AJAX search returned status 400 on attempt %d", attempt + 1)
                            self._invalidate_nonce()
                            continue
                        html = data.get("html", "")
                    else:
                        html = resp

                    if html:
                        results = parse_search_html(html)
                        if results:
                            return results
            except Exception as e:
                log.warning("AJAX search failed on attempt %d: %s", attempt + 1, e)
                self._invalidate_nonce()

        # Strategy 2: Direct WordPress GET search fallback (no nonce required!)
        log.info("Falling back to direct GET search for: %s", query)
        try:
            from urllib.parse import quote_plus
            if page > 1:
                fallback_url = f"{cfg.base_url}/page/{page}/?s={quote_plus(query)}"
            else:
                fallback_url = f"{cfg.base_url}/?s={quote_plus(query)}"
            html = await http_client.get(fallback_url, ttl=settings.cache.search_ttl)
            if html:
                results = parse_search_html(html)
                if results:
                    return results
        except Exception as e:
            log.warning("Direct GET search fallback failed for '%s': %s", query, e)

        return []

    async def search(self, query: str, page: int = 1) -> list[SearchResult]:
        """Search anime/movies prioritizing configured default source, with multi-source fallback."""
        query = query.strip()
        if not query:
            return []

        from bot.source_config import get_default_source, is_source
        default_src = await get_default_source()
        is_ad_default = is_source(default_src, "AnimeDekho")

        clean_q = re.sub(
            r"(?i)\s*(?:season\s*\d+|s\d+|part\s*\d+|cour\s*\d+|hindi|dubbed|subbed|multi-audio|tamil|telugu).*$",
            "",
            query,
        ).strip()
        has_clean = bool(clean_q and clean_q.lower() != query.lower())

        if is_ad_default:
            # 1. Primary search on AnimeDekho (default source)
            results = await self._search_animedekho(query, page=page)
            if results:
                return results

            # 2. Clean query search on AnimeDekho (strip Season/Dub suffixes)
            if has_clean:
                log.info("AnimeDekho 0 results for '%s'. Trying clean query: '%s'", query, clean_q)
                results = await self._search_animedekho(clean_q, page=page)
                if results:
                    return results

            # 3. Multi-source fallback search (AnimeDrive, ToonFlix, ToonWorld4All, RareAnimes, DeadToons, TOONo)
            log.info("AnimeDekho returned 0 results for '%s'. Querying multi-source fallback scrapers...", query)
            from extractors.multisource import multi_source_manager
            fallback_results = await multi_source_manager.search_fallback(query)
            if fallback_results:
                return fallback_results
            if has_clean:
                fallback_results = await multi_source_manager.search_fallback(clean_q)
                if fallback_results:
                    return fallback_results

            return []

        # Default source is an extractor (e.g. DeadToons, AnimeDubHindi, ToonWorld4All, etc.)
        from extractors.multisource import multi_source_manager
        log.info("Searching default source '%s' for '%s'...", default_src, query)
        results = await multi_source_manager.search_by_source(default_src, query)
        if results:
            return results

        if has_clean:
            log.info("Default source '%s' 0 results for '%s'. Trying clean query: '%s'", default_src, query, clean_q)
            results = await multi_source_manager.search_by_source(default_src, clean_q)
            if results:
                return results

        # Fallback 1: AnimeDekho
        log.info("Default source '%s' returned 0 results for '%s'. Falling back to AnimeDekho...", default_src, query)
        results = await self._search_animedekho(query, page=page)
        if results:
            return results
        if has_clean:
            results = await self._search_animedekho(clean_q, page=page)
            if results:
                return results

        # Fallback 2: Remaining multi-source scrapers
        log.info("AnimeDekho returned 0 results for '%s'. Querying remaining fallback scrapers...", query)
        fallback_results = await multi_source_manager.search_fallback(query, exclude_source=default_src)
        if fallback_results:
            return fallback_results
        if has_clean:
            fallback_results = await multi_source_manager.search_fallback(clean_q, exclude_source=default_src)
            if fallback_results:
                return fallback_results

        return []

    # ── Listings ──────────────────────────────────────────────────

    async def get_recent_series(self, page: int = 1) -> PaginatedResult:
        url = f"{cfg.base_url}{cfg.series_path}/"
        if page > 1:
            url = f"{cfg.base_url}{cfg.series_path}/page/{page}/"
        html = await http_client.get(url, ttl=settings.cache.listing_ttl)
        result = parse_listing_page(html, RE_SERIES_URL, "series")
        result.current_page = page
        return result

    async def get_recent_movies(self, page: int = 1) -> PaginatedResult:
        url = f"{cfg.base_url}{cfg.movies_path}/"
        if page > 1:
            url = f"{cfg.base_url}{cfg.movies_path}/page/{page}/"
        html = await http_client.get(url, ttl=settings.cache.listing_ttl)
        result = parse_listing_page(html, RE_MOVIE_URL, "movie")
        result.current_page = page
        return result

    # ── Detail pages ──────────────────────────────────────────────

    async def _fetch_with_domain_fallback(self, path: str) -> str:
        """V2 #7-#10 + V3 #9: try live .tv domain first, fall back to legacy .app.

        Raises the last error if both fail so callers can use the
        multi-source fallback instead of showing a bare 403. A 403 is never
        treated as success — it is logged distinctly with full diagnostics.
        """
        urls = [f"{cfg.base_url}{path}"]
        fallback_base = getattr(cfg, "fallback_base_url", "")
        if fallback_base and fallback_base != cfg.base_url:
            urls.append(f"{fallback_base}{path}")
        last_err: Exception | None = None
        for u in urls:
            try:
                return await http_client.get(u)
            except Exception as e:
                last_err = e
                msg = str(e)
                is_403 = "403" in msg or "forbidden" in msg.lower()
                # V3 #9: exact failure diagnostics — 403 must surface, never
                # silently succeed. Direct sources remain first; this raise
                # lets the caller fall back to MultiSource/AnimeDrive/ToonFlix.
                log.warning("AnimeDekho fetch %s for %s (%s), trying next domain...",
                            "403 Forbidden" if is_403 else "failed", u, e)
                continue
        raise last_err or RuntimeError(f"AnimeDekho fetch failed for {path}")

    async def get_series(self, slug: str) -> Series:
        url_path = f"{cfg.series_path}/{slug}/"
        try:
            html = await self._fetch_with_domain_fallback(url_path)
            series = parse_series_detail(html, slug)
        except Exception as e:
            # V2 #7-#10: log 403 distinctly, then retry base slug on both domains.
            log.warning("get_series 403/fetch failed for '%s' (%s)", slug, e)
            clean_s = re.sub(r'-(?:season-\d+|s\d+|part-\d+|cour-\d+|hindi|dubbed|subbed)$', '', slug)
            if clean_s != slug:
                log.info("get_series failed for '%s', retrying base slug '%s'", slug, clean_s)
                html = await self._fetch_with_domain_fallback(f"{cfg.series_path}/{clean_s}/")
                series = parse_series_detail(html, clean_s)
            else:
                raise e

        # If DGT series ID and season tabs are present, fetch dynamic episodes for all seasons
        if series.dgt_series_id and series.dgt_seasons:
            try:
                import asyncio
                async def _fetch_dgt_season(s_num: int):
                    ep_url = f"{cfg.base_url}/wp-json/dgt/v1/episodes?series={series.dgt_series_id}&season={s_num}"
                    try:
                        raw = await http_client.get(ep_url, ttl=settings.cache.listing_ttl)
                        if raw:
                            data = json.loads(raw)
                            return s_num, data.get("episodes", [])
                    except Exception as ex:
                        log.warning("Failed to fetch DGT episodes for series %s S%s: %s", series.slug, s_num, ex)
                    return s_num, []

                season_results = await asyncio.gather(
                    *[_fetch_dgt_season(s_num) for s_num in series.dgt_seasons],
                    return_exceptions=True
                )

                has_dgt_episodes = False
                for item in season_results:
                    if isinstance(item, tuple):
                        s_num, ep_list = item
                        if ep_list:
                            has_dgt_episodes = True
                            dgt_season = Season(number=s_num)
                            for it in ep_list:
                                ep_num = int(it.get("episode") or 0)
                                ep_page_url = it.get("url", "")
                                m_slug = re.search(r"/epi/([^/?#]+)/?", ep_page_url)
                                it_slug = m_slug.group(1) if m_slug else f"{series.slug}-{s_num}x{ep_num}"
                                ep_title = it.get("name") or it.get("label") or f"Episode {ep_num}"
                                dgt_season.episodes.append(Episode(
                                    number=ep_num,
                                    slug=it_slug,
                                    season=s_num,
                                    title=ep_title,
                                    page_url=ep_page_url,
                                    post_id=int(it.get("id") or 0),
                                    dgt_post_id=series.dgt_series_id,
                                    dgt_episode_id=int(it.get("id") or 0),
                                ))
                            dgt_season.episodes.sort(key=lambda x: x.number)
                            series.seasons[s_num] = dgt_season

                if has_dgt_episodes:
                    log.info("Populated %d season(s) with %d episodes from DGT REST for series '%s'",
                             len(series.seasons), series.total_episodes, series.slug)
            except Exception as e:
                log.warning("DGT REST season fetch error for series %s: %s", series.slug, e)

        try:
            from utils.anilist import resolve_best_poster
            best = await resolve_best_poster(series.title, series.poster, is_movie=False)
            if best:
                series.poster = best
        except Exception as e:
            log.debug("AniList poster resolution error for series %s: %s", slug, e)
        return series

    async def get_episode(self, ep_slug: str) -> Episode:
        url_path = f"{cfg.episode_path}/{ep_slug}/"
        url = f"{cfg.base_url}{url_path}"
        try:
            html = await self._fetch_with_domain_fallback(url_path)
        except Exception as e:
            log.warning("get_episode fetch failed for '%s' (%s)", ep_slug, e)
            raise

        # The site requires visiting a verification shortlink before
        # server data is shown. Extract and visit it, then re-fetch.
        shortlink_match = re.search(
            r'name=["\']shortlink["\'][^>]*value=["\']([^"\']+)["\']',
            html,
        )
        if shortlink_match:
            shortlink = shortlink_match.group(1)
            log.debug("Visiting verification shortlink: %s", shortlink)
            try:
                # Visit the shortlink — this sets the verification cookie
                await http_client.get_no_cache(shortlink)
                # Re-fetch the episode page — servers are now visible
                html = await http_client.get_no_cache(url)
            except Exception as e:
                log.warning("Verification shortlink failed: %s", e)

        episode = parse_episode_page(html, ep_slug)

        # DGT Watch API for dynamic servers
        dgt_pid = episode.dgt_post_id or episode.post_id
        if dgt_pid and episode.dgt_episode_id:
            try:
                watch_url = f"{cfg.base_url}/wp-json/dgt/v1/watch"
                post_body = json.dumps({
                    "post_id": dgt_pid,
                    "episode_id": episode.dgt_episode_id,
                })
                watch_resp = await http_client.post(
                    watch_url,
                    data=post_body,
                    headers={"Content-Type": "application/json"},
                )
                if watch_resp:
                    watch_data = json.loads(watch_resp)
                    dgt_sources = watch_data.get("sources", [])
                    dgt_servers: list[VideoServer] = []
                    for idx, src in enumerate(dgt_sources):
                        server_name = src.get("server") or f"Server {idx+1}"
                        embed_url = src.get("embed", "")
                        dl_url = src.get("download", "")
                        sid = int(src.get("id")) if str(src.get("id")).isdigit() else idx
                        if embed_url:
                            dgt_servers.append(VideoServer(
                                name=server_name,
                                server_id=sid,
                                proxy_url=embed_url,
                            ))
                        elif dl_url:
                            dgt_servers.append(VideoServer(
                                name=f"{server_name} (DL)",
                                server_id=sid,
                                proxy_url=dl_url,
                            ))
                    if dgt_servers:
                        episode.servers = dgt_servers
                        log.info("DGT Watch API returned %d server(s) for episode '%s'", len(dgt_servers), ep_slug)
            except Exception as e:
                log.warning("DGT Watch API failed for episode '%s': %s", ep_slug, e)

        return episode

    async def get_movie(self, slug: str) -> Movie:
        url_path = f"{cfg.movies_path}/{slug}/"
        url = f"{cfg.base_url}{url_path}"
        try:
            html = await self._fetch_with_domain_fallback(url_path)
        except Exception as e:
            log.warning("get_movie fetch failed for '%s' (%s)", slug, e)
            raise

        # Same verification shortlink flow as episodes
        shortlink_match = re.search(
            r'name=["\']shortlink["\'][^>]*value=["\']([^"\']+)["\']',
            html,
        )
        if shortlink_match:
            shortlink = shortlink_match.group(1)
            log.debug("Visiting verification shortlink: %s", shortlink)
            try:
                await http_client.get_no_cache(shortlink)
                html = await http_client.get_no_cache(url)
            except Exception as e:
                log.warning("Verification shortlink failed: %s", e)

        movie = parse_movie_page(html, slug)

        # DGT Watch API for dynamic movie servers
        movie_post_id = movie.dgt_post_id or movie.post_id
        if movie_post_id:
            try:
                watch_url = f"{cfg.base_url}/wp-json/dgt/v1/watch"
                post_body = json.dumps({
                    "post_id": movie_post_id,
                    "episode_id": 0,
                })
                watch_resp = await http_client.post(
                    watch_url,
                    data=post_body,
                    headers={"Content-Type": "application/json"},
                )
                if watch_resp:
                    watch_data = json.loads(watch_resp)
                    dgt_sources = watch_data.get("sources", [])
                    dgt_servers: list[VideoServer] = []
                    for idx, src in enumerate(dgt_sources):
                        server_name = src.get("server") or f"Server {idx+1}"
                        embed_url = src.get("embed", "")
                        dl_url = src.get("download", "")
                        sid = int(src.get("id")) if str(src.get("id")).isdigit() else idx
                        if embed_url:
                            dgt_servers.append(VideoServer(
                                name=server_name,
                                server_id=sid,
                                proxy_url=embed_url,
                            ))
                        elif dl_url:
                            dgt_servers.append(VideoServer(
                                name=f"{server_name} (DL)",
                                server_id=sid,
                                proxy_url=dl_url,
                            ))
                    if dgt_servers:
                        movie.servers = dgt_servers
                        log.info("DGT Watch API returned %d server(s) for movie '%s'", len(dgt_servers), slug)
            except Exception as e:
                log.warning("DGT Watch API failed for movie '%s': %s", slug, e)

        try:
            from utils.anilist import resolve_best_poster
            best = await resolve_best_poster(movie.title, movie.poster, is_movie=True)
            if best:
                movie.poster = best
        except Exception as e:
            log.debug("AniList poster resolution error for movie %s: %s", slug, e)
        return movie

    # ── Video server resolution ───────────────────────────────────

    async def resolve_server(self, server: VideoServer) -> VideoServer:
        """
        Fetch proxy URL, bypass shorteners if needed, and extract the
        real player/stream URL.

        Flow:
          1. GET the proxy_url (AnimeDekho's ``?trdekho=`` redirect page).
          2. Look for an ``<iframe src="...">`` pointing to the player.
          3. If the iframe src (or the page itself) goes through a link
             shortener, bypass it to get the real player embed.
          4. Check for direct m3u8/mp4 links in the page source.
        """
        from extractors.shortener import detect_and_bypass, is_shortener

        if not server.proxy_url:
            return server
        try:
            # Step 1: Fetch the proxy page (may be a redirect itself)
            final_url, html = await http_client.get_with_redirects(
                server.proxy_url,
                headers={"Referer": cfg.base_url + "/"},
            )

            # Step 2: If the whole page landed on a shortener, bypass it
            if is_shortener(final_url):
                bypassed = await detect_and_bypass(final_url)
                if bypassed and bypassed != final_url:
                    final_url, html = await http_client.get_with_redirects(bypassed)

            # Step 3: Look for <iframe src="..."> in the page
            iframe_match = re.search(r'<iframe[^>]*src\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
            if iframe_match:
                iframe_src = iframe_match.group(1)
                # Skip shortener bypass for known player/CDN domains
                _player_domains = ("as-cdn", "fireplayer", "vidmoly", "streamwish",
                                   "swish", "playerwish", "filemoon", "kerapoxy",
                                   "doodstream", "dood.", "streamtape", "strtape",
                                   "mp4upload", "vidguard", "vgfplay", "vimeo",
                                   "megacloud", "rabbitstream", "vidstream",
                                   "zephyrflick")
                iframe_lower = iframe_src.lower()
                if not any(d in iframe_lower for d in _player_domains):
                    iframe_src = await detect_and_bypass(iframe_src)
                server.player_url = iframe_src
            else:
                # Step 4: Check for shortener links in the page body
                # (some pages don't use iframes but have direct links)
                link_match = re.search(
                    r'href\s*=\s*["\']?(https?://[^\s"\'<>]+)["\']?',
                    html,
                )
                if link_match and is_shortener(link_match.group(1)):
                    bypassed = await detect_and_bypass(link_match.group(1))
                    if bypassed:
                        server.player_url = bypassed

            # Step 5: Also scan for direct video URLs in case the page
            # already contains the stream without needing a player
            if not server.player_url:
                m2 = re.search(
                    r'(?:src|url|file)\s*[=:]\s*["\']?(https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)',
                    html,
                )
                if m2:
                    server.direct_url = m2.group(1)
                    server.video_type = "m3u8" if ".m3u8" in m2.group(1) else "mp4"

        except Exception as e:
            log.warning("Failed to resolve server %s: %s", server.name, e)
        return server

    async def resolve_all_servers(self, servers: list[VideoServer]) -> list[VideoServer]:
        """Resolve all servers, return only available ones.
        
        Servers are sorted by priority (VidStream → HydraX → Vidmoly)
        so the preferred server appears first in the result list.
        """
        import asyncio
        from config.settings import settings
        preferred = settings.site.preferred_servers  # ["VidStream", "HydraX", "Vidmoly"]

        def _server_priority(srv):
            name_lower = srv.name.lower()
            for i, pref in enumerate(preferred):
                if pref.lower() in name_lower:
                    return i
            return len(preferred)

        # Sort by priority before resolving
        sorted_servers = sorted(servers, key=_server_priority)

        tasks = [self.resolve_server(s) for s in sorted_servers]
        resolved = await asyncio.gather(*tasks, return_exceptions=True)
        available = [s for s in resolved if isinstance(s, VideoServer) and s.is_available]

        # Re-sort available servers by priority (gather preserves order, but be safe)
        available.sort(key=_server_priority)
        return available

    # ── Categories ────────────────────────────────────────────────

    async def get_categories(self) -> list[Category]:
        # Primary: WP REST API (fast, includes item counts)
        url = f"{cfg.category_api}?per_page=50&orderby=count&order=desc&_fields=id,name,slug,count"
        try:
            data = await http_client.get_json(url, ttl=settings.cache.categories_ttl)
            cats = parse_categories(data)
            if cats:
                return cats
        except Exception as e:
            # wp-json is often Cloudflare-blocked and returns HTML instead
            log.warning("Category API failed (%s), falling back to HTML scraping", e)

        # Fallback: scrape /category/<slug>/ links from the site pages
        for page_url in (f"{cfg.base_url}/", f"{cfg.base_url}{cfg.series_path}/"):
            try:
                html = await http_client.get(page_url, ttl=settings.cache.categories_ttl)
                cats = parse_categories_html(html)
                if cats:
                    return cats
            except Exception as e:
                log.warning("Category scrape failed for %s: %s", page_url, e)

        return []

    async def get_category_items(self, cat_slug: str, page: int = 1) -> PaginatedResult:
        url = f"{cfg.base_url}/category/{cat_slug}/"
        if page > 1:
            url = f"{cfg.base_url}/category/{cat_slug}/page/{page}/"
        html = await http_client.get(url, ttl=settings.cache.listing_ttl)
        # Category pages can have both series and movies
        result = parse_listing_page(html, RE_SERIES_URL, "series")
        result.current_page = page
        return result


# Singleton
api = AnimeDekhoAPI()
api_client = api
