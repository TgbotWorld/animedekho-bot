"""Universal manual URL resolver — backing for /bypass (V2 #23, V3 #3).

V3 #3: website-specific resolver dispatch (no generic-only scan).
V3 #4: detected/requested/verified quality stay separate; Unknown stays Unknown.
V3 #11: ToonWorld4All redirect/archive → resolve → refetch final page →
        destination validation; navigation/HTML/challenge rejected; failure
        stage reports the real failing stage (never "done" on failure).
V3 #12: AnimeDubHindi returns provider-grouped qualities
        (GDFLIX / FPGO / HubCloud … × 480p/720p/1080p).

Reusable: normal download flow can call ``resolve_bypass_url()`` directly
instead of duplicating resolver code. Only publicly accessible content is
resolved — no private login cookies, auth tokens, or session secrets are
ever read or echoed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlparse, urljoin

log = logging.getLogger(__name__)


# ── Supported-source registry (domain fragment → source name) ────────────
# Website-specific implementations live in their extractor modules; this
# table only routes. Add future extractors here — /bypass picks them up
# with no command changes.

SUPPORTED_SOURCES: dict[str, str] = {
    "animedubhindi": "AnimeDubHindi",
    "adhlinks.com": "AnimeDubHindi",
    "toonworld4all": "ToonWorld4All",
    "toonanime": "ToonAnime",
    "rareanimes": "RareAnimes",
    "deadtoons": "DeadToons",
    "toono.app": "TOONo",
    "animedrive": "AnimeDrive",
    "toonflix": "ToonFlix",
}


def detect_source(url: str) -> str | None:
    """Return the supported Source name for *url*, or None if unsupported."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None
    for frag, name in SUPPORTED_SOURCES.items():
        if frag in host:
            return name
    return None


def _detect_quality_from_text(blob: str) -> str:
    """V3 #4: return '' when unknown — callers map to 'Unknown', never to
    the requested quality."""
    b = (blob or "").lower()
    for q in ("2160p", "2160", "4k", "uhd"):
        if q in b:
            return "4K"
    for q in ("1080p", "1080"):
        if q in b:
            return "1080p"
    for q in ("720p", "720"):
        if q in b:
            return "720p"
    for q in ("480p", "480"):
        if q in b:
            return "480p"
    for q in ("360p", "360"):
        if q in b:
            return "360p"
    return ""


def _parse_title_season_episode(url: str, html: str = "") -> tuple[str, int | None, int | None]:
    """Best-effort anime/season/episode parse from URL + page title.

    Returns (anime, season, episode) with Nones when not confidently found —
    callers must reject wrong matches, never guess.
    """
    anime = ""
    season: int | None = None
    episode: int | None = None
    # ToonWorld4All React pages embed authoritative metadata in __PROPS__
    # (show/season/episode) — prefer it over URL slug guessing.
    try:
        props = _parse_tw4_props(html)
        meta = (((props or {}).get("data") or {}).get("data") or {}).get("metadata") or {}
        if meta.get("show"):
            anime = str(meta["show"]).strip()[:120]
            if meta.get("season") is not None:
                season = int(meta["season"])
            if meta.get("episode") is not None:
                episode = int(meta["episode"])
            if anime:
                return anime, season, episode
    except Exception:
        pass
    try:
        path = urlparse(url).path.strip("/")
        parts = [p for p in path.split("/") if p]
        slug = parts[-1] if parts else ""
        slug = re.sub(r"\.(html?|php)$", "", slug, flags=re.I)
        # Handle DeadToons /episode/<slug>/<season>x<ep> structure
        if len(parts) >= 3 and parts[-3].lower() == "episode":
            slug_ep = parts[-1]
            m_dt = re.search(r"(\d+)[xX](\d+)", slug_ep)
            if m_dt:
                season, episode = int(m_dt.group(1)), int(m_dt.group(2))
            slug = parts[-2]
        # Season/episode patterns: s1e10, 1x10, season-1-episode-10, ep-10
        m = re.search(r"[Ss](\d+)[Ee](\d+)", slug)
        if not m:
            m = re.search(r"(\d+)[xX](\d+)", slug)
        if m:
            season, episode = int(m.group(1)), int(m.group(2))
        elif season is None:
            m2 = re.search(r"(?:season|s)[-_]?(\d+).*?(?:episode|ep)[-_]?(\d+)", slug, re.I)
            if m2:
                season, episode = int(m2.group(1)), int(m2.group(2))
            else:
                m3 = re.search(r"(?:episode|ep)[-_]?(\d+)", slug, re.I)
                if m3:
                    episode = int(m3.group(1))
        # Opaque redirect tokens (/redirect/<hex>) carry no title — never
        # present the token itself as the anime name (issue #30 card).
        if "/redirect/" in (url or "").lower() or re.fullmatch(r"[0-9a-f]{32,}", slug or ""):
            slug = ""
        base = re.sub(
            r"(?i)[-_ ]?(season[-_ ]?\d+|s\d+e\d+|\d+x\d+|episode[-_ ]?\d+|ep[-_ ]?\d+|hindi|multi[-_ ]?audio|dubbed).*$",
            "",
            slug,
        )
        anime = re.sub(r"[-_]+", " ", base).strip().title()
    except Exception:
        pass
    if html and not anime and "/redirect/" not in (url or "").lower():
        # Redirect/interstitial pages carry only generic titles
        # ("Redirecting…") — never present them as the anime name.
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            if soup.title and soup.title.get_text(strip=True):
                anime = soup.title.get_text(strip=True).split("|")[0].split("–")[0].strip()[:120]
        except Exception:
            pass
    return anime, season, episode


