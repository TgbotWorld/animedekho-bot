"""
Link shortener bypass module.

Bypasses these shortener families:
  - gplinks.co (+ gplinks.in, gplink.in)
  - vshort.in (+ vshort.me) 
  - cuty.io (+ cutt.ly variants)

All three use similar patterns:
  1. GET the short URL → page with countdown/ad
  2. Extract a hidden token/form or encoded destination
  3. POST or follow redirect to get the real URL
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from urllib.parse import urlparse, urljoin, parse_qs, unquote

log = logging.getLogger(__name__)

# ── Domain detection ───────────────────────────────────────────────────

_SHORTENER_DOMAINS: dict[str, str] = {
    # domain fragment → handler key
    "gplinks.co": "gplinks",
    "gplinks.in": "gplinks",
    "gplink.in": "gplinks",
    "gplinks.com": "gplinks",
    "vshort.in": "vshort",
    "vshort.me": "vshort",
    "vshort.xyz": "vshort",
    "vshortener.com": "vshort",
    "cuty.io": "cuty",
    "cutt.ly": "cuty",
    "cuty.me": "cuty",
    "droplink.co": "droplink",
    "droplink.net": "droplink",
    "shrinkme.io": "shrinkme",
    "shrinkme.net": "shrinkme",
    "shrinke.me": "shrinkme",
    "shrinkme.click": "shrinkme",
    "shrinkme.cc": "shrinkme",
    "shrinkme.vip": "shrinkme",
    "shareus.io": "shareus",
    "shareus.in": "shareus",
    "ouo.io": "ouo",
    "ouo.press": "ouo",
    "exe.io": "exe",
    "exey.io": "exe",
    "exe.app": "exe",
    "filepress.site": "filepress",
    "filepress.store": "filepress",
    "codedew.com": "codedew",
    "archive.toonworld4all.me": "generic",
}

_AD_AND_TRACKING_DOMAINS = {
    "doubleclick.net", "googleadservices.com", "adnxs.com", "popcash.net",
    "popads.net", "propellerads.com", "exoclick.com", "adsterra.com",
    "trafficjunky.com", "bet365.com", "1xbet.com", "dafabet.com",
    "yllix.com", "clickadu.com", "adtrue.com",
}

_KNOWN_EMBED_HOSTS = (
    "streamwish", "playerwish", "filemoon", "kerapoxy", "vidstream",
    "rabbitstream", "megacloud", "vidsrc", "xerver.xyz", "turboviplay",
    "turbosplayer", "emturbovid", "doodstream", "dood.", "streamtape",
    "strtape", "mp4upload", "vidguard", "vgfplay",
)


def detect_protection_challenge(html: str) -> str | None:
    """
    Detect anti-bot CAPTCHA or Cloudflare Turnstile challenge on the page (Issue #19).
    Returns challenge name if detected, else None.
    """
    if not html:
        return None
    if "challenges.cloudflare.com/turnstile" in html or "cf-turnstile" in html:
        return "Cloudflare Turnstile"
    if "google.com/recaptcha" in html or "class=\"g-recaptcha\"" in html or "class='g-recaptcha'" in html or "grecaptcha" in html:
        return "Google reCAPTCHA"
    if "hcaptcha.com" in html or "class=\"h-captcha\"" in html or "class='h-captcha'" in html:
        return "hCaptcha"
    if "cf-browser-verification" in html or "cf_chl_prog" in html or ("Just a moment..." in html and "Cloudflare" in html):
        return "Cloudflare Challenge"
    return None


def is_valid_media_destination(url: str) -> bool:
    """
    Verify that resolved URL is not a known tracking/ad URL, shortener,
    or intermediate HTML landing/redirect page (Issue #19, #21).
    """
    if not url or not url.startswith("http"):
        return False
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()

        # Cannot be a shortener
        if is_shortener(url):
            return False

        # Cannot be an ad/tracking domain
        if any(ad_dom in host for ad_dom in _AD_AND_TRACKING_DOMAINS):
            return False

        # Cannot be an intermediate redirect script or archive link
        if "redirect/main.php" in path or "archive.toonworld4all" in host:
            return False

        # Cannot be an intermediate HubCloud drive landing page
        if ("hubcloud" in host or "gamerxyt" in host) and ("/drive/" in path or "hubcloud.php" in path):
            return False

        # If it's a known embed host or has /embed/ or ?trembed=,
        # it is only valid if it points to a direct stream (.m3u8, .mp4, etc.)
        is_embed_pattern = (
            any(eh in host for eh in _KNOWN_EMBED_HOSTS)
            or "/embed/" in path
            or "/e/" in path
            or "trembed" in parsed.query
        )
        if is_embed_pattern:
            is_direct_stream = any(ext in path for ext in (".m3u8", ".mp4", ".mkv", ".webm", "/hls/", "/stream/"))
            if not is_direct_stream:
                return False

        return True
    except Exception:
        return False


def is_shortener(url: str) -> bool:
    """Return True if the URL belongs to a known shortener domain or redirector."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if any(domain in host for domain in _SHORTENER_DOMAINS):
            return True
        if "toonworld4all" in host and "redirect" in path:
            return True
        if any(k in host for k in ("bit.ly", "tinyurl.com", "linkvertise.com")):
            return True
        return False
    except Exception:
        return False


def _identify(url: str) -> str | None:
    """Return handler key for the URL, or None."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        for domain, key in _SHORTENER_DOMAINS.items():
            if domain in host:
                return key
        if "toonworld4all" in host and "redirect" in path:
            return "generic"
        if any(k in host for k in ("bit.ly", "tinyurl.com", "linkvertise.com")):
            return "generic"
    except Exception:
        pass
    return None


# ── Main entry points ──────────────────────────────────────────────────


async def bypass_shortener(url: str, *, http_client=None) -> str | None:
    """
    Bypass a link shortener and return the final destination URL.
    Returns None if bypass failed.
    """
    if http_client is None:
        from utils.http import http_client as _hc
        http_client = _hc

    handler = _identify(url)
    log.info("Shortener bypass [%s] for %s", handler, url)

    try:
        res = None
        if handler == "gplinks":
            res = await _bypass_gplinks(url, http_client)
        elif handler == "vshort":
            res = await _bypass_vshort(url, http_client)
        elif handler == "cuty":
            res = await _bypass_cuty(url, http_client)
        elif handler == "droplink":
            res = await _bypass_droplink(url, http_client)
        elif handler == "shrinkme":
            res = await _bypass_shrinkme(url, http_client)
        elif handler == "shareus":
            res = await _bypass_shareus(url, http_client)
        elif handler == "ouo":
            res = await _bypass_ouo(url, http_client)
        elif handler == "exe":
            res = await _bypass_exe(url, http_client)
        elif handler == "filepress":
            res = await _bypass_filepress(url, http_client)
        elif handler == "codedew":
            res = await _bypass_codedew(url, http_client)
        else:
            res = await _bypass_generic(url, http_client)

        if res and is_valid_media_destination(res):
            return res
        elif res and is_shortener(res) and res != url:
            log.info("Chained shortener detected: %s -> %s, following...", url[:60], res[:60])
            chained = await bypass_shortener(res, http_client=http_client)
            if chained and is_valid_media_destination(chained):
                return chained
        elif res:
            log.info("Shortener returned target destination: %s", res[:80])
            return res
        return None
    except Exception as e:
        log.warning("Shortener bypass failed for %s: %s", url, e)
        return None


async def detect_and_bypass(url: str, *, http_client=None) -> str:
    """
    If *url* is a known shortener or redirector, bypass it and return the destination.
    Otherwise return *url* unchanged. Safe to call on any URL.
    """
    if is_shortener(url) or "redirect" in url.lower():
        resolved = await bypass_shortener(url, http_client=http_client)
        if resolved:
            log.info("Bypassed: %s → %s", url[:60], resolved[:60])
            return resolved
        log.warning("Bypass returned None for %s, returning original", url[:60])
    return url


# ── GPLinks bypass ─────────────────────────────────────────────────────


async def _bypass_gplinks(url: str, http_client) -> str | None:
    """
    GPLinks.co bypass flow:
    
    1. GET the short URL → HTML page with encoded data
    2. Page contains a JS variable or hidden form with base64-encoded URL
       OR a /go endpoint that accepts a POST with token
    3. Sometimes uses intermediate page with countdown timer
    
    GPLinks typically:
    - Sets cookies on first visit
    - Has a "go" form with _token field  
    - May use atob() for the destination
    - Sometimes embeds URL in a script as encoded string
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Step 1: GET the shortener page
    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
        "Accept": "text/html,application/xhtml+xml",
    })
    if not html:
        return None

    # Strategy 1: Look for the go form with _token
    # GPLinks uses a form that POSTs to the same URL with a _token
    dest = await _gplinks_form_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 2: atob() / base64 encoded destination in JS
    dest = _extract_atob(html)
    if dest:
        return dest

    # Strategy 3: Look for encoded URL in script tags
    # GPLinks sometimes stores the URL in a var like: var url = "base64string"
    dest = _extract_encoded_var(html)
    if dest:
        return dest

    # Strategy 4: meta refresh
    dest = _extract_meta_refresh(html)
    if dest:
        return dest

    # Strategy 5: window.location redirect
    dest = _extract_js_redirect(html)
    if dest:
        return dest

    return None


