"""Worker Bot & Shared Admin Handlers (/users, /ban, /uban, /broadcast, /fsub, /dlt_time, /stats, /tutorial)."""

from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Any

from bot.telegram import Client, enums, filters, errors
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import settings
from bot.auth import is_owner
from bot.health import get_uptime, get_system_stats

log = logging.getLogger(__name__)

# Net I/O tracker for real-time DL/UL speed calculation
_last_net_time: float = time.time()
_last_net_bytes: tuple[int, int] = (0, 0)
try:
    import psutil
    _net = psutil.net_io_counters()
    _last_net_bytes = (_net.bytes_recv, _net.bytes_sent)
except Exception:
    pass


def get_network_speeds() -> tuple[str, str]:
    """Calculate current network download and upload speeds in human-readable format."""
    global _last_net_time, _last_net_bytes
    try:
        import psutil
        now = time.time()
        elapsed = max(0.1, now - _last_net_time)
        net = psutil.net_io_counters()
        recv_diff = max(0, net.bytes_recv - _last_net_bytes[0])
        sent_diff = max(0, net.bytes_sent - _last_net_bytes[1])
        _last_net_time = now
        _last_net_bytes = (net.bytes_recv, net.bytes_sent)

        dl_rate = recv_diff / elapsed
        ul_rate = sent_diff / elapsed

        def fmt_speed(rate_bytes: float) -> str:
            if rate_bytes >= 1024 * 1024:
                return f"{rate_bytes / (1024 * 1024):.2f} MB/s"
            elif rate_bytes >= 1024:
                return f"{rate_bytes / 1024:.1f} KB/s"
            else:
                return "0B/s"

        return fmt_speed(dl_rate), fmt_speed(ul_rate)
    except Exception:
        return "0B/s", "0B/s"


def format_stats_card() -> str:
    """Format the exact ASCII/Unicode VPS stats banner requested by user."""
    from bot.health import get_uptime, get_system_stats
    import psutil

    _, uptime_str = get_uptime()
    # Format uptime nicely as Xh Ym Zs
    uptime_fmt = uptime_str.replace(" ", "")

    sys_stats = get_system_stats()
    dl_str, ul_str = get_network_speeds()

    du = psutil.disk_usage("/")
    free_gb = round(du.free / (1024 ** 3), 2)
    total_disk_gb = round(du.total / (1024 ** 3), 2)

    vm = psutil.virtual_memory()
    used_mb = round(vm.used / (1024 ** 2), 2)
    total_ram_gb = round(vm.total / (1024 ** 3), 2)

    return (
        f"📊 <b>BOT STATS</b>   » <code>{total_disk_gb:.2f} GB</code>\n"
        "▬▬▬▬▬▬✘▬▬▬▬▬▬\n"
        f"╭ CPU » <code>{sys_stats['cpu_pct']:.1f}%</code> | UP » <code>{uptime_fmt}</code>\n"
        f"┊ RAM » <code>{sys_stats['ram_pct']:.2f}%</code> ({used_mb:.2f} MB/{total_ram_gb:.2f} GB)\n"
        f"┊ DISK » <code>{sys_stats['disk_pct']:.1f}%</code> | FREE <code>{free_gb:.2f} GB</code>\n"
        f"╰ DL » <code>{dl_str}</code> | UL » <code>{ul_str}</code>"
    )


# ── Commands ─────────────────────────────────────────────────────────

async def cmd_stats(client: Client, message: Message):
    """Display real-time VPS stats and bot performance."""
    user = message.from_user
    if user and not is_owner(user.id):
        from bot.auth import is_approved
        if not await is_approved(user.id):
            await message.reply_text("⛔ Access denied.")
            return

    card = format_stats_card()
    await message.reply_text(card, parse_mode=enums.ParseMode.HTML)