async def _fetch_public(url: str) -> tuple[str, str]:
    """GET a public page; returns (final_url, html). No auth/cookies sent.

    Falls back to cloudscraper when the plain fetch is challenged/empty —
    archive.toonworld4all.me and several providers sit behind Cloudflare
    "Just a moment" interstitials that plain HTTP clients cannot read.
    """
    from utils.http import http_client
    last: tuple[str, str] = (url, "")
    try:
        final_url, html = await http_client.get_with_redirects(url)
        last = (final_url, html or "")
        if last[1] and len(last[1]) > 400 and not _is_challenge_html(last[1]):
            return last
    except Exception as e:
        log.warning("bypass fetch failed for %s: %s", url[:100], e)

    # Cloudflare fallback: sync cloudscraper in an executor (bounded).
    def _cs() -> tuple[str, str]:
        import cloudscraper
        sess = cloudscraper.create_scraper()
        r = sess.get(url, timeout=20, allow_redirects=True)
        return str(r.url), r.text or ""

    try:
        loop = asyncio.get_running_loop()
        fu, h = await loop.run_in_executor(None, _cs)
        if h and len(h) > 200 and not _is_challenge_html(h):
            return fu, h
        if h and len(h) > len(last[1]):
            last = (fu, h)
    except Exception as e:
        log.debug("cloudscraper fallback failed for %s: %s", url[:80], e)
    return last


def _parse_tw4_props(html: str) -> dict | None:
    """Parse ``window.__PROPS__ = {...};`` from ToonWorld4All React pages.

    Episode pages carry ``data.data.encodes[]`` (resolution × host × /redirect
    link); redirect pages carry ``link.domain``+``link.hidden`` (the real
    provider file URL) and ``destination`` (ad shortener — never used first).
    """
    if not html:
        return None
    m = re.search(r"window\.__PROPS__\s*=\s*(\{.*?\})\s*;", html, re.S)
    if not m:
        return None
    try:
        props = json.loads(m.group(1))
        return props if isinstance(props, dict) else None
    except Exception:
        return None


def _split_archive_vs_media(urls: list[str]) -> tuple[list[str], list[str]]:
    """Separate ZIP/archive links from direct video links.

    V3 #11: only real archive *files* count — a bare
    ``archive.toonworld4all`` page or ``cdn-cgi/`` challenge asset is NOT an
    archive result.
    """
    archives, media = [], []
    for u in urls:
        low = u.lower()
        if re.search(r"\.(zip|rar|7z)(\?|#|$)", low):
            archives.append(u)
        else:
            media.append(u)
    return archives, media


# ── V3 #11: navigation / challenge rejection ─────────────────────────────

_NAVIGATION_HINTS = (
    "/tag/", "/tags/", "/category/", "/categories/", "/author/",
    "/contact", "/privacy", "/dmca", "/disclaimer", "/about",
    "cdn-cgi/", "/schedule", "schedule.php", "/page/",
    "-list", "list_", "/movies", "/shows/", "first-on-ne", "exclusive",
    "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com/",
    "instagram.com", "t.me/", "telegram.me",
)

_DOWNLOAD_HOSTS = (
    "drive.google", "mega.nz", "mediafire.com", "hubcloud", "gamerxyt",
    "gdflink", "gdflix", "filepress", "fpgo.", "workers.dev",
    "googleusercontent.com", "filesforever", "megaup", "multiup",
    "streamwish", "filemoon", "vidstream", "megacloud", "vidsrc",
    "dood", "streamtape", "mp4upload", "vidguard",
    "adhlinks.com/episode", "redirect",
)

_MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".m3u8", ".mpd", ".zip", ".rar", ".7z")


