"""Text message handler — treats any text as a search query."""

import logging

from bot.telegram import Client, enums
from bot.telegram.types import Message

from api.client import api
from bot.keyboards import search_results
from bot.auth import require_approved
import bot.logger
from utils.helpers import esc

log = logging.getLogger(__name__)


@require_approved
async def handle_text(client: Client, message: Message):
    query = message.text.strip()
    if not query:
        return

    user = message.from_user

    # Check if user is in userbot login wizard
    from bot.userbot import userbot_manager
    if userbot_manager and user and userbot_manager.is_in_login(user.id):
        reply_text, is_finished = await userbot_manager.handle_login_step(user.id, query)
        try:
            await message.delete()
        except Exception:
            pass
        await message.reply_text(reply_text, parse_mode=enums.ParseMode.HTML)
        return

    # Direct Anime Name Feature Toggle (Issue #9)
    from bot.database import db
    auto_search_enabled = True
    if db:
        auto_search_enabled = await db.get_auto_search()
    if not auto_search_enabled:
        return

    await do_search(client, message, query)


async def do_search(client: Client, message: Message, query: str):
    """Execute anime search and display result buttons."""
    user = message.from_user
    msg = await message.reply_text("🔍 Searching the library…")

    # Log the search
    if bot.logger.bot_logger and user:
        await bot.logger.bot_logger.log_search(user.id, user.username or user.first_name, query)

    try:
        results = await api.search(query)
        if not results:
            try:
                await msg.edit_text("🚫 No results — try another spelling or the original title.")
            except Exception:
                await message.reply_text("🚫 No results — try another spelling or the original title.")
            return

        diag_prefix = ""
        from config.settings import settings
        is_admin = bool(user and (user.id == settings.bot.owner_id or user.id in (settings.bot.admin_ids or [])))
        if is_admin and message.chat.type == enums.ChatType.PRIVATE:
            src_counts = {}
            for r in results:
                s = getattr(r, "source", "AnimeDekho")
                src_counts[s] = src_counts.get(s, 0) + 1
            diag_str = ", ".join(f"{k}: {v}" for k, v in src_counts.items())
            diag_prefix = f"🛠 <b>[Admin Diagnostics]</b> <i>{diag_str}</i>\n\n"

        search_header = (
            f"{diag_prefix}🔍 <b>Results for:</b> {esc(query)}\n\n"
            "🎯 <i>Tap a title to open it:</i>"
        )

        try:
            await msg.edit_text(
                search_header,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=search_results(results),
            )
        except Exception:
            await message.reply_text(
                search_header,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=search_results(results),
            )
    except Exception as e:
        log.exception("Search failed")
        try:
            await msg.edit_text(f"⚠️ Search error: {esc(str(e)[:150])}")
        except Exception:
            try:
                await message.reply_text(f"⚠️ Search error: {esc(str(e)[:150])}")
            except Exception:
                pass
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_error("search", str(e))