async def cmd_users_count(client: Client, message: Message):
    """Check total bot user count across network."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not connected.")
        return

    total_users = await db.get_bot_users_count()
    banned_users = await db.get_banned_users_count()
    approved_users = len(await db.get_users())

    text = (
        "👥 <b>Bot Network User Analytics</b>\n\n"
        f"• <b>Total Registered Users:</b> <code>{total_users}</code>\n"
        f"• <b>Approved Whitelist Users:</b> <code>{approved_users}</code>\n"
        f"• <b>Banned Users:</b> <code>{banned_users}</code>\n\n"
        "<i>All users who interacted with main bot or child workers are indexed.</i>"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


async def cmd_ban(client: Client, message: Message):
    """Ban a user from accessing all bots in the network."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    target_id = None
    reason = "Violating terms"

    # Check reply
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            reason = parts[1]
    else:
        parts = message.text.split(maxsplit=2)
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            target_id = int(parts[1])
            if len(parts) > 2:
                reason = parts[2]

    if not target_id:
        await message.reply_text("Usage: <code>/ban &lt;user_id&gt; [reason]</code> or reply to user message.", parse_mode=enums.ParseMode.HTML)
        return

    if is_owner(target_id):
        await message.reply_text("👑 You cannot ban the bot owner!")
        return

    success = await db.ban_user(target_id, reason=reason, banned_by=user.id if user else 0)
    if success:
        await message.reply_text(
            f"🚫 <b>User Banned</b>\n\n• <b>User ID:</b> <code>{target_id}</code>\n• <b>Reason:</b> {reason}",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text("⚠️ Failed to ban user.")


async def cmd_unban(client: Client, message: Message):
    """Unban a user from the bot network."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            target_id = int(parts[1])

    if not target_id:
        await message.reply_text("Usage: <code>/uban &lt;user_id&gt;</code> or <code>/unban &lt;user_id&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    success = await db.unban_user(target_id)
    if success:
        await message.reply_text(f"✅ User <code>{target_id}</code> has been unbanned.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"ℹ️ User <code>{target_id}</code> was not in the banned list.", parse_mode=enums.ParseMode.HTML)


async def cmd_broadcast(client: Client, message: Message):
    """Broadcast text message to all bot users."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    text_to_send = ""
    if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        text_to_send = message.reply_to_message.text or message.reply_to_message.caption
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            text_to_send = parts[1]

    if not text_to_send:
        await message.reply_text("Usage: <code>/broadcast &lt;message&gt;</code> or reply to a text message with /broadcast", parse_mode=enums.ParseMode.HTML)
        return

    user_ids = await db.get_all_bot_user_ids()
    if not user_ids:
        await message.reply_text("⚠️ No users found in database to broadcast to.")
        return

    status_msg = await message.reply_text(f"📢 <b>Broadcasting to {len(user_ids)} users...</b>", parse_mode=enums.ParseMode.HTML)
    sent, failed, blocked = 0, 0, 0

    for i, uid in enumerate(user_ids, 1):
        try:
            await client.send_message(chat_id=uid, text=text_to_send, parse_mode=enums.ParseMode.HTML)
            sent += 1
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value)
            try:
                await client.send_message(chat_id=uid, text=text_to_send, parse_mode=enums.ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        except Exception as e:
            err_str = str(e).lower()
            if "blocked" in err_str or "user_deactivated" in err_str:
                blocked += 1
            else:
                failed += 1

        if i % 30 == 0:
            try:
                await status_msg.edit_text(
                    f"📢 <b>Broadcasting Progress:</b> {i}/{len(user_ids)}\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 🚫 Blocked: {blocked}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"• Total Target: {len(user_ids)}\n"
        f"• Successfully Sent: {sent}\n"
        f"• Blocked / Deactivated: {blocked}\n"
        f"• Failed: {failed}",
        parse_mode=enums.ParseMode.HTML,
    )


async def cmd_pbroadcast(client: Client, message: Message):
    """Broadcast photo to all bot users (reply to a photo)."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply_text("Usage: Reply to a photo with <code>/pbroadcast [optional caption]</code>", parse_mode=enums.ParseMode.HTML)
        return

    from bot.database import db
    user_ids = await db.get_all_bot_user_ids()
    if not user_ids:
        await message.reply_text("⚠️ No users found.")
        return

    photo_id = message.reply_to_message.photo.file_id
    caption = message.reply_to_message.caption or ""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        caption = parts[1]

    status_msg = await message.reply_text(f"🖼 <b>Broadcasting photo to {len(user_ids)} users...</b>", parse_mode=enums.ParseMode.HTML)
    sent, failed, blocked = 0, 0, 0

    for i, uid in enumerate(user_ids, 1):
        try:
            await client.send_photo(chat_id=uid, photo=photo_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            sent += 1
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value)
            try:
                await client.send_photo(chat_id=uid, photo=photo_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
        except Exception as e:
            if "blocked" in str(e).lower():
                blocked += 1
            else:
                failed += 1

        if i % 30 == 0:
            try:
                await status_msg.edit_text(
                    f"🖼 <b>Photo Broadcast:</b> {i}/{len(user_ids)}\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 🚫 Blocked: {blocked}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Photo Broadcast Completed!</b>\n\n• Sent: {sent}\n• Blocked: {blocked}\n• Failed: {failed}",
        parse_mode=enums.ParseMode.HTML,
    )


async def cmd_dbroadcast(client: Client, message: Message):
    """Broadcast document/video to all bot users (reply to a doc or video)."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    rep = message.reply_to_message
    if not rep or (not rep.document and not rep.video):
        await message.reply_text("Usage: Reply to a video or document with <code>/dbroadcast [optional caption]</code>", parse_mode=enums.ParseMode.HTML)
        return

    from bot.database import db
    user_ids = await db.get_all_bot_user_ids()
    if not user_ids:
        await message.reply_text("⚠️ No users found.")
        return

    caption = rep.caption or ""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        caption = parts[1]

    status_msg = await message.reply_text(f"📁 <b>Broadcasting file to {len(user_ids)} users...</b>", parse_mode=enums.ParseMode.HTML)
    sent, failed, blocked = 0, 0, 0

    for i, uid in enumerate(user_ids, 1):
        try:
            if rep.video:
                await client.send_video(chat_id=uid, video=rep.video.file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            else:
                await client.send_document(chat_id=uid, document=rep.document.file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            sent += 1
        except Exception as e:
            if "blocked" in str(e).lower():
                blocked += 1
            else:
                failed += 1

        if i % 30 == 0:
            try:
                await status_msg.edit_text(
                    f"📁 <b>File Broadcast:</b> {i}/{len(user_ids)}\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed} | 🚫 Blocked: {blocked}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>File Broadcast Completed!</b>\n\n• Sent: {sent}\n• Blocked: {blocked}\n• Failed: {failed}",
        parse_mode=enums.ParseMode.HTML,
    )


async def cmd_fsub(client: Client, message: Message):
    """Manage Force Subscribe channel."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    if message.text and message.text.strip().startswith("/fsub"):
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            arg = parts[1].strip()
            if arg.lower() in ("off", "disable", "none", "0"):
                await db.set_fsub_channel(None)
                await message.reply_text("✅ Force Subscribe (FSub) has been disabled.")
                return
            else:
                # Set channel
                chan_val = int(arg) if arg.lstrip("-").isdigit() else arg
                await db.set_fsub_channel(chan_val)
                await message.reply_text(f"✅ FSub channel updated to: <code>{chan_val}</code>", parse_mode=enums.ParseMode.HTML)
                return

    # View status
    current_chan = await db.get_fsub_channel()
    fsub_mod = await db.get_fsub_mod()

    text = (
        "📢 <b>Force Subscribe (FSub) System</b>\n\n"
        f"• <b>Target Channel:</b> <code>{current_chan or 'Disabled'}</code>\n"
        f"• <b>Timer Link Mode (/fsub_mod):</b> <code>{'ON (2 min expiring links)' if fsub_mod else 'OFF (Standard links)'}</code>\n\n"
        "<b>Commands:</b>\n"
        "• <code>/fsub &lt;channel_id_or_username&gt;</code> — Set FSub channel\n"
        "• <code>/fsub off</code> — Disable FSub check\n"
        "• <code>/fsub_mod on</code> — Enable 2-minute expiring timer links\n"
        "• <code>/fsub_mod off</code> — Use standard permanent links"
    )
    buttons = [
        [
            InlineKeyboardButton(f"Timer Mode: {'ON ✅' if fsub_mod else 'OFF ❌'}", callback_data="toggle_fsub_mod"),
        ]
    ]
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cmd_fsub_mod(client: Client, message: Message):
    """Toggle FSub Timer Link Mode (ON: 2-min auto-expiring links; OFF: normal links)."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    from bot.database import db
    if not db:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        mode_str = parts[1].strip().lower()
        if mode_str in ("on", "true", "1", "yes", "enable"):
            await db.set_fsub_mod(True)
            await message.reply_text("🔒 <b>FSub Timer Mode: ON</b>\n\nAll FSub invite links will now be generated as <b>2-minute auto-expiring links</b> to prevent link sharing and copyright strikes.", parse_mode=enums.ParseMode.HTML)
            return
        elif mode_str in ("off", "false", "0", "no", "disable"):
            await db.set_fsub_mod(False)
            await message.reply_text("🔓 <b>FSub Timer Mode: OFF</b>\n\nFSub will use standard normal channel links.", parse_mode=enums.ParseMode.HTML)
            return

    # Toggle if no param
    current = await db.get_fsub_mod()
    new_state = not current
    await db.set_fsub_mod(new_state)
    state_str = "ON (2-min expiring links)" if new_state else "OFF (Normal links)"
    await message.reply_text(f"⚙️ <b>FSub Mode Toggled:</b> <code>{state_str}</code>", parse_mode=enums.ParseMode.HTML)


async def cmd_dlt_time(client: Client, message: Message):
    """Notify that auto-delete feature has been removed/disabled."""
    user = message.from_user
    if user and not is_owner(user.id):
        await message.reply_text("⛔ Owner only command.")
        return

    await message.reply_text(
        "ℹ️ <b>Auto-Delete Feature Removed</b>\n\n"
        "The auto-delete feature has been removed. All delivered files and messages are now permanent and will not be automatically deleted.",
        parse_mode=enums.ParseMode.HTML,
    )


def _get_tutorial_arch_text() -> str:
    return (
        "📚 <b>AnimeDekho System Architecture & Setup Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>1. Multi-Bot Worker Fleet (Load Balancing)</b>\n"
        "• Register child worker bots via <code>/addbot &lt;token&gt; [quality]</code>.\n"
        "• Assign dedicated quality tiers (<code>1080p</code>, <code>720p</code>, <code>480p</code>) or <code>all</code>.\n"
        "• Deep links on channel album cards automatically distribute user downloads across active child workers.\n\n"
        "<b>2. Channel Mapping & Audio Selection</b>\n"
        "• Map dedicated anime channels naturally: <code>/mapchannel Solo Leveling -100123456789</code>.\n"
        "• Interactive buttons prompt to select audio track: <i>Hindi, English, Japanese, Multi Audio</i>.\n"
        "• Change audio language anytime with <code>/setaudio &lt;slug&gt; &lt;language&gt;</code>.\n\n"
        "<b>3. Dedicated Storage Dump & Auto-Monitor</b>\n"
        "• Set dump channel: <code>/setdump -100123456789</code> (used for backup and monitor notices).\n"
        "• Toggle automatic monitoring: <code>/monitor on</code> (global) or <code>/monitor on &lt;channel_id&gt;</code>.\n"
        "• Auto-monitor automatically checks for releases, retries 3x, and cleans up notices on success.\n\n"
        "<b>4. Secure Base64 Deep Links</b>\n"
        "• URL-safe base64 deep links mask internal episode identifiers (<code>start=Z2V0...</code>).\n"
        "• Fast instant delivery directly to users' private chat with full permanence.\n\n"
        "<b>5. Visual Dashboard & UI Styles</b>\n"
        "• Open <code>/settings</code> to visually toggle styles, upload mode (Video/Document), and features.\n"
        "• <code>/startstyle modern</code>, <code>/poststyle modern</code>, <code>/schedstyle modern</code>.\n"
        "• <code>/postsched</code> triggers an instant schedule post test to your main channel.\n\n"
        "<b>6. Fleet Management & Monitoring</b>\n"
        "• <code>/users</code> — Total registered users\n"
        "• <code>/stats</code> — Real-time VPS performance & network speed card\n"
        "• <code>/health</code> — Diagnostic checks for Mongo, APIs, and disk\n"
        "• <code>/broadcast</code>, <code>/pbroadcast</code>, <code>/dbroadcast</code> — Global broadcasts\n"
        "• <code>/ban &lt;id&gt;</code> & <code>/uban &lt;id&gt;</code> — Manage user access"
    )


def _get_tutorial_pc_text() -> str:
    return (
        "💻 <b>AnimeDekho Local PC &amp; VPS Setup Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>1. System Prerequisites:</b>\n"
        "• <b>Python:</b> 3.10, 3.11, 3.12 (Python 3.14 compatible)\n"
        "• <b>FFmpeg:</b> Required in system PATH for video remuxing\n"
        "• <b>N_m3u8DL-RE:</b> (Recommended) For multi-audio HLS/DASH streams\n"
        "• <b>MongoDB:</b> MongoDB Atlas URI or local instance (v5.0+)\n\n"
        "<b>2. Step-by-Step Installation:</b>\n"
        "<code># 1. Clone repository</code>\n"
        "<code>git clone https://github.com/TgbotWorld/animedekho-bot.git</code>\n"
        "<code>cd animedekho-bot</code>\n\n"
        "<code># 2. Setup Virtual Environment</code>\n"
        "<code>python -m venv venv</code>\n"
        "<code># Windows: venv\\Scripts\\activate</code>\n"
        "<code># Linux/macOS: source venv/bin/activate</code>\n\n"
        "<code># 3. Install Python Dependencies</code>\n"
        "<code>pip install --upgrade pip</code>\n"
        "<code>pip install -r requirements.txt</code>\n\n"
        "<code># 4. Install FFmpeg</code>\n"
        "<code># Windows: winget install Gyan.FFmpeg</code>\n"
        "<code># Ubuntu/Debian: sudo apt update &amp;&amp; sudo apt install -y ffmpeg</code>\n"
        "<code># macOS: brew install ffmpeg</code>\n"
        "<code>ffmpeg -version  # Verify installation</code>\n\n"
        "<code># 5. Configure Environment Variables</code>\n"
        "<code>cp .env.example .env</code>\n"
        "Fill in <code>BOT_TOKEN</code>, <code>API_ID</code>, <code>API_HASH</code>, <code>OWNER_ID</code>, <code>MONGO_URI</code>.\n\n"
        "<code># 6. Launch Bot</code>\n"
        "<code>python main.py</code>\n\n"
        "<b>3. Common Errors &amp; Solutions:</b>\n"
        "• <b>FFmpeg not found in PATH:</b> Add FFmpeg binary folder to system environment variables and restart terminal.\n"
        "• <b>MongoDB ServerSelectionTimeoutError:</b> Add <code>0.0.0.0/0</code> in MongoDB Atlas <i>Network Access</i> IP whitelist.\n"
        "• <b>Telegram FLOOD_WAIT:</b> Bot features built-in backoff; wait out seconds gracefully.\n"
        "• <b>Missing Hindi Dub Audio:</b> Ensure FFmpeg 5.0+ or N_m3u8DL-RE is installed for multi-audio streams."
    )


def _get_tutorial_buttons(active_view: str) -> InlineKeyboardMarkup:
    if active_view == "pc":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 System & Fleet Architecture", callback_data="tutorial:arch")],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💻 Local PC Setup & Errors Guide", callback_data="tutorial:pc")],
        ])


