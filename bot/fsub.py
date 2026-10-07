"""Force Subscribe (FSub) & Timer Invite Link System (Anti-Copyright Protection)."""

from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.telegram import Client, enums
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.telegram.enums import ChatMemberStatus

from config.settings import settings
from bot.auth import is_owner

log = logging.getLogger(__name__)


async def create_timer_invite_link(
    client: Client,
    channel_id: int | str,
    expire_seconds: int = 120,
    member_limit: int = 1,
    name: str = "Timer Link",
) -> str | None:
    """
    Generate a Telegram-enforced temporary invite link that expires after expire_seconds.
    Because expire_date is set in Telegram servers, the link automatically invalidates
    even if the bot goes offline or restarts.
    """
    target_cid = int(channel_id) if str(channel_id).lstrip("-").isdigit() else channel_id
    exp_dt = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)

    try:
        invite = await client.create_chat_invite_link(
            chat_id=target_cid,
            expire_date=exp_dt,
            member_limit=member_limit,
            name=name[:32],
        )
        log.info("Created timer invite link for %s (expires in %ds)", target_cid, expire_seconds)
        return invite.invite_link
    except Exception as e:
        log.warning("Failed to create timer invite link for %s: %s", target_cid, e)
        # Try userbot if client lacks permissions
        from bot.userbot import userbot_manager
        if userbot_manager and userbot_manager.is_active and userbot_manager.client:
            try:
                u_inv = await userbot_manager.client.create_chat_invite_link(
                    chat_id=target_cid,
                    expire_date=exp_dt,
                    member_limit=member_limit,
                    name=name[:32],
                )
                log.info("Userbot created timer invite link for %s", target_cid)
                return u_inv.invite_link
            except Exception as ue:
                log.warning("Userbot also failed creating timer invite link: %s", ue)
        return None


async def _mention(client: Client, user_id: int) -> str:
    """``{mention}`` replacement for owner-authored prompts (issue #35)."""
    try:
        user = await client.get_users(user_id)
        name = (user.first_name or "there")
        name = re.sub(r"[<>&]", "", name)
    except Exception:
        name = "there"
    return f'<a href="tg://user?id={user_id}">{name}</a>'


async def _custom_prompt(db, default_text: str, client: Client, user_id: int) -> str:
    """Owner-supplied FSub text (config ``FSUB_MSG`` / DB ``fsub_msg``).

    config.py documents the ``{mention}`` placeholder; when the owner has not
    set one, the built-in prompt is returned unchanged.
    """
    try:
        custom = await db.get_fsub_msg()
    except Exception as e:
        log.debug("fsub_msg lookup failed: %s", e)
        return default_text
    if not custom or not custom.strip():
        return default_text
    return custom.replace("{mention}", await _mention(client, user_id))


async def send_fsub_prompt(
    message: Message,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send the FSub prompt with the owner's ``FSUB_PIC`` banner when set.

    Falls back to a plain text message (never a hard failure) when the picture
    cannot be sent — issue #35: config.py pictures were silently ignored.
    """
    pic = None
    try:
        from bot.database import db
        if db:
            pic = await db.get_fsub_pic()
    except Exception as e:
        log.debug("fsub_pic lookup failed: %s", e)

    if pic:
        try:
            from utils.helpers import resolve_photo_source
            await message.reply_photo(
                photo=resolve_photo_source(pic),
                caption=text,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=markup,
            )
            return
        except Exception as e:
            log.warning("FSub banner could not be sent (%s) — falling back to text", e)

    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def check_fsub(
    client: Client,
    user_id: int,
    retry_param: str = "",
) -> tuple[bool, str | None, InlineKeyboardMarkup | None]:
    """Check if a user is subscribed to the FSub channel.
    Returns: (is_subscribed, alert_text, reply_markup)
    """
    if is_owner(user_id):
        return True, None, None

    from bot.database import db
    if not db:
        return True, None, None

    # Determine FSub channel
    fsub_chan = await db.get_fsub_channel()
    if not fsub_chan:
        return True, None, None

    # Check member status
    is_member = False
    try:
        member = await client.get_chat_member(fsub_chan, user_id)
        if member and member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            is_member = True
    except Exception as ce:
        log.debug("FSub member check for %d failed: %s", user_id, ce)
        is_member = False

    if is_member:
        return True, None, None

    # User is not a member — construct prompt
    fsub_mod = await db.get_fsub_mod()
    bot_username = getattr(client, "me", None)
    b_user = bot_username.username if bot_username and bot_username.username else "bot"
    retry_url = f"https://t.me/{b_user}?start={retry_param}" if retry_param else f"https://t.me/{b_user}?start=start"

    if fsub_mod:
        # Issue #7: Timer Mode ON -> Timer Link Only.
        # NEVER fall back to permanent invite link!
        invite_url = await create_timer_invite_link(client, fsub_chan, expire_seconds=120, name="FSub 2m Link")

        if not invite_url:
            # Timer link generation failed -> show ONLY "Try Again" button
            log.warning("Timer link generation failed for FSub channel %s. Showing Try Again button.", fsub_chan)
            fail_buttons = [[InlineKeyboardButton("🔄 Try Again", url=retry_url)]]
            fail_text = (
                "⚠️ <b>Could Not Generate Temporary Invite Link</b>\n\n"
                "Unable to create a 2-minute expiring link for our channel right now.\n\n"
                "Please click the <b>Try Again</b> button below to re-generate your link!"
            )
            return False, fail_text, InlineKeyboardMarkup(fail_buttons)

        # Timer link generated successfully
        buttons = [
            [InlineKeyboardButton("📢 Join Channel", url=invite_url)],
            [InlineKeyboardButton("🔄 Try Again", url=retry_url)],
        ]
        text = (
            "📢 <b>Please Join Our Channel to Continue!</b>\n\n"
            "You must be a member of our channel to download files or view content.\n\n"
            "⏳ <i>Note: This invite link is temporary and will automatically expire in <b>2 minutes</b>!</i>\n\n"
            "After joining, click the <b>Try Again</b> button below!"
        )
        return False, await _custom_prompt(db, text, client, user_id), InlineKeyboardMarkup(buttons)

    # Standard link mode (Timer Mode OFF)
    invite_url = None
    if isinstance(fsub_chan, str) and not str(fsub_chan).startswith("-"):
        invite_url = f"https://t.me/{fsub_chan.lstrip('@')}"
    else:
        invite_url = await db.get_config("channel_invite_link")

    buttons = []
    if invite_url:
        buttons.append([InlineKeyboardButton("📢 Join Channel", url=invite_url)])
    buttons.append([InlineKeyboardButton("🔄 Try Again", url=retry_url)])

    text = (
        "📢 <b>Please Join Our Channel to Continue!</b>\n\n"
        "You must be a member of our channel to download files or view content.\n\n"
        "After joining, click the <b>Try Again</b> button below!"
    )

    return False, await _custom_prompt(db, text, client, user_id), InlineKeyboardMarkup(buttons)