async def _gplinks_form_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """
    GPLinks form-based bypass:
    Find the form with _token, extract all hidden inputs,
    POST to the form action, then extract redirect from response.
    """
    if not html:
        return None
    # Find CSRF token
    token_match = re.search(
        r'name=["\']_token["\']\s*value=["\']([^"\']+)["\']', html
    ) or re.search(
        r'value=["\']([^"\']+)["\']\s*name=["\']_token["\']', html
    )
    if not token_match:
        # Also try meta tag csrf
        token_match = re.search(
            r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html
        )
    
    if not token_match:
        return None

    token = token_match.group(1)

    # Find all hidden inputs
    data = {"_token": token}
    for m in re.finditer(
        r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        html, re.IGNORECASE
    ):
        data[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\'][^>]*type=["\']hidden["\']',
        html, re.IGNORECASE
    ):
        data[m.group(1)] = m.group(2)

    # Find form action
    form_match = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', html, re.IGNORECASE)
    action = form_match.group(1) if form_match else page_url
    if not action.startswith("http"):
        action = urljoin(page_url, action)

    log.debug("GPLinks form POST to %s with %d fields", action, len(data))

    try:
        resp_html, final_url = await http_client.post_follow_redirects(
            action, data=data, headers={
                "Referer": page_url,
                "Origin": base,
            }
        )

        # If we got redirected to a non-shortener URL, that's our destination
        if final_url and not is_shortener(final_url):
            return final_url

        # Otherwise parse the response for the destination
        for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect, _extract_encoded_var):
            dest = extractor(resp_html)
            if dest:
                return dest

    except Exception as e:
        log.warning("GPLinks form POST failed: %s", e)

    return None


