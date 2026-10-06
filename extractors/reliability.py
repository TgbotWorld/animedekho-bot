"""Issue #33 reliability layer — dead-host bench + per-URL failure TTL.

Three cooperating mechanisms (all in-memory, process-local, no DB writes):

1. **Per-URL failure TTL** — a link that 404'd / 403'd / failed to download is
   not worth re-probing for a while. ``record_failure`` parks that exact URL for
   :data:`FAILURE_TTL` seconds so a re-resolve cannot hand the same corpse back.
2. **Dead-host bench** — when a host itself is unreachable (connection refused,
   DNS failure) or accumulates :data:`BENCH_HOST_FAILURES` distinct dead URLs
   inside the TTL window, the whole host is benched for :data:`BENCH_SECS`
   (3 minutes per issue #33 item 3). While benched, probes return immediately
   and resolvers skip that host's candidates instead of burning 8s each.
3. **Retry budget** — :func:`next_attempt` hands out the caller's
   fresh-link retry attempts (max 3 per issue #33 item 2), counting down so
   callers never implement their own loop incorrectly.

Everything degrades to "allowed" on any internal error: a broken bookkeeping
map must never be able to block downloads.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: Seconds a dead *host* is benched (issue #33 item 3: "a 3-minute bench").
BENCH_SECS = 180.0

#: Seconds an individual dead *URL* is parked for.
FAILURE_TTL = 600.0

#: Distinct dead URLs from one host (inside TTL) that trigger a host bench.
BENCH_HOST_FAILURES = 3

#: Max fresh-link download attempts (issue #33 item 2).
MAX_DOWNLOAD_ATTEMPTS = 3

# url -> (parked_until, failure_count, last_reason)
_url_failures: dict[str, tuple[float, int, str]] = {}
# host -> benched_until
_host_bench: dict[str, float] = {}
# host -> set of dead URLs seen recently (drives the bench threshold)
_host_dead_urls: dict[str, set[str]] = {}

# Errors that mean "the host is gone", not "this path is gone".
_CONN_MARKERS = (
    "connection refused",
    "cannot connect",
    "all connection attempts failed",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "no route to host",
    "network is unreachable",
    "server disconnected",
    "remote end closed",
)


def host_of(url: str) -> str:
    """Lowercased host for *url*; ``""`` when unparseable/empty."""
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def reset() -> None:
    """Clear all bench/TTL state (tests + admin recovery)."""
    _url_failures.clear()
    _host_bench.clear()
    _host_dead_urls.clear()


def _prune(now: float | None = None) -> None:
    """Drop expired entries so the maps cannot grow without bound."""
    now = time.time() if now is None else now
    for k in [k for k, v in _url_failures.items() if v[0] <= now]:
        _url_failures.pop(k, None)
    for k in [k for k, v in _host_bench.items() if v <= now]:
        _host_bench.pop(k, None)
        _host_dead_urls.pop(k, None)


# ───────────────────────────── URL failure TTL ─────────────────────────────

def record_failure(url: str, reason: str = "") -> None:
    """Park *url* for :data:`FAILURE_TTL` and feed the host-bench counter."""
    if not url or not url.startswith("http"):
        return
    try:
        _prune()
        now = time.time()
        prev_count = 0
        hit = _url_failures.get(url)
        if hit and hit[0] > now:
            prev_count = hit[1]
        _url_failures[url] = (now + FAILURE_TTL, prev_count + 1, str(reason)[:120])

        host = host_of(url)
        if host:
            seen = _host_dead_urls.setdefault(host, set())
            seen.add(url)
            if len(seen) >= BENCH_HOST_FAILURES:
                _bench_host(host, f"{len(seen)} distinct dead URLs")
        log.info("URL parked for %.0fs (%s): %s", FAILURE_TTL, reason or "failure", url[:90])
    except Exception as e:
        log.debug("record_failure failed: %s", e)


def failure_remaining(url: str) -> float:
    """Seconds left on *url*'s TTL; ``0`` when it is not parked."""
    try:
        hit = _url_failures.get(url)
        if not hit:
            return 0.0
        left = hit[0] - time.time()
        return left if left > 0 else 0.0
    except Exception:
        return 0.0


def is_url_parked(url: str) -> bool:
    """True when *url* failed recently and should not be retried yet."""
    return failure_remaining(url) > 0


def clear_url(url: str) -> None:
    """Forget a URL's failures (a successful fetch proves it healthy)."""
    try:
        _url_failures.pop(url, None)
        host = host_of(url)
        if host:
            _host_dead_urls.get(host, set()).discard(url)
    except Exception:
        pass


# ────────────────────────────── host bench ─────────────────────────────────

def _bench_host(host: str, reason: str = "") -> None:
    if not host:
        return
    _host_bench[host] = time.time() + BENCH_SECS
    log.warning("Host benched for %.0fs (%s): %s", BENCH_SECS, reason or "dead", host)


def record_host_dead(url: str, reason: str = "") -> None:
    """Bench *url*'s host immediately (connection-level failure)."""
    host = host_of(url)
    if not host:
        return
    low = str(reason).lower()
    if not any(m in low for m in _CONN_MARKERS):
        # Not connection-level — leave it to record_failure's threshold.
        record_failure(url, reason)
        return
    _bench_host(host, reason)
    record_failure(url, reason)


def bench_remaining(url: str) -> float:
    """Seconds left on *url*'s host bench; ``0`` when not benched."""
    try:
        host = host_of(url)
        if not host:
            return 0.0
        until = _host_bench.get(host, 0.0)
        left = until - time.time()
        return left if left > 0 else 0.0
    except Exception:
        return 0.0


def is_host_benched(url: str) -> bool:
    return bench_remaining(url) > 0


def clear_host(url: str) -> None:
    host = host_of(url)
    if host:
        _host_bench.pop(host, None)
        _host_dead_urls.pop(host, None)


def is_available(url: str) -> bool:
    """True when *url* is neither TTL-parked nor host-benched."""
    if not url:
        return True
    return not is_url_parked(url) and not is_host_benched(url)


def first_available(urls, exclude: str = "") -> str:
    """First URL in *urls* still allowed; ``""`` when everything is parked."""
    for u in urls or []:
        if not u or u == exclude:
            continue
        if is_available(u):
            return u
    return ""


# ───────────────────────────── retry budget ────────────────────────────────

def attempts_for(limit: int | None = None) -> int:
    """Clamped fresh-link attempt budget (``MAX_DOWNLOAD_ATTEMPTS`` default)."""
    n = MAX_DOWNLOAD_ATTEMPTS if limit is None else int(limit)
    if n < 1:
        n = 1
    if n > 5:  # hard ceiling — never turn a retry loop into a hammer
        n = 5
    return n


def next_attempt(current: int, limit: int | None = None) -> int | None:
    """Return the next 1-based attempt number, or ``None`` when exhausted."""
    nxt = current + 1
    return nxt if nxt <= attempts_for(limit) else None


def snapshot() -> dict:
    """Diagnostics for tests / admin inspection."""
    _prune()
    return {
        "parked_urls": len(_url_failures),
        "benched_hosts": {h: round(until - time.time()) for h, until in _host_bench.items()
                          if until > time.time()},
        "failure_ttl": FAILURE_TTL,
        "bench_secs": BENCH_SECS,
        "max_attempts": MAX_DOWNLOAD_ATTEMPTS,
    }
