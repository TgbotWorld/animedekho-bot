"""Unit tests for DeadToons API extractor and shortlink bypass."""

import pytest
from unittest.mock import MagicMock, patch
from extractors.deadtoons import deadtoons, DeadToonsExtractor
from utils.anime_match import normalize_quality


SAMPLE_API_LINKS = [
    {
        "quality": "480p x264",
        "size": "101.57 MB",
        "servers": [
            {"name": "Telegram", "link_server_id": 100},
            {"name": "Pixeldrain", "link_server_id": 101},
            {"name": "HubCloud", "link_server_id": 102},
            {"name": "FilePress", "link_server_id": 103},
        ],
    },
    {
        "quality": "720p x265 10bit",
        "size": "130.7 MB",
        "servers": [
            {"name": "Telegram", "link_server_id": 200},
            {"name": "HubCloud", "link_server_id": 202},
            {"name": "Pixeldrain", "link_server_id": 201},
        ],
    },
    {
        "quality": "1080p x265 10bit",
        "size": "303.48 MB",
        "servers": [
            {"name": "HubCloud", "link_server_id": 302},
            {"name": "Pixeldrain", "link_server_id": 301},
        ],
    },
    {
        "quality": "1080p x264",
        "size": "1.38 GB",
        "servers": [
            {"name": "HubCloud", "link_server_id": 402},
            {"name": "Pixeldrain", "link_server_id": 401},
        ],
    },
]


def test_select_best_quality_link_1080p():
    extractor = DeadToonsExtractor()
    link = extractor._select_best_quality_link(SAMPLE_API_LINKS, "1080p")
    assert link is not None
    assert "1080p" in link["quality"]


def test_select_best_quality_link_720p():
    extractor = DeadToonsExtractor()
    link = extractor._select_best_quality_link(SAMPLE_API_LINKS, "720p")
    assert link is not None
    assert "720p" in link["quality"]


def test_select_best_quality_link_always_chooses_720p_x265_10bit_over_x264():
    extractor = DeadToonsExtractor()
    # DeadToons API commonly returns 720p x264 before 720p x265 10bit
    mixed_links = [
        {"quality": "480p x264", "size": "106.02 MB"},
        {"quality": "720p x264", "size": "204.15 MB"},
        {"quality": "720p x265 10bit", "size": "134.12 MB"},
        {"quality": "1080p x265 10bit", "size": "295.40 MB"},
    ]
    # 1. Standard "720p" preference
    link_720 = extractor._select_best_quality_link(mixed_links, "720p")
    assert link_720 is not None
    assert link_720["quality"] == "720p x265 10bit"
    assert link_720["size"] == "134.12 MB"

    # 2. Explicit "Quality720px26510bit" preference
    link_pref = extractor._select_best_quality_link(mixed_links, "Quality720px26510bit")
    assert link_pref is not None
    assert link_pref["quality"] == "720p x265 10bit"

    # 3. Even when 720p x264 comes first in a 2-item list
    reversed_mixed = [
        {"quality": "720p x264", "size": "204.15 MB"},
        {"quality": "720p x265 10bit", "size": "134.12 MB"},
    ]
    res = extractor._select_best_quality_link(reversed_mixed, "720p")
    assert res is not None
    assert res["quality"] == "720p x265 10bit"

    # 4. Fallback to 720p x264 only when 720p x265 does NOT exist
    x264_only = [
        {"quality": "480p x264", "size": "106.02 MB"},
        {"quality": "720p x264", "size": "204.15 MB"},
    ]
    res_fallback = extractor._select_best_quality_link(x264_only, "720p")
    assert res_fallback is not None
    assert res_fallback["quality"] == "720p x264"


def test_select_best_quality_link_480p():
    extractor = DeadToonsExtractor()
    link = extractor._select_best_quality_link(SAMPLE_API_LINKS, "480p")
    assert link is not None
    assert "480p" in link["quality"]


def test_select_best_quality_link_4k_size_logic():
    extractor = DeadToonsExtractor()
    # 303 MB 1080p x265 is NOT eligible for 4K
    links_small = [SAMPLE_API_LINKS[2]]
    assert extractor._select_best_quality_link(links_small, "4K") is None

    # 1.38 GB 1080p x264 / HQ satisfies 4K
    links_large = [
        {"quality": "1080p HQ x265", "size": "1.45 GB", "servers": [{"name": "HubCloud", "link_server_id": 500}]}
    ]
    matched = extractor._select_best_quality_link(links_large, "4K")
    assert matched is not None
    assert matched["size"] == "1.45 GB"


def test_server_prioritization_skips_telegram():
    extractor = DeadToonsExtractor()
    link = SAMPLE_API_LINKS[0]
    servers = [srv for srv in link["servers"] if (srv.get("name") or "").lower() != "telegram"]
    # HubCloud (0) must precede Pixeldrain (1) and FilePress (2)
    def server_weight(srv: dict) -> int:
        name = (srv.get("name") or "").lower()
        if "hubcloud" in name:
            return 0
        if "pixeldrain" in name:
            return 1
        if "filepress" in name or "fpgo" in name:
            return 2
        return 10
    servers.sort(key=server_weight)
    assert servers[0]["name"] == "HubCloud"
    assert servers[1]["name"] == "Pixeldrain"
    assert servers[2]["name"] == "FilePress"
    assert all(s["name"] != "Telegram" for s in servers)


@patch("extractors.deadtoons.time.sleep")
@patch("extractors.deadtoons.requests.Session")
def test_bypass_shrinkme_shortlink(mock_session_cls, mock_sleep):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session

    # Step 1: get mrproblogger page with go-link form
    page_resp = MagicMock()
    page_resp.status_code = 200
    page_resp.text = """
    <html>
      <body>
        <form id="go-link" action="/links/go" method="POST">
          <input type="hidden" name="_token" value="abc123token" />
          <input type="hidden" name="ad_form_data" value="xyzdata" />
        </form>
        <script>var app_vars = {"counter_value": "1"};</script>
      </body>
    </html>
    """
    page_resp.url = "https://en.mrproblogger.com/TestAlias"

    # Step 2: post go-link form returns callback URL
    post_resp = MagicMock()
    post_resp.status_code = 200
    post_resp.json.return_value = {
        "status": "success",
        "url": "https://archive.deadtoons.sbs/unlock/callback/mock_token_12345"
    }

    mock_session.get.return_value = page_resp
    mock_session.post.return_value = post_resp

    extractor = DeadToonsExtractor()
    cb_url = extractor._bypass_shrinkme_shortlink("https://shrinkme.click/TestAlias")
    assert cb_url == "https://archive.deadtoons.sbs/unlock/callback/mock_token_12345"
