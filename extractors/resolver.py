"""
Player URL resolver — attempts to extract direct video URLs
from known CDN player pages (Vimeo, StreamWish, FileMoon, etc.).

Also handles shortener bypass: if a URL is behind a link shortener,
it is resolved first before player extraction begins.
"""

from __future__ import annotations

import re
import logging
from urllib.parse import urljoin

from api.models import Quality
from utils.http import http_client
from extractors.shortener import detect_and_bypass, is_shortener

log = logging.getLogger(__name__)


# ── Public API ─────────────────────────────────────────────────────────


async def resolve_player_url(player_url: str) -> dict | None:
    """
    Given a CDN player embed URL (or shortener wrapping one), try to
    extract the direct stream URL.

    Returns: ``{"url": "...", "type": "m3u8|mp4", "quality": "...", "qualities": [...]}`` or ``None``.
    """
    if not player_url:
        return None

    # Step 1: bypass shortener if applicable
    resolved_url = await detect_and_bypass(player_url, http_client=http_client)

    domain = _get_domain(resolved_url)
    log.info("Resolving URL: %s (domain: %s, original: %s)", resolved_url, domain, player_url)

    try:
        if "vimeo.com" in domain:
            result = await _resolve_vimeo(resolved_url)
        elif any(x in domain for x in ("vidstream", "rabbitstream", "megacloud", "as-cdn", "fireplayer", "ravok")):
            result = await _resolve_vidstream_sidecar(resolved_url)
        elif any(x in domain for x in ("xerver.xyz", "mirror.xerver", "vidsrc")):
            result = await _resolve_vidsrc_xerver(resolved_url)
        elif "animedekho" in domain:
            result = await _resolve_animedekho(resolved_url)
        elif any(x in domain for x in ("emturbovid", "turboviplay", "turbosplayer")):
            result = await _resolve_packed_player(resolved_url) or await _resolve_generic(resolved_url)
        elif any(x in domain for x in ("streamwish", "swish", "playerwish")):
            result = await _resolve_packed_player(resolved_url)
        elif any(x in domain for x in ("filemoon", "kerapoxy")):
            result = await _resolve_packed_player(resolved_url)
        elif "doodstream" in domain or "dood." in domain:
            result = await _resolve_dood(resolved_url)
        elif any(x in domain for x in ("streamtape", "strtape")):
            result = await _resolve_streamtape(resolved_url)
        elif "mp4upload" in domain:
            result = await _resolve_packed_player(resolved_url)
        elif "vidguard" in domain or "vgfplay" in domain:
            result = await _resolve_packed_player(resolved_url)
        else:
            # Generic: try packed first, then scan for m3u8/mp4
            result = await _resolve_packed_player(resolved_url) or await _resolve_generic(resolved_url)

        # If we found an m3u8, try to get quality variants (if not already present)
        if result and result["type"] == "m3u8" and not result.get("qualities"):
            qualities = await get_m3u8_qualities(result["url"])
            result["qualities"] = qualities

        return result
    except Exception as e:
        log.warning("Extractor failed for %s: %s", resolved_url, e)
        return None


async def get_m3u8_qualities(m3u8_url: str) -> list[Quality]:
    """
    Fetch an m3u8 URL and parse quality variants from the master playlist.
    If it's not a master playlist (no variants), return a single 'auto' quality.
    """
    try:
        from urllib.parse import urlparse
        domain = urlparse(m3u8_url).netloc
        headers = {}
        if "megacloud" in domain or "rabbit" in domain or "dokicloud" in domain:
            headers["Referer"] = "https://megacloud.tv/"
        elif "vmeas" in domain or "vidmoly" in domain or "vmbox" in domain:
            headers["Referer"] = "https://vidmoly.to/"
        elif "turboviplay" in domain or "turbosplayer" in domain or "emturbovid" in domain:
            headers["Referer"] = "https://emturbovid.com/"
        elif "as-cdn" in domain or "fireplayer" in domain or "ravok" in domain:
            headers["Referer"] = f"https://{domain}/"
        elif domain:
            headers["Referer"] = f"https://{domain}/"
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            
        content = await http_client.get(m3u8_url, headers=headers, ttl=60)
        if not content:
            log.warning("No content returned from m3u8 %s", m3u8_url)
            return [Quality(resolution="auto", url=m3u8_url, label="Auto")]
        # Log full m3u8 for debugging audio tracks
        log.info("Master m3u8 content:\n%s", content[:2000])
        qualities = parse_m3u8_qualities(content, m3u8_url)
        if qualities:
            log.info("Found %d quality variants in master m3u8: %s", len(qualities),
                     ", ".join(q.resolution for q in qualities))
            return qualities
        # Not a master playlist — single quality
        log.info("No variants in m3u8 (single stream): %s", m3u8_url[:80])
        return [Quality(resolution="auto", url=m3u8_url, label="Auto")]
    except Exception as e:
        log.warning("Failed to fetch m3u8 qualities from %s: %s", m3u8_url, e)
        return [Quality(resolution="auto", url=m3u8_url, label="Auto")]