# ── VShort bypass ──────────────────────────────────────────────────────


async def _bypass_vshort(url: str, http_client) -> str | None:
    """
    VShort.in bypass flow:
    
    1. GET the short URL → countdown page
    2. Page has either:
       a) A hidden form that submits after countdown
       b) An AJAX call to an API endpoint that returns the destination  
       c) Base64-encoded URL in inline script
    3. May need to wait/simulate the countdown via a second request
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Strategy 0: Direct embedded url in query string (e.g. vshort.xyz/full?api=...&url=aHR0cHM...)
    qs = parse_qs(parsed.query)
    if "url" in qs:
        try:
            raw_url = qs["url"][0]
            dec = unquote(base64.b64decode(unquote(raw_url)).decode('utf-8'))
            if dec.startswith("http"):
                log.info("vshort query parameter directly decoded to: %s", dec[:60])
                return dec
        except Exception:
            pass

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
    })
    if not html:
        return None

    # Strategy 1: Look for API/AJAX endpoint that returns the link
    # VShort often has: $.ajax({ url: '/links/go', data: {id: X, token: Y} })
    dest = await _vshort_ajax_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 2: Hidden form with token (similar to GPLinks)
    dest = await _vshort_form_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 3: atob / base64
    dest = _extract_atob(html)
    if dest:
        return dest

    # Strategy 4: Encoded URL in a JS var or data attribute
    dest = _extract_encoded_var(html)
    if dest:
        return dest

    # Strategy 5: meta refresh / JS redirect
    dest = _extract_meta_refresh(html)
    if dest:
        return dest
    dest = _extract_js_redirect(html)
    if dest:
        return dest

    return None


async def _vshort_ajax_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """
    VShort AJAX bypass: find the go/redirect API call, extract params, call it directly.
    """
    if not html:
        return None

    # Look for AJAX URL patterns
    # Pattern: url: '/links/go' or '/api/links/go' etc.
    ajax_url_match = re.search(
        r"""(?:url|action)\s*:\s*['"](/[^'"]*(?:links|go|redirect)[^'"]*)['"]\s*""",
        html
    )
    
    # Look for the link ID / alias
    id_match = re.search(r'(?:id|alias|link_id)\s*:\s*["\']?(\w+)["\']?', html)
    token_match = re.search(
        r'name=["\']_token["\']\s*value=["\']([^"\']+)["\']', html
    ) or re.search(
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html
    )

    if ajax_url_match and (id_match or token_match):
        ajax_path = ajax_url_match.group(1)
        ajax_url = base + ajax_path

        data = {}
        if id_match:
            data["id"] = id_match.group(1)
        if token_match:
            data["_token"] = token_match.group(1)

        try:
            resp = await http_client.post_no_cache(ajax_url, data=data, headers={
                "Referer": page_url,
                "X-Requested-With": "XMLHttpRequest",
            })
            if not resp:
                return None
            
            # Response might be JSON with url field
            try:
                j = json.loads(resp)
                if isinstance(j, dict):
                    dest = j.get("url") or j.get("redirect") or j.get("link") or j.get("destination")
                    if dest and dest.startswith("http"):
                        return dest
            except (json.JSONDecodeError, ValueError):
                pass

            # Or it might be the URL directly
            if resp.strip().startswith("http"):
                return resp.strip()

            # Or HTML with redirect
            for extractor in (_extract_meta_refresh, _extract_js_redirect, _extract_atob):
                dest = extractor(resp)
                if dest:
                    return dest

        except Exception as e:
            log.debug("VShort AJAX bypass failed: %s", e)

    return None