async def cmd_tutorial(client: Client, message: Message):
    """Display comprehensive system tutorial explaining Main Bot, Workers & Channel Automation."""
    text = _get_tutorial_arch_text()
    buttons = _get_tutorial_buttons("arch")
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)


async def tutorial_callback(client: Client, query: CallbackQuery):
    """Toggle between architecture and PC setup guides."""
    data = query.data
    view = data.split(":", 1)[1] if ":" in data else "arch"
    if view == "pc":
        text = _get_tutorial_pc_text()
        buttons = _get_tutorial_buttons("pc")
    else:
        text = _get_tutorial_arch_text()
        buttons = _get_tutorial_buttons("arch")

    try:
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
    except Exception:
        pass
    await query.answer()


async def dlt_time_callback(client: Client, query: CallbackQuery):
    """Handle preset auto-delete time selection buttons (feature removed)."""
    await query.answer("Auto-delete feature has been removed.", show_alert=True)
    try:
        await query.message.edit_text(
            "ℹ️ <b>Auto-Delete Feature Removed</b>\n\n"
            "All user-delivered files and messages are now permanent and will not be automatically deleted.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


async def toggle_fsub_mod_callback(client: Client, query: CallbackQuery):
    """Handle FSub timer mode toggle button."""
    user = query.from_user
    if user and not is_owner(user.id):
        await query.answer("⛔ Owner only", show_alert=True)
        return

    from bot.database import db
    cur = await db.get_fsub_mod()
    new_st = not cur
    await db.set_fsub_mod(new_st)

    await query.answer(f"FSub Timer Mode: {'ON' if new_st else 'OFF'}")
    current_chan = await db.get_fsub_channel()

    text = (
        "📢 <b>Force Subscribe (FSub) System</b>\n\n"
        f"• <b>Target Channel:</b> <code>{current_chan or 'Disabled'}</code>\n"
        f"• <b>Timer Link Mode (/fsub_mod):</b> <code>{'ON (2 min expiring links)' if new_st else 'OFF (Standard links)'}</code>\n\n"
        "<b>Commands:</b>\n"
        "• <code>/fsub &lt;channel_id_or_username&gt;</code> — Set FSub channel\n"
        "• <code>/fsub off</code> — Disable FSub check\n"
        "• <code>/fsub_mod on</code> — Enable 2-minute expiring timer links\n"
        "• <code>/fsub_mod off</code> — Use standard permanent links"
    )
    buttons = [
        [
            InlineKeyboardButton(f"Timer Mode: {'ON ✅' if new_st else 'OFF ❌'}", callback_data="toggle_fsub_mod"),
        ]
    ]
    try:
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        pass