def parse_m3u8_qualities(m3u8_content: str, base_url: str) -> list[Quality]:
    """
    Parse #EXT-X-STREAM-INF lines from a master m3u8 playlist
    to extract available resolutions.
    """
    qualities: list[Quality] = []
    if not m3u8_content:
        return qualities
    lines = m3u8_content.strip().split("\n")

    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue

        # Parse attributes
        bandwidth = 0
        resolution = ""

        bw_match = re.search(r"BANDWIDTH=(\d+)", line)
        if bw_match:
            bandwidth = int(bw_match.group(1))

        res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
        if res_match:
            height = int(res_match.group(2))
            resolution = f"{height}p"
            
        name_match = re.search(r'NAME="([^"]+)"', line)
        if name_match and not resolution:
            resolution = name_match.group(1).lower()

        # Next non-empty, non-comment line is the URL
        url = ""
        for j in range(i + 1, min(i + 6, len(lines))):
            candidate = lines[j].strip()
            if candidate and not candidate.startswith("#"):
                url = candidate
                break

        if not url:
            continue

        # Resolve relative URLs — preserve auth query params from master URL
        if not url.startswith("http"):
            from urllib.parse import urlparse, urlunparse, urlencode, parse_qs
            resolved = urljoin(base_url, url)
            # If master URL had query params (auth tokens), append them
            master_parsed = urlparse(base_url)
            resolved_parsed = urlparse(resolved)
            if master_parsed.query and not resolved_parsed.query:
                resolved = resolved + "?" + master_parsed.query
            url = resolved

        if not resolution:
            # Try to infer from bandwidth (anime encodes are highly compressed)
            if bandwidth > 2_500_000:
                resolution = "1080p"
            elif bandwidth > 1_000_000:
                resolution = "720p"
            elif bandwidth > 500_000:
                resolution = "480p"
            else:
                resolution = "360p"

        label = resolution
        if bandwidth > 0:
            mbps = bandwidth / 1_000_000
            label = f"{resolution} ({mbps:.1f}Mbps)"

        qualities.append(Quality(
            resolution=resolution,
            url=url,
            bandwidth=bandwidth,
            label=label,
            master_url=base_url,
        ))

    # Sort by bandwidth (highest first)
    qualities.sort(key=lambda q: q.bandwidth, reverse=True)
    return qualities


# ── Helpers ────────────────────────────────────────────────────────────


def _get_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.lower()


# ── eval(function(p,a,c,k,e,d)) unpacker ──────────────────────────────


def _unpack_packed_js(html: str) -> str | None:
    """
    Decode Dean Edwards / eval(function(p,a,c,k,e,d){...}) packed JS.

    The packer pattern:
        eval(function(p,a,c,k,e,d){...}('PAYLOAD',RADIX,COUNT,'DICT'.split('|'),...))

    We replicate the unpacking logic in pure Python.
    """
    if not html:
        return None
    # Find all packed blocks in the page
    packed_re = re.compile(
        r"""eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[dr]\s*\)"""
        r"""\s*\{.*?\}\s*\(\s*'(.*?)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\s*\.split\s*\(\s*'([|])'\s*\)""",
        re.DOTALL,
    )

    results: list[str] = []
    for m in packed_re.finditer(html):
        payload = m.group(1)
        radix = int(m.group(2))
        count = int(m.group(3))
        keywords = m.group(4).split(m.group(5))

        try:
            unpacked = _do_unpack(payload, radix, count, keywords)
            if unpacked:
                results.append(unpacked)
        except Exception as e:
            log.debug("Packed JS unpack error: %s", e)

    return "\n".join(results) if results else None


