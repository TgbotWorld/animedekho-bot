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

# Issue #34/#35: resolution must not block on the slowest source.
#: Total seconds one resolution round may take (was unbounded: 8 sources ×
#: 75s each, 3 at a time ≈ 225s of waiting before any download could start).
_RESOLVE_HARD_CAP = 90.0
#: Extra seconds granted to the remaining sources after the first verified
#: exact-quality candidate lands.
_RESOLVE_GRACE = 8.0


class MultiSourceManager:
    """Manages primary, secondary, and fallback download/streaming sources."""

    def __init__(self):
        # Direct download/video sources prioritized first (Issue #22 & #23).
        # Issue #34: the concurrency semaphore grants slots in this order, so
        # the sources that actually resolve (AnimeDrive, ToonFlix,
        # AnimeDubHindi) must occupy the first wave instead of queueing behind
        # ones that routinely time out. Nothing is removed — a source that
        # still has the episode stays eligible.
        self.sources = [
            ("AnimeDrive", animedrive),
            ("ToonFlix", toonflix),
            ("AnimeDubHindi", animedubhindi),
            ("DeadToons", deadtoons),
            ("ToonWorld4All", toonworld4all),
            ("RareAnimes", rareanimes),
            ("TOONo", toono),
            ("ToonAnime", toonanime),
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
        """Search fallback sources when primary AnimeDekho returns 0 results.

        Issue #34: the sources are queried *concurrently* (bounded to 4 in
        flight, 25s each, 30s round cap) instead of one after another — a
        serial pass over 8 scrapers was costing the user tens of seconds on
        every miss. Results are still emitted in source-priority order and the
        round stops as soon as enough raw hits exist.
        """
        results: list[SearchResult] = []
        seen_slugs = set()

        _sem = asyncio.Semaphore(4)

        async def _search_one(item):
            name, extractor = item
            # Issue #34: a source sitting out its bench window is not worth a
            # round-trip — benched sources are skipped silently.
            try:
                from extractors import reliability as _rel
                if _rel.is_source_benched(name):
                    return name, None, None
            except Exception:
                pass
            async with _sem:
                try:
                    return name, await asyncio.wait_for(extractor.search(query), timeout=25), None
                except asyncio.TimeoutError:
                    return name, None, "timeout"
                except Exception as e:
                    return name, None, e

        _loop = asyncio.get_running_loop()
        _deadline = _loop.time() + 30.0
        _tasks = [asyncio.ensure_future(_search_one(it)) for it in self.sources]
        _pending = set(_tasks)
        _raw: dict[str, list] = {}
        try:
            while _pending:
                _budget = max(0.05, _deadline - _loop.time())
                _done, _pending = await asyncio.wait(
                    _pending, timeout=_budget, return_when=asyncio.FIRST_COMPLETED)
                for _t in _done:
                    try:
                        _name, _items, _err = _t.result()
                    except Exception as _te:
                        log.warning("Fallback search task failed for '%s': %s", query, _te)
                        continue
                    if _err is not None:
                        log.warning("Fallback search on %s failed for '%s': %s", _name, query, _err)
                        continue
                    if _items:
                        _raw[_name] = _items
                if not _done:
                    log.info("Fallback search: 30s cap reached (%d source(s) answered)", len(_raw))
                    break
                if sum(len(v) for v in _raw.values()) >= 10:
                    log.info("Fallback search: enough hits in hand from %d source(s)", len(_raw))
                    break
        finally:
            for _t in _pending:
                _t.cancel()
            if _pending:
                await asyncio.gather(*_pending, return_exceptions=True)

        # Emit in source-priority order regardless of who answered first.
        for name, _ in self.sources:
            raw_items = _raw.get(name)
            if not raw_items:
                continue
            try:
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

    async def get_recent_by_source(self, source_name: str, page: int = 1) -> list[SearchResult]:
        """Fetch recently released anime from a specific source."""
        from bot.source_config import normalize_source
        canonical = normalize_source(source_name) or source_name

        if canonical == "AnimeDekho":
            try:
                from api.client import api
                res = await api.get_recent_series(page=page)
                if res and res.items:
                    for it in res.items:
                        self._slug_registry[it.slug] = {
                            "source": "AnimeDekho",
                            "title": it.title,
                            "url": it.url,
                            "poster": it.poster,
                            "content_type": it.content_type,
                        }
                        from utils.helpers import short_slug
                        self._slug_registry[short_slug(it.slug)] = self._slug_registry[it.slug]
                    return res.items
            except Exception as e:
                log.warning("AnimeDekho get_recent_series failed for page %d: %s", page, e)
                return []
            return []

        # Find extractor in self.sources
        extractor = None
        for name, ext in self.sources:
            if name.lower() == canonical.lower():
                extractor = ext
                canonical = name
                break

        if not extractor or not hasattr(extractor, "get_recent"):
            log.warning("No recent releases extractor found for source '%s'", source_name)
            return []

        try:
            raw_items = await extractor.get_recent(page=page)
            if not raw_items:
                return []

            results: list[SearchResult] = []
            seen_slugs = set()
            for item in raw_items:
                raw_title = item.get("title", "")
                title = clean_title(raw_title) or raw_title
                url = item.get("url", "")
                poster = item.get("poster", "")

                slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
                if not slug or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                is_movie = "movie" in title.lower() or "film" in title.lower()
                self._slug_registry[slug] = {
                    "source": canonical,
                    "title": title,
                    "url": url,
                    "poster": poster,
                    "content_type": "movie" if is_movie else "series",
                }
                from utils.helpers import short_slug
                self._slug_registry[short_slug(slug)] = self._slug_registry[slug]

                results.append(SearchResult(
                    title=f"{title} [{canonical}]",
                    slug=slug,
                    url=url,
                    content_type="movie" if is_movie else "series",
                    poster=poster,
                    source=canonical,
                ))
            return results
        except Exception as e:
            log.warning("get_recent_by_source on %s failed (page %d): %s", canonical, page, e)
            return []

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
            m_eps = re.search(r"(\d+)\s*(?:eps?|episodes?)\b", title, re.IGNORECASE)
            if m_eps:
                total_eps = min(int(m_eps.group(1)), 48)

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

        season_num = 1
        m_s = re.search(r"(?i)\bseason\s*(\d+)\b", title)
        if m_s:
            try:
                season_num = int(m_s.group(1))
            except Exception:
                season_num = 1

        episodes = [
            Episode(
                number=i,
                slug=f"{slug}-{season_num}x{i}",
                season=season_num,
                title=f"{title} S{season_num}E{i:02d}",
                servers=[],
            )
            for i in range(1, total_eps + 1)
        ]
        season = Season(number=season_num, episodes=episodes)

        return Series(
            title=title,
            slug=slug,
            url=url,
            description=f"Available via {source} network. (~{total_eps} eps estimated via AniList)",
            poster=poster or None,
            genres=genres,
            seasons={season_num: season},
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
            try:
                from extractors import reliability as _rel
                if _rel.is_source_benched(name):
                    return None, f"{name}: source benched", "skip"
            except Exception:
                pass
            # AnimeDrive and ToonFlix are 4K-only sources to keep quality accurate
            is_4k_req = (quality_pref or "").lower() in ("4k", "2160p", "2160", "uhd")
            if name in ("AnimeDrive", "ToonFlix") and not is_4k_req:
                log.info("Source '%s' is 4K-only — skipping for %s request", name, quality_pref)
                return None, f"{name}: 4K-only source (skipped for {quality_pref})", "skip"

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

        is_4k = (quality_pref or "").lower() in ("4k", "2160p", "2160", "uhd")
        ordered = list(self.sources)
        if not is_4k:
            ordered = [(n, e) for n, e in ordered if n not in ("AnimeDrive", "ToonFlix")]
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
        #
        # Issue #34/#35: two guards on top of that — a source that keeps
        # timing out sits the round out (source bench), and once a verified
        # exact candidate is in hand the stragglers only get a short grace
        # window instead of holding the whole resolution hostage.
        from extractors import reliability as _rel

        _sem = asyncio.Semaphore(3)

        async def _one(item):
            name, extractor = item
            if _rel.is_source_benched(name):
                log.info("Source '%s' benched after repeated failures — skipping this round", name)
                return None, f"{name}: benched after repeated failures", "skip"
            async with _sem:
                try:
                    res = await asyncio.wait_for(
                        self._resolve_one_source(
                            name, extractor, search_title, season, episode,
                            quality_pref, want_norm,
                        ),
                        timeout=75,
                    )
                except asyncio.TimeoutError:
                    log.warning("Fallback source '%s' timed out (75s); skipping", name)
                    _rel.note_source_failure(name, "timeout after 75s")
                    return None, f"{name}: timeout", "skip"
                except Exception as e:
                    log.warning("Fallback resolver '%s' failed for '%s' S%dE%d: %s", name, search_title, season, episode, e)
                    _rel.note_source_failure(name, str(e))
                    return None, f"{name}: error {e}", "skip"
                # Producing an entry proves the source alive; "no result for
                # this title" is a normal miss and must not count against it.
                if res and res[0]:
                    _rel.note_source_success(name)
                return res

        _loop = asyncio.get_running_loop()
        _hard_deadline = _loop.time() + _RESOLVE_HARD_CAP
        _grace_deadline: float | None = None
        _tasks = [asyncio.ensure_future(_one(it)) for it in ordered]
        _pending = set(_tasks)
        try:
            while _pending:
                _budget = max(0.05, _hard_deadline - _loop.time())
                if _grace_deadline is not None:
                    _budget = min(_budget, max(0.05, _grace_deadline - _loop.time()))
                _done, _pending = await asyncio.wait(
                    _pending, timeout=_budget, return_when=asyncio.FIRST_COMPLETED,
                )
                for _task in _done:
                    try:
                        _res = _task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as _te:
                        _res = (None, f"source task error: {_te}", "skip")
                    if not _res:
                        continue
                    entry, diag, kind = _res
                    diag_trail.append(diag)
                    if kind == "exact" and entry:
                        exact_candidates.append(entry)
                    elif kind == "unknown" and entry:
                        unknown_candidates.append(entry)
                if not _done:
                    log.info(
                        "MultiSource: stopped waiting after %.0fs — %d exact / %d unknown candidate(s)",
                        _RESOLVE_HARD_CAP, len(exact_candidates), len(unknown_candidates),
                    )
                    break
                if _grace_deadline is None and exact_candidates:
                    # First verified exact candidate: let the others finish
                    # quickly, then cut them loose.
                    _grace_deadline = min(_loop.time() + _RESOLVE_GRACE, _hard_deadline)
                    log.info("MultiSource: exact candidate in hand — %.0fs grace for the rest",
                             _RESOLVE_GRACE)
        finally:
            for _task in _pending:
                _task.cancel()
            if _pending:
                await asyncio.gather(*_pending, return_exceptions=True)

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
