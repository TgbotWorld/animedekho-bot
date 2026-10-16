"""Unit tests for default source search and download prioritization."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from api.models import SearchResult, VideoServer, Quality
from extractors.multisource import MultiSourceManager
import bot.source_config as sc


@pytest.mark.anyio
async def test_search_by_source_extractor():
    msm = MultiSourceManager()
    mock_extractor = MagicMock()
    mock_extractor.search = AsyncMock(return_value=[
        {"title": "Solo Leveling", "url": "https://example.com/solo", "poster": "https://example.com/p.jpg"}
    ])

    with patch.object(msm, "sources", [("DeadToons", mock_extractor)]):
        results = await msm.search_by_source("DeadToons", "Solo Leveling")
        assert len(results) == 1
        assert results[0].title == "Solo Leveling [DeadToons]"
        assert results[0].source == "DeadToons"
        assert msm.get_source_for_slug(results[0].slug) == "DeadToons"


@pytest.mark.anyio
async def test_api_search_prioritizes_animedekho_when_default():
    from api.client import api

    with patch("bot.source_config.get_default_source", AsyncMock(return_value="AnimeDekho")), \
         patch.object(api, "_search_animedekho", AsyncMock(return_value=[
             SearchResult(title="Naruto", slug="naruto", url="https://animedekho.tv/naruto", content_type="series", source="AnimeDekho")
         ])) as mock_ad_search, \
         patch("extractors.multisource.multi_source_manager.search_fallback", AsyncMock()) as mock_fallback, \
         patch("extractors.multisource.multi_source_manager.search_by_source", AsyncMock()) as mock_by_source:

        res = await api.search("Naruto")
        assert len(res) == 1
        assert res[0].title == "Naruto"
        mock_ad_search.assert_called_once()
        mock_fallback.assert_not_called()
        mock_by_source.assert_not_called()


@pytest.mark.anyio
async def test_api_search_prioritizes_custom_default_source():
    from api.client import api

    mock_dt_results = [
        SearchResult(title="Solo Leveling [DeadToons]", slug="solo-leveling", url="https://deadtoons.sbs/solo", content_type="series", source="DeadToons")
    ]

    with patch("bot.source_config.get_default_source", AsyncMock(return_value="DeadToons")), \
         patch("extractors.multisource.multi_source_manager.search_by_source", AsyncMock(return_value=mock_dt_results)) as mock_by_source, \
         patch.object(api, "_search_animedekho", AsyncMock()) as mock_ad_search, \
         patch("extractors.multisource.multi_source_manager.search_fallback", AsyncMock()) as mock_fallback:

        res = await api.search("Solo Leveling")
        assert len(res) == 1
        assert res[0].source == "DeadToons"
        mock_by_source.assert_called_once_with("DeadToons", "Solo Leveling")
        mock_ad_search.assert_not_called()
        mock_fallback.assert_not_called()


@pytest.mark.anyio
async def test_api_search_falls_back_when_default_source_empty():
    from api.client import api

    ad_results = [
        SearchResult(title="Demon Slayer", slug="demon-slayer", url="https://animedekho.tv/ds", content_type="series", source="AnimeDekho")
    ]

    with patch("bot.source_config.get_default_source", AsyncMock(return_value="DeadToons")), \
         patch("extractors.multisource.multi_source_manager.search_by_source", AsyncMock(return_value=[])) as mock_by_source, \
         patch.object(api, "_search_animedekho", AsyncMock(return_value=ad_results)) as mock_ad_search:

        res = await api.search("Demon Slayer")
        assert len(res) == 1
        assert res[0].title == "Demon Slayer"
        mock_by_source.assert_called()
        mock_ad_search.assert_called()


@pytest.mark.anyio
async def test_resolve_episode_stream_favors_default_source():
    msm = MultiSourceManager()

    cand_deadtoons = {
        "url": "https://hubcloud.xyz/dt_link",
        "quality": "1080p",
        "source": "DeadToons",
        "provider": "HubCloud",
    }
    cand_toonflix = {
        "url": "https://drive.toonflix.in/tf_link",
        "quality": "1080p",
        "source": "ToonFlix",
        "provider": "ToonFlix",
    }

    # select_fastest_healthy returns the item and health diagnostics
    async def mock_select_fastest(items):
        return items[0], [{"status": "healthy", "source": items[0]["source"], "url": items[0]["url"]}]

    with patch("bot.source_config.get_default_source", AsyncMock(return_value="DeadToons")), \
         patch.object(msm, "_resolve_one_source", AsyncMock(side_effect=[
             (cand_toonflix, "ToonFlix: exact 1080p ✓", "exact"),
             (cand_deadtoons, "DeadToons: exact 1080p ✓", "exact"),
         ])), \
         patch("extractors.health_probe.select_fastest_healthy", side_effect=mock_select_fastest):

        # resolve_episode_stream with preferred_source="DeadToons"
        result = await msm.resolve_episode_stream(
            series_title="Solo Leveling",
            season=1,
            episode=1,
            quality_pref="1080p",
            preferred_source="DeadToons",
        )
        assert result is not None
        assert result["source"] == "DeadToons"


@pytest.mark.anyio
async def test_settings_default_source_toggle():
    from bot.handlers.settings import settings_callback
    from bot.database import db

    mock_query = MagicMock()
    mock_query.data = "set_toggle:default_source"
    mock_query.from_user = MagicMock(id=8973817948)
    mock_query.answer = AsyncMock()
    mock_query.message = MagicMock()
    mock_query.message.edit_text = AsyncMock()

    mock_db = MagicMock()
    mock_db.get_config = AsyncMock(return_value="AnimeDekho")
    mock_db.set_config = AsyncMock()
    mock_db.get_upload_mode = AsyncMock(return_value="video")
    mock_db.get_fsub_mod = AsyncMock(return_value=True)
    mock_db.get_dlt_time = AsyncMock(return_value=600)
    mock_db.get_enable_custom_emoji = AsyncMock(return_value=False)
    mock_db.get_thumb_template = AsyncMock(return_value="streaming")
    mock_db.get_random_thumb_template = AsyncMock(return_value=False)
    mock_db.get_start_style = AsyncMock(return_value="modern")
    mock_db.get_sched_style = AsyncMock(return_value="modern")
    mock_db.get_ep_style = AsyncMock(return_value="modern")
    mock_db.get_post_style = AsyncMock(return_value="modern")
    mock_db.get_auto_thumb = AsyncMock(return_value=True)
    mock_db.get_auto_schedule_post = AsyncMock(return_value=False)
    mock_db.get_auto_search = AsyncMock(return_value=True)

    with patch("bot.handlers.settings.is_owner", return_value=True), \
         patch("bot.database.db", mock_db), \
         patch("bot.source_config.set_default_source", AsyncMock(return_value="AnimeDubHindi")) as mock_set:

        await settings_callback(None, mock_query)
        mock_set.assert_called_once()
        mock_query.answer.assert_called_once()


@pytest.mark.anyio
async def test_search_animedekho_dgt_rest():
    from api.client import api
    import json

    mock_resp = json.dumps({
        "items": [
            {
                "id": 4198,
                "title": "Solo Leveling",
                "year": 2024,
                "type": "series",
                "url": "https://animedekho.tv/series-hindi/solo-leveling-hindi/",
                "poster": "https://image.tmdb.org/t/p/w342/rsOApVLbIQEcNkqSlOxNPyg3FyI.jpg"
            }
        ]
    })

    with patch("utils.http.http_client.get", AsyncMock(return_value=mock_resp)):
        res = await api._search_animedekho("Solo Leveling")
        assert len(res) == 1
        assert res[0].title == "Solo Leveling"
        assert res[0].slug == "solo-leveling-hindi"
        assert res[0].content_type == "series"
        assert res[0].source == "AnimeDekho"


@pytest.mark.anyio
async def test_dgt_series_seasons_parsing_and_fetch():
    from api.client import api
    import json

    series_html = """
    <html>
      <head><title>Solo Leveling (Hindi Dubbed) | AnimeDekho</title></head>
      <body>
        <div id="dgt-title" class="dgt-title" data-series="4198"></div>
        <div id="dgt-season-tabs" class="dgt-tabs" data-tabs='[{"season":1,"label":"Season 1"},{"season":2,"label":"Season 2"}]'></div>
        <a id="dgt-watch-btn" href="https://animedekho.tv/epi/solo-leveling-hindi-1x1/">Start</a>
      </body>
    </html>
    """

    s1_json = json.dumps({
        "episodes": [
            {"id": 101, "season": 1, "episode": 1, "name": "Episode 1", "url": "https://animedekho.tv/epi/solo-leveling-hindi-1x1/"},
            {"id": 102, "season": 1, "episode": 2, "name": "Episode 2", "url": "https://animedekho.tv/epi/solo-leveling-hindi-1x2/"},
        ]
    })
    s2_json = json.dumps({
        "episodes": [
            {"id": 201, "season": 2, "episode": 1, "name": "Episode 1", "url": "https://animedekho.tv/epi/solo-leveling-2x1/"},
            {"id": 202, "season": 2, "episode": 2, "name": "Episode 2", "url": "https://animedekho.tv/epi/solo-leveling-2x2/"},
            {"id": 203, "season": 2, "episode": 3, "name": "Episode 3", "url": "https://animedekho.tv/epi/solo-leveling-2x3/"},
        ]
    })

    async def mock_get(url, **kwargs):
        if "season=1" in url:
            return s1_json
        elif "season=2" in url:
            return s2_json
        return series_html

    with patch("utils.http.http_client.get", side_effect=mock_get):
        series = await api.get_series("solo-leveling-hindi")
        assert series.season_count == 2
        assert 1 in series.seasons
        assert 2 in series.seasons
        assert len(series.seasons[1].episodes) == 2
        assert len(series.seasons[2].episodes) == 3
        assert series.total_episodes == 5
        assert series.seasons[2].episodes[0].slug == "solo-leveling-2x1"


@pytest.mark.anyio
async def test_dgt_episode_watch_servers():
    from api.client import api
    import json

    ep_html = """
    <html>
      <head><title>Watch Solo Leveling S2E1 | AnimeDekho</title></head>
      <body>
        <div id="dgt-watch" data-title="4198" data-term="201"></div>
      </body>
    </html>
    """

    watch_json = json.dumps({
        "gated": False,
        "sources": [
            {"id": 1, "kind": "embed", "server": "HydraX", "embed": "https://animedekho.tv/player.php?url=xyz123"},
            {"id": 2, "kind": "embed", "server": "Vidmoly", "embed": "https://animedekho.tv/player.php?url=abc456"},
        ]
    })

    with patch("utils.http.http_client.get", AsyncMock(return_value=ep_html)), \
         patch("utils.http.http_client.post", AsyncMock(return_value=watch_json)):
        ep = await api.get_episode("solo-leveling-2x1")
        assert len(ep.servers) == 2
        assert ep.servers[0].name == "HydraX"
        assert ep.servers[0].proxy_url == "https://animedekho.tv/player.php?url=xyz123"
        assert ep.servers[1].name == "Vidmoly"