def _looks_like_download(url: str, label: str = "") -> bool:
    """V3 #11: a plain season/list/social page is navigation, never media.

    Download candidates are: archive/redirect/shortener links, iframes,
    direct files, known file hosts, or episode-pattern links (1x25/S01E25).
    """
    low = url.lower()
    if "archive.toonworld4all" in low or "redirect" in low:
        return True
    if any(ext in low for ext in _MEDIA_EXTS):
        return True
    if any(h in low for h in _DOWNLOAD_HOSTS):
        return True
    if re.search(r"\b\d{1,2}x\d{1,3}\b", f"{url} {label}", re.I):
        return True
    if re.search(r"[Ss]\d{1,2}[Ee]\d{1,3}\b", f"{url} {label}"):
        return True
    if re.search(r"(?:episode|ep)[-_ ]?\d{1,3}\b", f"{url} {label}", re.I):
        return True
    return False

_CHALLENGE_HINTS = (
    "just a moment", "cf-chl", "cf_turnstile", "turnstile",
    "g-recaptcha", "hcaptcha",
)


def _is_navigation_url(url: str, label: str = "") -> bool:
    """V3 #11: homepage / tag / contact / cdn-cgi must never be media links."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = (parsed.path or "/").lower()
        if not host:
            return True
        # Homepage / bare domain.
        if path in ("", "/"):
            return True
        if any(h in path or h in url.lower() for h in _NAVIGATION_HINTS):
            return True
        blob = f"{url} {label}".lower()
        if "telegram" in blob and not any(e in blob for e in (".mp4", ".mkv", ".m3u8", ".zip")):
            return True
        return False
    except Exception:
        return True


def _is_challenge_html(html: str) -> bool:
    h = (html or "").lower()
    return any(c in h for c in _CHALLENGE_HINTS)


def _infer_provider(url: str, label: str = "") -> str:
    try:
        from extractors.health_probe import infer_provider as _inf
        return _inf(url)
    except Exception:
        pass
    blob = f"{url} {label}".lower()
    for name in ("hubcloud", "gdflix", "gdflink", "fpgo", "filepress",
                 "mega", "drive.google", "mediafire", "multi"):
        if name in blob:
            return name.upper() if name in ("fpgo", "mega") else name.capitalize()
    return "Direct"


# ── V3 #3: website-specific resolvers ─────────────────────────────────────

async def _resolve_animedubhindi_page(
    html: str, base_url: str, quality_pref: str,
    detect_and_bypass, is_shortener, is_valid_media_destination,
) -> tuple[list[dict], dict]:
    """V3 #12: provider-grouped AnimeDubHindi parse.

    Returns (media_links, providers) where providers is
    {provider: {quality: [url, ...]}} for GDFLIX/FPGO/HubCloud/….
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    providers: dict[str, dict[str, list[str]]] = {}
    media_links: list[dict] = []
    seen: set[str] = set()

    def _bucket_for(elem_text: str) -> str:
        low = elem_text.lower()
        if "480p" in low and "720p" not in low and "1080p" not in low:
            return "480p"
        if "720p" in low and "1080p" not in low:
            # element header names one quality; mixed headers handled by link labels
            return "720p"
        if "1080p" in low:
            return "1080p"
        return ""

    # Strategy A: pro-ep-card layout with per-quality wrappers.
    cards = soup.find_all(class_="pro-ep-card")
    scoped = False
    for card in cards:
        for qw in card.find_all(class_="pro-quality-wrapper"):
            q_el = qw.find(class_="pro-ep-quality")
            q_txt = q_el.get_text(strip=True) if q_el else qw.get_text(" ", strip=True)[:120]
            bucket = _detect_quality_from_text(q_txt) or _bucket_for(q_txt) or "Unknown"
            for a in qw.find_all("a", href=True):
                href = a["href"].strip()
                if not href.startswith("http"):
                    continue
                label = a.get_text(" ", strip=True)[:120]
                # Per-link quality wins over wrapper header.
                link_q = _detect_quality_from_text(href + " " + label) or bucket
                if _is_navigation_url(href, label):
                    continue
                dest = href
                if is_shortener(href) or "redirect" in href.lower():
                    dest = await detect_and_bypass(href)
                    if not dest:
                        continue
                if dest in seen:
                    continue
                seen.add(dest)
                if not is_valid_media_destination(dest):
                    continue
                provider = _infer_provider(dest, label)
                # V3 #11: archive pages are not media; real zips go to archives.
                if re.search(r"\.(zip|rar|7z)(\?|#|$)", dest.lower()):
                    continue
                providers.setdefault(provider, {}).setdefault(link_q, []).append(dest)
                media_links.append({
                    "quality": link_q, "url": dest, "provider": provider,
                    "requested_quality": quality_pref, "detected_quality": link_q,
                    "verified_quality": link_q, "label": label,
                })
                scoped = True

    if scoped:
        return media_links, providers

    # Strategy B: generic strict scan (still provider-grouped, Unknown-safe).
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if not href.startswith("http") or len(href) < 12:
            continue
        label = a.get_text(" ", strip=True)[:200]
        if _is_navigation_url(href, label):
            continue
        if not _looks_like_download(href, label):
            continue  # V3 #11: related-post/season pages are navigation
        q = _detect_quality_from_text(href + " " + label) or "Unknown"
        dest = href
        if is_shortener(href) or "redirect" in href.lower():
            dest = await detect_and_bypass(href)
            if not dest:
                continue
        if dest in seen:
            continue
        seen.add(dest)
        if re.search(r"\.(zip|rar|7z)(\?|#|$)", dest.lower()):
            continue
        if not is_valid_media_destination(dest):
            continue
        provider = _infer_provider(dest, label)
        providers.setdefault(provider, {}).setdefault(q, []).append(dest)
        media_links.append({
            "quality": q, "url": dest, "provider": provider,
            "requested_quality": quality_pref, "detected_quality": q,
            "verified_quality": q, "label": label,
        })
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or "").strip()
        if not src.startswith("http") or len(src) <= 12:
            continue
        if _is_navigation_url(src):
            continue
        q = _detect_quality_from_text(src) or "Unknown"
        if src in seen:
            continue
        seen.add(src)
        if not is_valid_media_destination(src):
            continue
        provider = _infer_provider(src)
        providers.setdefault(provider, {}).setdefault(q, []).append(src)
        media_links.append({
            "quality": q, "url": src, "provider": provider,
            "requested_quality": quality_pref, "detected_quality": q,
            "verified_quality": q, "label": "iframe",
        })
    return media_links, providers


