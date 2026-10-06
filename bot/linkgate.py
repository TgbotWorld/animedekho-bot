"""Link Gate (issue #33) — the gated ⬇ DOWNLOAD button on channel posts.

Flow taken from the demo video in issue #33:

    channel post  →  ⬇ DOWNLOAD  →  bot  →  JOIN / REQUEST TO JOIN (2nd channel)
                  →  approved     →  TRY AGAIN  →  480p | 720p | 1080p buttons

The gate is **opt-in**: with no gate channel configured, ``_build_album_buttons``
keeps emitting its normal quality rows, so existing deployments and the whole
download path are untouched until an owner runs ``/linkgate <channel>``.

Three ways to present the second channel (``/linkgate <channel> <mode>``):

``request``  — Telegram *join-request* button ("Request to Join Channel"), the
               owner approves manually. This is what the demo video shows.
``timer``    — 2-minute expiring invite link (the FSub anti-copyright timer).
``link``     — plain public @username / stored invite link.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from bot.telegram import Client
from bot.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

CHANNEL_KEY = "link_gate_channel"
MODE_KEY = "link_gate_mode"
MODES = ("request", "timer", "link")
DEFAULT_MODE = "request"

#: Deep-link parameter prefix for a gated download.
PARAM_PREFIX = "dl_"


# ── deep-link parameter ─────────────────────────────────────────────────────

def encode_gate_param(slug: str, season: int = 1) -> str:
    """``dl_<slug>_<season>`` → URL-safe base64 (same codec as file links)."""
    from utils.helpers import encode_file_param
    return encode_file_param(f"{PARAM_PREFIX}{slug}_{int(season)}")


def decode_gate_param(param: str) -> tuple[str, int]:
    """``dl_<slug>_<season>`` → ``(slug, season)``. Tolerant of bad input."""
    raw = param or ""
    if not raw.startswith(PARAM_PREFIX):
        # Already-decoded values and stray raw params still resolve.
        if not raw or raw.startswith(("get_", "join_")):
            return "", 1
    else:
        raw = raw[len(PARAM_PREFIX):]
    if not raw:
        return "", 1
    slug, season = raw, 1
    if "_" in raw:
        head, tail = raw.rsplit("_", 1)
        if tail.isdigit() and head:
            slug, season = head, int(tail)
    return slug, max(1, season)


# ── config ──────────────────────────────────────────────────────────────────

async def _cfg(key: str, default=None):
    from bot.database import db
    if not db:
        return default
    try:
        return await db.get_config(key, default=default)
    except Exception as e:
        log.debug("linkgate config read failed (%s): %s", key, e)
        return default


async def _set_cfg(key: str, value) -> None:
    from bot.database import db
    if not db:
        return
    try:
        await db.set_config(key, value)
    except Exception as e:
        log.warning("linkgate config write failed (%s): %s", key, e)


async def get_gate_channel():
    """Gate channel id/username, or ``None`` when the gate is switched off."""
    val = await _cfg(CHANNEL_KEY, default=None)
    if val is None:
        return None
    if isinstance(val, str) and val.strip().lower() in ("", "off", "none", "0", "false"):
        return None
    text = str(val).strip()
    return int(text) if text.lstrip("-").isdigit() else text


async def set_gate_channel(value) -> None:
    await _set_cfg(CHANNEL_KEY, value)


async def get_gate_mode() -> str:
    val = await _cfg(MODE_KEY, default=None)
    mode = str(val or DEFAULT_MODE).strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


async def set_gate_mode(mode: str) -> None:
    await _set_cfg(MODE_KEY, mode if mode in MODES else DEFAULT_MODE)


async def is_enabled() -> bool:
    return await get_gate_channel() is not None


# ── membership ──────────────────────────────────────────────────────────────

async def _is_member(client: Client, channel, user_id: int) -> bool:
    if not channel or not user_id:
        return False
    try:
        from bot.telegram.enums import ChatMemberStatus
        member = await client.get_chat_member(channel, user_id)
        return bool(member) and member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception as e:
        # Not a member is the common case here (Telegram raises for outsiders).
        log.debug("Gate membership check failed for %d on %s: %s", user_id, channel, e)
        return False


# ── invite links ────────────────────────────────────────────────────────────

async def _join_request_link(client: Client, channel, user_id: int) -> str:
    """Invite link that makes Telegram show the *Request to Join* button."""
    name = f"Gate {user_id}"[:32]
    try:
        invite = await client.create_chat_invite_link(
            chat_id=channel, name=name, creates_join_request=True
        )
        if invite and invite.invite_link:
            return invite.invite_link
    except Exception as e:
        log.debug("create_chat_invite_link(join request) failed for %s: %s", channel, e)

    # Fallback: the bot may lack invite rights — the userbot often has them.
    try:
        from bot.userbot import userbot_manager
        if userbot_manager and userbot_manager.is_active and userbot_manager.client:
            invite = await userbot_manager.client.create_chat_invite_link(
                chat_id=channel, name=name, creates_join_request=True
            )
            if invite and invite.invite_link:
                return invite.invite_link
    except Exception as ue:
        log.debug("userbot join-request link failed for %s: %s", channel, ue)
    return ""


async def _timer_link(client: Client, channel, user_id: int) -> str:
    from bot.fsub import create_timer_invite_link
    return await create_timer_invite_link(
        client, channel, expire_seconds=120, member_limit=1,
        name=f"Gate {user_id}"[:32],
    ) or ""


async def _plain_link(client: Client, channel) -> str:
    """Public @username, else a stored invite link, else nothing."""
    if isinstance(channel, str):
        return f"https://t.me/{channel.lstrip('@')}"
    from bot.database import db
    if db:
        try:
            link = await db.get_config("channel_invite_link")
            if link:
                return str(link)
        except Exception:
            pass
    try:
        chat = await client.get_chat(channel)
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}"
        invite = getattr(chat, "invite_link", None)
        if invite:
            return str(invite)
    except Exception as e:
        log.debug("Gate plain-link lookup failed for %s: %s", channel, e)
    return ""


async def _invite_link(client: Client, channel, user_id: int) -> tuple[str, str]:
    """Return ``(url, button_label)`` for the configured mode, with fallbacks."""
    mode = await get_gate_mode()

    if mode == "request":
        url = await _join_request_link(client, channel, user_id)
        if url:
            return url, "✋ REQUEST TO JOIN"
        # Join-request links need invite rights + the channel's approval
        # setting — never dead-end when that isn't available.
        log.warning("Gate mode 'request' unavailable for %s — falling back to timer link", channel)
        url = await _timer_link(client, channel, user_id)
        if url:
            return url, "📢 JOIN CHANNEL"

    elif mode == "timer":
        url = await _timer_link(client, channel, user_id)
        if url:
            return url, "📢 JOIN CHANNEL"

    url = await _plain_link(client, channel)
    if url:
        return url, "📢 JOIN CHANNEL"
    return "", ""


# ── the gate itself ─────────────────────────────────────────────────────────

async def gate_prompt(
    client: Client,
    user_id: int,
    slug: str,
    season: int = 1,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Block until the user has joined the gate channel.

    Returns ``(text, markup)`` when the user still has to pass the gate, or
    ``None`` when they are through (or the gate is switched off) — the caller
    then hands over the real content.
    """
    if not user_id:
        return None
    from bot.auth import is_owner
    if is_owner(user_id):
        return None

    channel = await get_gate_channel()
    if channel is None:
        return None
    if await _is_member(client, channel, user_id):
        return None

    url, label = await _invite_link(client, channel, user_id)
    retry = f"https://t.me/{_bot_username(client)}?start={encode_gate_param(slug, season)}"

    buttons: list[list[InlineKeyboardButton]] = []
    if url:
        buttons.append([InlineKeyboardButton(label, url=url)])
    buttons.append([InlineKeyboardButton("🔄 TRY AGAIN", url=retry)])

    head = "✅ <b>HERE IS YOUR LINK!</b>" if url else "⏳ <b>ONE MOMENT…</b>"
    text = (
        f"{head}\n\n"
        "<b>CLICK BELOW TO PROCEED</b>\n\n"
        "You need to be a member of our channel first — your request is\n"
        "reviewed by an admin, then tap <b>TRY AGAIN</b> to collect the\n"
        "480p / 720p / 1080p download buttons."
    )
    if not url:
        text = (
            "⚠️ <b>Could Not Generate An Invite Link</b>\n\n"
            "The gate channel did not return a join link right now.\n"
            "Tap <b>TRY AGAIN</b> in a moment to retry."
        )
    return text, InlineKeyboardMarkup(buttons)


def _bot_username(client: Client) -> str:
    me = getattr(client, "me", None)
    if me and getattr(me, "username", None):
        return me.username
    try:
        from config import Config
        uname = getattr(Config, "BOT_USERNAME", "") or ""
        if uname:
            return uname.lstrip("@")
    except Exception:
        pass
    return "bot"