async def _vshort_form_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """VShort form bypass — similar to GPLinks form approach."""
    if not html:
        return None
    # Find all forms
    forms = re.finditer(
        r'<form[^>]*action=["\']([^"\']*)["\'][^>]*>(.*?)</form>',
        html, re.DOTALL | re.IGNORECASE
    )

    for form_match in forms:
        action = form_match.group(1)
        form_body = form_match.group(2)

        if not action.startswith("http"):
            action = urljoin(page_url, action)

        # Skip if action is to external ad/tracking
        action_host = urlparse(action).netloc.lower()
        page_host = urlparse(page_url).netloc.lower()
        if action_host and action_host != page_host:
            continue

        # Extract hidden inputs
        data = {}
        for inp in re.finditer(
            r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(1)] = inp.group(2)
        for inp in re.finditer(
            r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(2)] = inp.group(1)

        if not data:
            continue

        try:
            resp_html, final_url = await http_client.post_follow_redirects(
                action, data=data, headers={
                    "Referer": page_url,
                    "Origin": base,
                }
            )

            if final_url and not is_shortener(final_url):
                return final_url

            for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect):
                dest = extractor(resp_html)
                if dest:
                    return dest
        except Exception as e:
            log.debug("VShort form POST failed: %s", e)

    return None


# ── Cuty.io bypass ─────────────────────────────────────────────────────