async def _resolve_toonworld_url(
    page_url: str, html: str, quality_pref: str,
    detect_and_bypass, is_shortener, is_valid_media_destination,
    season: int | None = None, episode: int | None = None,
) -> tuple[list[dict], list[str], str]:
    """V3 #11: real redirect resolve → final page refetch → validation.

    Returns (media_links, archive_urls, note). Archive *pages* are followed;
    only real archive *files* are reported as archives; cdn-cgi/navigation
    is rejected; HTML/challenge pages are never media.
    """
    from bs4 import BeautifulSoup
    media_links: list[dict] = []
    archive_urls: list[str] = []
    seen: set[str] = set()
    # Live-discovered budget: a season page can list 30+ episode/zip links;
    # resolving every one sequentially takes minutes. Bound bypass attempts
    # and prefer links matching the parsed episode. Episode __PROPS__ pages
    # need one fetch per provider file (qualities × hosts) — keep enough
    # headroom for all qualities while staying bounded.
    budget = {"n": 10}

    async def _bounded_bypass(href: str) -> str | None:
        if budget["n"] <= 0:
            return None
        budget["n"] -= 1
        try:
            return await detect_and_bypass(href)
        except Exception:
            return None

    def _ep_priority(href: str, label: str) -> int:
        if season is not None and episode is not None \
                and re.search(rf"{season}x0*{episode}\b", f"{href} {label}", re.I):
            return 0
        if "/zip/" in href.lower():
            return 2  # batch archives last; skipped below unless nothing else
        return 1

    async def _resolve_hubcloud_target(tgt: str) -> str | None:
        """HubCloud video/drive page → direct URL (None when dead/404)."""
        try:
            from extractors.animedrive import animedrive
            loop = asyncio.get_running_loop()
            tried = [tgt]
            if "/video/" in tgt:
                tried.append(tgt.replace("/video/", "/drive/"))
            for u in tried:
                direct = await loop.run_in_executor(
                    None, animedrive._resolve_hubcloud, animedrive._get_scraper(), u
                )
                if direct:
                    return direct
        except Exception as he:
            log.debug("hubcloud resolve failed for %s: %s", tgt[:80], he)
        return None

    async def _provider_target(redirect_url: str) -> str | None:
        """archive /redirect/<token> → real provider file URL via __PROPS__."""
        _, rhtml = await _fetch_public(redirect_url)
        rp = _parse_tw4_props(rhtml)
        if not rp:
            return None
        link = rp.get("link") or {}
        dom, hid = str(link.get("domain") or ""), str(link.get("hidden") or "")
        if dom and hid:
            return dom.rstrip("/") + "/" + hid.lstrip("/")
        return None

    # Precise source-side failure reasons (set inside _props_resolution).
    props_fail: list[str] = []

    async def _props_resolution() -> None:
        """V3 #11: structured extraction from React __PROPS__.

        Episode pages: encodes[] × files[] → each /redirect → provider file
        URL (quality + host preserved). Redirect pages: link.domain+hidden
        directly. The ad-shortener ``destination`` (exe.io Turnstile) is never
        used when the real provider link is present.

        ``props_fail`` records precise source-side failures (e.g. an expired
        provider file) so the caller reports the REAL reason instead of
        walking into the exe.io ad wall.
        """
        props = _parse_tw4_props(html)
        if not props:
            return

        # ── Redirect page: single provider target ─────────────────────
        link = props.get("link")
        if isinstance(link, dict) and link.get("domain") and not encodes_of(props):
            tgt = str(link["domain"]).rstrip("/") + "/" + str(link.get("hidden") or "").lstrip("/")
            provider = _infer_provider(tgt)
            if "hubcloud" in tgt.lower():
                direct = await _resolve_hubcloud_target(tgt)
                if not direct:
                    props_fail.append(
                        f"Provider {provider} file expired — HTTP 404 at source "
                        "(no mirror on this redirect token); try the episode link instead."
                    )
                    log.info("ToonWorld4All: redirect provider file dead at source: %s", tgt[:90])
                    return
                tgt = direct
            if tgt and tgt.startswith("http") and is_valid_media_destination(tgt):
                q = quality_pref if quality_pref and quality_pref.lower() != "auto" else "Unknown"
                media_links.append({
                    "quality": q, "url": tgt, "provider": provider,
                    "requested_quality": quality_pref, "detected_quality": q,
                    "verified_quality": q, "label": provider,
                })
                seen.add(tgt)
            return

        # ── Episode page: every quality × host, requested quality first ──
        encs = encodes_of(props)
        if not encs:
            return
        before_links = len(media_links)

        def _qprio(e: dict) -> int:
            return 0 if str(e.get("resolution") or "").lower() == str(quality_pref).lower() else 1

        for enc in sorted(encs, key=_qprio):
            if budget["n"] <= 0:
                break
            eq = str(enc.get("resolution") or "Unknown")
            for f in (enc.get("files") or []):
                if budget["n"] <= 0:
                    break
                budget["n"] -= 1
                red = urljoin(page_url, str(f.get("link") or ""))
                if not red.startswith("http"):
                    continue
                tgt = await _provider_target(red)
                if not tgt:
                    continue
                provider = _infer_provider(tgt, str(f.get("host") or ""))
                if "hubcloud" in tgt.lower():
                    direct = await _resolve_hubcloud_target(tgt)
                    if not direct:
                        continue  # dead/404 HubCloud file → try next host
                    tgt = direct
                if not tgt.startswith("http") or not is_valid_media_destination(tgt):
                    continue
                if tgt in seen:
                    continue
                seen.add(tgt)
                media_links.append({
                    "quality": eq, "url": tgt, "provider": provider,
                    "requested_quality": quality_pref, "detected_quality": eq,
                    "verified_quality": eq, "label": str(f.get("host") or provider),
                })
        if len(media_links) == before_links:
            props_fail.append(
                "All provider files on this episode failed resolution "
                "(expired/404/challenge at source)."
            )

    def encodes_of(props: dict) -> list:
        try:
            encs = ((props.get("data") or {}).get("data") or {}).get("encodes")
            return encs if isinstance(encs, list) else []
        except Exception:
            return []

    await _props_resolution()

    async def _refetch_and_extract(target: str, depth: int = 0) -> None:
        if depth > 2:
            return
        final_url, page_html = await _fetch_public(target)
        if not page_html or len(page_html) < 300 or _is_challenge_html(page_html):
            return
        soup = BeautifulSoup(page_html, "html.parser")
        links: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            links.append(((a["href"] or "").strip(), a.get_text(" ", strip=True)[:200]))
        links.sort(key=lambda hl: _ep_priority(hl[0], hl[1]))
        for href, label in links:
            if not href.startswith("http"):
                continue
            if _is_navigation_url(href, label):
                continue
            if "/zip/" in href.lower():
                continue  # batch-archive pages: not episode files, skip fast
            if not _looks_like_download(href, label):
                continue  # V3 #11: plain season/list pages are navigation
            dest = href
            # Follow nested archive/redirect one more hop, then refetch.
            if "archive.toonworld4all" in href.lower() or "redirect" in href.lower() or is_shortener(href):
                nxt = await _bounded_bypass(href)
                if nxt and nxt != href:
                    # If bypass lands on another HTML page, refetch it.
                    if not is_valid_media_destination(nxt) and nxt.startswith("http") \
                            and not _is_navigation_url(nxt):
                        await _refetch_and_extract(nxt, depth + 1)
                        continue
                    dest = nxt
                else:
                    continue
            if dest in seen:
                continue
            seen.add(dest)
            # Real archive files only.
            if re.search(r"\.(zip|rar|7z)(\?|#|$)", dest.lower()):
                archive_urls.append(dest)
                continue
            if "archive.toonworld4all" in dest.lower():
                continue  # intermediate page, not a file
            if not is_valid_media_destination(dest):
                continue
            q = _detect_quality_from_text(dest + " " + label) or "Unknown"
            media_links.append({
                "quality": q, "url": dest, "provider": _infer_provider(dest, label),
                "requested_quality": quality_pref, "detected_quality": q,
                "verified_quality": q, "label": label,
            })
        for iframe in soup.find_all("iframe"):
            src = (iframe.get("src") or "").strip()
            if not src.startswith("http") or _is_navigation_url(src):
                continue
            if src in seen:
                continue
            seen.add(src)
            if not is_valid_media_destination(src):
                continue
            q = _detect_quality_from_text(src) or "Unknown"
            media_links.append({
                "quality": q, "url": src, "provider": _infer_provider(src),
                "requested_quality": quality_pref, "detected_quality": q,
                "verified_quality": q, "label": "iframe",
            })

    # Structured props extraction ran above; generic HTML scan only when it
    # produced nothing (React pages expose no <a> download links anyway).
    # When props already identified a dead provider, do NOT fall back to the
    # generic shortener walk (exe.io Turnstile → misleading "bot challenge").
    if not media_links and not props_fail:
        # If the input itself is an archive/redirect link, resolve first.
        start = page_url
        if "archive.toonworld4all" in page_url.lower() or "redirect" in page_url.lower() or is_shortener(page_url):
            dest = await detect_and_bypass(page_url)
            if dest and dest != page_url:
                start = dest
        await _refetch_and_extract(start, 0)
    # Also scan the original page HTML for direct episode links.
    if not media_links and not archive_urls and html:
        soup0 = BeautifulSoup(html, "html.parser")
        links0: list[tuple[str, str]] = []
        for a in soup0.find_all("a", href=True):
            links0.append(((a["href"] or "").strip(), a.get_text(" ", strip=True)[:200]))
        links0.sort(key=lambda hl: _ep_priority(hl[0], hl[1]))
        for href, label in links0:
            if not href.startswith("http"):
                continue
            if _is_navigation_url(href, label):
                continue
            if "/zip/" in href.lower():
                continue
            if not _looks_like_download(href, label):
                continue  # V3 #11: plain season/list pages are navigation
            if "archive.toonworld4all" in href.lower() or "redirect" in href.lower() or is_shortener(href):
                dest = await _bounded_bypass(href)
                if dest and dest != href and dest not in seen:
                    seen.add(dest)
                    if re.search(r"\.(zip|rar|7z)(\?|#|$)", dest.lower()):
                        archive_urls.append(dest)
                    elif is_valid_media_destination(dest):
                        q = _detect_quality_from_text(dest + " " + label) or "Unknown"
                        media_links.append({
                            "quality": q, "url": dest, "provider": _infer_provider(dest, label),
                            "requested_quality": quality_pref, "detected_quality": q,
                            "verified_quality": q, "label": label,
                        })
    note = "redirect→refetch→validate" if ("redirect" in page_url.lower() or "archive" in page_url.lower()) else "page-scan"
    if props_fail and not media_links:
        note = props_fail[0]
    return media_links, sorted(set(archive_urls)), note


