import json
import pytest
from unittest.mock import AsyncMock, patch

from api.client import AnimeDekhoAPI
from api.models import VideoServer, Quality
from extractors.resolver import _resolve_animedekho, resolve_player_url
from bot.handlers.callbacks import _lazy_resolve_servers, _find_quality_candidates


@pytest.mark.anyio
async def test_parse_dgt_sources():
    api = AnimeDekhoAPI()
    raw_sources = [
        {
            "id": 0,
            "server": "Vidstream",
            "embed": "https://animedekho.tv/embed/123/1-1",
            "download": "",
            "show_embed": True,
        },
        {
            "id": 1,
            "server": "GDFlix-720p",
            "embed": "https://animedekho.tv/player.php?url=abc123embed",
            "download": "https://animedekho.tv/download2.php?url=abc123dl",
            "show_embed": False,
        },
        {
            "id": 2,
            "server": "VidSrc-1080p",
            "embed": "https://animedekho.tv/player.php?url=xyz1080embed",
            "download": "https://animedekho.tv/download.php?url=xyz1080dl",
            "show_embed": True,
        },
    ]

    servers = api._parse_dgt_sources(raw_sources)
    assert len(servers) == 3
    assert servers[0].name == "Vidstream"
    assert servers[0].proxy_url == "https://animedekho.tv/embed/123/1-1"

    # GDFlix has show_embed: False, so dl_url takes preference
    assert servers[1].name == "GDFlix-720p"
    assert servers[1].proxy_url == "https://animedekho.tv/download2.php?url=abc123dl"

    # VidSrc has show_embed: True, so embed_url takes preference
    assert servers[2].name == "VidSrc-1080p"
    assert servers[2].proxy_url == "https://animedekho.tv/player.php?url=xyz1080embed"


@pytest.mark.anyio
async def test_fetch_watch_data_unlocks_gate():
    api = AnimeDekhoAPI()

    gated_resp = json.dumps({
        "gated": True,
        "gate": "https://animedekho.tv/?dgt_gate=1&next=xyz",
        "hours": 24,
    })
    unlocked_resp = json.dumps({
        "gated": False,
        "sources": [
            {"id": 0, "server": "Vidstream", "embed": "https://animedekho.tv/embed/1/1-1"}
        ]
    })
    gate_html = """
    <html><body>
    <form>
    <input type="hidden" name="shortlink" value="https://animedekho.tv/?dgt_verify=1&amp;token=abc123">
    </form>
    </body></html>
    """

    with patch("api.client.http_client.post", side_effect=[gated_resp, unlocked_resp]) as mock_post, \
         patch("api.client.http_client.get_no_cache", return_value=gate_html) as mock_get:
        watch_data = await api._fetch_watch_data(post_id=874, episode_id=58646)
        assert watch_data.get("gated") is False
        assert len(watch_data.get("sources", [])) == 1

        # Check that gate was fetched and verify link was visited
        mock_get.assert_any_call("https://animedekho.tv/?dgt_gate=1&next=xyz")
        mock_get.assert_any_call("https://animedekho.tv/?dgt_verify=1&token=abc123")


@pytest.mark.anyio
async def test_resolve_animedekho_play_php():
    play_url = "https://animedekho.tv/aaa/get/play.php?url=encrypted_token"
    fetch_resp = json.dumps({
        "results": {
            "instant_dl": {
                "label": "Direct-Link",
                "url": "https://video-downloads.googleusercontent.com/test_video.mp4"
            }
        }
    })

    with patch("extractors.resolver.http_client.get_no_cache", return_value=fetch_resp):
        res = await _resolve_animedekho(play_url)
        assert res is not None
        assert res["url"] == "https://video-downloads.googleusercontent.com/test_video.mp4"
        assert res["type"] == "mp4"


@pytest.mark.anyio
async def test_resolve_animedekho_player_php_wrapper():
    player_url = "https://animedekho.tv/player.php?url=initial_token"
    player_html = '<iframe src="https://animedekho.tv/aaa/get/play.php?url=inner_token"></iframe>'
    fetch_resp = json.dumps({
        "results": {
            "instant_dl": {
                "label": "Direct-Link",
                "url": "https://video-downloads.googleusercontent.com/inner_video.mp4"
            }
        }
    })

    with patch("extractors.resolver.http_client.get", return_value=player_html), \
         patch("extractors.resolver.http_client.get_no_cache", return_value=fetch_resp):
        res = await _resolve_animedekho(player_url)
        assert res is not None
        assert res["url"] == "https://video-downloads.googleusercontent.com/inner_video.mp4"


