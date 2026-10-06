"""Multi-Source Fallback Architecture — orchestrates primary and fallback scrapers."""

from __future__ import annotations
import asyncio
import logging
import re
from typing import TYPE_CHECKING

from api.models import SearchResult
from utils.helpers import clean_title, slug_to_title
from extractors.animedubhindi import animedubhindi
from extractors.animedrive import animedrive
from extractors.toonflix import toonflix
from extractors.rareanimes import rareanimes
from extractors.deadtoons import deadtoons
from extractors.toonworld4all import toonworld4all
from extractors.toono import toono
from extractors.toonanime import toonanime
from extractors.resolver import resolve_player_url
from extractors.shortener import is_shortener, detect_and_bypass, is_valid_media_destination

log = logging.getLogger(__name__)


class MultiSourceManager:
    """Manages primary, secondary, and fallback download/streaming sources."""

    def __init__(self):
        # Direct download/video sources prioritized first (Issue #22 & #23)
        self.sources = [
            ("AnimeDubHindi", animedubhindi),
            ("ToonWorld4All", toonworld4all),
            ("RareAnimes", rareanimes),
            ("DeadToons", deadtoons),
            ("TOONo", toono),
            ("ToonAnime", toonanime),
            ("AnimeDrive", animedrive),
            ("ToonFlix", toonflix),
        ]
        self._slug_registry: dict[str, dict] = {}

    def get_source_for_slug(self, slug: str) -> str | None:
        """V2 #11-#13: return the provider that produced this slug, if any.

        Lets callbacks preserve the user's selected-source context instead
        of re-routing everything through AnimeDekho.
        """
        if not slug:
            return None
        try:
            from utils.helpers import short_slug
            item = self._slug_registry.get(slug) or self._slug_registry.get(short_slug(slug))
            if item:
                return item.get("source")
        except Exception:
            pass
        return None

    async def search_fallback(self, query: str) -> list[SearchResult]:
        """Search fallback sources when primary AnimeDekho returns 0 results."""
        results: list[SearchResult] = []
        seen_slugs = set()

        for name, extractor in self.sources:
            try:
                raw_items = await extractor.search(query)
                if not raw_items:
                    continue

                for item in raw_items:
                    raw_title = item.get("title", "")
                    title = clean_title(raw_title) or raw_title
                    url = item.get("url", "")
                    poster = item.get("poster", "")

                    # Generate clean slug
                    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
                    if not slug or slug in seen_slugs:
                        continue
                    seen_slugs.add(slug)

                    is_movie = "movie" in title.lower() or "film" in title.lower()
                    self._slug_registry[slug] = {
                        "source": name,
                        "title": title,
                        "url": url,
                        "poster": poster,
                        "content_type": "movie" if is_movie else "series",
                    }
                    from utils.helpers import short_slug
                    self._slug_registry[short_slug(slug)] = self._slug_registry[slug]

                    results.append(SearchResult(
                        title=f"{title} [{name}]",
                        slug=slug,
                        url=url,
                        content_type="movie" if is_movie else "series",
                        poster=poster,
                        source=name,
                    ))

                if len(results) >= 10:
                    break
            except Exception as e:
                log.warning("Fallback search on %s failed for '%s': %s", name, query, e)

        return results

    async def get_fallback_series(self, slug: str):
        """Build a Series model for fallback sources.

        V2 #17: never fabricate a fake episode list. If the provider page
        yields a real episode count (via AniList) we use it as an *estimate*
        clearly labelled in the description; otherwise we return the series
        with empty seasons so the UI shows 'episode list unavailable' instead
        of a fake default-12 list.
        """
        from api.models import Series, Season, Episode
        from utils.helpers import short_slug

        item = self._slug_registry.get(slug) or self._slug_registry.get(short_slug(slug))
        title = item.get("title") if item else slug_to_title(slug)
        url = item.get("url", "") if item else ""
        poster = item.get("poster") if item else ""
        source = item.get("source", "MultiSource") if item else "MultiSource"

        total_eps: int | None = None
        genres = []
        try:
            from utils.anilist import get_anilist_metadata
            meta = await get_anilist_metadata(title)
            if meta:
                if meta.get("episodes"):
                    total_eps = min(int(meta["episodes"]), 48)
                if meta.get("cover"):
                    poster = poster or meta["cover"]
                if meta.get("genres"):
                    genres = meta["genres"]
        except Exception:
            pass

        if not total_eps:
            # No verified count — return shell with no fake episodes.
            return Series(
                title=title,
                slug=slug,
                url=url,
                description=(
                    f"Available via {source} network. "
                    f"Episode list unavailable — open the source page or use /bypass."
                ),
                poster=poster or None,
                genres=genres,
                seasons={},
                source=source,
            )

        episodes = [
            Episode(
                number=i,
                slug=f"{slug}-1x{i}",
                season=1,
                title=f"{title} S1E{i:02d}",
                servers=[],
            )
            for i in range(1, total_eps + 1)
        ]
        season = Season(number=1, episodes=episodes)

        return Series(
            title=title,
            slug=slug,
            url=url,
            description=f"Available via {source} network. (~{total_eps} eps estimated via AniList)",
            poster=poster or None,
            genres=genres,
            seasons={1: season},
            source=source,
        )

    async def _resolve_one_source(
        self, name: str, extractor, search_title: str, season: int,
        episode: int, quality_pref: str, want_norm: str,
    ) -> tuple[dict | None, str, str]:
        """Resolve + validate a single source. Returns (entry, diag, kind)
        with kind in {"exact", "unknown", "skip"}."""
        from utils.anime_match import normalize_quality
        from extractors.health_probe import infer_provider
        try:
            log.info("Trying fallback source '%s' for '%s' S%dE%d [%s]...", name, search_title, season, episode, quality_pref)
            res = await extractor.resolve_episode(
                anime_title=search_title,
                season=season,
                episode=episode,
                quality_pref=quality_pref,
            )
            if not res or not res.get("url"):
                return None, f"{name}: no result", "skip"

            # Issue #33: if this source's first pick is benched/TTL-parked,
            # swap in one of its own spare mirrors before giving up on it.
            try:
                from extractors import reliability as _rel
                _alts = [u for u in (res.get("alternates") or []) if u]
                if not _rel.is_available(res["url"]):
                    _swap = _rel.first_available(_alts)
                    if not _swap:
                        return None, f"{name}: host benched / URL in failure TTL", "skip"
                    _alts = [u for u in _alts if u != _swap] + [res["url"]]
                    log.info("Source '%s' primary URL parked — using spare mirror", name)
                    res = dict(res)
                    res["url"] = _swap
                    res["alternates"] = _alts
            except Exception as _be:
                log.debug("bench check skipped: %s", _be)

            # V3 #2: skip known non-exact qualities outright.
            got_q = res.get("quality", "Unknown") or "Unknown"
            got_norm = normalize_quality(got_q)
            if want_norm != "auto" and got_norm != "Unknown" and got_norm != want_norm:
                log.info("Source '%s' returned [%s] for [%s] request — skipping (V3 #2 exact-only)", name, got_q, quality_pref)
                return None, f"{name}: {got_q} ≠ {quality_pref} → skipped", "skip"

            curr_url = res["url"].strip()
            original_url = curr_url
            log.info("Source '%s' returned initial URL: %s", name, curr_url[:80])

            # Stage 1: Shortener / Redirect resolution
            if is_shortener(curr_url) or "redirect" in curr_url.lower() or "archive.toonworld4all" in curr_url.lower():
                bypassed = await detect_and_bypass(curr_url)
                if bypassed and bypassed != curr_url:
                    log.info("Bypassed intermediate redirect/shortener on %s: %s -> %s", name, curr_url[:60], bypassed[:60])
                    curr_url = bypassed
                elif is_shortener(curr_url):
                    log.warning("Shortener bypass failed for %s on %s; skipping source", curr_url[:60], name)
                    return None, f"{name}: shortener bypass failed", "skip"

            # Stage 2: HubCloud provider resolution
            if any(x in curr_url.lower() for x in ("hubcloud", "gamerxyt")):
                try:
                    loop = asyncio.get_running_loop()
                    hub_res = await loop.run_in_executor(
                        None, animedrive._resolve_hubcloud, animedrive._get_scraper(), curr_url
                    )
                    if hub_res and is_valid_media_destination(hub_res):
                        log.info("HubCloud resolved via animedrive helper: %s -> %s", curr_url[:60], hub_res[:60])
                        curr_url = hub_res
                    else:
                        log.warning("HubCloud resolution returned unplayable target: %s", hub_res)
                        return None, f"{name}: HubCloud unplayable", "skip"
                except Exception as he:
                    log.warning("HubCloud resolution failed for %s: %s", curr_url, he)
                    return None, f"{name}: HubCloud error", "skip"

            # Stage 3: Player / Embed resolution
            is_player = any(k in curr_url.lower() for k in (
                "embed", "player", "trembed", "trid", "streamwish", "playerwish",
                "filemoon", "kerapoxy", "vidstream", "rabbitstream", "megacloud",
                "vidsrc", "xerver.xyz", "turboviplay", "turbosplayer", "emturbovid",
                "doodstream", "dood.", "streamtape", "strtape", "mp4upload", "vidguard", "vgfplay"
            ))
            if is_player and not any(ext in curr_url.lower() for ext in (".m3u8", ".mp4", ".mkv", ".webm")):
                resolved = await resolve_player_url(curr_url)
                if resolved and resolved.get("url") and is_valid_media_destination(resolved["url"]):
                    log.info("Successfully resolved player embed via %s -> %s", name, resolved["url"][:80])
                    curr_url = resolved["url"]
                else:
                    log.warning("Player embed resolution failed or returned invalid media for %s on %s; rejecting embed page", curr_url[:60], name)
                    return None, f"{name}: player unresolvable", "skip"

            # Stage 4: Final Validation
            if not is_valid_media_destination(curr_url):
                log.warning("Candidate URL failed media destination validation (%s) on %s; rejecting", curr_url[:80], name)
                return None, f"{name}: media validation failed", "skip"

            entry = {
                "url": curr_url,
                "quality": got_q,
                "requested_quality": res.get("requested_quality", quality_pref),
                "detected_quality": res.get("detected_quality", got_q),
                "verified_quality": res.get("verified_quality", got_q),
                "source": name,
                "provider": infer_provider(curr_url),
                "original_url": original_url,
                "resolved_url": curr_url,
                "resolver_stage": f"{name} → {infer_provider(curr_url)} → Direct Media",
                "poster": res.get("poster"),
                # Referer matters for probes/downloads on gated hosts
                # (HubCloud googleapis, ToonFlix worker proxy).
                "referer": res.get("referer", ""),
                # Issue #33 item 1: spare mirrors the source listed for this
                # episode — handed to the downloader as free retry ammo.
                "alternates": [u for u in (res.get("alternates") or []) if u and u != curr_url],
            }
            if got_norm == "Unknown":
                return entry, f"{name}: Unknown quality (verify post-download)", "unknown"
            return entry, f"{name}: exact {got_q} ✓", "exact"
        except Exception as e:
            log.warning("Fallback resolver '%s' failed for '%s' S%dE%d: %s", name, search_title, season, episode, e)
            return None, f"{name}: error {e}", "skip"

    async def resolve_episode_stream(
        self,
        series_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
        series_slug: str = "",
        preferred_source: str | None = None,
    ) -> dict | None:
        """
        Iterate through fallback scrapers to resolve an episode stream.
        Applies full resolver pipeline (shortener -> HubCloud -> player embed -> validation).
        Never returns intermediate HTML or ad pages!
        V2 #13: when ``preferred_source`` (the user's selected button source)
        is given, that extractor is tried first before the default order.
        V3 #2: known non-exact qualities are skipped (next source, not fallback).
        V3 #4: detected/requested/verified quality fields propagate separately.
        V3 #10: result carries provider/original/resolved/stage diagnostics.
        V3 #16: among exact-quality candidates the fastest healthy link wins.
        """
        from utils.anime_match import normalize_quality, qualities_match
        from extractors.health_probe import infer_provider, select_fastest_healthy
        clean_title = re.sub(r"(?i)\s*(?:season\s*\d+|s\d+|hindi|dubbed|subbed|multi-audio|tamil|telugu).*$", "", series_title).strip()
        search_title = clean_title or series_title or slug_to_title(series_slug)
        search_title = re.sub(r"[’'\"\-_:!?]+", " ", search_title).strip()
        search_title = re.sub(r"\s+", " ", search_title)
        want_norm = normalize_quality(quality_pref)

        ordered = list(self.sources)
        if preferred_source:
            pref = preferred_source.strip().lower()
            ordered.sort(key=lambda kv: 0 if kv[0].lower() == pref else 1)
        else:
            # /source default: the owner-selected default source leads the
            # extractor order whenever the caller has no explicit
            # slug-registry preference (unknown names — e.g. AnimeDekho —
            # leave the natural order untouched).
            try:
                from bot.source_config import get_default_source, _key as _skey
                _def = _skey(await get_default_source())
                if _def and _def != _skey("AnimeDekho"):
                    ordered.sort(key=lambda kv: 0 if _skey(kv[0]) == _def else 1)
            except Exception as _de:
                log.debug("default source ordering skipped: %s", _de)

        exact_candidates: list[dict] = []
        unknown_candidates: list[dict] = []
        diag_trail: list[str] = []

        # Download-fix: resolve sources CONCURRENTLY (bounded) instead of
        # sequentially. Time-limited signed URLs (HubCloud googleapis,
        # worker proxies) expire while a sequential loop burns 60-90s;
        # concurrency keeps every candidate fresh for the download step.
        _sem = asyncio.Semaphore(3)

        async def _one(item):
            name, extractor = item
            async with _sem:
                try:
                    return await asyncio.wait_for(
                        self._resolve_one_source(
                            name, extractor, search_title, season, episode,
                            quality_pref, want_norm,
                        ),
                        timeout=75,
                    )
                except asyncio.TimeoutError:
                    log.warning("Fallback source '%s' timed out (75s); skipping", name)
                    return None, f"{name}: timeout", "skip"
                except Exception as e:
                    log.warning("Fallback resolver '%s' failed for '%s' S%dE%d: %s", name, search_title, season, episode, e)
                    return None, f"{name}: error {e}", "skip"

        for entry, diag, kind in await asyncio.gather(*[_one(it) for it in ordered]):
            diag_trail.append(diag)
            if kind == "exact" and entry:
                exact_candidates.append(entry)
            elif kind == "unknown" and entry:
                unknown_candidates.append(entry)

        # V3 #16: fastest healthy exact-quality link wins; Unknown only when
        # no exact candidate exists (post-download ffprobe still enforces).
        pool = exact_candidates or unknown_candidates
        if not pool:
            log.info("MultiSource: no candidates (%s)", "; ".join(diag_trail))
            return None
        best, health_diags = await select_fastest_healthy(pool)
        # V3 #16 diagnostics: every candidate's health status in the log.
        for d in health_diags:
            log.info("Health check [%s] %s via %s: %s%s",
                     d.get("quality"), d.get("source"), d.get("provider"),
                     d.get("status"), f" ({d.get('error')})" if d.get("error") else "")
        if not best:
            log.info("MultiSource: all %d candidates unhealthy — failing (V3 #16)", len(pool))
            return None
        best["health_diagnostics"] = health_diags
        best["diagnostic_trail"] = diag_trail
        log.info("Fallback source '%s' selected [%s via %s]: %s",
                 best["source"], best["quality"], best["provider"], best["url"][:80])
        return best


multi_source_manager = MultiSourceManager()
