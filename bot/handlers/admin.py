"""Owner-only admin commands."""

import logging
import os
import re
import html as htmlmod
from datetime import datetime, timezone
from pathlib import Path
from bot.telegram import Client, enums
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.auth import require_owner, add_user, remove_user, get_users, is_owner
import bot.logger
from bot.endseason import EOS_STICKER_KEY as _EOS_KEY  # single source of truth

log = logging.getLogger(__name__)


def _parse_args(message: Message) -> list[str]:
    """Parse arguments from message text (everything after the command)."""
    parts = message.text.split()
    return parts[1:] if len(parts) > 1 else []


@require_owner
async def cmd_adduser(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /adduser <telegram_id>")
        return
    uid = int(args[0])
    if is_owner(uid):
        await message.reply_text("👑 That's the owner — already has access.")
        return
    if await add_user(uid, added_by=message.from_user.id):
        await message.reply_text(f"✅ User <code>{uid}</code> added.", parse_mode=enums.ParseMode.HTML)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_user_added(uid)
    else:
        await message.reply_text("ℹ️ User already approved.")


@require_owner
async def cmd_removeuser(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /removeuser <telegram_id>")
        return
    uid = int(args[0])
    if is_owner(uid):
        await message.reply_text("👑 Can't remove the owner.")
        return
    if await remove_user(uid):
        await message.reply_text(f"✅ User <code>{uid}</code> removed.", parse_mode=enums.ParseMode.HTML)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_user_removed(uid)
    else:
        await message.reply_text("ℹ️ User not in the approved list.")


@require_owner
async def cmd_users(client: Client, message: Message):
    users = await get_users()
    if not users:
        await message.reply_text("📋 No approved users (only owner has access).")
        return
    lines = [f"  • <code>{uid}</code>" for uid in users]
    await message.reply_text(
        f"📋 <b>Approved Users ({len(users)}):</b>\n" + "\n".join(lines),
        parse_mode=enums.ParseMode.HTML,
    )



@require_owner
async def cmd_setchannellink(client: Client, message: Message):
    """Set the channel invite link for force subscribe button."""
    args = _parse_args(message)
    if not args:
        await message.reply_text(
            "Usage: /setchannellink <invite_link>\n\n"
            "Example: /setchannellink https://t.me/yourchannel"
        )
        return
    link = args[0].strip()
    if not link.startswith("https://"):
        await message.reply_text("⚠️ Please provide a valid HTTPS link.")
        return

    from bot.database import db
    if db:
        await db.set_config("channel_invite_link", link)
        await message.reply_text(
            f"✅ Channel invite link set:\n<code>{link}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text("⚠️ Database not initialized yet.")


@require_owner
async def cmd_addbot(client: Client, message: Message):
    """Add a child worker bot to distribute download load."""
    args = _parse_args(message)
    if not args:
        await message.reply_text(
            "<b>Usage:</b> <code>/addbot &lt;bot_token&gt; [quality]</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/addbot 123456:ABC... 480p</code> (serves 480p)\n"
            "• <code>/addbot 123456:ABC... 720p</code> (serves 720p)\n"
            "• <code>/addbot 123456:ABC... 1080p</code> (serves 1080p)\n"
            "• <code>/addbot 123456:ABC... 4K</code> (serves 4K/HQ)\n"
            "• <code>/addbot 123456:ABC... all</code> (serves all qualities)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    token = args[0].strip()
    quality = args[1].strip().lower() if len(args) > 1 else "all"

    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager is not initialized.")
        return

    status_msg = await message.reply_text("⏳ Verifying token with Telegram...", parse_mode=enums.ParseMode.HTML)
    try:
        res = await child_bot_manager.add_bot(token, quality=quality)
        await status_msg.edit_text(
            f"✅ <b>Child Worker Bot Connected!</b>\n\n"
            f"🤖 <b>Bot:</b> @{res['username']} (<code>{res['bot_id']}</code>)\n"
            f"⚡ <b>Assigned Quality:</b> <code>{res['quality'].upper()}</code>\n"
            f"🟢 <b>Status:</b> Online & Serving\n\n"
            f"<i>Channel post buttons will now direct users to this bot for {res['quality'].upper()} requests.</i>\n"
            f"💡 <i>Tip: Run /refreshalbums to update all existing channel album buttons!</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Failed to add child bot:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_delbot(client: Client, message: Message):
    """Remove a child worker bot."""
    args = _parse_args(message)
    if not args:
        await message.reply_text("Usage: <code>/delbot &lt;@username or bot_id&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    identifier = args[0].strip()
    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager not initialized.")
        return

    success = await child_bot_manager.remove_bot(identifier)
    if success:
        await message.reply_text(f"🗑️ Child bot <code>{identifier}</code> stopped and removed from system.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"❌ Child bot <code>{identifier}</code> not found in database.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_bots(client: Client, message: Message):
    """List all connected child worker bots."""
    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager not initialized.")
        return

    bots = await child_bot_manager.get_all_bots()
    main_me = await client.get_me()

    text = f"👑 <b>Main Controller Bot:</b> @{main_me.username}\n\n"
    if not bots:
        text += (
            "🤖 <b>No Child Worker Bots Connected.</b>\n\n"
            "All download requests and links are handled directly by the Main Bot.\n\n"
            "To add worker bots for load balancing:\n"
            "<code>/addbot &lt;token&gt; 480p</code>\n"
            "<code>/addbot &lt;token&gt; 720p</code>\n"
            "<code>/addbot &lt;token&gt; 1080p</code>\n"
            "<code>/addbot &lt;token&gt; 4K</code>"
        )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        return

    text += f"🤖 <b>Child Worker Bots ({len(bots)}):</b>\n\n"
    for i, b in enumerate(bots, 1):
        status_icon = "🟢 Online" if b.get("is_online") else "🔴 Offline"
        served = b.get("files_served", 0)
        q = b.get("quality", "all").upper()
        text += (
            f"<b>{i}. @{b.get('username', 'Unknown')}</b>\n"
            f"   • <b>ID:</b> <code>{b.get('bot_id')}</code>\n"
            f"   • <b>Target Quality:</b> <code>{q}</code>\n"
            f"   • <b>Status:</b> {status_icon}\n"
            f"   • <b>Files Delivered:</b> {served}\n\n"
        )

    text += "<i>Commands: /addbot, /delbot, /setbotquality, /refreshalbums</i>"
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_setbotquality(client: Client, message: Message):
    """Change the assigned quality tier for a child worker bot."""
    args = _parse_args(message)
    if len(args) < 2:
        await message.reply_text("Usage: <code>/setbotquality &lt;@username or bot_id&gt; &lt;480p|720p|1080p|4k|all&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    identifier = args[0].strip()
    quality = args[1].strip().lower()

    from bot.child_bots import child_bot_manager
    if not child_bot_manager:
        await message.reply_text("⚠️ Child bot manager not initialized.")
        return

    success = await child_bot_manager.set_bot_quality(identifier, quality)
    if success:
        await message.reply_text(f"✅ Child bot <code>{identifier}</code> quality updated to <b>[{quality.upper()}]</b>.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"❌ Child bot <code>{identifier}</code> not found.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_refreshalbums(client: Client, message: Message):
    """Re-build buttons on all existing channel album posts with current child bot links."""
    from bot.library import library_manager
    if not library_manager or not library_manager.channel:
        await message.reply_text("⚠️ Main channel or library manager is not configured.")
        return

    status_msg = await message.reply_text("🔄 Updating channel album posts with latest child bot links...", parse_mode=enums.ParseMode.HTML)
    try:
        count = await library_manager.refresh_all_albums()
        await status_msg.edit_text(f"✅ Successfully refreshed <b>{count}</b> channel album post(s) with updated bot links!", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to refresh channel albums: {e}")


@require_owner
async def cmd_source(client: Client, message: Message):
    """Show or change the default download source (runtime, no restart)."""
    from bot.source_config import (
        SOURCE_CATALOG,
        get_default_source,
        set_default_source,
        normalize_source,
    )
    args = _parse_args(message)
    current = await get_default_source()

    if not args:
        lines = [
            "🎬 <b>Default Download Source</b>",
            f"Current: <b>{htmlmod.escape(current)}</b>",
            "",
            "<b>Available sources:</b>",
        ]
        for name, desc in SOURCE_CATALOG:
            mark = "  ⬅ <b>DEFAULT</b>" if name == current else ""
            lines.append(f"• <code>{htmlmod.escape(name)}</code> — {desc}{mark}")
        lines += [
            "",
            "Usage: <code>/source &lt;name&gt;</code>",
            "The default source resolves <b>first</b> for every new download;",
            "all other sources stay as automatic fallback. Changes apply instantly.",
        ]
        await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
        return

    requested = " ".join(args)
    if not normalize_source(requested):
        await message.reply_text(
            f"❌ Unknown source <code>{htmlmod.escape(requested)}</code>.\n"
            "Use <code>/source</code> to list the available sources.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    try:
        new = await set_default_source(requested)
    except Exception as e:
        await message.reply_text(f"⚠️ Could not change default source: {e}")
        return

    if new == current:
        await message.reply_text(
            f"ℹ️ <code>{htmlmod.escape(new)}</code> is already the default source.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    await message.reply_text(
        f"✅ <b>Default source changed</b>\n"
        f"<code>{htmlmod.escape(current)}</code> → <b>{htmlmod.escape(new)}</b>\n\n"
        f"New downloads now resolve from <b>{htmlmod.escape(new)}</b> first; "
        "every other source remains as fallback. No restart needed.",
        parse_mode=enums.ParseMode.HTML,
    )


@require_owner
async def cmd_delete(client: Client, message: Message):
    """Interactive delete — shows all downloaded series as buttons."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not initialized.")
        return

    # Get all unique series from files collection
    pipeline = [
        {"$group": {
            "_id": "$series_slug",
            "title": {"$first": "$series_title"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"title": 1}},
    ]
    series_list = await db.files.aggregate(pipeline).to_list(length=100)

    if not series_list:
        await message.reply_text("📂 No downloaded files in the library.")
        return

    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from utils.helpers import slug_to_title

    buttons = []
    for s in series_list:
        slug = s["_id"]
        title = s.get("title") or slug_to_title(slug)
        count = s["count"]
        buttons.append([InlineKeyboardButton(
            f"📺 {title} ({count} files)",
            callback_data=f"del:s:{slug[:40]}",
        )])

    await message.reply_text(
        "🗑️ <b>Delete Manager</b>\n\nSelect a series to manage:",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def delete_callback(client: Client, query):
    """Handle all delete-related callbacks (del:*)."""
    from bot.database import db
    from bot.library import library_manager
    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from utils.helpers import slug_to_title
    from bot.telegram import enums as pe
    from bot.auth import is_owner

    user = query.from_user
    if not user or not is_owner(user.id):
        await query.answer("⛔ Access denied: Owner only.", show_alert=True)
        return

    if not db:
        await query.answer("DB not ready", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data.startswith("del:s:"):
        # Show episodes for this series
        series_slug = data[6:]
        cursor = db.files.find({"series_slug": series_slug})
        all_files = await cursor.to_list(length=None)

        if not all_files:
            await query.edit_message_text("⚠️ No files found for this series.")
            return

        title = all_files[0].get("series_title") or slug_to_title(series_slug)

        # Group by episode
        episodes: dict[str, list[str]] = {}
        for f in all_files:
            ep = f["episode_key"]
            q = f["quality"]
            episodes.setdefault(ep, []).append(q)

        # Sort episodes
        import re
        def _sort_key(k):
            m = re.match(r"S(\d+)E(\d+)", k, re.IGNORECASE)
            return (int(m.group(1)), int(m.group(2))) if m else (999, 0)

        sorted_eps = sorted(episodes.keys(), key=_sort_key)

        buttons = []
        for ep in sorted_eps:
            quals = ", ".join(sorted(episodes[ep]))
            label = f"🎬 {ep} [{quals}]" if ep.lower() == "movie" else f"▶️ {ep} [{quals}]"
            buttons.append([InlineKeyboardButton(
                label,
                callback_data=f"del:e:{series_slug[:30]}:{ep}",
            )])

        # Add "Delete ALL" button
        buttons.append([InlineKeyboardButton(
            f"🗑️ DELETE ENTIRE SERIES ({len(all_files)} files)",
            callback_data=f"del:all:{series_slug[:40]}",
        )])
        # Back button
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="del:back")])

        await query.edit_message_text(
            f"🗑️ <b>{title}</b>\n\n"
            f"📂 {len(sorted_eps)} episode(s) · {len(all_files)} file(s)\n\n"
            f"Tap an episode to delete it:",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:e:"):
        # Show confirmation for deleting a specific episode
        parts = data[6:].rsplit(":", 1)
        series_slug = parts[0]
        episode_key = parts[1]

        # Get qualities for this episode
        cursor = db.files.find({"series_slug": series_slug, "episode_key": episode_key})
        files = await cursor.to_list(length=None)
        title = files[0].get("series_title", series_slug) if files else series_slug
        quals = [f["quality"] for f in files]

        buttons = []
        # Delete specific quality
        for q in sorted(quals):
            buttons.append([InlineKeyboardButton(
                f"🗑️ Delete {episode_key} [{q}]",
                callback_data=f"del:x:{series_slug[:25]}:{episode_key}:{q}",
            )])
        # Delete all qualities for this episode
        if len(quals) > 1:
            buttons.append([InlineKeyboardButton(
                f"🗑️ Delete {episode_key} [ALL qualities]",
                callback_data=f"del:x:{series_slug[:25]}:{episode_key}:*",
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data=f"del:s:{series_slug[:40]}")])

        await query.edit_message_text(
            f"🗑️ <b>Delete {episode_key}</b>\n"
            f"📺 {slug_to_title(series_slug)}\n"
            f"📊 Qualities: {', '.join(sorted(quals))}\n\n"
            f"What do you want to delete?",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:x:"):
        # Execute deletion of specific episode+quality
        parts = data[6:].rsplit(":", 2)
        series_slug = parts[0]
        episode_key = parts[1]
        quality = parts[2]  # "*" means all qualities

        file_query = {"series_slug": series_slug, "episode_key": episode_key}
        if quality != "*":
            file_query["quality"] = quality

        deleted = await db.files.delete_many(file_query)
        await _refresh_album(db, library_manager, series_slug)

        await query.edit_message_text(
            f"✅ <b>Deleted!</b>\n"
            f"🗑️ {deleted.deleted_count} file(s) removed\n"
            f"📺 {episode_key} {'[' + quality + ']' if quality != '*' else '[all qualities]'}",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Back to series", callback_data=f"del:s:{series_slug[:40]}")],
                [InlineKeyboardButton("🏠 Delete menu", callback_data="del:back")],
            ]),
        )

    elif data.startswith("del:all:"):
        # Confirm delete entire series
        series_slug = data[8:]
        count = await db.files.count_documents({"series_slug": series_slug})
        title = slug_to_title(series_slug)

        buttons = [
            [InlineKeyboardButton(
                f"⚠️ YES, DELETE ALL {count} FILES",
                callback_data=f"del:confirm:{series_slug[:40]}",
            )],
            [InlineKeyboardButton("◀️ Cancel", callback_data=f"del:s:{series_slug[:40]}")],
        ]

        await query.edit_message_text(
            f"⚠️ <b>Are you sure?</b>\n\n"
            f"This will permanently delete:\n"
            f"📺 {title}\n"
            f"📁 {count} file(s)\n"
            f"📨 Album post from channel\n\n"
            f"<b>This cannot be undone!</b>",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:confirm:"):
        # Execute full series deletion
        series_slug = data[12:]
        deleted = await db.files.delete_many({"series_slug": series_slug})

        if library_manager:
            await library_manager.delete_album(series_slug)

        await db.downloads.delete_many({"series_slug": series_slug})

        await query.edit_message_text(
            f"✅ <b>Series deleted permanently!</b>\n"
            f"🗑️ {deleted.deleted_count} file(s) removed\n"
            f"📨 Album removed from channel",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Delete menu", callback_data="del:back")],
            ]),
        )

    elif data == "del:back":
        # Back to series list — re-run the list
        pipeline = [
            {"$group": {
                "_id": "$series_slug",
                "title": {"$first": "$series_title"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"title": 1}},
        ]
        series_list = await db.files.aggregate(pipeline).to_list(length=100)

        if not series_list:
            await query.edit_message_text("📂 Library is empty — nothing to delete.")
            return

        buttons = []
        for s in series_list:
            slug = s["_id"]
            title = s.get("title") or slug_to_title(slug)
            count = s["count"]
            buttons.append([InlineKeyboardButton(
                f"📺 {title} ({count} files)",
                callback_data=f"del:s:{slug[:40]}",
            )])

        await query.edit_message_text(
            "🗑️ <b>Delete Manager</b>\n\nSelect a series to manage:",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def _refresh_album(db, library_manager, series_slug: str):
    """After deletion, update or remove the album in main channel."""
    remaining = await db.files.count_documents({"series_slug": series_slug})
    if remaining == 0:
        if library_manager:
            await library_manager.delete_album(series_slug)
    else:
        if library_manager:
            sample = await db.files.find_one({"series_slug": series_slug})
            if sample:
                await library_manager.save_to_library(
                    series_slug=series_slug,
                    series_title=sample.get("series_title", series_slug),
                    quality=sample["quality"],
                    episode_key=sample["episode_key"],
                    file_id=sample["file_id"],
                    file_unique_id=sample["file_unique_id"],
                )


# ── Userbot & Channel Mapping Commands ──────────────────────────


@require_owner
async def cmd_login(client: Client, message: Message):
    """
    Login userbot session.
    Usage:
      /login - Start interactive phone login wizard
      /login <string_session> - Direct login with Pyrogram string session
    """
    from bot.userbot import userbot_manager
    if not userbot_manager:
        await message.reply_text("⚠️ Userbot Manager not initialized.")
        return

    args = _parse_args(message)
    if args:
        session_str = args[0].strip()
        try:
            await message.delete()
        except Exception:
            pass
        status_msg = await message.reply_text("🔄 Validating and connecting string session...")
        ok, text, _ = await userbot_manager.login_with_session(session_str, message.from_user.id)
        if ok:
            await status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
        else:
            await status_msg.edit_text(f"❌ <b>Login Failed:</b> {text}", parse_mode=enums.ParseMode.HTML)
    else:
        prompt = await userbot_manager.start_interactive_login(message.from_user.id)
        await message.reply_text(prompt, parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_logout(client: Client, message: Message):
    """Logout userbot and clear session from database."""
    from bot.userbot import userbot_manager
    if not userbot_manager or not userbot_manager.is_active:
        await message.reply_text("ℹ️ Userbot is not currently logged in.")
        return

    await userbot_manager.logout()
    await message.reply_text("✅ <b>Userbot logged out</b> and session deleted from database.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_userbot(client: Client, message: Message):
    """Show current userbot session status."""
    from bot.userbot import userbot_manager
    from bot.database import db

    if not userbot_manager:
        await message.reply_text("⚠️ Userbot Manager not initialized.")
        return

    st = userbot_manager.get_status()
    is_active = st["is_active"]

    auto_chan = False
    album_mode = "channel"
    channel_count = 0
    if db:
        auto_chan = await db.get_config("auto_channel_creation", default=False)
        album_mode = await db.get_config("album_mode", default="channel")
        channels = await db.list_channel_mappings()
        channel_count = len(channels)

    if is_active:
        status_text = "🟢 <b>Connected</b>"
        user_info = (
            f"👤 <b>Name:</b> {st['name']}\n"
            f"🔗 <b>Username:</b> @{st['username'] or 'None'}\n"
            f"🆔 <b>User ID:</b> <code>{st['user_id']}</code>\n"
        )
    else:
        status_text = "🔴 <b>Disconnected</b>"
        user_info = "<i>Run /login to connect a userbot session.</i>\n"

    msg = (
        f"🤖 <b>Userbot Status</b>\n"
        f"⟐━━━━━━━━━━━━━━━━━⟐\n"
        f"⚡ <b>State:</b> {status_text}\n"
        f"{user_info}"
        f"📁 <b>Mapped Channels:</b> {channel_count}\n"
        f"⚙️ <b>Auto Channel Creation:</b> {'✅ Enabled' if auto_chan else '❌ Disabled'}\n"
        f"🖼️ <b>Album Mode:</b> <code>{album_mode}</code>\n"
        f"⟐━━━━━━━━━━━━━━━━━⟐\n"
        f"<b>Commands:</b>\n"
        f"• /login - Connect userbot\n"
        f"• /logout - Disconnect userbot\n"
        f"• /autochannel &lt;on|off&gt; - Toggle auto channel creation\n"
        f"• /albummode &lt;channel|both|direct&gt; - Set poster album mode\n"
        f"• /channels - List mapped channels\n"
        f"• /createchannel &lt;slug&gt; - Create channel for series\n"
        f"• /mapchannel &lt;slug&gt; &lt;channel_id&gt; [link] - Map channel"
    )
    await message.reply_text(msg, parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_cancel(client: Client, message: Message):
    """Cancel active userbot login wizard."""
    from bot.userbot import userbot_manager
    if userbot_manager and userbot_manager.is_in_login(message.from_user.id):
        await userbot_manager.cancel_login(message.from_user.id)
        await message.reply_text("❌ Login cancelled.")
    else:
        await message.reply_text("ℹ️ No active login wizard.")


@require_owner
async def cmd_autochannel(client: Client, message: Message):
    """Toggle auto-channel creation."""
    from bot.database import db
    args = _parse_args(message)
    if not args or args[0].lower() not in ("on", "off", "enable", "disable"):
        await message.reply_text("Usage: /autochannel <on|off>")
        return

    enabled = args[0].lower() in ("on", "enable")
    if db:
        await db.set_config("auto_channel_creation", enabled)
    await message.reply_text(
        f"✅ <b>Auto Channel Creation:</b> {'Enabled' if enabled else 'Disabled'}\n"
        f"When enabled, downloading a series will automatically create a dedicated channel.",
        parse_mode=enums.ParseMode.HTML
    )


@require_owner
async def cmd_albummode(client: Client, message: Message):
    """Configure poster album display mode."""
    from bot.database import db
    args = _parse_args(message)
    valid_modes = ("channel", "both", "direct")
    if not args or args[0].lower() not in valid_modes:
        await message.reply_text(
            "Usage: /albummode <channel|both|direct>\n\n"
            "• <code>channel</code>: Main channel poster has direct button to the dedicated series channel\n"
            "• <code>both</code>: Main channel poster has channel button + direct download buttons\n"
            "• <code>direct</code>: Main channel poster has only direct download buttons",
            parse_mode=enums.ParseMode.HTML
        )
        return

    mode = args[0].lower()
    if db:
        await db.set_config("album_mode", mode)
    await message.reply_text(
        f"✅ <b>Poster Album Mode set to:</b> <code>{mode}</code>",
        parse_mode=enums.ParseMode.HTML
    )


@require_owner
async def cmd_createchannel(client: Client, message: Message):
    """Manually create and map a dedicated channel for an anime series."""
    from bot.userbot import userbot_manager
    from api.client import api

    if not userbot_manager or not userbot_manager.is_active:
        await message.reply_text("❌ Userbot is not connected. Use /login first.")
        return

    args = _parse_args(message)
    if not args:
        await message.reply_text("Usage: /createchannel <series_slug or search query>\nExample: /createchannel solo-leveling-hindi")
        return

    query = " ".join(args).strip()
    wait_msg = await message.reply_text(f"🔍 Looking up anime series for: <code>{query}</code>...", parse_mode=enums.ParseMode.HTML)

    slug = query
    series_title = query
    poster_url = None

    try:
        series = await api.get_series(slug)
        if series:
            series_title = series.title
            poster_url = series.poster
    except Exception:
        try:
            results = await api.search(query)
            if results:
                match = results[0]
                slug = match.slug
                series_title = match.title
                poster_url = match.poster
        except Exception as se:
            log.warning("Search failed in createchannel: %s", se)

    # Poster ALWAYS comes from AniList, no matter which source matched.
    try:
        from utils.anilist import resolve_best_poster
        _best_cc = await resolve_best_poster(series_title, poster_url)
        if _best_cc:
            poster_url = _best_cc
    except Exception as _pe:
        log.debug("AniList poster override failed in createchannel: %s", _pe)

    await wait_msg.edit_text(f"🔨 Creating channel for <b>{series_title}</b>...", parse_mode=enums.ParseMode.HTML)

    try:
        mapping = await userbot_manager.create_anime_channel(
            series_title=series_title,
            series_slug=slug,
            poster_url=poster_url,
        )
        await wait_msg.edit_text(
            f"🎉 <b>Dedicated Channel Created & Mapped!</b>\n\n"
            f"📺 <b>Series:</b> {mapping.get('series_title', series_title)}\n"
            f"🆔 <b>Channel ID:</b> <code>{mapping['channel_id']}</code>\n"
            f"🔗 <b>Invite Link:</b> {mapping.get('invite_link')}\n"
            f"🏷️ <b>Slug:</b> <code>{slug}</code>",
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("cmd_createchannel failed")
        await wait_msg.edit_text(f"❌ <b>Channel creation failed:</b> {e}", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_mapchannel(client: Client, message: Message):
    """Manually map an existing Telegram channel to an anime series title/slug.
    
    Accepts natural series names without forced dashes:
      /mapchannel Solo Leveling -100123456789
      /mapchannel Solo Leveling -100123456789 https://t.me/+AbCdEf
    """
    from bot.database import db
    args = _parse_args(message)
    if len(args) < 2:
        await message.reply_text(
            "<b>Usage:</b> <code>/mapchannel &lt;series_name or slug&gt; &lt;channel_id&gt; [invite_link]</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/mapchannel Solo Leveling -100123456789</code>\n"
            "• <code>/mapchannel Solo Leveling -100123456789 https://t.me/+AbCdEf</code>\n"
            "• <code>/mapchannel solo-leveling-hindi -100123456789</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    channel_id = None
    invite_link = ""
    language = ""
    name_tokens = []
    audio_keys = {"hindi", "english", "japanese", "multi", "multiaudio", "multi-audio"}

    for token in args:
        t = token.strip()
        if (t.startswith("-100") and t[4:].isdigit()) or (t.startswith("-") and len(t) >= 9 and t[1:].isdigit()) or (len(t) >= 12 and t.isdigit()):
            channel_id = int(t) if t.startswith("-") else int(f"-100{t}")
        elif t.startswith("http://") or t.startswith("https://") or t.startswith("t.me"):
            invite_link = t
        elif t.lower() in audio_keys:
            language = "multi" if "multi" in t.lower() else t.lower()
        elif t.startswith("@") and channel_id is None:
            try:
                chat = await client.get_chat(t)
                channel_id = chat.id
            except Exception:
                name_tokens.append(t)
        else:
            name_tokens.append(t)

    if channel_id is None:
        await message.reply_text("❌ Please specify a valid Channel ID (e.g. <code>-100123456789</code> or <code>@channel_username</code>).", parse_mode=enums.ParseMode.HTML)
        return

    if not name_tokens:
        await message.reply_text("❌ Please specify the anime series name (e.g. <code>Solo Leveling</code>).", parse_mode=enums.ParseMode.HTML)
        return

    series_name = " ".join(name_tokens).strip()
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', series_name).strip('-').lower()

    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    # Check if DB already has files or series for this slug or title, align with it
    existing = await db.files.find_one({"$or": [
        {"series_slug": slug},
        {"series_title": {"$regex": f"^{re.escape(series_name)}$", "$options": "i"}},
    ]})
    if existing:
        slug = existing.get("series_slug", slug)
        series_name = existing.get("series_title", series_name)

    mapping = await db.set_channel_mapping(
        series_slug=slug,
        channel_id=channel_id,
        invite_link=invite_link,
        series_title=series_name,
        auto_created=False,
        created_by=message.from_user.id,
        language=language,
    )

    # Auto-enable monitoring for this mapped channel (Issue #12)
    await db.set_monitored_channel_status(channel_id, True)
    await db.add_monitored_series(series_slug=slug, series_title=series_name, channel_id=channel_id)

    # Interactive audio selection keyboard (Issue #14)
    audio_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇮🇳 Hindi", callback_data=f"map_audio:{slug}:hindi"),
            InlineKeyboardButton("🇬🇧 English", callback_data=f"map_audio:{slug}:english"),
        ],
        [
            InlineKeyboardButton("🇯🇵 Japanese", callback_data=f"map_audio:{slug}:japanese"),
            InlineKeyboardButton("🌐 Multi Audio", callback_data=f"map_audio:{slug}:multi"),
        ],
    ])

    lang_note = f"\n🌐 <b>Audio:</b> <code>{language.upper()}</code>" if language else "\n🌐 <b>Audio:</b> <i>Select below</i>"
    await message.reply_text(
        f"✅ <b>Channel Mapped Successfully!</b>\n\n"
        f"📺 <b>Series:</b> {htmlmod.escape(series_name)}\n"
        f"🏷️ <b>Slug:</b> <code>{slug}</code>\n"
        f"🆔 <b>Channel ID:</b> <code>{channel_id}</code>{lang_note}\n"
        f"🔗 <b>Invite Link:</b> {invite_link or 'None'}\n"
        f"🔄 <b>Auto-Monitor:</b> 🟢 Enabled\n\n"
        f"🎧 <b>Choose Audio Track for uploads to this channel:</b>\n"
        f"<i>(You can change this later with /setaudio {slug} &lt;audio&gt;)</i>\n\n"
        f"🏷️ <i>Optional — reply to a sticker with <code>/endsticker</code> and it will be "
        f"posted here as the END OF SEASON card once a season's batch completes.</i>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=audio_markup,
        disable_web_page_preview=True,
    )


@require_owner
async def cmd_setaudio(client: Client, message: Message):
    """Change or set audio language route for a mapped anime channel."""
    from bot.database import db
    args = _parse_args(message)
    if len(args) < 2:
        await message.reply_text(
            "<b>Usage:</b> <code>/setaudio &lt;series_slug&gt; &lt;hindi|english|japanese|multi&gt;</code>\n"
            "<b>Example:</b> <code>/setaudio solo-leveling hindi</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    slug = args[0].strip().lower()
    audio = args[1].strip().lower()
    if audio not in ("hindi", "english", "japanese", "multi", "multi audio"):
        await message.reply_text("❌ Valid audio options: <code>hindi</code>, <code>english</code>, <code>japanese</code>, <code>multi</code>", parse_mode=enums.ParseMode.HTML)
        return

    if db:
        mapping = await db.get_channel_mapping(slug)
        if not mapping:
            await message.reply_text(f"❌ No mapped channel found for series <code>{slug}</code>.", parse_mode=enums.ParseMode.HTML)
            return
        await db.set_channel_mapping(
            series_slug=slug,
            channel_id=mapping["channel_id"],
            invite_link=mapping.get("invite_link", ""),
            series_title=mapping.get("series_title", slug),
            language=audio,
        )
        await message.reply_text(f"✅ Audio for <code>{slug}</code> updated to: <b>{audio.upper()}</b>", parse_mode=enums.ParseMode.HTML)


async def map_audio_callback(client: Client, query: CallbackQuery):
    """Handle interactive audio language selection after mapping."""
    user = query.from_user
    from bot.auth import is_owner
    if user and not is_owner(user.id):
        await query.answer("⛔ Owner only", show_alert=True)
        return

    data = query.data
    parts = data.split(":", 2)
    if len(parts) < 3:
        return
    slug, audio = parts[1], parts[2]

    from bot.database import db
    if db:
        mapping = await db.get_channel_mapping(slug)
        if mapping:
            await db.set_channel_mapping(
                series_slug=slug,
                channel_id=mapping["channel_id"],
                invite_link=mapping.get("invite_link", ""),
                series_title=mapping.get("series_title", slug),
                language=audio,
            )
    await query.answer(f"Audio set to {audio.upper()}!")
    try:
        await query.message.edit_text(
            f"✅ <b>Audio Configured!</b>\n\n"
            f"🏷️ <b>Slug:</b> <code>{slug}</code>\n"
            f"🎧 <b>Selected Audio:</b> <code>{audio.upper()}</code>\n\n"
            f"<i>Channel post buttons and monitor downloads will target {audio.upper()} audio.</i>\n"
            f"<i>To change this later, use: <code>/setaudio {slug} &lt;audio&gt;</code></i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


@require_owner
async def cmd_unmapchannel(client: Client, message: Message):
    """Remove channel mapping or specific language route for a series slug."""
    from bot.database import db
    args = _parse_args(message)
    if not args:
        await message.reply_text("Usage: <code>/unmapchannel &lt;series_slug&gt; [language]</code>", parse_mode=enums.ParseMode.HTML)
        return

    slug = args[0].strip()
    language = args[1].strip().lower() if len(args) > 1 else ""

    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    deleted = await db.delete_channel_mapping(slug, language=language)
    if deleted:
        if language:
            await message.reply_text(f"✅ Removed <code>{language.upper()}</code> route for <code>{slug}</code>.", parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(f"✅ Removed channel mapping for <code>{slug}</code>.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"ℹ️ No channel mapping or language route found for <code>{slug}</code>.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_channels(client: Client, message: Message):
    """List all mapped channels and language routes."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    channels = await db.list_channel_mappings()
    if not channels:
        await message.reply_text("📂 <b>No mapped channels found.</b>\nUse /createchannel or /mapchannel to add channels.", parse_mode=enums.ParseMode.HTML)
        return

    lines = []
    for c in channels:
        title = c.get("series_title") or c.get("series_slug")
        cid = c.get("channel_id")
        link = c.get("invite_link")
        link_str = f"<a href='{link}'>Link</a>" if link else "No link"
        lang_routes = c.get("language_routes", {})
        routes_str = ""
        if lang_routes:
            r_items = [f"{l.upper()}: <code>{r['channel_id']}</code>" for l, r in lang_routes.items()]
            routes_str = f"\n  🌐 <b>Routes:</b> {', '.join(r_items)}"
        audio = c.get("language")
        audio_str = f" | Audio: <code>{audio.upper()}</code>" if audio else ""
        lines.append(f"• <b>{title}</b>\n  Default ID: <code>{cid}</code> | {link_str}{audio_str} | Slug: <code>{c.get('series_slug')}</code>{routes_str}")

    header = f"📋 <b>Mapped Series Channels ({len(channels)}):</b>\n\n"
    chunks = []
    current_chunk = header

    for line in lines:
        if len(current_chunk) + len(line) + 2 > 3900:
            chunks.append(current_chunk)
            current_chunk = f"📋 <b>Mapped Series Channels (cont.):</b>\n\n{line}"
        else:
            if current_chunk == header:
                current_chunk += line
            else:
                current_chunk += "\n\n" + line

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await message.reply_text(chunk, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


# ── Health, Diagnostics & System Monitoring ──────────────────────


@require_owner
async def cmd_health(client: Client, message: Message):
    """
    System health, bot status, and diagnostic dashboard.
    Usage:
      /health - Full diagnostic dashboard
      /health errors - Recent download failure details
      /health logs - Environment log events
    """
    from bot.health import format_health_dashboard, format_download_errors_view, format_logs_view
    args = _parse_args(message)
    sub = args[0].lower() if args else ""

    if sub in ("error", "errors", "fail", "failed"):
        text, markup = await format_download_errors_view()
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    elif sub in ("log", "logs"):
        text, markup = format_logs_view()
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    else:
        status_msg = await message.reply_text("🩺 Checking all bot connections and system metrics...")
        text, markup = await format_health_dashboard(client)
        try:
            await status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            # Send/edit race (message not yet visible server-side) — never
            # leave the user with no dashboard; fall back to a fresh reply.
            try:
                await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
            except Exception:
                pass


@require_owner
async def cmd_logs(client: Client, message: Message):
    """View recent environment log events or export as text document."""
    from bot.health import format_logs_view, ring_buffer_handler
    args = _parse_args(message)
    sub = args[0].lower() if args else ""

    if sub in ("export", "file", "download"):
        import tempfile
        logs_text = ring_buffer_handler.get_formatted_text(limit=150)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(logs_text)
            f_path = f.name
        try:
            await client.send_document(
                chat_id=message.chat.id,
                document=f_path,
                file_name="bot_environment_logs.txt",
                caption="📜 <b>Bot Environment Logs Export</b> (Latest 150 events)",
                parse_mode=enums.ParseMode.HTML,
            )
        finally:
            if os.path.exists(f_path):
                os.remove(f_path)
    elif sub in ("error", "errors"):
        text, markup = format_logs_view(level="ERROR")
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    else:
        text, markup = format_logs_view()
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


@require_owner
async def cmd_errors(client: Client, message: Message):
    """View recent download failures."""
    from bot.health import format_download_errors_view
    text, markup = await format_download_errors_view()
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


@require_owner
async def cmd_clearerrors(client: Client, message: Message):
    """Clear all logged download errors from database."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not available.")
        return
    cleared = await db.clear_download_errors()
    await message.reply_text(f"🧹 Cleared <b>{cleared}</b> download error records.", parse_mode=enums.ParseMode.HTML)


async def health_callback(client: Client, query: CallbackQuery):
    """Handle interactive buttons on the health dashboard."""
    from bot.auth import is_owner
    if not is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    data = query.data
    from bot.health import format_health_dashboard, format_download_errors_view, format_logs_view
    from bot.database import db

    if data in ("health:refresh", "health:back"):
        await query.answer("Refreshing health metrics...")
        text, markup = await format_health_dashboard(client)
        try:
            await query.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass

    elif data == "health:errors":
        await query.answer()
        text, markup = await format_download_errors_view()
        try:
            await query.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass

    elif data == "health:logs":
        await query.answer()
        text, markup = format_logs_view()
        try:
            await query.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass

    elif data == "health:logs_errors":
        await query.answer()
        text, markup = format_logs_view(level="ERROR")
        try:
            await query.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass

    elif data == "health:logs_all":
        await query.answer()
        text, markup = format_logs_view()
        try:
            await query.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass

    elif data == "health:clear_errors":
        count = 0
        if db:
            count = await db.clear_download_errors()
        await query.answer(f"🧹 Cleared {count} error records!", show_alert=True)
        text, markup = await format_download_errors_view()
        try:
            await query.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass


# ── Dump / Storage Channel (Point 4) ─────────────────────────────────

@require_owner
async def cmd_setdump(client: Client, message: Message):
    """Configure or disable dump/storage channel."""
    from bot.database import db
    args = _parse_args(message)
    if not args:
        current = await db.get_dump_channel() if db else None
        status = f"<code>{current}</code>" if current else "<i>Disabled (OFF by default)</i>"
        await message.reply_text(
            f"💾 <b>Dump / Storage Channel Configuration</b>\n\n"
            f"• <b>Current Dump Channel:</b> {status}\n\n"
            f"<b>Usage:</b>\n"
            f"• <code>/setdump &lt;channel_id&gt;</code> — Set dump channel (e.g. <code>/setdump -100123456789</code>)\n"
            f"• <code>/setdump off</code> — Disable dump channel (uploads directly as before)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    val = args[0].strip().lower()
    if val in ("off", "disable", "none", "0"):
        if db:
            await db.set_dump_channel(None)
        await message.reply_text("✅ <b>Dump Channel Disabled.</b> Videos will be uploaded directly as before.", parse_mode=enums.ParseMode.HTML)
        return

    try:
        cid = int(val)
        if db:
            await db.set_dump_channel(cid)
        await message.reply_text(
            f"✅ <b>Dump Channel Configured:</b> <code>{cid}</code>\n\n"
            "Downloaded video files will now be cached in this storage channel first, and mapped anime channels / users will receive files instantly via Telegram file_id without re-downloading!",
            parse_mode=enums.ParseMode.HTML,
        )
    except ValueError:
        await message.reply_text("❌ Channel ID must be an integer (e.g. <code>-100123456789</code>).", parse_mode=enums.ParseMode.HTML)


# ── Custom Thumbnail System (Point 5) ────────────────────────────────

@require_owner
async def cmd_setthumb(client: Client, message: Message):
    """
    Set custom thumbnail from replied photo.
    Usage:
      Reply to a photo with /setthumb -> Global thumbnail
      Reply to a photo with /setthumb <series_slug> -> Anime-specific thumbnail
      Reply to a photo with /setthumb <series_slug> <language> -> Language-specific thumbnail
    """
    from bot.database import db
    rep = message.reply_to_message
    file_id = None
    if rep:
        if rep.photo:
            file_id = rep.photo.file_id
        elif rep.document and rep.document.mime_type and rep.document.mime_type.startswith("image/"):
            file_id = rep.document.file_id

    if not file_id:
        await message.reply_text(
            "⚠️ <b>Please reply to an image/photo with:</b>\n\n"
            "• <code>/setthumb</code> — Set global thumbnail for all video uploads\n"
            "• <code>/setthumb &lt;series_slug&gt;</code> — Set thumbnail for a specific anime\n"
            "• <code>/setthumb &lt;series_slug&gt; &lt;language&gt;</code> — Set thumbnail for a specific anime & language (e.g. hindi, tamil)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    args = _parse_args(message)
    thumb_type = "global"
    key = ""

    if len(args) == 1:
        thumb_type = "series"
        key = args[0].strip().lower()
    elif len(args) >= 2:
        thumb_type = "language"
        key = f"{args[0].strip().lower()}_{args[1].strip().lower()}"

    if db:
        await db.set_custom_thumbnail(thumb_type, key, file_id)

    scope_name = f"for anime <code>{key}</code>" if key else "<b>Globally</b> (all anime)"
    await message.reply_text(
        f"🖼 <b>Custom Thumbnail Saved!</b>\n\n"
        f"• <b>Scope:</b> {scope_name}\n"
        f"• <b>Type:</b> <code>{thumb_type}</code>\n"
        f"• <b>Processing:</b> Auto-enhanced to 1280x720 HD with razor-sharp clarity",
        parse_mode=enums.ParseMode.HTML,
    )


@require_owner
async def cmd_delthumb(client: Client, message: Message):
    """Delete configured custom thumbnail."""
    from bot.database import db
    args = _parse_args(message)
    thumb_type = "global"
    key = ""

    if len(args) == 1:
        thumb_type = "series"
        key = args[0].strip().lower()
    elif len(args) >= 2:
        thumb_type = "language"
        key = f"{args[0].strip().lower()}_{args[1].strip().lower()}"

    if db:
        deleted = await db.delete_custom_thumbnail(thumb_type, key)
        if deleted:
            await message.reply_text(f"✅ Removed custom thumbnail ({thumb_type}: <code>{key or 'global'}</code>). Will fall back to official poster.", parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(f"ℹ️ No custom thumbnail found for ({thumb_type}: <code>{key or 'global'}</code>).", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_viewthumb(client: Client, message: Message):
    """View the currently active custom thumbnail."""
    from bot.database import db
    args = _parse_args(message)
    series_slug = args[0].strip().lower() if len(args) > 0 else None
    language = args[1].strip().lower() if len(args) > 1 else None

    if not db:
        return

    thumb_id = await db.get_custom_thumbnail(series_slug=series_slug, language=language)
    if thumb_id:
        scope = f"{series_slug} ({language})" if series_slug and language else (series_slug or "Global")
        await message.reply_photo(
            photo=thumb_id,
            caption=f"🖼 <b>Custom Thumbnail for:</b> <code>{scope}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text("ℹ️ No custom thumbnail configured. Bot is using automatic official AniList / scraped posters by default.", parse_mode=enums.ParseMode.HTML)


# ── Thumbnail branding (issue #33: username + PNG logo via command) ────────

_BRAND_USER_KEY = "thumb_brand_username"
_BRAND_LOGO_KEY = "thumb_brand_logo_path"


def _brand_logo_path() -> str:
    """Stable local path for the admin-supplied PNG logo (kept out of git)."""
    return str(Path(__file__).resolve().parent.parent.parent / "data" / "thumb_brand_logo.png")


# ── Link gate (issue #33): second-channel gate for the ⬇ DOWNLOAD button ───

@require_owner
async def cmd_linkgate(client: Client, message: Message):
    """Configure the second channel that gates the channel post's DOWNLOAD button.

    Usage:
      /linkgate                          — show current state
      /linkgate <channel_id|@user>       — turn the gate on (join-request mode)
      /linkgate <channel_id|@user> <mode> — mode: request | timer | link
      /linkgate off                      — turn the gate off (old behaviour)
    """
    from bot.database import db
    import bot.linkgate as lg

    args = _parse_args(message)

    if args and args[0].lower() in ("off", "disable", "none", "0"):
        await lg.set_gate_channel(None)
        await message.reply_text(
            "✅ <b>Link gate disabled.</b>\n\nChannel posts go back to showing the "
            "480p · 720p · 1080p buttons directly.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if args:
        target = args[0].strip()
        mode = args[1].strip().lower() if len(args) > 1 else None
        if mode is not None and mode not in lg.MODES:
            await message.reply_text(
                f"❌ Unknown mode <code>{htmlmod.escape(mode)}</code>. "
                f"Use one of: {', '.join(lg.MODES)}",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        # Validate the channel exists before accepting it.
        chan_val: int | str = int(target) if target.lstrip("-").isdigit() else target
        try:
            chat = await client.get_chat(chan_val)
            chan_val = chat.id
        except Exception as e:
            await message.reply_text(
                f"❌ Could not open that channel: <code>{htmlmod.escape(str(e)[:160])}</code>\n\n"
                "Give me a channel the <b>bot is an admin of</b>, e.g. "
                "<code>/linkgate -100123456789 request</code>",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        await lg.set_gate_channel(chan_val)
        if mode:
            await lg.set_gate_mode(mode)
        eff_mode = await lg.get_gate_mode()
        await message.reply_text(
            "✅ <b>Link gate enabled</b> (issue #33)\n\n"
            f"• <b>Channel:</b> <code>{chan_val}</code>\n"
            f"• <b>Mode:</b> <code>{eff_mode}</code>\n"
            f"• <b>Button:</b> <code>{'✋ REQUEST TO JOIN' if eff_mode == 'request' else '📢 JOIN CHANNEL'}</code>\n\n"
            "Channel posts now show a single <b>⬇️ DOWNLOAD</b> button. Users pass "
            "the gate first, then the bot reveals <b>480p · 720p · 1080p</b>.\n\n"
            "<i>Run <code>/refreshalbums</code> to re-render existing posts.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # Status view.
    chan = await lg.get_gate_channel()
    mode = await lg.get_gate_mode()
    text = (
        "🔒 <b>Link Gate (issue #33)</b>\n\n"
        f"• <b>State:</b> <code>{'ON' if chan else 'OFF'}</code>\n"
        f"• <b>Channel:</b> <code>{htmlmod.escape(str(chan)) if chan else '—'}</code>\n"
        f"• <b>Mode:</b> <code>{mode}</code>\n\n"
        "<b>Commands:</b>\n"
        "• <code>/linkgate &lt;channel_id_or_@user&gt; [mode]</code> — enable\n"
        f"• Modes: {', '.join(f'<code>{m}</code>' for m in lg.MODES)}\n"
        "• <code>/linkgate off</code> — disable (show quality buttons directly)\n"
        "• <code>/refreshalbums</code> — re-render existing channel posts"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


# ── END OF SEASON sticker (issue #33, optional) ─────────────────────────────


async def _get_end_of_season_sticker() -> str:
    from bot.endseason import get_end_of_season_sticker
    return await get_end_of_season_sticker()


@require_owner
async def cmd_endsticker(client: Client, message: Message):
    """Set/clear the END OF SEASON sticker posted after a full batch.

    Usage:
      Reply to a sticker with /endsticker  — install it
      /endsticker clear                    — remove it (posts skip the sticker)
      /endsticker                          — show current state
    """
    from bot.database import db
    args = _parse_args(message)

    if args and args[0].lower() in ("clear", "off", "none", "reset"):
        if db:
            await db.set_config(_EOS_KEY, "")
        await message.reply_text(
            "✅ END OF SEASON sticker cleared — the bot will no longer post a sticker.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # Install from a replied sticker FIRST — same order as /setthumb and
    # /thumblogo. (Checking `not args` first made the documented
    # "reply to a sticker" flow unreachable.)
    rep = message.reply_to_message
    st = getattr(rep, "sticker", None) if rep else None
    file_id = getattr(st, "file_id", None) if st else None

    if file_id:
        if db:
            await db.set_config(_EOS_KEY, file_id)
        try:
            await message.reply_sticker(sticker=file_id)
        except Exception:
            pass
        await message.reply_text(
            "✅ <b>END OF SEASON sticker installed.</b>\n\n"
            "It will be posted to the series channel after a season's batch finishes.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not args:
        current = await _get_end_of_season_sticker()
        if current:
            try:
                await message.reply_sticker(sticker=current)
            except Exception:
                pass
            await message.reply_text(
                "🏷 <b>END OF SEASON sticker is installed.</b>\n\n"
                "<i>Reply to a sticker with <code>/endsticker</code> to replace it, "
                "or <code>/endsticker clear</code> to remove it.</i>",
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await message.reply_text(
                "ℹ️ <b>No END OF SEASON sticker set.</b>\n\n"
                "Optional — reply to a sticker with <code>/endsticker</code> and the bot "
                "posts it to the series channel once a season's batch completes.\n"
                "<code>/endsticker clear</code> removes it (default: nothing is posted).",
                parse_mode=enums.ParseMode.HTML,
            )
        return

    await message.reply_text(
        "⚠️ <b>Please reply to a sticker with:</b> <code>/endsticker</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@require_owner
async def cmd_thumbuser(client: Client, message: Message):
    """Set/clear the channel handle stamped on auto-generated thumbnails.

    Usage:
      /thumbuser @MyChannel   — show @MyChannel in the lockup + watermark
      /thumbuser clear        — fall back to the bot's own username
      /thumbuser              — show the current value
    """
    from bot.database import db
    from config import Config
    args = _parse_args(message)
    current = getattr(Config, "THUMB_BRAND_USERNAME", "") or ""

    if not args:
        await message.reply_text(
            f"🏷 <b>Thumbnail handle:</b> <code>{htmlmod.escape(current) or '(bot username)'}</code>\n\n"
            "<b>Usage:</b> <code>/thumbuser @YourChannel</code>\n"
            "<b>Clear:</b> <code>/thumbuser clear</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    token = args[0].strip()
    if token.lower() in ("clear", "off", "none", "reset"):
        token = ""
    elif not token.startswith("@"):
        token = "@" + token

    Config.THUMB_BRAND_USERNAME = token
    if db:
        await db.set_config(_BRAND_USER_KEY, token)
    await message.reply_text(
        f"✅ <b>Thumbnail handle set to:</b> <code>{htmlmod.escape(token) or '(bot username)'}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@require_owner
async def cmd_thumblogo(client: Client, message: Message):
    """Set/clear the PNG logo shown in the thumbnail lockup (issue #33).

    Usage:
      Reply to a PNG/JPG with /thumblogo  — install that image as the logo
      /thumblogo clear                    — remove it (monogram tile returns)
      /thumblogo                          — preview the current logo
    """
    from bot.database import db
    from config import Config
    from pathlib import Path
    from PIL import Image
    args = _parse_args(message)
    current = getattr(Config, "THUMB_BRAND_LOGO", "") or ""

    if args and args[0].lower() in ("clear", "off", "none", "reset"):
        Config.THUMB_BRAND_LOGO = ""
        if db:
            await db.set_config(_BRAND_LOGO_KEY, "")
        try:
            p = _brand_logo_path()
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        await message.reply_text("✅ Logo removed — the monogram tile is back.", parse_mode=enums.ParseMode.HTML)
        return

    # Install from the replied message FIRST — same order as /setthumb.
    # (Checking `not args` before this made the documented "reply to an image"
    # flow unreachable: a bare reply always fell through to the status view.)
    rep = message.reply_to_message
    media = None
    if rep:
        if rep.photo:
            media = rep.photo.file_id
        elif rep.document and rep.document.mime_type and rep.document.mime_type.startswith("image/"):
            media = rep.document.file_id
    if not media and args:
        await message.reply_text(
            "⚠️ <b>Please reply to a PNG/JPG with:</b> <code>/thumblogo</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not media:
        # Status / preview view (no image was replied to and no args given).
        if current and os.path.exists(current):
            try:
                await message.reply_photo(
                    photo=current,
                    caption="🏷 <b>Current thumbnail logo</b>\n<i>Reply to an image with /thumblogo to replace it.</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
                return
            except Exception:
                pass
        await message.reply_text(
            "ℹ️ <b>No logo installed.</b>\n\n"
            "<b>Set:</b> reply to a PNG/JPG with <code>/thumblogo</code>\n"
            "<b>Clear:</b> <code>/thumblogo clear</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    try:
        dest = _brand_logo_path()
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        got = await client.download_media(media, file_name=dest)
        if not got or not os.path.exists(str(got)):
            raise IOError("download failed")
        # Guard: must actually be an image (a stray PDF/video would break PIL).
        with Image.open(str(got)) as im:
            im.verify()
        Config.THUMB_BRAND_LOGO = str(got)
        if db:
            await db.set_config(_BRAND_LOGO_KEY, str(got))
        await message.reply_text(
            "✅ <b>Logo installed</b> — it will appear top-left on every auto-generated thumbnail.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ Could not install logo: <code>{htmlmod.escape(str(e)[:160])}</code>",
                                 parse_mode=enums.ParseMode.HTML)


# ── Automatic Episode Monitoring (Point 1) ───────────────────────────

@require_owner
async def cmd_automonitor(client: Client, message: Message):
    """
    Manage automatic episode monitoring service (OFF by default).
    Subcommands:
      /automonitor on — Turn monitoring ON
      /automonitor off — Turn monitoring OFF
      /automonitor status — View current status
      /automonitor interval <minutes> — Set check interval
      /automonitor quality <quality> — Set download quality
      /automonitor add <slug> [quality] — Add anime to watchlist
      /automonitor del <slug> — Remove anime from watchlist
      /automonitor list — List monitored series
      /automonitor check — Run an immediate check cycle right now
    """
    from bot.database import db
    from bot.monitor import monitor_service
    args = _parse_args(message)

    if not args:
        # Show help and current status
        enabled = await db.get_auto_monitor_enabled() if db else False
        interval = await db.get_auto_monitor_interval() if db else 30
        quality = await db.get_auto_monitor_quality() if db else "720p"
        monitored = await db.list_monitored_series() if db else []

        status_badge = "🟢 <b>ACTIVE (ON)</b>" if enabled else "🔴 <b>DISABLED (OFF by default)</b>"
        text = (
            f"🔄 <b>Automatic Episode Monitoring System</b>\n\n"
            f"• <b>Status:</b> {status_badge}\n"
            f"• <b>Check Interval:</b> <code>{interval} minutes</code>\n"
            f"• <b>Target Quality:</b> <code>{quality.upper()}</code>\n"
            f"• <b>Monitored Watchlist:</b> <code>{len(monitored)} series</code> (if 0, monitors all mapped channels)\n\n"
            "<b>Commands:</b>\n"
            "• <code>/automonitor on</code> — Turn monitoring ON\n"
            "• <code>/automonitor off</code> — Turn monitoring OFF\n"
            "• <code>/automonitor interval &lt;minutes&gt;</code> — Set check interval (min 5m)\n"
            "• <code>/automonitor quality &lt;quality&gt;</code> — Set target quality (e.g. 720p, 1080p)\n"
            "• <code>/automonitor add &lt;slug&gt; [quality]</code> — Add anime to watchlist\n"
            "• <code>/automonitor del &lt;slug&gt;</code> — Remove anime from watchlist\n"
            "• <code>/automonitor list</code> — List watchlist anime\n"
            "• <code>/automonitor check</code> — Run an immediate check cycle now"
        )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        return

    sub = args[0].strip().lower()

    if sub in ("on", "enable", "start", "1"):
        if len(args) > 1 and args[1].lstrip("-").isdigit():
            cid = int(args[1])
            if db:
                await db.set_monitored_channel_status(cid, True)
            await message.reply_text(f"🟢 <b>Monitoring Enabled for Channel:</b> <code>{cid}</code>", parse_mode=enums.ParseMode.HTML)
        else:
            if db:
                await db.set_auto_monitor_enabled(True)
            await message.reply_text("🟢 <b>Auto-Monitor Enabled globally!</b> The bot will periodically check for new episode releases and upload them automatically.", parse_mode=enums.ParseMode.HTML)

    elif sub in ("off", "disable", "stop", "0"):
        if len(args) > 1 and args[1].lstrip("-").isdigit():
            cid = int(args[1])
            if db:
                await db.set_monitored_channel_status(cid, False)
            await message.reply_text(f"🔴 <b>Monitoring Disabled for Channel:</b> <code>{cid}</code>", parse_mode=enums.ParseMode.HTML)
        else:
            if db:
                await db.set_auto_monitor_enabled(False)
            await message.reply_text("🔴 <b>Auto-Monitor Disabled globally.</b> Background checks stopped.", parse_mode=enums.ParseMode.HTML)

    elif sub in ("status", "info"):
        enabled = await db.get_auto_monitor_enabled() if db else False
        interval = await db.get_auto_monitor_interval() if db else 30
        quality = await db.get_auto_monitor_quality() if db else "720p"
        monitored = await db.list_monitored_series() if db else []
        last_run = monitor_service._last_run_time
        last_str = datetime.fromtimestamp(last_run, tz=timezone.utc).strftime("%H:%M:%S UTC") if last_run else "Never"

        text = (
            f"🔄 <b>Auto-Monitor Status:</b> {'🟢 ON' if enabled else '🔴 OFF'}\n"
            f"• <b>Interval:</b> {interval} mins\n"
            f"• <b>Quality:</b> {quality.upper()}\n"
            f"• <b>Monitored Series:</b> {len(monitored)}\n"
            f"• <b>Last Check:</b> {last_str}"
        )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

    elif sub == "interval" and len(args) > 1 and args[1].isdigit():
        mins = int(args[1])
        if db:
            await db.set_auto_monitor_interval(mins)
        await message.reply_text(f"⏱ <b>Auto-Monitor interval updated to {max(5, mins)} minutes.</b>", parse_mode=enums.ParseMode.HTML)

    elif sub == "quality" and len(args) > 1:
        q_val = args[1].strip()
        if db:
            await db.set_auto_monitor_quality(q_val)
        await message.reply_text(f"🎬 <b>Auto-Monitor default quality set to {q_val.upper()}.</b>", parse_mode=enums.ParseMode.HTML)

    elif sub == "add" and len(args) > 1:
        slug = args[1].strip()
        q_target = args[2].strip() if len(args) > 2 else ""
        if db:
            await db.add_monitored_series(slug, quality=q_target)
        await message.reply_text(f"✅ Added <code>{slug}</code> to auto-monitor watchlist (Quality: {q_target.upper() or 'Default'}).", parse_mode=enums.ParseMode.HTML)

    elif sub == "del" and len(args) > 1:
        slug = args[1].strip()
        if db:
            del_ok = await db.remove_monitored_series(slug)
            if del_ok:
                await message.reply_text(f"✅ Removed <code>{slug}</code> from auto-monitor watchlist.", parse_mode=enums.ParseMode.HTML)
            else:
                await message.reply_text(f"ℹ️ <code>{slug}</code> was not in the watchlist.", parse_mode=enums.ParseMode.HTML)

    elif sub == "list":
        monitored = await db.list_monitored_series() if db else []
        if not monitored:
            await message.reply_text("📋 <b>Watchlist is empty.</b> (When enabled, bot will monitor all mapped anime series channels).", parse_mode=enums.ParseMode.HTML)
            return
        lines = [f"• <code>{m['series_slug']}</code> [{m.get('quality', 'Default').upper()}]" for m in monitored]
        await message.reply_text(f"📋 <b>Auto-Monitored Series ({len(monitored)}):</b>\n\n" + "\n".join(lines), parse_mode=enums.ParseMode.HTML)

    elif sub == "check":
        p_msg = await message.reply_text("🔍 <i>Running manual episode check cycle...</i>", parse_mode=enums.ParseMode.HTML)
        res = await monitor_service.run_check_cycle()
        await p_msg.edit_text(
            f"✅ <b>Episode Check Completed!</b>\n\n"
            f"• <b>Series Checked:</b> {res['checked']}\n"
            f"• <b>New Episodes Detected & Uploaded:</b> {res['new_episodes']}\n"
            f"• <b>Errors:</b> {res['errors']}",
            parse_mode=enums.ParseMode.HTML,
        )

    else:
        await message.reply_text("Usage: <code>/automonitor &lt;on|off|status|interval|quality|add|del|list|check&gt;</code>", parse_mode=enums.ParseMode.HTML)


# ── Post Style Configuration (V3 #7: modern-only, classic retired) ───

@require_owner
async def cmd_poststyle(client: Client, message: Message):
    """V3 #7: classic retired — modern-only. Migrates legacy value."""
    from bot.database import db
    if db:
        try:
            await db.migrate_classic_styles_to_modern()
        except Exception:
            pass
    await message.reply_text(
        "🎨 <b>Channel Post Style: MODERN-ONLY</b>\n\n"
        "Classic style was retired in V3 #7. The bot now always uses the modern styled card.\n"
        "Any stored <code>classic</code> value was migrated to <code>modern</code>.",
        parse_mode=enums.ParseMode.HTML,
    )
    return


@require_owner
async def cmd_startstyle(client: Client, message: Message):
    """V3 #7: classic retired — modern-only."""
    from bot.database import db
    if db:
        try:
            await db.migrate_classic_styles_to_modern()
        except Exception:
            pass
    await message.reply_text(
        "🎨 <b>Start Menu UI: MODERN-ONLY</b>\n\n"
        "Classic style was retired in V3 #7. <code>/start</code> always uses the modern card.",
        parse_mode=enums.ParseMode.HTML,
    )
    return


@require_owner
async def cmd_startpic(client: Client, message: Message):
    """Set or reset custom image banner for /start modern UI."""
    from bot.database import db
    args = _parse_args(message)

    photo_file_id = None
    if message.reply_to_message and message.reply_to_message.photo:
        photo_file_id = message.reply_to_message.photo.file_id

    if not photo_file_id and not args:
        cur_pic = await db.get_start_pic() if db else None
        status = f"<code>{cur_pic[:60]}...</code>" if cur_pic else "<i>Default Anime Girl Banner</i>"
        await message.reply_text(
            f"🖼️ <b>Start Banner Settings</b>\n\n"
            f"• <b>Current Banner:</b> {status}\n\n"
            f"<b>Usage:</b>\n"
            f"• Reply to any photo with <code>/startpic</code>\n"
            f"• Or send <code>/startpic &lt;image_url&gt;</code>\n"
            f"• Or send <code>/startpic reset</code> to restore default banner",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if args and args[0].strip().lower() in ("reset", "clear", "default"):
        if db:
            await db.set_start_pic(None)
        await message.reply_text("✅ <b>Start banner reset to default!</b>", parse_mode=enums.ParseMode.HTML)
        return

    target_pic = photo_file_id or args[0].strip()
    if db:
        await db.set_start_pic(target_pic)
    await message.reply_text("✅ <b>Custom start banner saved!</b> It will be displayed when Modern Start Style is active.", parse_mode=enums.ParseMode.HTML)


@require_owner
async def cmd_epstyle(client: Client, message: Message):
    """V3 #7: classic retired — modern-only."""
    from bot.database import db
    if db:
        try:
            await db.migrate_classic_styles_to_modern()
        except Exception:
            pass
    await message.reply_text(
        "🎨 <b>Episode Post Style: MODERN-ONLY</b>\n\n"
        "Classic style was retired in V3 #7. Episode posts always use the modern card.",
        parse_mode=enums.ParseMode.HTML,
    )
    return


@require_owner
async def cmd_schedstyle(client: Client, message: Message):
    """V3 #7: classic retired — modern-only."""
    from bot.database import db
    if db:
        try:
            await db.migrate_classic_styles_to_modern()
        except Exception:
            pass
    await message.reply_text(
        "📅 <b>Schedule UI: MODERN-ONLY</b>\n\n"
        "Classic style was retired in V3 #7. <code>/schedule</code> always uses modern cards.",
        parse_mode=enums.ParseMode.HTML,
    )
    return


@require_owner
async def cmd_postsched(client: Client, message: Message):
    """Manually test and trigger the daily schedule post to the main channel or specified channel."""
    from bot.schedule import auto_schedule_service
    from bot.database import db
    from config.settings import settings

    args = _parse_args(message)
    target_channel = None
    if args and args[0].lstrip("-").isdigit():
        target_channel = int(args[0])
    else:
        target_channel = settings.bot.main_channel
        if not target_channel and db:
            target_channel = await db.get_main_channel()

    if not target_channel:
        await message.reply_text("❌ No main channel configured. Usage: <code>/postsched &lt;channel_id&gt;</code>", parse_mode=enums.ParseMode.HTML)
        return

    status_msg = await message.reply_text(f"🔄 Posting daily schedule to channel <code>{target_channel}</code>...", parse_mode=enums.ParseMode.HTML)
    ok = await auto_schedule_service.post_daily_schedule(client, target_channel)
    if ok:
        await status_msg.edit_text(f"✅ <b>Daily schedule successfully posted</b> to channel <code>{target_channel}</code>!", parse_mode=enums.ParseMode.HTML)
    else:
        await status_msg.edit_text(f"❌ <b>Failed to post daily schedule</b> to channel <code>{target_channel}</code>. Check bot logs.", parse_mode=enums.ParseMode.HTML)