def _do_unpack(payload: str, radix: int, count: int, keywords: list[str]) -> str:
    """Core unpacker: substitute base-N tokens with dictionary words."""

    def _base_n(num: int, base: int) -> str:
        """Convert *num* to a base-*base* string (up to base 36)."""
        if num < 0:
            return "-" + _base_n(-num, base)
        digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if num < base:
            return digits[num]
        return _base_n(num // base, base) + digits[num % base]

    # Build substitution lookup  (token → keyword)
    lookup: dict[str, str] = {}
    for i in range(count):
        token = _base_n(i, radix)
        if i < len(keywords) and keywords[i]:
            lookup[token] = keywords[i]
        else:
            lookup[token] = token

    # Replace word-boundary tokens in the payload
    def replacer(match: re.Match) -> str:
        word = match.group(0)
        return lookup.get(word, word)

    return re.sub(r'\b\w+\b', replacer, payload)


# ── Per-site resolvers ─────────────────────────────────────────────────


async def _resolve_vimeo(url: str) -> dict | None:
    """Vimeo player — extract from config JSON."""
    html = await http_client.get(url, ttl=60)
    m = re.search(r'"hls":\s*\{[^}]*"url":\s*"([^"]+)"', html)
    if m:
        return {"url": m.group(1), "type": "m3u8", "quality": "auto"}
    m = re.search(r'"url":\s*"(https://[^"]+\.mp4[^"]*)"', html)
    if m:
        return {"url": m.group(1), "type": "mp4", "quality": "auto"}
    return None


async def _resolve_packed_player(url: str) -> dict | None:
    """
    Generic packed/obfuscated player resolver.

    1. Fetch the page.
    2. Try to find m3u8/mp4 URLs in plain HTML.
    3. If not found, unpack any eval(function(p,a,c,k,e,d)...) blocks
       and search the unpacked JS for stream URLs.
    """
    html = await http_client.get(url, ttl=60)
    if not html:
        return None

    # Try plain-text patterns first
    result = _scan_for_stream(html)
    if result:
        return result

    # Unpack obfuscated JS and scan again
    unpacked = _unpack_packed_js(html)
    if unpacked:
        log.debug("Unpacked %d chars of packed JS from %s", len(unpacked), url[:60])
        result = _scan_for_stream(unpacked)
        if result:
            return result

    return None


def _scan_for_stream(text: str) -> dict | None:
    """Scan *text* for m3u8/mp4 stream URLs.
    
    Priority: master playlist > generic m3u8 > mp4.
    Collects ALL m3u8 URLs and picks the best one (master playlist preferred).
    """
    if not text:
        return None
    all_m3u8: list[str] = []
    
    # Collect ALL m3u8 URLs from common patterns
    m3u8_patterns = [
        r'file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'source\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'src\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r"file\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
        r"source\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
        r'sources\s*:\s*\[\s*\{[^}]*file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'"(https?://[^"]+\.m3u8[^"]*)"',
        r"'(https?://[^']+\.m3u8[^']*)'",
    ]
    
    seen = set()
    for pattern in m3u8_patterns:
        for m in re.finditer(pattern, text):
            url = m.group(1)
            if url not in seen:
                seen.add(url)
                all_m3u8.append(url)
    
    if all_m3u8:
        # Prefer master/index playlists (these contain quality variants)
        master_keywords = ("master", "index", "playlist", "main")
        # Also deprioritize URLs that look like specific quality variants
        variant_keywords = ("240", "360", "480", "720", "1080", "/chunk", "/seg")
        
        def _score(url: str) -> int:
            """Higher score = more likely to be master playlist."""
            u = url.lower()
            score = 0
            # Boost for master playlist indicators
            for kw in master_keywords:
                if kw in u:
                    score += 10
            # Penalize URLs that look like quality-specific variants
            for kw in variant_keywords:
                if kw in u:
                    score -= 5
            return score
        
        best = max(all_m3u8, key=_score)
        log.info("Found %d m3u8 URLs, selected best: %s", len(all_m3u8), best[:100])
        if len(all_m3u8) > 1:
            log.debug("All m3u8 URLs found: %s", [u[:80] for u in all_m3u8])
        return {"url": best, "type": "m3u8", "quality": "auto"}
    
    # Fallback: mp4 URLs
    mp4_patterns = [
        r'file\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r'source\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
    ]
    for pattern in mp4_patterns:
        m = re.search(pattern, text)
        if m:
            return {"url": m.group(1), "type": "mp4", "quality": "auto"}
    
    return None


async def _resolve_dood(url: str) -> dict | None:
    """Doodstream — these change rapidly, return embed URL."""
    # Dood uses dynamic token generation, hard to extract without JS
    return None


async def _resolve_streamtape(url: str) -> dict | None:
    """Streamtape extractor."""
    html = await http_client.get(url, ttl=60)
    if not html:
        return None
    m = re.search(
        r"getElementById\('robotlink'\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*\('([^']+)'\)",
        html,
    )
    if m:
        direct = "https:" + m.group(1) + m.group(2)
        return {"url": direct, "type": "mp4", "quality": "auto"}
    return None


async def _resolve_generic(url: str) -> dict | None:
    """Last resort — scan page for any m3u8/mp4 URL."""
    html = await http_client.get(url, ttl=60)
    if not html:
        return None
    return _scan_for_stream(html)

async def _resolve_vidstream_sidecar(url: str) -> dict | None:
    """
    Resolve VidStream / FirePlayer URLs (as-cdn*.top and similar).
    
    Calls the FirePlayer getVideo API to obtain the master m3u8 with all qualities.
    Falls back to the Node.js sidecar for rabbitstream/megacloud domains.
    """
    import os
    from urllib.parse import quote, urlparse
    
    parsed = urlparse(url)
    domain = parsed.netloc
    
    # FirePlayer domains (as-cdn*.top pattern, ravok.buzz, etc.) — use direct API
    if any(x in domain for x in ("as-cdn", "fireplayer", "ravok")):
        return await _resolve_fireplayer(url)
    
    # RabbitStream/MegaCloud — use Node.js sidecar
    sidecar_url = os.environ.get("VIDSTREAM_API_URL", "http://localhost:4030")
    
    try:
        res = await http_client.get_json(f"{sidecar_url}/decrypt?url={quote(url)}")
        
        if not res or not res.get("sources"):
            return None
            
        source = res["sources"][0]
        stream_url = source.get("file")
        if not stream_url:
            return None
            
        vtype = "m3u8" if "hls" in source.get("type", "").lower() or ".m3u8" in stream_url else "mp4"
        return {"url": stream_url, "type": vtype, "quality": "auto"}
    except Exception as e:
        log.warning("VidStream sidecar extraction failed for %s: %s", url, e)
        return None


async def _resolve_fireplayer(url: str) -> dict | None:
    """
    Extract stream from FirePlayer (as-cdn*.top) by calling its getVideo API.
    Returns master m3u8 URL with all quality variants.
    """
    from urllib.parse import urlparse
    
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    # Extract video ID from URL path: /video/{id} or /embed/{id}
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) < 2:
        return None
    video_id = path_parts[-1]
    
    try:
        api_url = f"{base_url}/player/index.php?data={video_id}&do=getVideo"
        headers = {
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": base_url,
        }
        post_data = {"hash": video_id, "r": "https://animedekho.tv/"}
        
        import json
        res_text = await http_client.post_no_cache(api_url, data=post_data, headers=headers)
        
        if not res_text:
            return None
        
        res = json.loads(res_text)
        stream_url = res.get("videoSource") or res.get("securedLink")
        if not stream_url:
            return None
        
        vtype = "m3u8" if ".m3u8" in stream_url or res.get("hls") else "mp4"
        log.info("FirePlayer resolved: %s (type: %s)", stream_url, vtype)
        return {"url": stream_url, "type": vtype, "quality": "auto"}
    except Exception as e:
        log.warning("FirePlayer extraction failed for %s: %s", url, e)
        return None