async def _bypass_cuty(url: str, http_client) -> str | None:
    """
    Cuty.io bypass flow:
    
    1. GET the short URL → page with obfuscated JS
    2. Cuty typically uses:
       a) A multi-step redirect with cookies
       b) Encoded destination in inline script (often double-base64 or reversed)
       c) An API call after countdown
    3. The real URL is often in a data-url attribute or encoded variable
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
    })
    if not html:
        return None

    # Strategy 1: Look for data-url or data-href attributes
    dest = _extract_data_attributes(html)
    if dest:
        return dest

    # Strategy 2: Cuty often has the URL in a script with specific patterns
    dest = _extract_cuty_script(html)
    if dest:
        return dest

    # Strategy 3: atob / base64
    dest = _extract_atob(html)
    if dest:
        return dest

    # Strategy 4: Form-based (similar to others)
    dest = await _cuty_form_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 5: Try AJAX endpoint
    dest = await _cuty_ajax_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 6: meta refresh / JS redirect
    dest = _extract_meta_refresh(html)
    if dest:
        return dest
    dest = _extract_js_redirect(html)
    if dest:
        return dest

    return None


def _extract_cuty_script(html: str) -> str | None:
    """
    Cuty.io specific: look for encoded URL patterns in their scripts.
    They often use patterns like:
    - var href = atob(atob("doublebase64"))
    - String.fromCharCode() arrays
    - Reversed strings
    """
    if not html:
        return None
    # Double base64 pattern: atob(atob("..."))
    for m in re.finditer(r'atob\s*\(\s*atob\s*\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)\s*\)', html):
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            decoded2 = base64.b64decode(decoded).decode("utf-8", errors="ignore")
            if decoded2.startswith("http"):
                return decoded2
        except Exception:
            continue

    # String.fromCharCode pattern
    for m in re.finditer(r'String\.fromCharCode\s*\(([\d,\s]+)\)', html):
        try:
            chars = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
            decoded = "".join(chr(c) for c in chars)
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    # Reversed string pattern: "...".split("").reverse().join("")
    for m in re.finditer(r'["\']([^"\']{20,})["\']\.split\s*\(\s*["\']["\'].*?\.reverse\s*\(\s*\)\.join', html):
        try:
            reversed_str = m.group(1)[::-1]
            if reversed_str.startswith("http"):
                return reversed_str
        except Exception:
            continue

    return None


def _extract_data_attributes(html: str) -> str | None:
    """Extract destination from data-url, data-href, data-link attributes."""
    if not html:
        return None
    for attr in ("data-url", "data-href", "data-link", "data-redirect"):
        m = re.search(rf'{attr}\s*=\s*["\']([^"\']+)["\']', html)
        if m:
            val = m.group(1)
            # Might be base64 encoded
            if not val.startswith("http"):
                try:
                    decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                    if decoded.startswith("http"):
                        return decoded
                except Exception:
                    pass
            else:
                if not is_shortener(val):
                    return val
    return None


async def _cuty_form_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """Cuty form bypass — extract and submit any redirect forms."""
    if not html:
        return None
    forms = re.finditer(
        r'<form[^>]*action=["\']([^"\']*)["\'][^>]*method=["\']post["\'][^>]*>(.*?)</form>',
        html, re.DOTALL | re.IGNORECASE
    )

    for form_match in forms:
        action = form_match.group(1)
        form_body = form_match.group(2)

        if not action.startswith("http"):
            action = urljoin(page_url, action)

        data = {}
        for inp in re.finditer(
            r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(1)] = inp.group(2)
        for inp in re.finditer(
            r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(2)] = inp.group(1)

        if not data:
            continue

        try:
            resp_html, final_url = await http_client.post_follow_redirects(
                action, data=data, headers={
                    "Referer": page_url,
                    "Origin": base,
                }
            )
            if final_url and not is_shortener(final_url):
                return final_url

            for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect):
                dest = extractor(resp_html)
                if dest:
                    return dest
        except Exception as e:
            log.debug("Cuty form POST failed: %s", e)

    return None


async def _cuty_ajax_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """Cuty AJAX bypass — look for API endpoints in the scripts."""
    if not html:
        return None

    # Look for fetch/ajax calls
    api_match = re.search(
        r"""(?:fetch|ajax|post)\s*\(\s*['"](/[^'"]*(?:go|redirect|link|click)[^'"]*)['"]\s*""",
        html
    )
    if not api_match:
        return None

    api_path = api_match.group(1)
    api_url = base + api_path

    # Extract any token/id
    token_match = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html)
    id_match = re.search(r'["\'](?:id|alias|code)["\']:\s*["\'](\w+)["\']', html)

    data = {}
    if token_match:
        data["_token"] = token_match.group(1)
    if id_match:
        data["id"] = id_match.group(1)

    if not data:
        return None

    try:
        resp = await http_client.post_no_cache(api_url, data=data, headers={
            "Referer": page_url,
            "X-Requested-With": "XMLHttpRequest",
        })
        if not resp:
            return None

        try:
            j = json.loads(resp)
            if isinstance(j, dict):
                dest = j.get("url") or j.get("redirect") or j.get("link") or j.get("destination")
                if dest and dest.startswith("http"):
                    return dest
        except (json.JSONDecodeError, ValueError):
            pass

        if resp.strip().startswith("http"):
            return resp.strip()

    except Exception as e:
        log.debug("Cuty AJAX bypass failed: %s", e)

    return None


# ── Generic bypass (fallback) ──────────────────────────────────────────


def _extract_landing_links(html: str, base_url: str = "") -> str | None:
    """Extract destination links from intermediate landing/redirect pages."""
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        target_keywords = (
            "hubcloud", "filepress", "drive.google.com", "mega.nz",
            "streamwish", "playerwish", "filemoon", "vidstream",
            "pixeldrain", "workers.dev", "droplink", "shrinkme",
            "gplinks", "shareus", "ouo"
        )
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if any(k in href.lower() for k in target_keywords):
                if not href.startswith("http") and base_url:
                    href = urljoin(base_url, href)
                return href

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            txt = a.get_text(" ", strip=True).lower()
            if any(w in txt for w in ("download", "get link", "proceed", "continue", "direct")):
                if href.startswith("http") and not any(ad in href.lower() for ad in _AD_AND_TRACKING_DOMAINS):
                    return href
    except Exception:
        pass
    return None