async def _resolve_deadtoons_bypass(
    page_url: str,
    html: str,
    quality_pref: str,
    season: int | None,
    episode: int | None,
    is_valid_media_destination,
) -> list[dict]:
    """DeadToons resolver — parses API links or unlocks mirrors to direct Google CDN / Pixeldrain."""
    from extractors.deadtoons import deadtoons
    from urllib.parse import urlparse, parse_qs
    from bs4 import BeautifulSoup
    media_links: list[dict] = []

    # 1. Direct unlock link: /unlock?link_server_id=...
    parsed = urlparse(page_url)
    qs = parse_qs(parsed.query)
    if "link_server_id" in qs:
        try:
            lsid = int(qs["link_server_id"][0])
            loop = asyncio.get_running_loop()
            scraper = deadtoons._get_scraper()
            direct_url = await loop.run_in_executor(None, deadtoons._unlock_server, scraper, lsid)
            if direct_url and is_valid_media_destination(direct_url):
                q = _detect_quality_from_text(direct_url) or "Unknown"
                media_links.append({
                    "quality": q,
                    "url": direct_url,
                    "provider": "DeadToons Cloud",
                    "requested_quality": quality_pref,
                    "detected_quality": q,
                    "verified_quality": q,
                    "label": "Direct Unlocked Stream",
                })
                return media_links
        except Exception as e:
            log.warning("DeadToons unlock link resolve error: %s", e)

    # 2. Episode or post page -> resolve via DeadToons API
    soup = BeautifulSoup(html, "html.parser")
    if not season or not episode:
        import re as re_mod
        m_ep = re_mod.search(r"/episode/[^/?#]+/(\d+)x(\d+)", page_url)
        if m_ep:
            if not season:
                season = int(m_ep.group(1))
            if not episode:
                episode = int(m_ep.group(2))

    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None,
        deadtoons._resolve_via_api,
        deadtoons._get_scraper(),
        soup,
        {"url": page_url, "poster": ""},
        season or 1,
        episode or 1,
        quality_pref,
    )

    if res and res.get("url"):
        q = res.get("quality", "Unknown")
        media_links.append({
            "quality": q,
            "url": res["url"],
            "provider": res.get("server", "DeadToons Cloud"),
            "requested_quality": quality_pref,
            "detected_quality": q,
            "verified_quality": q,
            "label": f"{res.get('title', 'DeadToons')} [{res.get('size', '')}]".strip(),
        })

    return media_links


