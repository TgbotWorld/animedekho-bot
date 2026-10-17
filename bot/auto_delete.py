"""Persistent Auto-Delete System for Videos & Files — resilient across bot restarts."""

from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.telegram import Client, enums
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)

DELETED_NOTICE_TEXT = (
    "<u><b>Pʀᴇᴠɪᴏᴜs Mᴇssᴀɢᴇ ᴡᴀs Dᴇʟᴇᴛᴇᴅ</b></u> 🗑️\n"
    "<blockquote><b>Iғ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ɢᴇᴛ ᴛʜᴇ ғɪʟᴇ(s) ᴀɢᴀɪɴ, ᴛʜᴇɴ ᴄʟɪᴄᴋ ᴏɴ ɢᴇᴛ ғɪʟᴇ ᴀɢᴀɪɴ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴇʟsᴇ ᴄʟᴏsᴇ ᴛʜɪs ᴍᴇssᴀɢᴇ ʙʏ ᴄʟɪᴄᴋ ᴏɴ ᴄʟᴏsᴇ.</b></blockquote>"
)


class AutoDeleteService:
    """Auto-delete service - disabled per configuration (all sent files/messages are permanent)."""

    def __init__(self, main_client: Client | None = None):
        self.main_client = main_client
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self, main_client: Client | None = None):
        if main_client:
            self.main_client = main_client
        self._running = False
        log.info("AutoDeleteService: Auto-delete feature is disabled. No deletion worker started.")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("AutoDeleteService: Stopped.")

    async def schedule_deletion(
        self,
        client: Client,
        chat_id: int,
        message_id: int,
        get_file_link: str = "",
        file_title: str = "",
        custom_seconds: int | None = None,
    ):
        """No-op: Auto-delete feature has been removed/disabled."""
        return

    async def _worker_loop(self):
        """No-op: worker loop disabled."""
        return

    async def _process_single_job(self, job: dict[str, Any]):
        """No-op: Clear DB job without deleting messages."""
        from bot.database import db
        chat_id = job.get("chat_id")
        message_id = job.get("message_id")
        if db and chat_id and message_id:
            await db.remove_auto_delete_job(chat_id, message_id)


# Singleton instance
auto_delete_service = AutoDeleteService()


async def handle_close_dlt_notice(client: Client, query: CallbackQuery):
    """Callback query handler to dismiss the auto-delete notification."""
    try:
        await query.message.delete()
    except Exception:
        pass
    try:
        await query.answer("Closed")
    except Exception:
        pass