async def _resolve_vidsrc_xerver(url: str) -> dict | None:
    """
    Extract stream from VidSrc (mirror.xerver.xyz / xerver.xyz).
    Calls fetch=1 endpoint to obtain direct MP4 stream or mirrors.
    """
    from urllib.parse import urlparse, parse_qs, quote
    import json

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    qs = parse_qs(parsed.query)
    encrypted_url = qs.get("url", [""])[0]

    if not encrypted_url:
        html = await http_client.get(url, headers={"Referer": "https://animedekho.tv/"})
        m = re.search(r'ENCRYPTED_URL\s*=\s*["\']([^"\']+)["\']', html)
        if m:
            encrypted_url = m.group(1)

    if not encrypted_url:
        return None

    fetch_url = f"{base_url}{parsed.path}?url={quote(encrypted_url)}&fetch=1"
    headers = {
        "Referer": url,
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    try:
        resp_text = await http_client.get_no_cache(fetch_url, headers=headers)
        if not resp_text:
            return None
        data = json.loads(resp_text)
        results = data.get("results", {})

        # Priority: instant_dl -> cloud_r2 -> direct_mgt
        for key in ("instant_dl", "cloud_r2", "direct_mgt"):
            src = results.get(key)
            if src and isinstance(src, dict) and src.get("url"):
                stream_url = src["url"]
                vtype = "m3u8" if ".m3u8" in stream_url else "mp4"
                log.info("VidSrc resolved: %s (type: %s, source: %s)", stream_url[:80], vtype, key)
                return {"url": stream_url, "type": vtype, "quality": "auto"}
    except Exception as e:
        log.warning("VidSrc xerver extraction failed for %s: %s", url, e)

    return None


async def _resolve_animedekho(url: str) -> dict | None:
    """
    Extract stream from AnimeDekho player/embed/api endpoints.
    Handles:
      - /aaa/myth/play2.php or /aaa/myth/fetch.php (NeoCDN)
      - /aaa/get/play.php or /aaa/get/file.php (direct GDrive/UserContent via _resolve_vidsrc_xerver)
      - /player.php?url=... (iframes play.php or other CDN)
      - /download.php?url=... or /download2.php?url=... (links to file.php or gdflix)
      - /embed/... (iframes ravok.buzz or other player)
    """
    from urllib.parse import urlparse, quote, urljoin
    import json

    parsed = urlparse(url)
    path = parsed.path
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # 1. NeoCDN (play2.php)
    if "play2.php" in path or "/aaa/myth/" in path:
        try:
            html = await http_client.get(url, headers={"Referer": f"{base_url}/"})
            m = re.search(r'/aaa/myth/fetch\.php\?id=([a-zA-Z0-9_-]+)', html or "")
            if m:
                fetch_url = f"{base_url}/aaa/myth/fetch.php?id={m.group(1)}"
                resp_text = await http_client.get(fetch_url, headers={"Referer": url})
                if resp_text:
                    resp = json.loads(resp_text)
                    if resp and resp.get("sources"):
                        worker = "https://young-dust-31f3.reimoto.workers.dev/?url="
                        qualities = []
                        for s in resp["sources"]:
                            q_res = s.get("type", "auto")
                            q_url = worker + quote(s.get("url", ""))
                            qualities.append(Quality(
                                resolution=q_res,
                                url=q_url,
                                label=f"{q_res} - {s.get('size', '')}".strip(" -"),
                            ))
                        if qualities:
                            return {
                                "url": qualities[-1].url,
                                "type": "mp4",
                                "quality": qualities[-1].resolution,
                                "qualities": qualities,
                            }
        except Exception as e:
            log.warning("AnimeDekho play2 extraction failed for %s: %s", url, e)

    # 2. VidSrc / GDFlix get endpoint (aaa/get/play.php or aaa/get/file.php)
    if any(p in path for p in ("play.php", "file.php")) and ("/aaa/get/" in path or "url=" in parsed.query):
        return await _resolve_vidsrc_xerver(url)

    # 3. AnimeDekho player wrapper (/player.php?url=...)
    if "player.php" in path:
        try:
            html = await http_client.get(url, headers={"Referer": f"{base_url}/"})
            if html:
                iframe_m = re.search(r'<iframe[^>]*src\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
                if iframe_m:
                    iframe_src = iframe_m.group(1)
                    if iframe_src.startswith("/"):
                        iframe_src = urljoin(base_url, iframe_src)
                    return await resolve_player_url(iframe_src)
        except Exception as e:
            log.warning("AnimeDekho player.php extraction failed for %s: %s", url, e)

    # 4. AnimeDekho embed wrapper (/embed/{post_id}/{ep})
    if "/embed/" in path:
        try:
            html = await http_client.get(url, headers={"Referer": f"{base_url}/"})
            if html:
                iframe_m = re.search(r'<iframe[^>]*src\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
                if iframe_m:
                    iframe_src = iframe_m.group(1)
                    if iframe_src.startswith("/"):
                        iframe_src = urljoin(base_url, iframe_src)
                    return await resolve_player_url(iframe_src)
        except Exception as e:
            log.warning("AnimeDekho embed extraction failed for %s: %s", url, e)

    # 5. AnimeDekho download wrapper (/download.php or /download2.php)
    if "download.php" in path or "download2.php" in path:
        try:
            html = await http_client.get(url, headers={"Referer": f"{base_url}/"})
            if html:
                href_m = re.search(r'href\s*=\s*["\'](https?://[^"\']*(?:file\.php|play\.php|gdflix|hubcloud)[^"\']*)["\']', html, re.IGNORECASE)
                if href_m:
                    return await resolve_player_url(href_m.group(1))
        except Exception as e:
            log.warning("AnimeDekho download page extraction failed for %s: %s", url, e)

    return None