async def _resolve_generic_page(
    html: str, quality_pref: str,
    detect_and_bypass, is_shortener, is_valid_media_destination,
) -> list[dict]:
    """Strict generic scan for remaining sources (Unknown-safe)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if not href.startswith("http") or len(href) < 12:
            continue
        label = a.get_text(" ", strip=True)[:200]
        if _is_navigation_url(href, label):
            continue
        if not _looks_like_download(href, label):
            continue  # V3 #11: plain pages are navigation, never media
        q = _detect_quality_from_text(href + " " + label) or "Unknown"
        dest = href
        if is_shortener(href) or "redirect" in href.lower():
            dest = await detect_and_bypass(href)
            if not dest:
                continue
        if dest in seen:
            continue
        seen.add(dest)
        if _is_navigation_url(dest):
            continue
        if not is_valid_media_destination(dest):
            continue
        out.append({
            "quality": q, "url": dest, "provider": _infer_provider(dest, label),
            "requested_quality": quality_pref, "detected_quality": q,
            "verified_quality": q, "label": label,
        })
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or "").strip()
        if not src.startswith("http") or len(src) <= 12:
            continue
        if _is_navigation_url(src):
            continue
        q = _detect_quality_from_text(src) or "Unknown"
        if src in seen:
            continue
        seen.add(src)
        if not is_valid_media_destination(src):
            continue
        out.append({
            "quality": q, "url": src, "provider": _infer_provider(src),
            "requested_quality": quality_pref, "detected_quality": q,
            "verified_quality": q, "label": "iframe",
        })
    return out


async def resolve_bypass_url(url: str, quality_pref: str = "1080p") -> dict:
    """Website-dispatched universal resolver (V3 #3).

    Never returns fake results: unsupported sites → {"ok": False,
    "error": "Unsupported Source ..."}; HTML/navigation pages are never
    reported as media URLs (validated via is_valid_media_destination +
    navigation rejection). Failure keeps the real stage (never "done").
    """
    from extractors.shortener import detect_and_bypass, is_shortener, is_valid_media_destination

    original_url = (url or "").strip()
    result: dict = {
        "ok": False,
        "source": None,
        "provider": None,
        "original_url": original_url,
        "final_url": original_url,
        "resolved_url": original_url,
        "anime": "",
        "season": None,
        "episode": None,
        "qualities": [],
        "media_links": [],
        "archive_links": [],
        "providers": {},
        "resolver_stage": "detect",
        "fallback_stage": "",
        "failure_reason": "",
        "stage": "detect",
        "error": "",
    }
    if not original_url or not original_url.lower().startswith(("http://", "https://")):
        result["error"] = "Provide a public http(s) page/episode URL: /bypass <URL>"
        result["failure_reason"] = result["error"]
        return result

    source = detect_source(original_url)
    if not source:
        result["stage"] = "detect"
        result["resolver_stage"] = "detect"
        result["error"] = f"Unsupported Source for {urlparse(original_url).netloc or original_url} — no resolver registered."
        result["failure_reason"] = result["error"]
        return result
    result["source"] = source
    result["resolver_stage"] = f"{source} dispatcher"

    # ── Stage: redirect chain ──────────────────────────────────────────
    curr = original_url
    try:
        # archive.toonworld4all /redirect/<token> pages are NOT shorteners:
        # generic-bypassing them walks into the exe.io ad wall. Fetch the
        # page directly — its __PROPS__ carries the real provider link.
        _is_archive = "archive.toonworld4all" in curr.lower()
        if (is_shortener(curr) or "redirect" in curr.lower()) and not _is_archive:
            bypassed = await detect_and_bypass(curr)
            if bypassed and bypassed != curr:
                curr = bypassed
        final_url, html = await _fetch_public(curr)
        result["final_url"] = final_url or curr
        result["resolved_url"] = result["final_url"]
        result["stage"] = "fetch"
        result["resolver_stage"] = f"{source} → fetch"
    except Exception as e:
        result["stage"] = "fetch"
        result["resolver_stage"] = f"{source} → fetch (failed)"
        result["error"] = f"Fetch failed: {e}"
        result["failure_reason"] = result["error"]
        return result

    if not html or len(html) < 500:
        result["stage"] = "fetch"
        result["error"] = "Page fetch returned empty/blocked content (Cloudflare/antibot?)."
        result["failure_reason"] = result["error"]
        return result
    if _is_challenge_html(html):
        result["stage"] = "fetch"
        result["error"] = "Page is behind a bot challenge/CAPTCHA — refusing to guess links."
        result["failure_reason"] = result["error"]
        return result

    # ── Stage: validate anime/season/episode ─────────────────────────
    anime, season, episode = _parse_title_season_episode(result["final_url"], html)
    result["anime"] = anime
    result["season"] = season
    result["episode"] = episode
    result["stage"] = "validate"
    result["resolver_stage"] = f"{source} → validate"
    if not anime or len(anime) < 3:
        # Archive /redirect/ targets embed no title/metadata — the props
        # link itself is authoritative, so allow resolution to proceed
        # (anime stays "—"; never guessed).
        if not ((_parse_tw4_props(html) or {}).get("link")):
            result["error"] = "Could not validate anime title from URL/page — refusing to guess."
            result["failure_reason"] = result["error"]
            return result

    # ── Stage: website-specific resolution (V3 #3 dispatch) ──────────
    result["stage"] = "resolve"
    try:
        media_links: list[dict] = []
        archive_urls: list[str] = []
        providers: dict = {}
        if source == "AnimeDubHindi":
            result["resolver_stage"] = "AnimeDubHindi → provider blocks → Direct Media"
            media_links, providers = await _resolve_animedubhindi_page(
                html, result["final_url"], quality_pref,
                detect_and_bypass, is_shortener, is_valid_media_destination,
            )
            result["providers"] = providers
        elif source == "ToonWorld4All":
            result["resolver_stage"] = "ToonWorld4All → redirect → refetch → Direct Media"
            media_links, archive_urls, note = await _resolve_toonworld_url(
                result["final_url"], html, quality_pref,
                detect_and_bypass, is_shortener, is_valid_media_destination,
                season, episode,
            )
            result["fallback_stage"] = note
        elif source == "DeadToons":
            result["resolver_stage"] = "DeadToons → API/mirror unlock → Direct Media"
            media_links = await _resolve_deadtoons_bypass(
                result["final_url"], html, quality_pref, season, episode, is_valid_media_destination,
            )
            if not media_links:
                media_links = await _resolve_generic_page(
                    html, quality_pref, detect_and_bypass, is_shortener, is_valid_media_destination,
                )
        else:
            result["resolver_stage"] = f"{source} → page scan → Direct Media"
            media_links = await _resolve_generic_page(
                html, quality_pref, detect_and_bypass, is_shortener, is_valid_media_destination,
            )

        # Dedupe by URL, collect quality set (Unknown-safe, V3 #4).
        uniq: dict[str, dict] = {}
        for m in media_links:
            uniq.setdefault(m["url"], m)
        media_links = list(uniq.values())
        qualities = sorted({m["quality"] for m in media_links},
                           key=lambda x: {"480p": 0, "720p": 1, "1080p": 2, "4K": 3, "Unknown": 9}.get(x, 9))
        result["media_links"] = media_links
        result["archive_links"] = sorted(set(archive_urls))
        result["qualities"] = qualities
        if media_links:
            bymedia = {}
            for m in media_links:
                bymedia[m.get("provider", "Direct")] = bymedia.get(m.get("provider", "Direct"), 0) + 1
            top_provider = max(bymedia, key=bymedia.get)
            result["provider"] = top_provider

        if not media_links and not archive_urls:
            # V3 #11: failure stage is the real stage, never "done".
            result["stage"] = "extract"
            _fb = result.get("fallback_stage") or ""
            if _fb.startswith("Provider") or _fb.startswith("All provider"):
                # Precise source-side reason beats the generic message.
                result["error"] = _fb
            else:
                result["error"] = "No public download/media/archive links found on this page."
            result["failure_reason"] = result["error"]
            return result
        result["stage"] = "done"
        result["ok"] = True
        return result
    except Exception as e:
        result["stage"] = "extract"
        result["error"] = f"Extraction failed: {e}"
        result["failure_reason"] = result["error"]
        return result
