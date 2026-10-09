"""
Browser Source Handler (/browser_source).

Allows users to browse recently released anime and series across different sources:
AnimeDekho, DeadToons, ToonFlix, AnimeDrive, AnimeDubHindi, ToonWorld4All, TOONo.
"""

from __future__ import annotations

import html
import logging
from bot.telegram import Client, enums
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.auth import require_approved
from bot.keyboards import browse_source_menu, browse_source_page
from bot.source_config import normalize_source, BROWSE_SOURCES
from extractors.multisource import multi_source_manager

log = logging.getLogger(__name__)

BROWSE_SOURCE_NAMES = [name for name, _, _ in BROWSE_SOURCES]


def _sources_menu_text() -> str:
    lines = [
        "🌐 <b>Browse Recent Anime Releases by Source</b>\n",
        "Select a source below to browse its newest anime episodes &amp; releases:\n",
    ]
    for name, desc, emoji in BROWSE_SOURCES:
        lines.append(f"• {emoji} <b>{html.escape(name)}</b> — {html.escape(desc)}")
    lines.append(
        "\n<i>💡 Tip: You can also type <code>/browser_source &lt;source&gt;</code> to jump straight in!</i>"
    )
    return "\n".join(lines)


@require_approved
async def cmd_browser_source(client: Client, message: Message):
    """Handler for /browser_source [source_name]."""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        req = parts[1].strip()
        norm = normalize_source(req)
        if norm and norm in BROWSE_SOURCE_NAMES:
            await _show_source_page(client, message, norm, page=1)
            return
        elif norm:
            await _show_source_page(client, message, norm, page=1)
            return
        else:
            await message.reply_text(
                f"⚠️ Unknown source <code>{html.escape(req)}</code>.\n"
                "Please choose from the available sources below:",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=browse_source_menu(BROWSE_SOURCES),
            )
            return

    # No argument: show sources menu
    await _show_sources_menu(client, message)


async def _show_sources_menu(client: Client, target: Message | CallbackQuery):
    text = _sources_menu_text()
    markup = browse_source_menu(BROWSE_SOURCES)

    if isinstance(target, CallbackQuery):
        try:
            if target.message and target.message.photo:
                await target.message.delete()
                await client.send_message(
                    chat_id=target.message.chat.id,
                    text=text,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=markup,
                )
            elif target.message:
                await target.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
            await target.answer()
        except Exception as e:
            log.debug("Failed to edit message in _show_sources_menu: %s", e)
            if target.message:
                await client.send_message(
                    target.message.chat.id,
                    text,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=markup,
                )
    else:
        await target.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def _show_source_page(
    client: Client,
    target: Message | CallbackQuery,
    source_name: str,
    page: int = 1,
):
    chat_id = target.chat.id if isinstance(target, Message) else target.message.chat.id
    status_msg = None

    if isinstance(target, CallbackQuery):
        try:
            await target.answer(f"Loading {source_name} (Page {page})...")
        except Exception:
            pass
    else:
        status_msg = await target.reply_text(
            f"🔄 <i>Fetching recent releases from <b>{html.escape(source_name)}</b>...</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        items = await multi_source_manager.get_recent_by_source(source_name, page=page)
    except Exception as e:
        log.warning("get_recent_by_source failed for %s (page %d): %s", source_name, page, e)
        items = []

    if not items:
        err_text = (
            f"⚠️ No recent releases found for <b>{html.escape(source_name)}</b> (page {page}).\n\n"
            "The source may be temporarily unavailable or has reached the end of the listing."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Choose Another Source", callback_data="bs:menu")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="m:main")],
        ])
        if status_msg:
            await status_msg.edit_text(err_text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        elif isinstance(target, CallbackQuery) and target.message:
            await target.message.edit_text(err_text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        else:
            await client.send_message(chat_id, err_text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        return

    text = (
        f"🌐 <b>{html.escape(source_name)}</b> — Recently Released Anime\n"
        f"<i>Page {page} • Tap an anime below to view episodes &amp; download:</i>"
    )
    markup = browse_source_page(items, source_name, page, has_next=(len(items) >= 8))

    if status_msg:
        await status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    elif isinstance(target, CallbackQuery) and target.message:
        try:
            if target.message.photo:
                await target.message.delete()
                await client.send_message(chat_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
            else:
                await target.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            await client.send_message(chat_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    else:
        await client.send_message(chat_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def browser_source_callback(client: Client, query: CallbackQuery):
    """Handle all bs:* callback queries for source browsing."""
    data = query.data or ""
    if data == "bs:menu":
        await _show_sources_menu(client, query)
        return

    parts = data.split(":")
    if len(parts) >= 3:
        source_name = parts[1]
        try:
            page = int(parts[2])
        except ValueError:
            page = 1
        await _show_source_page(client, query, source_name, page=page)
