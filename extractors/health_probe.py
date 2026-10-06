"""V3 #16: bounded fastest-healthy link selection within exact quality.

Priority (per issue):
    1. Exact anime/episode (enforced by callers/extractors)
    2. Exact requested quality (enforced before probing)
    3. Valid/public working link (is_valid_media_destination)
    4. Fastest healthy provider (this module)
    5. Download

Health check uses bounded timeout + small response test. A slow/dead link
is skipped in favour of the next valid exact-quality source. Quality never
influences selection beyond the exact-match gate done by callers.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger(__name__)

_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=8, connect=5)
_PROBE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Range": "bytes=0-65535",
}

# Hosts that answered 429 recently: skip network probes until cooldown ends
# (self-inflicted rate limits made workers.dev ungated pages look dead).
_HOST_COOLDOWN_UNTIL: dict[str, float] = {}
_HOST_COOLDOWN_SECS = 120.0


def _cooldown_active(host: str) -> bool:
    try:
        return time.time() < _HOST_COOLDOWN_UNTIL.get(host, 0)
    except Exception:
        return False


def note_host_throttled(url: str) -> None:
    """Mark a host throttled (HTTP 429) so probes back off briefly."""
    try:
        from urllib.parse import urlparse as _up
        host = _up(url).netloc.lower()
        if host:
            _HOST_COOLDOWN_UNTIL[host] = time.time() + _HOST_COOLDOWN_SECS
            log.info("Host %s throttled — probing paused for %ds", host, _HOST_COOLDOWN_SECS)
    except Exception:
        pass


async def probe_url_health(url: str, referer: str = "") -> dict:
    """Bounded probe: returns {ok, latency_ms, bytes, status, error}."""
    res = {"ok": False, "latency_ms": None, "bytes": 0, "status": None, "error": ""}
    if not url or not url.startswith("http"):
        res["error"] = "bad-url"
        return res
    try:
        from urllib.parse import urlparse as _up
        _host = _up(url).netloc.lower()
        if _host and _cooldown_active(_host):
            res["error"] = "429-cooldown"
            return res
    except Exception:
        pass
    # Issue #33: dead-host bench (3 min) + per-URL failure TTL. A parked link
    # answers instantly instead of costing another 8s probe round-trip.
    try:
        from extractors import reliability as _rel
        if _rel.is_host_benched(url):
            res["error"] = "host-benched"
            return res
        if _rel.is_url_parked(url):
            res["error"] = "url-ttl"
            return res
    except Exception:
        pass
    headers = dict(_PROBE_HEADERS)
    if referer:
        headers["Referer"] = referer
    t0 = time.perf_counter()
    try:
        async with aiohttp.ClientSession(timeout=_PROBE_TIMEOUT, headers=headers) as sess:
            async with sess.get(url, allow_redirects=True) as resp:
                res["status"] = resp.status
                if resp.status == 429:
                    note_host_throttled(url)
                    res["error"] = "http-429"
                elif resp.status in (200, 206):
                    chunk = await resp.content.read(65536)
                    res["bytes"] = len(chunk)
                    res["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    # Must have received some bytes quickly.
                    res["ok"] = res["bytes"] > 0
                    if not res["ok"]:
                        res["error"] = "empty-response"
                else:
                    res["error"] = f"http-{resp.status}"
    except asyncio.TimeoutError:
        # Ambiguous (slow ≠ dead) — never TTL a link just for being slow.
        res["error"] = "timeout"
    except Exception as e:
        res["error"] = str(e)[:80]
    # Issue #33: feed probe outcomes back into the reliability layer.
    # Conservative on purpose: park only *definitive* answers. A 4xx means the
    # link itself is gone (TTL); a connection-level error means the host is
    # gone (bench). Timeouts / 5xx stay retryable — slow is not dead.
    try:
        from extractors import reliability as _rel
        if res["ok"]:
            _rel.clear_url(url)
        elif str(res.get("status") or "") in ("404", "410", "403", "451"):
            _rel.record_failure(url, res["error"])
        elif res["error"] and _is_conn_error(res["error"]):
            _rel.record_host_dead(url, res["error"])
    except Exception:
        pass
    return res


def _is_conn_error(err: str) -> bool:
    low = str(err).lower()
    return any(m in low for m in (
        "connection refused", "cannot connect", "all connection attempts failed",
        "name or service not known", "temporary failure in name resolution",
        "getaddrinfo failed", "no route to host", "network is unreachable",
        "server disconnected", "remote end closed",
    ))


async def select_fastest_healthy(
    candidates: list[dict],
    *,
    probe: bool = True,
    max_probe: int = 4,
) -> tuple[dict | None, list[dict]]:
    """Pick fastest healthy candidate. Returns (best, diagnostics).

    Each candidate: {url, source, quality, provider, ...}.
    Diagnostics entries: {source, provider, quality, status, latency_ms, error}
    with status in (Fast/Healthy, Slow→skipped, Timeout, Dead, Unprobed).
    Probing is bounded; first candidates only (max_probe) to avoid long waits.
    Order preserved for equal health (stable).
    """
    if not candidates:
        return None, []
    # Issue #33: benched/parked links never get probed — and never win — but
    # the bench alone must not make us fail outright (they stay as a last
    # resort so a bad TTL can't turn into "no sources left").
    try:
        from extractors import reliability as _rel
        avail = [c for c in candidates if _rel.is_available(c.get("url", ""))]
    except Exception:
        avail, _rel = list(candidates), None
    if not avail:
        avail = list(candidates)
    diags_pre: list[dict] = []
    if _rel is not None and len(avail) != len(candidates):
        for c in candidates:
            if c in avail:
                continue
            why = "Host benched (3m)" if _rel.is_host_benched(c.get("url", "")) else "URL in failure TTL"
            diags_pre.append({
                "source": c.get("source", "?"),
                "provider": c.get("provider", "?"),
                "quality": c.get("quality", "?"),
                "status": "Skipped — " + why,
                "latency_ms": None,
                "error": why,
            })
        log.info("MultiSource: bench/TTL skipped %d candidate(s)", len(candidates) - len(avail))
    candidates = avail
    if not probe:
        diag = [{
            "source": c.get("source", "?"),
            "provider": c.get("provider", "?"),
            "quality": c.get("quality", "?"),
            "status": "Unprobed (disabled)",
            "latency_ms": None,
            "error": "",
        } for c in candidates]
        return candidates[0], diags_pre + diag
    # NOTE: single candidates are probed too — a lone dead/403 link must be
    # visible in diagnostics instead of being selected blind.
    subset = candidates[:max_probe]
    results = await asyncio.gather(*[probe_url_health(c.get("url", ""), c.get("referer", "")) for c in subset])
    scored = []
    diags = list(diags_pre)
    for cand, pr in zip(subset, results):
        if pr["ok"]:
            scored.append((pr["latency_ms"], cand))
            diags.append({
                "source": cand.get("source", "?"),
                "provider": cand.get("provider", "?"),
                "quality": cand.get("quality", "?"),
                "status": "Fast/Healthy",
                "latency_ms": pr["latency_ms"],
                "error": "",
            })
        else:
            err = pr.get("error", "unhealthy")
            status = "Timeout" if "timeout" in err.lower() else "Slow → skipped" if pr.get("status") else "Dead"
            diags.append({
                "source": cand.get("source", "?"),
                "provider": cand.get("provider", "?"),
                "quality": cand.get("quality", "?"),
                "status": status,
                "latency_ms": pr.get("latency_ms"),
                "error": err,
            })
    # Append unprobed remainder as fallback diags.
    for cand in candidates[max_probe:]:
        diags.append({
            "source": cand.get("source", "?"),
            "provider": cand.get("provider", "?"),
            "quality": cand.get("quality", "?"),
            "status": "Unprobed (overflow)",
            "latency_ms": None,
            "error": "",
        })
    if not scored:
        # Everything probed dead. Attempting the first candidate still beats
        # refusing outright: probes can false-negative (referer/UA gated
        # hosts), and the downloader preflights + refreshes with exact
        # diagnostics. Diagnostics keep the Dead statuses for the admin card.
        log.info("MultiSource: all %d probed candidates unhealthy — attempting first anyway", len(candidates))
        return candidates[0], diags
    scored.sort(key=lambda x: x[0])
    return scored[0][1], diags


def infer_provider(url: str) -> str:
    u = (url or "").lower()
    for name in ("hubcloud", "gdflink", "gdflix", "fpgo", "filepress", "fpress",
                 "mega", "drive.google", "mediafire", "streamwish", "filemoon",
                 "vidstream", "megacloud", "vidsrc", "dood", "streamtape",
                 "mp4upload", "vidguard", "toonflix", "animedrive", "archive"):
        if name in u:
            return name.upper() if len(name) <= 4 else name.capitalize()
    return "Direct"