async def _bypass_generic(url: str, http_client) -> str | None:
    """Generic bypass: tries query param decoding, HTTP redirects, and HTML extraction."""
    # 1. First check if destination is directly encoded in query params
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in ("url", "link", "target", "dest", "destination", "r", "to", "go"):
            if key in qs:
                for val in qs[key]:
                    val = unquote(val)
                    if val.startswith("http") and (is_valid_media_destination(val) or is_shortener(val)):
                        return val
                    try:
                        decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                        if decoded.startswith("http"):
                            return decoded
                    except Exception:
                        pass
    except Exception:
        pass

    # 2. Try following HTTP redirects
    html = None
    try:
        final_url, resp_html = await http_client.get_with_redirects(url)
        if final_url and final_url != url and (is_valid_media_destination(final_url) or is_shortener(final_url)):
            return final_url
        html = resp_html
    except Exception:
        try:
            html = await http_client.get_text_no_cache(url)
        except Exception:
            html = None

    if not html:
        return None

    # 3. Check for anti-bot challenge
    challenge = detect_protection_challenge(html)
    if challenge:
        log.warning("[generic] Interactive %s challenge on %s", challenge, url)
        return None

    # 4. Try extractors in priority order
    for extractor in (_extract_meta_refresh, _extract_atob, _extract_encoded_var,
                      _extract_data_attributes, _extract_js_redirect):
        dest = extractor(html)
        if dest:
            return dest

    # 5. Extract links from landing page HTML
    landing_dest = _extract_landing_links(html, url)
    if landing_dest:
        return landing_dest

    return None


# ── Shared extraction strategies ───────────────────────────────────────


def _extract_meta_refresh(html: str) -> str | None:
    """Extract URL from <meta http-equiv="refresh" ...> tag."""
    if not html:
        return None
    m = re.search(
        r'<meta[^>]*http-equiv\s*=\s*["\']refresh["\'][^>]*content\s*=\s*["\'][^"\']*url\s*=\s*([^"\'>\s]+)',
        html, re.IGNORECASE,
    )
    if m:
        dest = m.group(1).strip()
        if dest.startswith("http"):
            return dest
    return None


def _extract_atob(html: str) -> str | None:
    """Decode atob('BASE64') calls and raw base64 strings that decode to URLs."""
    if not html:
        return None
    # Explicit atob('...')
    for m in re.finditer(r'atob\s*\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)', html):
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    # Long base64 strings that might be URLs
    for m in re.finditer(r'["\']([A-Za-z0-9+/=]{40,})["\']', html):
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    return None


