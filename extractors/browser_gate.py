"""DGT ad-gate unlock via a real browser context (Issue: site-side gate upgrade).

The AnimeDekho `/wp-json/dgt/v1/watch` endpoint no longer returns ``sources``
directly. It answers with::

    {"gated": true, "gate": "https://animedekho.tv/?dgt_gate=1&next=...", "hours": 24}

The gate page renders several ad-skip forms (``/skip/*.php``). Submitting one
bounces through a third-party shortener (shrinkme / gplinks / cuty …) and the
verification URL is only honoured by the backend once that round-trip has
happened, from the same browser identity:

* GET-ing the ``?dgt_verify=…`` URL directly → ``403 Verification — That was
  too fast. Please complete the ad step first.``
* cloudscraper cannot clear it either — the shortener serves an interactive
  Cloudflare Turnstile.

The working flow (verified live 2026-10-24) is:

1. warm the site so Cloudflare issues ``cf_clearance``
2. open the gate page and read the hidden ``input[name=shortlink]`` verify URL
3. submit the first ``form.dgt-ch__form`` (the "Skip AD" form → shortener hop)
4. wait for the gate's server-side timer to elapse
5. navigate the *same* browser context to the verify URL
6. POST ``/wp-json/dgt/v1/watch`` from inside that context

Headless Chromium is served a Cloudflare challenge instead of the gate page, so
this module launches a **non-headless** browser; under Docker that requires a
virtual display (``xvfb-run``, provided by the image).

If Playwright or a display is unavailable, every entry point degrades to
``None`` and the caller keeps its existing behaviour — no hard dependency.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

log = logging.getLogger(__name__)

#: Server-side gate timer. Verification is rejected before this elapses
#: ("too fast"), so we wait it out in-process.
_DEFAULT_GATE_WAIT = float(os.environ.get("DGT_GATE_WAIT", "42"))
#: Hard ceiling for one unlock attempt (warmup + gate + wait + verify + watch).
_DEFAULT_TIMEOUT = float(os.environ.get("DGT_GATE_TIMEOUT", "150"))
#: "off" disables browser unlocking entirely (caller falls back to old path).
_ENABLED = os.environ.get("DGT_GATE_BROWSER", "auto").strip().lower() not in ("off", "0", "false", "no")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
]


def browser_available() -> bool:
    """True when Playwright is importable and unlocking has not been disabled."""
    if not _ENABLED:
        return False
    try:
        import playwright.async_api  # noqa: F401
    except Exception:
        return False
    return True


class _BrowserGate:
    """Lazy, single-context browser holder. One context keeps ``cf_clearance``."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._lock = asyncio.Lock()

    async def _ensure(self, base_url: str):
        if self._context is not None:
            return self._context
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        # Non-headless is required: headless Chromium receives a CF challenge
        # for the gate page instead of the forms. Under Docker this relies on
        # Xvfb (DISPLAY); without a display we fall back to headless, which
        # usually just fails closed (caller keeps the gated response).
        has_display = bool(os.environ.get("DISPLAY"))
        launch_args = {"args": _CHROMIUM_ARGS, "headless": not has_display}
        self._browser = await self._pw.chromium.launch(**launch_args)
        self._context = await self._browser.new_context(
            user_agent=_UA,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        return self._context

    async def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass
        self._context = None
        self._browser = None
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None


_holder = _BrowserGate()


async def _watch_json(page, base_url: str, post_id, episode_id) -> dict | None:
    """POST the watch endpoint from inside the browser (carries all cookies)."""
    try:
        raw = await page.evaluate(
            """async (a) => {
                const r = await fetch(a.base + '/wp-json/dgt/v1/watch', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({post_id: a.post, episode_id: a.ep}),
                    credentials: 'include',
                });
                return await r.text();
            }""",
            {"base": base_url, "post": post_id, "ep": episode_id},
        )
    except Exception as e:
        log.warning("Browser gate: watch fetch failed: %s", e)
        return None
    if not raw:
        return None
    try:
        import json as _json
        return _json.loads(raw)
    except Exception:
        log.warning("Browser gate: non-JSON watch response: %r", (raw or "")[:120])
        return None


async def _unlock_once(base_url: str, page_url: str, post_id, episode_id,
                       wait_s: float, timeout: float) -> dict | None:
    """One full unlock attempt. Returns watch JSON with sources, or None."""
    context = await _holder._ensure(base_url)
    page = await context.new_page()
    try:
        # 1) warm-up — lets Cloudflare issue cf_clearance for the context
        await page.goto(base_url + "/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3500)

        # 2) gate page — the verify URL lives in a hidden input
        gate_url = f"{base_url}/?dgt_gate=1&next={_quote(page_url)}"
        await page.goto(gate_url, wait_until="domcontentloaded", timeout=60000)

        verify_url = None
        for _ in range(12):
            verify_url = await page.evaluate(
                "() => document.querySelector("
                "'form.dgt-ch__form input[name=shortlink]')?.value || null"
            )
            if verify_url:
                break
            await page.wait_for_timeout(2000)
        if not verify_url:
            log.warning("Browser gate: gate form did not render (CF/challenge?) for %s", page_url)
            return None

        # 3) submit the first skip form (Server-side timer starts here)
        forms = await page.query_selector_all("form.dgt-ch__form")
        if not forms:
            log.warning("Browser gate: no skip form to submit for %s", page_url)
            return None
        btn = await forms[0].query_selector("button[type=submit]")
        if btn is not None:
            await btn.click()

        # 4) wait out the server-side timer, then 5) return + verify
        await page.wait_for_timeout(int(wait_s * 1000))
        await page.goto(base_url + "/", wait_until="domcontentloaded", timeout=45000)
        await page.goto(verify_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)

        # 5b) poll watch briefly — the unlock can take a beat to register
        deadline = asyncio.get_running_loop().time() + min(20.0, timeout / 4)
        while True:
            data = await _watch_json(page, base_url, post_id, episode_id)
            if data and not data.get("gated") and data.get("sources"):
                log.info("Browser gate: unlocked DGT gate for %s (%d source(s))",
                         page_url, len(data.get("sources") or []))
                return data
            if asyncio.get_running_loop().time() >= deadline:
                return data
            await page.wait_for_timeout(2500)
    finally:
        try:
            await page.close()
        except Exception:
            pass


def _quote(url: str) -> str:
    from urllib.parse import quote
    return quote(url, safe="")


async def fetch_watch_unlocked(
    base_url: str,
    page_url: str,
    post_id,
    episode_id,
    *,
    wait_s: float | None = None,
    timeout: float | None = None,
) -> dict | None:
    """Unlock the DGT gate in a real browser and return the watch JSON.

    Returns ``None`` when browser unlocking is unavailable or the attempt
    fails; callers must treat that as "keep the existing behaviour".
    """
    if not browser_available():
        log.debug("Browser gate: playwright unavailable/disabled — skipping")
        return None
    wait_s = _DEFAULT_GATE_WAIT if wait_s is None else wait_s
    timeout = _DEFAULT_TIMEOUT if timeout is None else timeout
    async with _holder._lock:
        try:
            return await asyncio.wait_for(
                _unlock_once(base_url, page_url, post_id, episode_id, wait_s, timeout),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning("Browser gate: unlock timed out after %.0fs for %s", timeout, page_url)
            return None
        except Exception as e:
            log.warning("Browser gate: unlock error for %s: %s", page_url, e)
            return None


async def shutdown() -> None:
    """Close the shared browser (call on bot shutdown)."""
    await _holder.close()


# ── Ad-mediator unlock (4KHDHub & friends) ───────────────────────────────
# A "mediator" page (e.g. greenmotors.club/?id=<blob>) is an ad-gate that,
# after a verify click, opens the real HubCloud/drive URL. We drive it in the
# same kind of non-headless browser and return the first real link.

_MEDIATOR_TARGET_HINTS = (
    "hubcloud", "gamerxyt", "hubdrive", "gdflix", "gdflink", "fsl",
    "workers.dev", "storage.googleapis", "cloudflarestorage", "pixeldrain",
)
#: Ad networks the mediator opens first — never treat these as the target.
_MEDIATOR_AD_HINTS = (
    "werefilledwit", "google-analytics", "googletagmanager", "doubleclick",
    "adservice", "popads", "propellerads", "onclick", "mgid",
)


def _looks_like_mediator_target(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    u = url.lower()
    if any(a in u for a in _MEDIATOR_AD_HINTS):
        return False
    return any(h in u for h in _MEDIATOR_TARGET_HINTS)


def unlock_mediator_sync(mediator_url: str, *, referer: str = "",
                        timeout: float = 60.0) -> str | None:
    """Clear an ad-mediator page and return the real drive URL (sync).

    Runs the Playwright **sync** API, so it must be called from a plain worker
    thread (e.g. inside ``run_in_executor``) — never from the event loop thread.
    Returns ``None`` when no target link can be recovered.
    """
    if not browser_available():
        return None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    import time as _time
    deadline = _time.monotonic() + timeout
    try:
        with sync_playwright() as pw:
            has_display = bool(os.environ.get("DISPLAY"))
            browser = pw.chromium.launch(headless=not has_display, args=_CHROMIUM_ARGS)
            try:
                ctx = browser.new_context(user_agent=_UA,
                                          viewport={"width": 1366, "height": 768},
                                          locale="en-US")
                ctx.add_init_script(
                    "window.__opens=[];const _o=window.open;"
                    "window.open=function(u){try{window.__opens.push(u)}catch(e){};"
                    "try{return _o.call(window,u)}catch(e){return null}};"
                )
                page = ctx.new_page()
                try:
                    page.goto(mediator_url, wait_until="domcontentloaded",
                              timeout=45000, referer=referer or None)
                except Exception:
                    pass

                def _collect() -> str | None:
                    # 1) navigated popups / new tabs
                    for p in ctx.pages:
                        if p is not page and _looks_like_mediator_target(p.url):
                            return p.url
                    # 2) window.open targets
                    try:
                        for u in page.evaluate("() => window.__opens || []"):
                            if _looks_like_mediator_target(u):
                                return u
                    except Exception:
                        pass
                    # 3) anchors injected into the DOM
                    try:
                        for u in page.evaluate(
                            "() => [...document.querySelectorAll('a')].map(a=>a.href)"
                        ):
                            if _looks_like_mediator_target(u):
                                return u
                    except Exception:
                        pass
                    return None

                # wait for the verify button, clicking it a few times as the
                # gate re-renders (it often needs the click after its timer).
                while _time.monotonic() < deadline:
                    found = _collect()
                    if found:
                        log.info("Browser mediator: unlocked %s → %s",
                                 mediator_url[:60], found[:80])
                        return found
                    for sel in ("#verify_btn", "a.get-link", "button:has-text('Continue')",
                                "a:has-text('CONTINUE')", "button:has-text('CLICK')"):
                        try:
                            el = page.query_selector(sel)
                            if el is not None:
                                el.click(timeout=2000)
                                break
                        except Exception:
                            continue
                    page.wait_for_timeout(2500)
                log.warning("Browser mediator: no target link found for %s", mediator_url[:60])
                return None
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        log.warning("Browser mediator: error for %s: %s", mediator_url[:60], e)
        return None
