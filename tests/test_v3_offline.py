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

    print("\nALL PASS" if ok else "\nSOME FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