def _extract_js_redirect(html: str) -> str | None:
    """Extract URL from window.location / location.href assignments."""
    if not html:
        return None
    patterns = [
        r'(?:window|document)\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
        r'location\.replace\s*\(\s*["\']([^"\']+)["\']\s*\)',
        r'location\.assign\s*\(\s*["\']([^"\']+)["\']\s*\)',
        r'window\.open\s*\(\s*["\']([^"\']+)["\']\s*[,)]',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            url = m.group(1)
            if url.startswith("http"):
                return url
    return None


def _extract_encoded_var(html: str) -> str | None:
    """
    Look for JS variables containing encoded/escaped URLs.
    Patterns like: var link = "aHR0cHM6Ly..." or var url = decodeURIComponent("...")
    """
    if not html:
        return None
    # decodeURIComponent pattern
    for m in re.finditer(r'decodeURIComponent\s*\(\s*["\']([^"\']+)["\']\s*\)', html):
        try:
            decoded = unquote(m.group(1))
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    # Hex-escaped string: "\x68\x74\x74\x70..."
    for m in re.finditer(r'["\']((\\x[0-9a-fA-F]{2}){10,})["\']', html):
        try:
            decoded = m.group(1).encode().decode("unicode_escape")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    return None


# ── Additional Dedicated Shortener Bypasses (Issue #19) ───────────────


async def _bypass_adlinkfly(url: str, http_client, handler_name: str) -> str | None:
    """
    AdLinkFly-based shortener bypass (DropLink, ShrinkMe, Exe.io).
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    if not html:
        return None

    challenge = detect_protection_challenge(html)
    if challenge:
        log.warning("[%s] Unsupported interactive %s challenge on %s", handler_name, challenge, url)
        return None

    for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect, _extract_encoded_var, _extract_data_attributes):
        dest = extractor(html)
        if dest and is_valid_media_destination(dest):
            return dest

    forms = re.finditer(
        r'<form[^>]*action=["\']([^"\']*)["\'][^>]*method=["\']post["\'][^>]*>(.*?)</form>',
        html, re.DOTALL | re.IGNORECASE
    )

    for form_match in forms:
        action = form_match.group(1) or ""
        form_body = form_match.group(2)

        if not action.startswith("http"):
            action = urljoin(url, action)

        data = {}
        for inp in re.finditer(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', form_body, re.I):
            data[inp.group(1)] = inp.group(2)
        for inp in re.finditer(r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']', form_body, re.I):
            data[inp.group(2)] = inp.group(1)

        if not data:
            continue

        try:
            resp_text = await http_client.post(
                action,
                data=data,
                headers={
                    "Referer": url,
                    "Origin": base,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
            )
            if resp_text:
                try:
                    resp_json = json.loads(resp_text)
                    if isinstance(resp_json, dict) and resp_json.get("url"):
                        target = resp_json["url"]
                        if is_valid_media_destination(target) or is_shortener(target):
                            return target
                except Exception:
                    pass

            resp_html, final_url = await http_client.post_follow_redirects(
                action, data=data, headers={"Referer": url, "Origin": base}
            )
            if final_url and (is_valid_media_destination(final_url) or is_shortener(final_url)) and final_url != url:
                return final_url

            if resp_html:
                for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect):
                    dest = extractor(resp_html)
                    if dest and (is_valid_media_destination(dest) or is_shortener(dest)):
                        return dest
        except Exception as e:
            log.debug("[%s] Form submit failed on %s: %s", handler_name, action, e)

    return None


async def _bypass_droplink(url: str, http_client) -> str | None:
    return await _bypass_adlinkfly(url, http_client, "droplink")


async def _bypass_shrinkme(url: str, http_client) -> str | None:
    # 1. Fast direct MrProBlogger bypass (studied from IndraYuda13/shortlink-bypass-bot)
    try:
        alias = urlparse(url).path.strip("/")
        if alias:
            import asyncio
            from bs4 import BeautifulSoup

            def _sync_direct_mrproblogger():
                import requests
                sess = requests.Session()
                sess.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                mr_url = f"https://en.mrproblogger.com/{alias}"
                r = sess.get(mr_url, headers={"Referer": "https://themezon.net/"}, timeout=15)
                soup = BeautifulSoup(r.text, "html.parser")
                form = soup.select_one("form#go-link")
                if not form:
                    # ThemeZon hop fallback
                    try:
                        hop = sess.post(
                            "https://themezon.net/?redirect_to=random",
                            data={"newwpsafelink": alias},
                            headers={"Referer": "https://themezon.net/", "Origin": "https://themezon.net"},
                            timeout=12,
                            allow_redirects=False,
                        )
                        next_loc = hop.headers.get("Location")
                        if next_loc:
                            r = sess.get(mr_url, headers={"Referer": next_loc}, timeout=15)
                            soup = BeautifulSoup(r.text, "html.parser")
                            form = soup.select_one("form#go-link")
                    except Exception:
                        pass
                if not form:
                    return None
                hidden = {inp.get("name"): inp.get("value", "") for inp in form.select("input[name]")}
                action = urljoin(r.url, form.get("action") or "/links/go")
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
                    return data.get("url")
                return None

            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, _sync_direct_mrproblogger)
            if res:
                return res
    except Exception as e:
        log.debug("Direct MrProBlogger bypass error: %s", e)

    return await _bypass_adlinkfly(url, http_client, "shrinkme")


async def _bypass_exe(url: str, http_client) -> str | None:
    return await _bypass_adlinkfly(url, http_client, "exe")


async def _bypass_ouo(url: str, http_client) -> str | None:
    """
    Ouo.io / Ouo.press bypass flow.
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": "https://google.com/",
        "Accept": "text/html,application/xhtml+xml",
    })
    if not html:
        return None

    challenge = detect_protection_challenge(html)
    if challenge:
        log.warning("[ouo] Unsupported interactive %s challenge on %s", challenge, url)
        return None

    form_m = re.search(r'<form[^>]*action=["\']([^"\']+)["\'][^>]*>(.*?)</form>', html, re.DOTALL | re.IGNORECASE)
    if form_m:
        action = form_m.group(1)
        body = form_m.group(2)
        if not action.startswith("http"):
            action = urljoin(url, action)

        data = {}
        for inp in re.finditer(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', body, re.I):
            data[inp.group(1)] = inp.group(2)
        for inp in re.finditer(r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']', body, re.I):
            data[inp.group(2)] = inp.group(1)

        try:
            resp_html, final_url = await http_client.post_follow_redirects(
                action, data=data, headers={"Referer": url, "Origin": base}
            )
            if final_url and is_valid_media_destination(final_url) and final_url != url:
                return final_url

            if resp_html:
                for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect):
                    dest = extractor(resp_html)
                    if dest and is_valid_media_destination(dest):
                        return dest
        except Exception as e:
            log.debug("Ouo form POST failed: %s", e)

    return None


