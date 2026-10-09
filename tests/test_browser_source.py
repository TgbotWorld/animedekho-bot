"""Unit tests for /browser_source command and multi-source recent releases browsing."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.models import SearchResult
from bot.telegram.types import Message, CallbackQuery
from bot.keyboards import browse_source_menu, browse_source_page, main_menu
from bot.source_config import BROWSE_SOURCES, normalize_source
from bot.handlers.browser_source import cmd_browser_source, browser_source_callback, BROWSE_SOURCE_NAMES
from config.settings import settings
from extractors.multisource import MultiSourceManager


def test_browse_source_menu_keyboard():
    markup = browse_source_menu(BROWSE_SOURCES)
    assert markup is not None
    assert len(markup.inline_keyboard) > 0
    # Check that known sources are present as buttons
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert "bs:DeadToons:1" in callbacks
    assert "bs:ToonFlix:1" in callbacks
    assert "bs:AnimeDrive:1" in callbacks
    assert "bs:AnimeDekho:1" in callbacks


def test_browse_source_page_keyboard():
    items = [
        SearchResult(
            title="Solo Leveling Season 2 [DeadToons]",
            slug="solo-leveling-season-2",
            url="https://deadtoons.sbs/posts/solo-leveling-season-2",
            content_type="series",
            source="DeadToons",
        ),
        SearchResult(
            title="Tower of God Season 2 [DeadToons]",
            slug="tower-of-god-season-2",
            url="https://deadtoons.sbs/posts/tower-of-god-season-2",
            content_type="series",
            source="DeadToons",
        ),
    ]
    # Page 1, has_next=True
    markup = browse_source_page(items, "DeadToons", page=1, has_next=True)
    cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert any(cb.startswith("sr:") for cb in cbs)
    assert "bs:DeadToons:2" in cbs
    assert "bs:menu" in cbs

    # Page 2, has_next=False
    markup_p2 = browse_source_page(items, "DeadToons", page=2, has_next=False)
    cbs_p2 = [btn.callback_data for row in markup_p2.inline_keyboard for btn in row if btn.callback_data]
    assert "bs:DeadToons:1" in cbs_p2
    assert "bs:DeadToons:3" not in cbs_p2


def test_main_menu_includes_browse_sources():
    markup = main_menu()
    cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert "bs:menu" in cbs


@pytest.mark.anyio
async def test_multisource_get_recent_by_source_registers_slug():
    mgr = MultiSourceManager()
    dummy_items = [
        {"title": "Attack on Titan Final Season", "url": "https://example.com/aot", "poster": "https://example.com/p.jpg"}
    ]
    mock_extractor = MagicMock()
    mock_extractor.get_recent = AsyncMock(return_value=dummy_items)
    mgr.sources = [("DummySource", mock_extractor)]

    results = await mgr.get_recent_by_source("DummySource", page=1)
    assert len(results) == 1
    assert results[0].slug == "attack-on-titan-final-season"
    assert mgr.get_source_for_slug("attack-on-titan-final-season") == "DummySource"


@pytest.mark.anyio
async def test_cmd_browser_source_bare():
    client = MagicMock()
    message = MagicMock(spec=Message)
    message.from_user = MagicMock()
    message.from_user.id = settings.bot.owner_id
    message.text = "/browser_source"
    message.reply_text = AsyncMock()

    await cmd_browser_source(client, message)
    message.reply_text.assert_called_once()
    call_args = message.reply_text.call_args
    assert "Browse Recent Anime Releases by Source" in call_args[0][0]
    assert call_args[1]["reply_markup"] is not None


@pytest.mark.anyio
async def test_cmd_browser_source_with_valid_arg():
    client = MagicMock()
    message = MagicMock(spec=Message)
    message.from_user = MagicMock()
    message.from_user.id = settings.bot.owner_id
    message.text = "/browser_source deadtoons"
    message.chat = MagicMock()
    message.chat.id = 12345
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    message.reply_text = AsyncMock(return_value=status_msg)

    with patch("extractors.multisource.multi_source_manager.get_recent_by_source", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [
            SearchResult(
                title="Bleach TYBW Part 3 [DeadToons]",
                slug="bleach-tybw-part-3",
                url="https://deadtoons.sbs/posts/bleach-tybw-part-3",
                content_type="series",
                source="DeadToons",
            )
        ]
        await cmd_browser_source(client, message)
        mock_get.assert_called_once_with("DeadToons", page=1)
        status_msg.edit_text.assert_called_once()
        assert "DeadToons" in status_msg.edit_text.call_args[0][0]


@pytest.mark.anyio
async def test_browser_source_callback_menu_and_page():
    client = MagicMock()
    query = MagicMock(spec=CallbackQuery)
    query.data = "bs:menu"
    query.message = MagicMock()
    query.message.photo = None
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    await browser_source_callback(client, query)
    query.message.edit_text.assert_called_once()
    assert "Browse Recent Anime Releases by Source" in query.message.edit_text.call_args[0][0]

    # Test bs:ToonFlix:2
    query2 = MagicMock(spec=CallbackQuery)
    query2.data = "bs:ToonFlix:2"
    query2.message = MagicMock()
    query2.message.photo = None
    query2.message.chat = MagicMock()
    query2.message.chat.id = 999
    query2.message.edit_text = AsyncMock()
    query2.answer = AsyncMock()

    with patch("extractors.multisource.multi_source_manager.get_recent_by_source", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [
            SearchResult(
                title="Naruto Shippuden [ToonFlix]",
                slug="naruto-shippuden",
                url="https://toonflix.in/naruto-shippuden",
                content_type="series",
                source="ToonFlix",
            )
        ]
        await browser_source_callback(client, query2)
        mock_get.assert_called_once_with("ToonFlix", page=2)
        query2.message.edit_text.assert_called_once()
        assert "ToonFlix" in query2.message.edit_text.call_args[0][0]
