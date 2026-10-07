"""User authorization — owner-managed approved user list (MongoDB-backed)."""

from __future__ import annotations
import logging
import re
from functools import wraps

from bot.telegram import Client
from bot.telegram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
)

from config.settings import settings

log = logging.getLogger(__name__)


# ── Checks ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == settings.bot.owner_id


async def is_approved(user_id: int) -> bool:
    """Check if user is approved (async — uses MongoDB)."""
    if is_owner(user_id):
        return True
    from bot.database import db
    if db is None:
        log.warning("Database not initialized, denying user %d", user_id)
        return False
    return await db.is_approved(user_id)


async def add_user(user_id: int, username: str = "", added_by: int = 0) -> bool:
    """Add user. Returns True if newly added."""
    from bot.database import db
    if db is None:
        return False
    return await db.add_user(user_id, username, added_by)


async def remove_user(user_id: int) -> bool:
    """Remove user. Returns True if removed."""
    from bot.database import db
    if db is None:
        return False
    return await db.remove_user(user_id)


async def get_users() -> list[int]:
    """Get all approved user IDs."""
    from bot.database import db
    if db is None:
        return []
    return await db.get_users()


# ── Decorator ──────────────────────────────────────────────────────────

def require_approved(func):
    """Decorator: blocks unapproved users. Checks force sub too.
    
    Works with both Message and CallbackQuery handlers.
    Pyrogram signature: async def handler(client: Client, update: Message|CallbackQuery)
    """
    @wraps(func)
    async def wrapper(client: Client, update):
        # update is either Message or CallbackQuery
        user = update.from_user
        user_id = user.id if user else 0

        if not await is_approved(user_id):
            if isinstance(update, CallbackQuery):
                await update.answer("🔒 You don't have access. Ask the owner to add you.", show_alert=True)
            elif isinstance(update, Message):
                await update.reply_text("🔒 You don't have access. Ask the owner to add you.")
            return

        # Force sub check (owner bypasses)
        if not is_owner(user_id):
            from bot.fsub import check_fsub, send_fsub_prompt
            is_sub, fsub_text, fsub_markup = await check_fsub(client, user_id)
            if not is_sub:
                if isinstance(update, CallbackQuery):
                    # Alerts are plain text — strip the HTML the prompt uses.
                    plain = re.sub(r"<[^>]+>", "", fsub_text or "") or "📢 Please join our channel to use this bot!"
                    await update.answer(plain[:180], show_alert=True)
                    if update.message:
                        await send_fsub_prompt(update.message, fsub_text, fsub_markup)
                elif isinstance(update, Message):
                    await send_fsub_prompt(update, fsub_text, fsub_markup)
                return

        return await func(client, update)
    return wrapper


def require_owner(func):
    """Decorator: blocks non-owners.
    
    Pyrogram signature: async def handler(client: Client, message: Message)
    """
    @wraps(func)
    async def wrapper(client: Client, message: Message):
        user_id = message.from_user.id if message.from_user else 0
        if not is_owner(user_id):
            await message.reply_text("⚠️ This command is owner-only.")
            return
        return await func(client, message)
    return wrapper