async def _bypass_shareus(url: str, http_client) -> str | None:
    """
    Shareus.io / Shareus.in bypass flow.
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": "https://google.com/",
        "Accept": "text/html,application/xhtml+xml",
    })
    if not html:
        return None

    challenge = detect_protection_challenge(html)
    if challenge:
        log.warning("[shareus] Unsupported interactive %s challenge on %s", challenge, url)
        return None

    for extractor in (_extract_data_attributes, _extract_meta_refresh, _extract_atob, _extract_js_redirect, _extract_encoded_var):
        dest = extractor(html)
        if dest and is_valid_media_destination(dest):
            return dest

    qs = parse_qs(parsed.query)
    short_id = qs.get("i", [""])[0] or qs.get("id", [""])[0]
    if not short_id:
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            short_id = path_parts[-1]

    if short_id:
        try:
            api_url = f"{base}/api?shortid={short_id}"
            res_text = await http_client.get_text_no_cache(api_url, headers={
                "Referer": url,
                "Origin": base,
                "X-Requested-With": "XMLHttpRequest",
            })
            if res_text:
                try:
                    data = json.loads(res_text)
                    if isinstance(data, dict):
                        target = data.get("link") or data.get("url") or data.get("target")
                        if target and is_valid_media_destination(target):
                            return target
                except Exception:
                    pass
        except Exception as e:
            log.debug("Shareus API call failed: %s", e)

    return None


async def _bypass_filepress(url: str, http_client) -> str | None:
    """
    Filepress.site / Filepress.store bypass flow.
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
        "Accept": "text/html,application/xhtml+xml",
    })
    if not html:
        return None

    challenge = detect_protection_challenge(html)
    if challenge:
        log.warning("[filepress] Unsupported interactive %s challenge on %s", challenge, url)
        return None

    for m in re.finditer(r'href=["\'](https?://drive\.google\.com/[^"\']+)["\']', html):
        return m.group(1)

    for m in re.finditer(r'src=["\'](https?://drive\.google\.com/[^"\']+)["\']', html):
        return m.group(1)

    for extractor in (_extract_data_attributes, _extract_js_redirect, _extract_atob, _extract_meta_refresh):
        dest = extractor(html)
        if dest and is_valid_media_destination(dest):
            return dest

    stream_m = re.search(r'["\'](https?://[^"\']+\.(?:mp4|mkv|m3u8)[^"\']*)["\']', html, re.I)
    return None


async def _bypass_codedew(url: str, http_client) -> str | None:
    """
    Codedew.com / RareAnimes zipper shortener bypass:
    Step 1: GET url -> find #goBtn with data-href (/zipper/?url=...&ad_done=1)
    Step 2: GET step2 URL -> find #goBtn with data-href (destination URL, e.g. Mega / direct stream)
    """
    try:
        import asyncio
        import cloudscraper
        from bs4 import BeautifulSoup
        s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})

        loop = asyncio.get_running_loop()

        def _sync_bypass():
            r = s.get(url, timeout=12)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            btn = soup.find(id="goBtn")
            if not btn or not btn.get("data-href"):
                return None

            step2_href = btn.get("data-href")
            step2_url = urljoin(url, step2_href)
            r2 = s.get(step2_url, headers={"Referer": url}, timeout=12)
            if r2.status_code != 200:
                return None
            soup2 = BeautifulSoup(r2.text, "html.parser")
            btn2 = soup2.find(id="goBtn")
            if btn2 and btn2.get("data-href"):
                dest = btn2.get("data-href")
                if dest.startswith("http"):
                    return dest
                return urljoin(step2_url, dest)
            return None

        dest = await loop.run_in_executor(None, _sync_bypass)
        if dest:
            log.info("Codedew bypassed: %s -> %s", url[:60], dest[:60])
            return dest
    except Exception as e:
        log.warning("Codedew bypass failed for %s: %s", url, e)
    return None

