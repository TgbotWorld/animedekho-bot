"""V3 #1-#16 offline verification — deterministic, no network.

Run: python3 tests/test_v3_offline.py  (exit 0 = all pass)
Covers: MongoDB upsert sanitizer, strict quality, Unknown labeling,
confident matching, modern-only styles, bypass navigation rejection +
failure stages, batch parent/child cancel, worker routing helper,
M3U8 exact-only (logic), health-probe fastest selection, audit claims.
"""

import asyncio
import sys


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    return bool(cond)


def main() -> int:
    ok = True

    # ── V3 #1: $set/$setOnInsert overlap sanitizer ──
    from bot.database import _sanitize_upsert
    s = {"series_title": "X", "updated_at": "t"}
    i = {"series_slug": "x", "series_title": "X", "created_at": "t"}
    _sanitize_upsert(s, i)
    ok &= check("V3#1 sanitizer strips overlap", "series_title" not in i and s["series_title"] == "X")
    # full-repo audit: no overlapping keys in any update_one upsert definition
    import pathlib, re
    overlap_found = []
    for fn in ["bot/database.py"]:
        text = pathlib.Path(fn).read_text()
        # find $set:{...}, $setOnInsert:{...} pairs textually is hard; instead
        # verify helper is used at every upsert site.
        n_update = len(re.findall(r"\.update_one\(", text))
        n_sanitize = text.count("_sanitize_upsert(")
        # definitions: add_child_bot, set_channel_mapping x2, track_bot_user = 4 sites
        ok &= check(f"V3#1 {fn} sanitizer wired ({n_sanitize} uses, {n_update} update_ones)",
                    n_sanitize >= 4, f"uses={n_sanitize} updates={n_update}")

    # ── V3 #2: strict quality candidates (no closest/auto fallback) ──
    from api.models import VideoServer, Quality
    from bot.handlers.callbacks import _find_quality_candidates
    srv = VideoServer(name="S1", server_id=1, player_url="",
                      qualities=[Quality(resolution="720p", url="u720")],
                      direct_url="")
    c1080 = _find_quality_candidates([srv], "1080p")
    ok &= check("V3#2 1080p request vs 720p-only → [] (no closest)", c1080 == [])
    c720 = _find_quality_candidates([srv], "720p")
    ok &= check("V3#2 exact 720p → 1 candidate", len(c720) == 1 and c720[0][1].resolution == "720p")
    srv4 = VideoServer(name="S4", server_id=4, player_url="",
                       qualities=[Quality(resolution="1080p", url="u1080")], direct_url="")
    c4k = _find_quality_candidates([srv4], "4K")
    ok &= check("V3#2 4K vs 1080p-only → []", c4k == [])

    # ── V3 #2b: M3U8 exact-only has no closest fallback in source ──
    import inspect
    from bot import downloader as dlmod
    src = inspect.getsource(dlmod.resolve_m3u8_variant)
    ok &= check("V3#2 M3U8 no closest fallback", "Closest variant" not in src and 'return ""' in src)
    nsrc = inspect.getsource(dlmod.n_m3u8dl_re_download)
    ok &= check("V3#2 N_m3u8DL-RE no blind --auto-select",
                "no Step-3 --auto-select retry" in nsrc or "never --auto-select" in nsrc)

    # ── V3 #2c: ffprobe verification strict bands ──
    async def _bands():
        r1 = await dlmod.fix_or_verify_video_resolution("/nonexistent.mp4", "1080p",
                                                        {"width": 1280, "height": 720})
        r2 = await dlmod.fix_or_verify_video_resolution("/nonexistent.mp4", "720p",
                                                        {"width": 1280, "height": 720})
        return r1, r2
    (ok1, _, _), (ok2, _, _) = asyncio.run(_bands())
    ok &= check("V3#2 1080p req vs 720p actual → reject", ok1 is False)
    ok &= check("V3#2 720p req vs 720p actual → accept", ok2 is True)

    # ── V3 #4: Unknown stays Unknown ──
    from extractors.bypass import _detect_quality_from_text
    from utils.anime_match import normalize_quality, qualities_match
    ok &= check("V3#4 unknown text → '' (caller maps Unknown)", _detect_quality_from_text("homepage contact telegram") == "")
    ok &= check("V3#4 normalize unknown", normalize_quality("") == "Unknown")
    ok &= check("V3#4 Unknown ≠ 1080p", qualities_match("1080p", "Unknown") is False)
    ok &= check("V3#4 4K ≡ 2160p", qualities_match("4K", "2160p") is True)
    t = pathlib.Path("extractors/animedubhindi.py").read_text()
    ok &= check("V3#4 animedubhindi no 'or quality_pref' mislabel",
                "det or quality_pref" not in t and " or quality_pref" not in t.replace('"requested_quality": quality_pref', ''))
    t2 = pathlib.Path("extractors/toonanime.py").read_text()
    ok &= check("V3#4 toonanime no mislabel", "det or quality_pref" not in t2)

    # ── V3 #5: confident matching ──
    from utils.anime_match import is_confident_match, matches_season
    ok &= check("V3#5 confident demon slayer", is_confident_match("Demon Slayer", "Demon Slayer Season 2 Hindi", "http://x/demon-slayer-s2"))
    ok &= check("V3#5 rejects wrong anime", not is_confident_match("Demon Slayer", "Clevatess Season 2 Hindi", "http://x/clevatess-s2"))
    toono_src = "\n".join(l for l in pathlib.Path("extractors/toono.py").read_text().splitlines() if not l.strip().startswith("#"))
    ok &= check("V3#5 TOONo first-result patched", "search_results[0]" not in toono_src)
    ok &= check("V3#5 toonflix first-result fallback removed", 'search_results[0]["url"]' not in pathlib.Path("extractors/toonflix.py").read_text())
    ok &= check("V3#5 season gate", matches_season("Demon Slayer Season 2", "http://x/y", 2) and not matches_season("Demon Slayer Season 2", "http://x/y", 1))

    # ── V3 #7: modern-only ──
    import inspect as _insp
    from bot.database import Database
    for fn in ["get_post_style", "get_start_style", "get_ep_style", "get_sched_style"]:
        src_fn = _insp.getsource(getattr(Database, fn))
        ok &= check(f"V3#7 {fn} modern-only", 'return "modern"' in src_fn)
    ok &= check("V3#7 migration helper exists", hasattr(Database, "migrate_classic_styles_to_modern"))
    adm = pathlib.Path("bot/handlers/admin.py").read_text()
    ok &= check("V3#7 admin toggles retired", adm.count("MODERN-ONLY") >= 4)

    # ── V3 #13: dynamic db ──
    hsrc = pathlib.Path("bot/health.py").read_text()
    ok &= check("V3#13 no stale module import", "from bot.database import db" not in hsrc)
    ok &= check("V3#13 dynamic accessor", "_get_db()" in hsrc)

    # ── V3 #3/#11: bypass dispatch + navigation rejection + stages ──
    from extractors.bypass import _is_navigation_url, detect_source, resolve_bypass_url
    ok &= check("V3#11 homepage rejected", _is_navigation_url("https://toonworld4all.me/"))
    ok &= check("V3#11 tag page rejected", _is_navigation_url("https://toonworld4all.me/tag/naruto/"))
    ok &= check("V3#11 cdn-cgi rejected", _is_navigation_url("https://x/cdn-cgi/content/img.jpg"))
    ok &= check("V3#11 contact rejected", _is_navigation_url("https://animedubhindi.link/contact-us/"))
    ok &= check("V3#11 real episode kept", not _is_navigation_url("https://archive.toonworld4all.me/episode/naruto-1x10", "Episode 10 1080p"))
    res = asyncio.run(resolve_bypass_url("https://example.com/video/1"))
    ok &= check("V3#11 unsupported keeps detect stage", res["stage"] == "detect" and res["ok"] is False)
    res2 = asyncio.run(resolve_bypass_url("not-a-url"))
    ok &= check("V3#11 invalid URL failure reason set", bool(res2.get("failure_reason")))
    bsrc = pathlib.Path("extractors/bypass.py").read_text()
    ok &= check("V3#3 dispatch present", "_resolve_animedubhindi_page" in bsrc and "_resolve_toonworld_url" in bsrc and "V3 #12" in bsrc)

    # ── V3 #6: batch lifecycle ──
    batch_src = pathlib.Path("bot/downloader.py").read_text()
    ok &= check("V3#6 batch parent/child API", "create_batch_job" in batch_src and "create_episode_job" in batch_src and "batch_is_cancelled" in batch_src)
    mgr = dlmod.download_job_manager
    bid = mgr.create_batch_job(111, "T", 2)
    cid = mgr.create_episode_job(bid, 111, "T E1")
    ok &= check("V3#6 child linked", cid in mgr._batch_children.get(bid, set()))
    asyncio.run(mgr.cancel_job(bid, 111, True))
    ok &= check("V3#6 batch cancel cascades to child", mgr.is_job_cancelled(cid))
    # other user's job untouched
    other = mgr.create_job(222, "other")
    ok &= check("V3#6 isolation (other job live)", not mgr.is_job_cancelled(other))
    mgr.remove_job(other)
    cb = pathlib.Path("bot/handlers/callbacks.py").read_text()
    ok &= check("V3#6 persistent cancel markup", cb.count("reply_markup=cancel_markup") >= 2 or cb.count("reply_markup=c_kb") >= 2)

    # ── V3 #14/#15: channel target + worker routing ──
    lib = pathlib.Path("bot/library.py").read_text()
    ok &= check("V3#14 target channel helper", "_target_channel" in lib and "chat_id=target_channel_id" in lib)
    ok &= check("V3#14 no raw self.channel posting", "chat_id=self.channel" not in lib)
    ok &= check("V3#15 worker routing helper", "_bot_username_for_quality" in lib and "get_bot_for_quality" in lib)
    dlsrc = pathlib.Path("bot/downloader.py").read_text()
    ok &= check("V3#15 episode buttons use worker", "_worker_for(" in dlsrc)

    # ── V3 #16: fastest healthy ──
    async def _probe_pick():
        from extractors.health_probe import select_fastest_healthy
        cands = [
            {"url": "http://a/v.mp4", "source": "A", "provider": "P", "quality": "1080p"},
            {"url": "http://b/v.mp4", "source": "B", "provider": "P", "quality": "1080p"},
        ]
        best, diags = await select_fastest_healthy(cands, probe=False)
        return best, diags
    best, diags = asyncio.run(_probe_pick())
    ok &= check("V3#16 single-pool pick works", best is not None and len(diags) == 2)
    ms = pathlib.Path("extractors/multisource.py").read_text()
    ok &= check("V3#16 multisource fastest selection", "select_fastest_healthy" in ms and "exact_candidates" in ms)

    # ── V3 #9/#10: diagnostics ──
    ok &= check("V3#9 403 surfaced distinctly", "403 Forbidden" in pathlib.Path("api/client.py").read_text())
    ok &= check("V3#10 failure diagnostics block", "Provider/Source" in cb and "Failure reason" in cb)
    bh = pathlib.Path("bot/handlers/bypass.py").read_text()
    ok &= check("V3#10 bypass diagnostics", "Failure reason" in bh and "Provider" in bh)

    # ── V3 #8: audit honesty ──
    rep = pathlib.Path("complete-anime-dekho-audit-report.md").read_text()
    ok &= check("V3#8 no absolute verified claim", "Fully Audited, Implemented, Tested & Verified" not in rep.split("V3 #8 note")[0] or "withdrawn" in rep.lower())
    ok &= check("V3#8 scratch PASS withdrawn", "Withdrawn claims" in rep or "not present in this checkout" in rep)

    # ── Download-fix: concurrency + cooldown + refresh plumbing ──
    from extractors.health_probe import note_host_throttled, _cooldown_active, _HOST_COOLDOWN_UNTIL
    import time as _t
    note_host_throttled("https://example-host.invalid/x.mp4")
    ok &= check("dlfix 429 cooldown engages", _cooldown_active("example-host.invalid"))
    _HOST_COOLDOWN_UNTIL.pop("example-host.invalid", None)
    ok &= check("dlfix cooldown clears", not _cooldown_active("example-host.invalid"))
    ms_src = pathlib.Path("extractors/multisource.py").read_text()
    ok &= check("dlfix concurrent resolution", "_resolve_one_source" in ms_src and "Semaphore(3)" in ms_src)
    dl_src = pathlib.Path("bot/downloader.py").read_text()
    ok &= check("dlfix preflight+refresh params", "refresh_url=None" in dl_src and "Preflight" in dl_src)
    cb_src = pathlib.Path("bot/handlers/callbacks.py").read_text()
    ok &= check("dlfix refresh closures wired", cb_src.count("refresh_url=_") >= 9)

    # ── Issue #30: archive __PROPS__ bypass, worker labels, main channel ──
    from extractors.bypass import _parse_tw4_props, _is_challenge_html
    ep_html = 'window.__PROPS__ = {"data":{"error":false,"data":{"metadata":{"show":"JoJo\'s Bizarre Adventure","season":3,"episode":4},"encodes":[{"resolution":"1080p","files":[{"host":"MEGA","link":"/redirect/abc","short":"mega.nz"}]}]}}};'
    p = _parse_tw4_props(ep_html)
    ok &= check("i30 props episode parse", p and p["data"]["data"]["encodes"][0]["resolution"] == "1080p")
    ok &= check("i30 props redirect parse",
                (_parse_tw4_props('window.__PROPS__ = {"destination":"https://exe.io/x","link":{"domain":"https://h/file/","hidden":"abc"}};') or {}).get("link", {}).get("hidden") == "abc")
    ok &= check("i30 props None-safe", _parse_tw4_props("no props here") is None)
    ok &= check("i30 cf challenge detected", _is_challenge_html("<title>Just a moment...</title>"))
    from bot.child_bots import ChildBotManager
    _cbm = ChildBotManager.__new__(ChildBotManager)
    _cbm.bot_info_cache = {1: {"bot_id": 1, "quality": "1080p", "username": "w1080"}}
    _cbm.active_clients = {1: object()}
    _cbm._round_robin_indices = {}
    ok &= check("i30 compound label routes to worker",
                _cbm.get_bot_for_quality("1080p HQ x265") == "w1080")
    ok &= check("i30 plain label still routes",
                _cbm.get_bot_for_quality("1080p") == "w1080")
    ok &= check("i30 unknown quality returns None",
                _cbm.get_bot_for_quality("480p") is None)
    from bot.database import Database
    ok &= check("i30 db.get_main_channel exists", hasattr(Database, "get_main_channel"))
    cb = pathlib.Path("bot/handlers/callbacks.py").read_text()
    ok &= check("i30 real title helper wired", cb.count("await _real_series_title(") >= 4)
    dl = pathlib.Path("bot/downloader.py").read_text()
    ok &= check("i30 googleapis direct-file routing", '".googleapis.com" in dl')
    ok &= check("i30 ffmpeg stderr captured", "stderr_tail" in dl and "FFmpeg stderr tail" in dl)
    ok &= check("i30 main-channel upload fallback", "get_main_channel() if _db_main else None" in dl)
    ok &= check("i30 worker recovery link", "_worker_username_for_quality(quality, bname)" in dl)
    bp = pathlib.Path("extractors/bypass.py").read_text()
    ok &= check("i30 archive props resolver", "_props_resolution" in bp and "_provider_target" in bp)
    ok &= check("i30 cloudscraper fetch fallback", "create_scraper" in bp)
    hp = pathlib.Path("extractors/health_probe.py").read_text()
    ok &= check("i30 single candidates probed", 'if not probe:' in hp and "Unprobed (disabled)" in hp)

    # ZIP-wrapped video (HubCloud application/x-zip) must extract before validation.
    try:
        import os, tempfile, zipfile, asyncio as _aio
        from bot.downloader import _maybe_unzip_download
        _td = tempfile.mkdtemp()
        _zp = os.path.join(_td, "episode.mp4")
        with zipfile.ZipFile(_zp, "w", zipfile.ZIP_DEFLATED) as _zf:
            _zf.writestr("Solo [DeadToons] S1E01.mkv", b"FAKEVIDEO" * 4096)
        _out = _aio.run(_maybe_unzip_download(_zp))
        ok &= check("i30 zip extraction returns mkv",
                    _out.endswith(".mkv") and os.path.exists(_out) and not os.path.exists(_zp)
                    and os.path.getsize(_out) == 9 * 4096)
        # non-zip passes through untouched
        _np = os.path.join(_td, "plain.mp4")
        open(_np, "wb").write(b"\x00\x01\x02not a zip")
        _out2 = _aio.run(_maybe_unzip_download(_np))
        ok &= check("i30 non-zip passthrough", _out2 == _np and os.path.exists(_np))
    except Exception as _ze:
        print(f"[FAIL] i30 zip extraction: {_ze}")
        ok = False

    # ── /source default-source feature (owner runtime switch) ────────────
    try:
        import asyncio as _ai
        from bot.source_config import (
            normalize_source, get_default_source, set_default_source,
            DEFAULT_SOURCE, SOURCE_CATALOG,
        )
        ok &= check("src default is AnimeDekho", DEFAULT_SOURCE == "AnimeDekho")
        ok &= check("src tolerant name matching",
                    normalize_source("ANIME DRIVE") == "AnimeDrive"
                    and normalize_source("adk") == "AnimeDekho"
                    and normalize_source("toonflix") == "ToonFlix"
                    and normalize_source("bogus") is None
                    and normalize_source("") is None)
        ok &= check("src catalog covers all extractors",
                    set(n for n, _ in SOURCE_CATALOG) == {
                        "AnimeDekho", "AnimeDubHindi", "ToonWorld4All", "RareAnimes",
                        "DeadToons", "TOONo", "ToonAnime", "AnimeDrive", "ToonFlix"})
        ok &= check("src default w/o db", _ai.run(get_default_source()) == "AnimeDekho")
        try:
            _ai.run(set_default_source("definitely-not-a-source"))
            ok &= check("src rejects unknown source", False)
        except ValueError:
            ok &= check("src rejects unknown source", True)
        init_txt = pathlib.Path("bot/handlers/__init__.py").read_text()
        admin_txt = pathlib.Path("bot/handlers/admin.py").read_text()
        cb_txt = pathlib.Path("bot/handlers/callbacks.py").read_text()
        ms_txt = pathlib.Path("extractors/multisource.py").read_text()
        ok &= check("src command registered",
                    'filters.command(["source", "setsource"])' in init_txt and "cmd_source" in init_txt)
        ok &= check("src command handler",
                    "async def cmd_source" in admin_txt and "set_default_source" in admin_txt)
        ok &= check("src episode tier ordering",
                    "ad_is_default" in cb_txt and "_step_animedekho()" in cb_txt
                    and "_needs_next_tier" in cb_txt)
        ok &= check("src batch tier ordering",
                    "_batch_animedekho" in cb_txt and "is_source(_def_src" in cb_txt)
        ok &= check("src multisource default ordering", "get_default_source" in ms_txt)
    except Exception as _se:
        print(f"[FAIL] source feature: {_se}")
        ok = False

    # ── Issue #33 reliability: failure TTL + dead-host bench + retries ────
    try:
        from extractors import reliability as rel
        rel.reset()

        # item 3: 3-minute dead-host bench
        ok &= check("i33 bench window is 180s", rel.BENCH_SECS == 180.0, str(rel.BENCH_SECS))
        # item 2: max 3 fresh-link attempts
        ok &= check("i33 max download attempts is 3", rel.attempts_for() == 3, str(rel.attempts_for()))
        ok &= check("i33 attempt budget stops at limit",
                    rel.next_attempt(1) == 2 and rel.next_attempt(2) == 3
                    and rel.next_attempt(3) is None and rel.next_attempt(9) is None)

        # item 3: connection-level error benches the whole host
        rel.record_host_dead("https://dead.example/f1.mp4", "All connection attempts failed")
        ok &= check("i33 conn error benches host",
                    rel.is_host_benched("https://dead.example/other.mp4")
                    and rel.bench_remaining("https://dead.example/other.mp4") > 170)
        ok &= check("i33 bench leaves other hosts alone",
                    not rel.is_host_benched("https://alive.example/x.mp4"))
        ok &= check("i33 benched host also parks its URL",
                    rel.is_url_parked("https://dead.example/f1.mp4"))

        # item 1/3: 3 distinct dead URLs on one host trip the bench
        rel.reset()
        for i in range(rel.BENCH_HOST_FAILURES):
            rel.record_failure(f"https://flaky.example/f{i}.mp4", "http-404")
        ok &= check("i33 3 dead URLs bench the host",
                    rel.is_host_benched("https://flaky.example/anything"),
                    str(rel.snapshot()))

        # item 1: per-URL TTL + pick-first-available
        rel.reset()
        rel.record_failure("https://h.example/a.mp4", "http-404")
        ok &= check("i33 dead URL parked", rel.is_url_parked("https://h.example/a.mp4")
                    and rel.failure_remaining("https://h.example/a.mp4") > 500)
        ok &= check("i33 sibling URLs unaffected", not rel.is_url_parked("https://h.example/b.mp4"))
        ok &= check("i33 first_available skips parked",
                    rel.first_available(["https://h.example/a.mp4", "https://h.example/b.mp4"])
                    == "https://h.example/b.mp4")
        rel.clear_url("https://h.example/a.mp4")
        ok &= check("i33 success clears TTL", not rel.is_url_parked("https://h.example/a.mp4"))
        ok &= check("i33 snapshot reports state", isinstance(rel.snapshot(), dict))

        # probe must refuse benched/parked URLs without touching the network
        from extractors.health_probe import probe_url_health, select_fastest_healthy
        rel.reset()
        rel.record_host_dead("https://benchme.example/a.mp4", "connection refused")
        _pr = asyncio.run(probe_url_health("https://benchme.example/b.mp4"))
        ok &= check("i33 probe short-circuits benched host",
                    _pr.get("error") == "host-benched" and not _pr.get("ok"))
        rel.reset()
        rel.record_failure("https://parkme.example/a.mp4", "http-404")
        _pr2 = asyncio.run(probe_url_health("https://parkme.example/a.mp4"))
        ok &= check("i33 probe short-circuits TTL-parked URL",
                    _pr2.get("error") == "url-ttl" and not _pr2.get("ok"))

        # selection must skip benched candidates and say why
        rel.reset()
        rel.record_host_dead("https://bad.example/x.mp4", "connection refused")
        _cands = [
            {"url": "https://bad.example/x.mp4", "source": "A", "quality": "720p", "provider": "bad"},
            {"url": "https://good.example/y.mp4", "source": "B", "quality": "720p", "provider": "good"},
        ]
        _best, _diags = asyncio.run(select_fastest_healthy(_cands, probe=False))
        ok &= check("i33 selection skips benched host",
                    _best and _best["source"] == "B", str(_best))
        ok &= check("i33 bench visible in diagnostics",
                    any("benched" in (d.get("error") or "").lower() for d in _diags),
                    str(_diags))
        # bench alone must not hard-fail: last resort still usable
        _only = [{"url": "https://bad.example/x.mp4", "source": "A", "quality": "720p", "provider": "bad"}]
        _best2, _ = asyncio.run(select_fastest_healthy(_only, probe=False))
        ok &= check("i33 benched candidate is last resort, not a dead end",
                    _best2 is not None and _best2["source"] == "A")

        # item 1: RareAnimes returns every mirror for the episode
        # (extractors/__init__ re-exports the instance as `rareanimes`, which
        #  shadows the submodule attribute — import the real module.)
        import importlib as _il
        _ra = _il.import_module("extractors.rareanimes")
        _HTML = """
        <div class="entry-content">
          <a href="https://srv1.example/dl">Episode 01 Server 1</a>
          <a href="https://srv2.example/dl">Ep 1 Server 2</a>
          <a href="https://srv1.example/dl">Episode 01 dup</a>
          <a href="https://srv3.example/dl">Episode 02</a>
        </div>"""

        class _Resp:
            text = _HTML
            status_code = 200

        class _Scraper:
            def get(self, *a, **k):
                return _Resp()

        _orig_scraper, _orig_search = _ra._get_scraper, _ra.rareanimes._sync_search
        _ra._get_scraper = lambda: _Scraper()  # module-level helper, not a method
        _ra.rareanimes._sync_search = lambda q: [{
            "title": "Solo Leveling Season 1 Hindi Dubbed",
            "url": "https://www.rareanimes.mov/solo-leveling-season-1/",
            "poster": "",
        }]
        try:
            rel.reset()
            _res = _ra.rareanimes._sync_resolve("Solo Leveling", 1, 1, "480p")
            ok &= check("i33 rareanimes returns a link", bool(_res and _res.get("url")),
                        str(_res))
            _alts = (_res or {}).get("alternates") or []
            ok &= check("i33 rareanimes exposes spare mirrors",
                        len(_alts) == 1 and _res.get("servers_found") == 2,
                        f"alts={_alts} found={(_res or {}).get('servers_found')}")
            ok &= check("i33 rareanimes dedupes mirrors",
                        _res.get("url") != _alts[0] if _alts else False)
            # bench the chosen mirror → extractor must fall back to the spare
            rel.record_host_dead(_res["url"], "connection refused")
            _res2 = _ra.rareanimes._sync_resolve("Solo Leveling", 1, 1, "480p")
            ok &= check("i33 rareanimes fails over past a benched mirror",
                        _res2 and _res2.get("url") == _alts[0], str(_res2))
        finally:
            _ra._get_scraper, _ra.rareanimes._sync_search = _orig_scraper, _orig_search
            rel.reset()

        # item 2: download_media retries with a fresh link (max 3)
        import bot.downloader as _dl
        _orig_once, _orig_probe = _dl._download_once, None
        import extractors.health_probe as _hp
        _orig_probe = _hp.probe_url_health
        _calls = {"once": 0, "refresh": 0}
        _seen_urls = []

        async def _ok_probe(u, referer=""):
            return {"ok": True, "latency_ms": 1.0, "bytes": 1, "status": 200, "error": ""}

        async def _fail_then_ok(u, q, op, pm, t, v, r, j):
            _calls["once"] += 1
            _seen_urls.append(u)
            return _calls["once"] >= 3  # fail twice, succeed on attempt 3

        async def _refresh():
            _calls["refresh"] += 1
            return f"https://fresh.example/take{_calls['refresh']}.mp4"

        _hp.probe_url_health = _ok_probe
        _dl._download_once = _fail_then_ok
        try:
            rel.reset()
            _ok = asyncio.run(_dl.download_media(
                "https://start.example/a.mp4", "720p",
                "/tmp/does-not-matter.mp4", None, "Solo Leveling",
                refresh_url=_refresh,
            ))
            ok &= check("i33 download succeeds within 3 attempts",
                        _ok and _calls["once"] == 3, str(_calls))
            ok &= check("i33 each retry used a fresh link",
                        _calls["refresh"] == 2 and len(set(_seen_urls)) == 3,
                        f"{_calls} {_seen_urls}")
            ok &= check("i33 failed start URL parked after failure",
                        rel.is_url_parked("https://start.example/a.mp4"))

            # budget respected: never more than 3 engine passes
            _calls["once"] = 0
            _calls["refresh"] = 0
            _seen_urls.clear()

            async def _always_fail(u, q, op, pm, t, v, r, j):
                _calls["once"] += 1
                _seen_urls.append(u)
                return False

            _dl._download_once = _always_fail
            _ok2 = asyncio.run(_dl.download_media(
                "https://start2.example/a.mp4", "720p",
                "/tmp/does-not-matter.mp4", None, "Solo",
                refresh_url=_refresh,
            ))
            ok &= check("i33 stops after 3 attempts (no hammering)",
                        not _ok2 and _calls["once"] == 3, str(_calls))

            # spare mirrors (item 1) are consumed before paying for re-resolve
            _calls["once"] = 0
            _calls["refresh"] = 0
            _seen_urls.clear()
            _ok3 = asyncio.run(_dl.download_media(
                "https://start3.example/a.mp4", "720p",
                "/tmp/does-not-matter.mp4", None, "Solo",
                refresh_url=_refresh,
                alternates=["https://mirror.example/b.mp4", "https://mirror.example/b.mp4"],
            ))
            ok &= check("i33 spare mirror preferred over re-resolve",
                        _calls["refresh"] == 1 and "https://mirror.example/b.mp4" in _seen_urls,
                        f"{_calls} {_seen_urls}")
        finally:
            _dl._download_once = _orig_once
            _hp.probe_url_health = _orig_probe
            rel.reset()

        # nothing in the tree should hand a benched URL straight back
        import pathlib as _pl
        ms_src = _pl.Path("extractors/multisource.py").read_text()
        ok &= check("i33 multisource honours bench", "reliability" in ms_src
                    and "first_available" in ms_src)
        hp_src = _pl.Path("extractors/health_probe.py").read_text()
        ok &= check("i33 probes consult the bench", "is_host_benched" in hp_src
                    and "is_url_parked" in hp_src)
        cb_src = pathlib.Path("bot/handlers/callbacks.py").read_text()
        ok &= check("i33 multisource results carry mirrors",
                    "alternates=ms_res.get" in cb_src and "alternates=ms0.get" in cb_src)
    except Exception as _r33:
        import traceback as _tb
        _tb.print_exc()
        print(f"[FAIL] issue #33 reliability: {_r33}")
        ok = False

    # ── Issue #33: single streaming-card thumbnail + branding commands ────
    try:
        import tempfile as _tf
        import pathlib as _pl2
        from PIL import Image as _PILImage
        from bot.thumbnail import (
            generate_auto_thumbnail, list_available_templates, get_template,
        )

        # One style only — the five legacy templates were retired.
        ok &= check("i33 only streaming template registered",
                    list_available_templates() == ["streaming"],
                    str(list_available_templates()))
        # ...but every legacy config value still resolves (no silent regression).
        for _legacy in ("modern", "cinematic", "movie_gold", "neon_cyber", "minimal", "", "STREAMING"):
            ok &= check(f"i33 legacy template '{_legacy or '(empty)'}' aliases",
                        get_template(_legacy).name == "streaming",
                        get_template(_legacy).name)

        _thumb_out = os.path.join(_tf.gettempdir(), "adk_thumb_test.jpg")
        _thumb = generate_auto_thumbnail(
            title="Welcome to the Outcast's Restaurant!",
            episode_info="Episodes: 12 | S01",
            quality="1080p",
            audio="Hindi Dub",
            poster_path="",
            output_path=_thumb_out,
            bot_username="AnimeDekhoBot",
            template_name="streaming",
            brand_username="@Animerulz_Pro",
        )
        ok &= check("i33 streaming thumb renders", bool(_thumb) and os.path.exists(_thumb))
        if _thumb and os.path.exists(_thumb):
            with _PILImage.open(_thumb) as _im:
                ok &= check("i33 thumb is 1280x720", _im.size == (1280, 720), f"got {_im.size}")
            ok &= check("i33 thumb has real content", os.path.getsize(_thumb) > 20000,
                        f"{os.path.getsize(_thumb)}B")
            os.remove(_thumb)

        # Legacy stored template names keep rendering (library passes them through).
        _legacy_out = os.path.join(_tf.gettempdir(), "adk_thumb_legacy.jpg")
        _lt = generate_auto_thumbnail(
            title="Solo Leveling", episode_info="S01 E05", quality="720p",
            audio="Hindi Dub", poster_path="", output_path=_legacy_out,
            template_name="movie_gold",
        )
        ok &= check("i33 legacy template name still renders", bool(_lt) and os.path.exists(_lt))
        if _lt:
            os.remove(_lt)

        # Branding logo: a PNG set via /thumblogo must composite into the lockup.
        _logo = os.path.join(_tf.gettempdir(), "adk_test_logo.png")
        _PILImage.new("RGBA", (128, 128), (236, 72, 153, 255)).save(_logo)
        _logo_out = os.path.join(_tf.gettempdir(), "adk_thumb_logo.jpg")
        _with_logo = generate_auto_thumbnail(
            title="Solo Leveling", episode_info="S01 E01", quality="1080p",
            audio="Hindi Dub", poster_path="", output_path=_logo_out,
            logo_path=_logo,
        )
        ok &= check("i33 thumb with PNG logo renders",
                    bool(_with_logo) and os.path.exists(_with_logo))
        if _with_logo:
            with _PILImage.open(_with_logo) as _im:
                _px = list(_im.convert("RGB").crop((72, 52, 132, 112)).getdata())
                _pink = sum(1 for r, g, b in _px if r > 200 and g < 130 and 110 < b < 200)
                ok &= check("i33 logo pixels present in lockup", _pink > 200, f"pink={_pink}")
            os.remove(_with_logo)
        os.remove(_logo)

        # Commands registered + wired.
        _hsrc = _pl2.Path("bot/handlers/__init__.py").read_text()
        ok &= check("i33 thumbuser registered",
                    'filters.command("thumbuser")' in _hsrc)
        ok &= check("i33 thumblogo registered",
                    'filters.command("thumblogo")' in _hsrc)
        _asrc = _pl2.Path("bot/handlers/admin.py").read_text()
        ok &= check("i33 thumbuser handler exists", "async def cmd_thumbuser" in _asrc)
        ok &= check("i33 thumblogo handler exists", "async def cmd_thumblogo" in _asrc)
        ok &= check("i33 thumblogo validates image",
                    "im.verify()" in _asrc)
        ok &= check("i33 branding persisted to db",
                    "thumb_brand_username" in _asrc and "thumb_brand_logo_path" in _asrc)
        _apsrc = _pl2.Path("bot/app.py").read_text()
        ok &= check("i33 branding restored on boot",
                    "thumb_brand_username" in _apsrc and "thumb_brand_logo_path" in _apsrc)

        # Runtime (not just text) checks — a static grep passed while the
        # helper itself raised NameError on every call.
        from bot.handlers.admin import _brand_logo_path
        _bp = _brand_logo_path()
        ok &= check("i33 _brand_logo_path resolves without NameError",
                    isinstance(_bp, str) and _bp.endswith("data/thumb_brand_logo.png"), str(_bp))
        _asrc2 = _pl2.Path("bot/handlers/admin.py").read_text()
        _head15 = "\n".join(_asrc2.splitlines()[:15])
        ok &= check("i33 admin.py imports Path + datetime",
                    "from pathlib import Path" in _head15
                    and "from datetime import datetime, timezone" in _head15)

        # Repo-wide: no handler module may reference an undefined name — that
        # was how /help stayed broken (NameError: settings) unnoticed.
        try:
            import pyflakes  # noqa: F401
            import subprocess as _sp
            _out = _sp.run([sys.executable, "-m", "pyflakes", "bot/", "utils/", "api/", "extractors/"],
                           capture_output=True, text=True, cwd=str(pathlib.Path.cwd()))
            import re as _re
            _undef = [ln for ln in _out.stdout.splitlines()
                      if _re.search(r"undefined name '", ln)]
            ok &= check("no undefined names (pyflakes)", not _undef,
                        "; ".join(_undef[:5]))
        except ImportError:
            print("[SKIP] pyflakes not installed — undefined-name audit skipped")
        # No template class may be left registered besides 'streaming'.
        _tsrc = _pl2.Path("bot/thumbnail.py").read_text()
        _regs = [ln for ln in _tsrc.splitlines() if ln.startswith("@register_template")]
        ok &= check("i33 exactly one register_template decorator",
                    len(_regs) == 1, str(_regs))
        # Category settings must not offer retired styles.
        _ssrc = _pl2.Path("bot/handlers/settings.py").read_text()
        ok &= check("i33 settings no longer offer retired styles",
                    '"cinematic"' not in _ssrc and '"neon_cyber"' not in _ssrc)
    except Exception as _te:
        import traceback as _tb2
        _tb2.print_exc()
        print(f"[FAIL] i33 thumbnail: {_te}")
        ok = False

    # ── Issue #33 (C): gated DOWNLOAD button + END OF SEASON sticker ────────
    try:
        import pathlib as _pl3

        # Deep-link codec accepts the new dl_ family.
        from utils.helpers import encode_file_param, decode_file_param
        _raw = encode_file_param("dl_solo-leveling_2")
        ok &= check("i33 dl_ param survives encode/decode",
                    decode_file_param(_raw) == "dl_solo-leveling_2",
                    decode_file_param(_raw))
        ok &= check("i33 raw dl_ param passes through",
                    decode_file_param("dl_x_1") == "dl_x_1")

        from bot.linkgate import encode_gate_param, decode_gate_param, MODES, DEFAULT_MODE
        ok &= check("i33 gate param round-trips",
                    decode_gate_param(decode_file_param(encode_gate_param("solo-leveling", 2)))
                    == ("solo-leveling", 2))
        ok &= check("i33 gate param tolerates junk",
                    decode_gate_param("dl_") == ("", 1) and decode_gate_param("") == ("", 1)
                    and decode_gate_param("get_x") == ("", 1))
        ok &= check("i33 gate param survives a non-encoded slug",
                    decode_gate_param("dl_my_show_3") == ("my_show", 3))
        ok &= check("i33 gate modes are request|timer|link",
                    MODES == ("request", "timer", "link") and DEFAULT_MODE == "request",
                    str(MODES))

        # Gate OFF (the default) ⇒ buttons are byte-identical to before.
        from bot.library import LibraryManager, build_quality_buttons
        _mgr = LibraryManager(None, None, -100123456789, "AnimeDekhoBot")
        _off = _mgr._build_album_buttons(
            "solo-leveling", ["S01E01"], ["480p", "720p", "1080p"], False)
        _off_texts = [b.text for r in _off.inline_keyboard for b in r]
        ok &= check("i33 gate off keeps quality buttons",
                    _off_texts == ["480p", "720p", "1080p"], str(_off_texts))
        ok &= check("i33 gate off has no DOWNLOAD button",
                    not any("DOWNLOAD" in t.upper() for t in _off_texts))

        # Gate ON ⇒ a single ⬇ DOWNLOAD deep link, no quality rows leaked.
        _on = _mgr._build_album_buttons(
            "solo-leveling", ["S02E05"], ["480p", "720p", "1080p"], False,
            gated=True, season=2)
        _on_rows = _on.inline_keyboard
        _on_texts = [b.text for r in _on_rows for b in r]
        ok &= check("i33 gate on = one DOWNLOAD button",
                    len(_on_rows) == 1 and len(_on_rows[0]) == 1
                    and "DOWNLOAD" in _on_texts[0], str(_on_texts))
        _dl_url = _on_rows[0][0].url
        ok &= check("i33 DOWNLOAD is a bot deep link",
                    "?start=" in _dl_url, _dl_url)
        ok &= check("i33 gate param resolves back to slug+season",
                    decode_gate_param(decode_file_param(_dl_url.split("start=")[1]))
                    == ("solo-leveling", 2))
        ok &= check("i33 gate on hides quality buttons",
                    not any(t in _on_texts for t in ("480p", "720p", "1080p")))

        # Shared builder: same buttons for the post and the post-gate reveal.
        _shared = build_quality_buttons(["480p", "720p"], "solo-leveling",
                                        bot_username="AnimeDekhoBot")
        _shared_texts = [b.text for r in _shared.inline_keyboard for b in r]
        ok &= check("i33 shared quality builder used by both paths",
                    _shared_texts == ["480p", "720p"], str(_shared_texts))

        # Gate-off default is honoured by the async resolver with no DB.
        _res = asyncio.run(_mgr._album_gate_kwargs(["S02E05"], False))
        ok &= check("i33 gate resolver defaults off without db",
                    _res.get("gated") is False and _res.get("season") == 2, str(_res))

        # END OF SEASON: unset ⇒ never posts; set ⇒ posts to the channel.
        from bot import endseason as _eos
        _sticker = asyncio.run(_eos.get_end_of_season_sticker())
        ok &= check("i33 end-of-season unset by default", _sticker == "", repr(_sticker))

        class _FakeClient:
            def __init__(self):
                self.sent = []
            async def send_sticker(self, chat_id=None, sticker=None, **kw):
                self.sent.append((chat_id, sticker))
        _fc = _FakeClient()
        _posted = asyncio.run(_eos.post_end_of_season(_fc, -100123456789, "Solo Leveling", 2))
        ok &= check("i33 end-of-season no-ops when unset", _posted is False and not _fc.sent)

        async def _fake_get():
            return "STICKER_FILE_ID"
        _orig_get = _eos.get_end_of_season_sticker
        _eos.get_end_of_season_sticker = _fake_get
        try:
            _posted2 = asyncio.run(_eos.post_end_of_season(_fc, -100123456789, "Solo Leveling", 2))
            ok &= check("i33 end-of-season posts when set",
                        _posted2 is True and _fc.sent == [(-100123456789, "STICKER_FILE_ID")],
                        str(_fc.sent))
            # A failing channel must not raise out of the batch flow.
            class _Boom:
                async def send_sticker(self, **kw):
                    raise RuntimeError("no rights")
            _posted3 = asyncio.run(_eos.post_end_of_season(_Boom(), -100123456789, "Solo Leveling", 2))
            ok &= check("i33 end-of-season swallows send errors", _posted3 is False)
            _posted4 = asyncio.run(_eos.post_end_of_season(_fc, None, "Solo Leveling", 2))
            ok &= check("i33 end-of-season needs a channel", _posted4 is False)
        finally:
            _eos.get_end_of_season_sticker = _orig_get

        # ── gate_prompt: the viewer-facing prompt from the demo video ──────
        from bot import linkgate as _lg
        from config.settings import settings as _settings

        class _GMe:
            username = "AnimeDekhoBot"

        class _GCli:
            me = _GMe()

        _g_on, _g_mem, _g_inv = _lg.get_gate_channel, _lg._is_member, _lg._invite_link

        async def _gate_on():
            return -1001112223334

        async def _not_member(c, ch, uid):
            return False

        async def _is_member_yes(c, ch, uid):
            return True

        async def _req_link(c, ch, uid):
            return "https://t.me/+GATEREQ", "✋ REQUEST TO JOIN"

        async def _plain_lbl(c, ch, uid):
            return "https://t.me/+GATETIMER", "📢 JOIN CHANNEL"

        async def _no_link(c, ch, uid):
            return "", ""

        _stranger = 987654321
        try:
            from bot.auth import is_owner as _isown
            ok &= check("i33 test stranger is not an owner",
                        not _isown(_stranger) and _isown(_settings.bot.owner_id))

            # Gate off ⇒ never blocks.
            ok &= check("i33 gate_prompt off => through",
                        asyncio.run(_lg.gate_prompt(_GCli(), _stranger, "solo", 1)) is None)
            ok &= check("i33 gate_prompt without a user id => through",
                        asyncio.run(_lg.gate_prompt(_GCli(), 0, "solo", 1)) is None)

            _lg.get_gate_channel = _gate_on
            _lg._is_member = _not_member
            _lg._invite_link = _req_link
            _out = asyncio.run(_lg.gate_prompt(_GCli(), _stranger, "solo-leveling", 2))
            ok &= check("i33 gate_prompt blocks a non-member", _out is not None)
            if _out:
                _txt, _kb = _out
                _rows = _kb.inline_keyboard
                ok &= check("i33 prompt headline matches the demo",
                            "HERE IS YOUR LINK" in _txt
                            and "CLICK BELOW TO PROCEED" in _txt, _txt[:90])
                ok &= check("i33 prompt is REQUEST TO JOIN + TRY AGAIN",
                            len(_rows) == 2
                            and _rows[0][0].text == "✋ REQUEST TO JOIN"
                            and _rows[1][0].text == "🔄 TRY AGAIN",
                            str([b.text for r in _rows for b in r]))
                _retry = _rows[1][0].url
                ok &= check("i33 TRY AGAIN deep-links back to the content",
                            "?start=" in _retry and decode_gate_param(
                                decode_file_param(_retry.split("start=")[1]))
                            == ("solo-leveling", 2), _retry)

            # Already a member ⇒ straight through.
            _lg._is_member = _is_member_yes
            ok &= check("i33 a member is straight through",
                        asyncio.run(_lg.gate_prompt(_GCli(), _stranger, "solo", 1)) is None)

            # Owner bypass.
            _lg._is_member = _not_member
            ok &= check("i33 owner bypasses the gate",
                        asyncio.run(_lg.gate_prompt(_GCli(), _settings.bot.owner_id,
                                                    "solo", 1)) is None)

            # Whatever label the configured mode picked is what the user sees.
            _lg._invite_link = _plain_lbl
            _out2 = asyncio.run(_lg.gate_prompt(_GCli(), _stranger, "solo", 1))
            ok &= check("i33 mode's button label is surfaced",
                        _out2 is not None
                        and _out2[1].inline_keyboard[0][0].text == "📢 JOIN CHANNEL",
                        str(_out2 and [b.text for r in _out2[1].inline_keyboard
                                       for b in r]))

            # No invite available ⇒ still offers TRY AGAIN, never a dead end.
            _lg._invite_link = _no_link
            _out3 = asyncio.run(_lg.gate_prompt(_GCli(), _stranger, "solo", 1))
            ok &= check("i33 gate never dead-ends without an invite",
                        _out3 is not None
                        and "Could Not Generate An Invite Link" in _out3[0]
                        and _out3[1].inline_keyboard[0][0].text == "🔄 TRY AGAIN",
                        str(_out3 and [b.text for r in _out3[1].inline_keyboard
                                       for b in r]))
        finally:
            _lg.get_gate_channel = _g_on
            _lg._is_member = _g_mem
            _lg._invite_link = _g_inv

        # Commands registered + handlers exist.
        _hsrc = _pl3.Path("bot/handlers/__init__.py").read_text()
        ok &= check("i33 linkgate registered",
                    'filters.command("linkgate")' in _hsrc)
        ok &= check("i33 endsticker registered",
                    'filters.command("endsticker")' in _hsrc)
        _asrc = _pl3.Path("bot/handlers/admin.py").read_text()
        ok &= check("i33 linkgate handler exists", "async def cmd_linkgate" in _asrc)
        ok &= check("i33 endsticker handler exists", "async def cmd_endsticker" in _asrc)
        ok &= check("i33 mapchannel advertises the sticker prompt",
                    "endsticker" in _asrc.split("Channel Mapped Successfully")[1][:1400])

        # ── Reply-first install flows, exercised for real ──────────────────
        # A status branch placed before the reply check made the documented
        # "reply to an image/sticker" flow unreachable (it always answered
        # with the status view instead of installing).
        import tempfile as _tf
        import types as _otypes
        import bot.handlers.admin as _adm
        from config import Config as _Cfg

        class _Rep:
            def __init__(self, kind=""):
                self.photo = None
                self.document = None
                self.sticker = None
                if kind == "photo":
                    self.photo = _otypes.SimpleNamespace(file_id="FAKE_PHOTO_ID")
                elif kind == "sticker":
                    self.sticker = _otypes.SimpleNamespace(file_id="FAKE_STICKER_ID")

        class _Msg:
            def __init__(self, text, rep=None):
                self.text = text
                self.reply_to_message = rep
                self.from_user = None
                self.replies, self.photos, self.stickers = [], [], []

            async def reply_text(self, text, **kw):
                self.replies.append(text)

            async def reply_photo(self, photo=None, caption=None, **kw):
                self.photos.append(photo)
                self.replies.append(caption or "")

            async def reply_sticker(self, sticker=None, **kw):
                self.stickers.append(sticker)

        class _DLClient:
            async def download_media(self, media, file_name=None, **kw):
                from PIL import Image as _PImg
                _PImg.new("RGBA", (64, 64), (17, 24, 39, 255)).save(file_name)
                return file_name

        # `@require_owner` uses functools.wraps, which sets __wrapped__.
        # (functools.unwrap is absent from this Python build.)
        def _raw(fn):
            seen = 0
            while hasattr(fn, "__wrapped__") and seen < 5:
                fn = fn.__wrapped__
                seen += 1
            return fn
        _raw_logo = _raw(_adm.cmd_thumblogo)
        _raw_eos = _raw(_adm.cmd_endsticker)

        _tmpdir = _tf.mkdtemp(prefix="i33_logo_")
        _tmp_logo = str(_pl3.Path(_tmpdir) / "logo.png")
        _orig_path_fn = _adm._brand_logo_path
        _orig_logo_cfg = getattr(_Cfg, "THUMB_BRAND_LOGO", "")
        _adm._brand_logo_path = lambda: _tmp_logo
        _Cfg.THUMB_BRAND_LOGO = ""
        # These commands write config when a DB is attached — keep the run
        # offline so the live database is never touched by the fixture.
        import bot.database as _dbmod
        _saved_db = _dbmod.db
        _dbmod.db = None
        try:
            # 1. THE BUG: bare /thumblogo replying to an image must install.
            _m1 = _Msg("/thumblogo", _Rep("photo"))
            asyncio.run(_raw_logo(_DLClient(), _m1))
            ok &= check("i33 /thumblogo installs from a replied image",
                        any("Logo installed" in r for r in _m1.replies),
                        str(_m1.replies))
            ok &= check("i33 /thumblogo persists the installed path",
                        _Cfg.THUMB_BRAND_LOGO == _tmp_logo
                        and _pl3.Path(_tmp_logo).exists(),
                        _Cfg.THUMB_BRAND_LOGO)

            # 2. Bare /thumblogo with nothing replied → previews the logo.
            _m2 = _Msg("/thumblogo")
            asyncio.run(_raw_logo(_DLClient(), _m2))
            ok &= check("i33 /thumblogo bare previews an installed logo",
                        any("Current thumbnail logo" in r for r in _m2.replies),
                        str(_m2.replies) + str(_m2.photos))

            # 2b. …and says so when there is nothing to preview.
            _Cfg.THUMB_BRAND_LOGO = ""
            _m2b = _Msg("/thumblogo")
            asyncio.run(_raw_logo(_DLClient(), _m2b))
            ok &= check("i33 /thumblogo bare (no logo) shows status",
                        any("No logo installed" in r for r in _m2b.replies),
                        str(_m2b.replies))

            # 3. Args but no media reply → explicit usage error.
            _m3 = _Msg("/thumblogo something")
            asyncio.run(_raw_logo(_DLClient(), _m3))
            ok &= check("i33 /thumblogo with args but no reply asks for one",
                        any("Please reply to a PNG/JPG" in r for r in _m3.replies),
                        str(_m3.replies))

            # 4. Bare /endsticker replying to a sticker must install.
            _e1 = _Msg("/endsticker", _Rep("sticker"))
            asyncio.run(_raw_eos(None, _e1))
            ok &= check("i33 /endsticker installs from a replied sticker",
                        any("END OF SEASON sticker installed" in r for r in _e1.replies),
                        str(_e1.replies))

            # 5. Bare /endsticker with nothing replied → status view.
            _e2 = _Msg("/endsticker")
            asyncio.run(_raw_eos(None, _e2))
            ok &= check("i33 /endsticker bare (no reply) shows status",
                        any("No END OF SEASON sticker" in r for r in _e2.replies),
                        str(_e2.replies))

            # 6. Args but no sticker reply → explicit usage error.
            _e3 = _Msg("/endsticker nope")
            asyncio.run(_raw_eos(None, _e3))
            ok &= check("i33 /endsticker with args but no reply asks for one",
                        any("Please reply to a sticker" in r for r in _e3.replies),
                        str(_e3.replies))
        finally:
            _adm._brand_logo_path = _orig_path_fn
            _Cfg.THUMB_BRAND_LOGO = _orig_logo_cfg
            _dbmod.db = _saved_db
            import shutil as _sh
            _sh.rmtree(_tmpdir, ignore_errors=True)

        # /start routes the dl_ family.
        _csrc = _pl3.Path("bot/handlers/commands.py").read_text()
        ok &= check("i33 /start routes dl_ params",
                    'param.startswith("dl_")' in _csrc)
        ok &= check("i33 gated download handler exists",
                    "async def _handle_gated_download" in _csrc)
        ok &= check("i33 gated download runs fsub then gate",
                    "check_fsub" in _csrc.split("async def _handle_gated_download")[1][:3000]
                    and "gate_prompt" in _csrc.split("async def _handle_gated_download")[1][:3000])

        # Album posts consult the gate (and fail open).
        _lsrc = _pl3.Path("bot/library.py").read_text()
        ok &= check("i33 all 3 album call sites gate-aware",
                    _lsrc.count("_album_gate_kwargs(sorted_eps, is_movie)") == 3,
                    str(_lsrc.count("_album_gate_kwargs(sorted_eps, is_movie)")))
        ok &= check("i33 gate failure fails open (posts still go out)",
                    "gated = False" in _lsrc)

        # Batch completion fires the end-of-season hook.
        _cbsrc = _pl3.Path("bot/handlers/callbacks.py").read_text()
        ok &= check("i33 batch completion posts end-of-season sticker",
                    "post_end_of_season" in _cbsrc and "completed == total" in _cbsrc)

        # Nobody can hit the gate without the config: linkgate is owner-only.
        ok &= check("i33 linkgate is owner-only",
                    _asrc.split("async def cmd_linkgate")[0].rstrip().endswith("@require_owner"))
    except Exception as _ce:
        import traceback as _tb3
        _tb3.print_exc()
        print(f"[FAIL] i33 channel flow: {_ce}")
        ok = False

    # ── Issue #34: source bench + bounded resolution round ────────────────
    try:
        import pathlib as _pl5
        from extractors import reliability as _rel5

        _rel5.reset()
        for _ in range(_rel5.SOURCE_FAIL_LIMIT - 1):
            _rel5.note_source_failure("RareAnimes", "timeout")
        ok &= check("i34 source not benched below the limit",
                    not _rel5.is_source_benched("RareAnimes"))
        _rel5.note_source_failure("RareAnimes", "timeout")
        ok &= check("i34 source benched at the limit",
                    _rel5.is_source_benched("RareAnimes"))
        ok &= check("i34 bench visible in snapshot",
                    "RareAnimes" in (_rel5.snapshot().get("benched_sources") or {}))
        ok &= check("i34 a healthy source is untouched by another's bench",
                    not _rel5.is_source_benched("AnimeDrive"))
        _rel5.note_source_success("RareAnimes")
        ok &= check("i34 success clears the bench",
                    not _rel5.is_source_benched("RareAnimes"))

        _msrc = _pl5.Path("extractors/multisource.py").read_text()
        ok &= check("i34 multisource sits out benched sources",
                    "is_source_benched" in _msrc and "note_source_failure" in _msrc)
        ok &= check("i34 resolution round has a hard cap",
                    "_RESOLVE_HARD_CAP" in _msrc and "asyncio.wait(" in _msrc)
        ok &= check("i34 first exact candidate releases the stragglers",
                    "_RESOLVE_GRACE" in _msrc and "FIRST_COMPLETED" in _msrc)
        # a plain miss must never count as a source failure
        ok &= check("i34 misses do not bench a source",
                    "note_source_success" in _msrc
                    and "normal miss" in _msrc)
        # the first semaphore wave must be the sources that actually resolve
        from extractors.multisource import multi_source_manager as _msmgr
        _first3 = [n for n, _ in _msmgr.sources[:3]]
        ok &= check("i34 proven sources lead the first wave",
                    _first3 == ["AnimeDrive", "ToonFlix", "AnimeDubHindi"],
                    str(_first3))
        ok &= check("i34 no source was dropped",
                    len(_msmgr.sources) == 8, str(len(_msmgr.sources)))

        # ── functional: a good candidate must not wait for the stragglers ──
        import time as _time5
        import extractors.multisource as _ms5
        import extractors.health_probe as _hp5

        class _FakeMgr(_ms5.MultiSourceManager):
            async def _resolve_one_source(self, name, extractor, *a, **kw):
                if name == "FastSource":
                    await asyncio.sleep(0.2)
                    return ({"source": name, "quality": "720p", "provider": "probe",
                             "url": "https://ok.example/v.mp4",
                             "detected_quality": "720p"},
                            f"{name}: ok", "exact")
                await asyncio.sleep(30)
                return None, f"{name}: slow", "skip"

        _fake_mgr = _FakeMgr()
        _fake_mgr.sources = [("FastSource", object()),
                             ("SlowA", object()), ("SlowB", object())]

        async def _fake_select(pool):
            return pool[0], [{"quality": "720p", "source": "FastSource",
                              "provider": "probe", "status": "Fast/Healthy"}]

        _orig_grace = _ms5._RESOLVE_GRACE
        _orig_cap = _ms5._RESOLVE_HARD_CAP
        _orig_select = _hp5.select_fastest_healthy
        _ms5._RESOLVE_GRACE = 0.5
        _hp5.select_fastest_healthy = _fake_select
        try:
            _t0 = _time5.monotonic()
            _res5 = asyncio.run(_fake_mgr.resolve_episode_stream(
                "Solo Leveling", season=1, episode=1, quality_pref="720p"))
            _fast_dt = _time5.monotonic() - _t0

            # every source dead → the round stops at the cap, not at N×75s
            _ms5._RESOLVE_HARD_CAP = 1.5
            _ms5._RESOLVE_GRACE = 1.5

            class _AllSlow(_ms5.MultiSourceManager):
                async def _resolve_one_source(self, name, extractor, *a, **kw):
                    await asyncio.sleep(30)
                    return None, f"{name}: slow", "skip"

            _slow_mgr = _AllSlow()
            _slow_mgr.sources = [("DeadA", object()), ("DeadB", object())]
            _t1 = _time5.monotonic()
            _res6 = asyncio.run(_slow_mgr.resolve_episode_stream(
                "Solo Leveling", season=1, episode=1, quality_pref="720p"))
            _cap_dt = _time5.monotonic() - _t1
        finally:
            _ms5._RESOLVE_GRACE = _orig_grace
            _ms5._RESOLVE_HARD_CAP = _orig_cap
            _hp5.select_fastest_healthy = _orig_select

        ok &= check("i34 good candidate not held up by slow sources",
                    _res5 is not None and _fast_dt < 6.0,
                    f"dt={_fast_dt:.1f}s res={bool(_res5)}")
        ok &= check("i34 all-dead round stops at the cap",
                    _res6 is None and _cap_dt < 8.0, f"dt={_cap_dt:.1f}s")

        _rel5.reset()
    except Exception as _e34:
        import traceback as _tb5
        _tb5.print_exc()
        print(f"[FAIL] issue #34 source health: {_e34}")
        ok = False

    # ── Issue #35: ffmpeg audio codec, TLS fail-fast, config messages ─────
    try:
        import pathlib as _pl6
        import tempfile as _tf6
        from bot.downloader import _bsf_failed
        from utils.http import _is_tls_cert_error
        from utils.helpers import resolve_photo_source

        # Vorbis/Opus streams cannot pass aac_adtstoasc → must trigger a retry.
        ok &= check("i35 non-AAC bitstream failure is detected",
                    _bsf_failed(["Codec 'vorbis' (86021) is not supported by the bitstream "
                                 "filter 'aac_adtstoasc'"]))
        ok &= check("i35 unrelated ffmpeg errors do not trigger a retry",
                    not _bsf_failed(["HTTP error 403 Forbidden"])
                    and not _bsf_failed([]))
        _dsrc = _pl6.Path("bot/downloader.py").read_text()
        ok &= check("i35 ffmpeg retries as a plain copy",
                    "retrying as a plain copy" in _dsrc and "_bsf_failed(" in _dsrc)

        # Expired certificates never heal on retry.
        ok &= check("i35 certificate errors are recognised",
                    _is_tls_cert_error(Exception("SSLCertVerificationError: certificate has expired")))
        ok &= check("i35 ordinary network errors still retry",
                    not _is_tls_cert_error(Exception("Cannot connect to host x:443")))
        _hsrc = _pl6.Path("utils/http.py").read_text()
        ok &= check("i35 http fails fast on broken TLS",
                    "not retrying" in _hsrc and "_note_dead_host" in _hsrc)

        # Owner-authored /start text (config START_MSG / DB start_msg).
        from bot.handlers.commands import _welcome_caption

        class _StartDb:
            async def get_start_msg(self):
                return "Hi {mention} — custom welcome"

        class _EmptyDb:
            async def get_start_msg(self):
                return None

        _cap = asyncio.run(_welcome_caption(_StartDb(), "<a>A</a>"))
        ok &= check("i35 configured START_MSG is used",
                    _cap == "Hi <a>A</a> — custom welcome", _cap)
        _cap2 = asyncio.run(_welcome_caption(_EmptyDb(), "X"))
        ok &= check("i35 default welcome when nothing is configured",
                    "on demand" in _cap2, _cap2[:60])

        # Owner-authored FSub prompt + banner (FSUB_MSG / FSUB_PIC).
        from bot.fsub import _custom_prompt, send_fsub_prompt

        class _FsubDb:
            async def get_fsub_msg(self):
                return "Join to continue, {mention}"

        _txt = asyncio.run(_custom_prompt(_FsubDb(), "DEFAULT PROMPT", None, 42))
        ok &= check("i35 configured FSUB_MSG is used",
                    _txt.startswith("Join to continue") and "tg://user?id=42" in _txt, _txt)

        class _PlainDb:
            async def get_fsub_msg(self):
                return ""

        ok &= check("i35 default FSub prompt when nothing is configured",
                    asyncio.run(_custom_prompt(_PlainDb(), "DEFAULT PROMPT", None, 42))
                    == "DEFAULT PROMPT")

        class _Msg:
            def __init__(self, fail_photo: bool = False):
                self.photo = None
                self.text = None
                self._fail = fail_photo

            async def reply_photo(self, *a, **kw):
                if self._fail:
                    raise RuntimeError("FILE_REFERENCE_INVALID")
                self.photo = {"args": a, **kw}

            async def reply_text(self, *a, **kw):
                self.text = {"args": a, **kw}

        import bot.database as _dbmod

        class _PicDb:
            async def get_fsub_pic(self):
                return "https://example.test/banner.jpg"

        _orig_db = _dbmod.db
        _dbmod.db = _PicDb()
        try:
            _m1 = _Msg()
            asyncio.run(send_fsub_prompt(_m1, "join please", None))
            ok &= check("i35 configured FSUB_PIC banner is sent",
                        _m1.photo is not None and _m1.photo.get("caption") == "join please"
                        and _m1.text is None)
            _m2 = _Msg(fail_photo=True)
            asyncio.run(send_fsub_prompt(_m2, "join please", None))
            ok &= check("i35 broken banner degrades to text",
                        _m2.photo is None and _m2.text is not None)
        finally:
            _dbmod.db = _orig_db

        # Local banner paths must be uploaded, not passed as a URL.
        _pic_path = os.path.join(_tf6.gettempdir(), "adk_banner_test.png")
        with open(_pic_path, "wb") as _fh:
            _fh.write(b"\x89PNG\r\n\x1a\n")
        _opened = resolve_photo_source(_pic_path)
        ok &= check("i35 local banner path is opened for upload",
                    hasattr(_opened, "read"))
        try:
            _opened.close()
        except Exception:
            pass
        ok &= check("i35 banner URL passes through unchanged",
                    resolve_photo_source("https://example.test/b.jpg")
                    == "https://example.test/b.jpg")
        os.remove(_pic_path)

        # Every FSub call site goes through the banner-aware sender.
        _fsub_calls = 0
        for _p in ("bot/handlers/commands.py", "bot/child_bots.py", "bot/auth.py"):
            _src6 = _pl6.Path(_p).read_text()
            _fsub_calls += _src6.count("send_fsub_prompt(")
        ok &= check("i35 all FSub prompts honour FSUB_PIC",
                    _fsub_calls >= 6, f"call sites={_fsub_calls}")
    except Exception as _e35:
        import traceback as _tb6
        _tb6.print_exc()
        print(f"[FAIL] issue #35 reliability/UI: {_e35}")
        ok = False

    print("\nALL PASS" if ok else "\nSOME FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