@pytest.mark.anyio
async def test_resolve_animedekho_neocdn():
    play2_url = "https://animedekho.tv/aaa/myth/play2.php?id=abyss_id"
    play2_html = '<script>fetch("/aaa/myth/fetch.php?id=fetch_token_123")</script>'
    fetch_json = json.dumps({
        "sources": [
            {"type": "480p", "size": "100 MB", "url": "https://storage.example/video_480.mp4"},
            {"type": "720p", "size": "200 MB", "url": "https://storage.example/video_720.mp4"},
            {"type": "1080p", "size": "400 MB", "url": "https://storage.example/video_1080.mp4"},
        ]
    })

    with patch("extractors.resolver.http_client.get", side_effect=[play2_html, fetch_json]):
        res = await _resolve_animedekho(play2_url)
        assert res is not None
        assert res["quality"] == "1080p"
        assert len(res["qualities"]) == 3
        resolutions = [q.resolution for q in res["qualities"]]
        assert resolutions == ["480p", "720p", "1080p"]


@pytest.mark.anyio
async def test_server_priority_boosts_requested_quality():
    servers = [
        VideoServer(name="VidSrc-480p", server_id=1, proxy_url="https://animedekho.tv/player.php?url=480"),
        VideoServer(name="VidSrc-720p", server_id=2, proxy_url="https://animedekho.tv/player.php?url=720"),
        VideoServer(name="VidSrc-1080p", server_id=3, proxy_url="https://animedekho.tv/player.php?url=1080"),
    ]

    # Resolve with quality_pref = "1080p"
    # Even if mocked resolve returns direct urls, VidSrc-1080p should be evaluated first
    call_order = []

    async def mock_resolve_player(url: str):
        if "1080" in url:
            call_order.append("1080p")
            return {"url": "https://dl.example/1080.mp4", "type": "mp4", "quality": "1080p"}
        elif "720" in url:
            call_order.append("720p")
            return {"url": "https://dl.example/720.mp4", "type": "mp4", "quality": "720p"}
        elif "480" in url:
            call_order.append("480p")
            return {"url": "https://dl.example/480.mp4", "type": "mp4", "quality": "480p"}
        return None

    async def mock_resolve_srv(s):
        s.player_url = s.proxy_url
        return s

    with patch("extractors.resolver.resolve_player_url", side_effect=mock_resolve_player), \
         patch("api.client.AnimeDekhoAPI.resolve_server", side_effect=mock_resolve_srv):
        resolved = await _lazy_resolve_servers(servers, quality_pref="1080p")
        # 1080p server was attempted first and succeeded
        assert len(call_order) >= 1
        assert call_order[0] == "1080p"
        cands = _find_quality_candidates(resolved, "1080p")
        assert len(cands) >= 1
        assert cands[0][0].name == "VidSrc-1080p"
        assert cands[0][1].resolution == "1080p"


@pytest.mark.anyio
async def test_server_priority_and_fallback_for_4k():
    servers = [
        VideoServer(name="VidSrc-480p", server_id=1, proxy_url="https://animedekho.tv/player.php?url=480"),
        VideoServer(name="VidSrc-720p", server_id=2, proxy_url="https://animedekho.tv/player.php?url=720"),
        VideoServer(name="VidSrc-1080p", server_id=3, proxy_url="https://animedekho.tv/player.php?url=1080"),
    ]

    call_order = []

    async def mock_resolve_player(url: str):
        if "1080" in url:
            call_order.append("1080p")
            return {"url": "https://dl.example/1080.mp4", "type": "mp4", "quality": "1080p"}
        elif "720" in url:
            call_order.append("720p")
            return {"url": "https://dl.example/720.mp4", "type": "mp4", "quality": "720p"}
        elif "480" in url:
            call_order.append("480p")
            return {"url": "https://dl.example/480.mp4", "type": "mp4", "quality": "480p"}
        return None

    async def mock_resolve_srv(s):
        s.player_url = s.proxy_url
        return s

    with patch("extractors.resolver.resolve_player_url", side_effect=mock_resolve_player), \
         patch("api.client.AnimeDekhoAPI.resolve_server", side_effect=mock_resolve_srv):
        resolved = await _lazy_resolve_servers(servers, quality_pref="4K")
        assert len(call_order) >= 1
        assert call_order[0] == "1080p"
        cands_4k = _find_quality_candidates(resolved, "4K")
        assert len(cands_4k) == 0
        cands_fallback = _find_quality_candidates(resolved, "1080p")
        assert len(cands_fallback) >= 1
        assert cands_fallback[0][0].name == "VidSrc-1080p"
        assert cands_fallback[0][1].resolution == "1080p"
