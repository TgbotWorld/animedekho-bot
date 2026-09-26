"""Slash command handlers."""

import logging
import re

from bot.telegram import Client, enums
from bot.telegram.types import Message

from bot.keyboards import main_menu
from bot.auth import require_approved, require_owner
import bot.logger

log = logging.getLogger(__name__)


@require_approved
async def cmd_start(client: Client, message: Message):
    user = message.from_user
    user_id = user.id if user else 0

    from bot.database import db
    if db and user_id:
        if await db.is_banned(user_id):
            await message.reply_text("⛔ You are banned from using this bot.")
            return
        await db.track_bot_user(user_id, user.username or "", user.first_name or "")

    # Check for deep link parameters (file requests from library or channel join)
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1]
        if param.startswith("get_"):
            await _handle_file_request(client, message, param)
            return
        elif param.startswith("join_"):
            await _handle_channel_join_request(client, message, param[5:])
            return

    if bot.logger.bot_logger and user:
        await bot.logger.bot_logger.log_bot_start(user.id, user.username or user.first_name)

    # Get channel invite link for the welcome message
    invite_link = None
    if db:
        invite_link = await db.get_config("channel_invite_link")

    start_style = await db.get_start_style() if db else "classic"

    if start_style == "modern":
        from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton
        first_name = user.first_name if user else "Friend"
        user_mention = f"<a href='tg://user?id={user_id}'>{re.sub(r'[<>&]', '', first_name)}</a>" if user_id else (first_name or "Friend")

        main_chan = await db.get_config("main_channel_link") or invite_link or "https://t.me/animedekho"
        modern_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("• ⚡ MAIN CHANNEL • ↗", url=main_chan)],
            [
                InlineKeyboardButton("• ABOUT •", callback_data="start:about"),
                InlineKeyboardButton("HELP •", callback_data="start:help"),
            ]
        ])

        caption = (
            f"Bᴀᴋᴀᴀᴀ!!!.....{user_mention}\n\n"
            f"<blockquote><b>I AM FILE STORE + AUTO ANIME BOT, I CAN STORE PRIVATE FILES IN SPECIFIED CHANNEL AND OTHER USERS CAN ACCESS IT FROM SPECIAL LINK.</b></blockquote>"
        )

        start_pic = await db.get_start_pic() if db else None
        # Default stylish banner fallback if user has not set a custom start picture
        pic_to_send = start_pic or "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=1000&auto=format&fit=crop"

        try:
            await message.reply_photo(
                photo=pic_to_send,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=modern_markup,
            )
            return
        except Exception as pe:
            log.debug("Photo send failed for /start modern, falling back to text: %s", pe)
            await message.reply_text(
                caption,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=modern_markup,
            )
            return

    welcome_text = (
        "🎌 <b>AnimeDekho Bot</b>\n\n"
        "Stream Hindi dubbed anime!\n\n"
        "• 📺 <b>Series</b> — browse recent series\n"
        "• 📂 <b>Genres</b> — filter by genre\n\n"
        "Just type any anime name to search!"
    )

    markup = main_menu(invite_link=invite_link)

    await message.reply_text(
        welcome_text,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=markup,
    )


async def start_callback(client: Client, query):
    """Handle modern start menu callbacks (About, Help, Home)."""
    data = query.data
    from bot.database import db
    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton

    if data == "start:about":
        text = (
            "✨ <b>ABOUT ANIME DEKHO BOT</b> ✨\n\n"
            "<blockquote><b>🤖 Name:</b> AnimeDekho Bot\n"
            "<b>⚡ Version:</b> 2.5 Modern\n"
            "<b>🐍 Framework:</b> Wzgram / Pyrogram\n"
            "<b>📦 Engine:</b> Multi-Audio HLS / DASH\n"
            "<b>🚀 Features:</b>\n"
            "• Channel Auto-Mapping &amp; Dedicated Channels\n"
            "• Auto Episode Monitor &amp; Airing Schedules\n"
            "• Anti-Copyright 2-Min Invite Links &amp; Auto-Delete\n"
            "• Dump Cache Channel &amp; Custom Thumbnails</blockquote>\n\n"
            "<i>Click below to return to the main menu.</i>"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="start:home")]])
    elif data == "start:help":
        text = (
            "📖 <b>HELP & COMMANDS GUIDE</b> 📖\n\n"
            "<blockquote><b>User Commands:</b>\n"
            "• /start — Open start menu\n"
            "• /search &lt;anime&gt; — Search anime series &amp; movies\n"
            "• /schedule — View today's anime release schedule\n"
            "• /help — Show help information\n\n"
            "<b>Admin / Owner Commands:</b>\n"
            "• /automonitor — Toggle automated episode downloader\n"
            "• /mapchannel — Route anime uploads to dedicated channel\n"
            "• /startstyle — Switch /start UI (classic / modern)\n"
            "• /schedstyle — Switch /schedule UI (classic / modern)\n"
            "• /epstyle — Switch episode upload post UI (classic / modern)\n"
            "• /poststyle — Switch channel album card UI (classic / modern)\n"
            "• /setthumb — Configure custom thumbnails\n"
            "• /setdump — Configure dump storage channel</blockquote>\n\n"
            "<i>Click below to return to the main menu.</i>"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="start:home")]])
    else:  # start:home
        user = query.from_user
        user_id = user.id if user else 0
        first_name = user.first_name if user else "Friend"
        user_mention = f"<a href='tg://user?id={user_id}'>{re.sub(r'[<>&]', '', first_name)}</a>" if user_id else (first_name or "Friend")
        invite_link = await db.get_config("channel_invite_link") if db else None
        main_chan = (await db.get_config("main_channel_link") if db else None) or invite_link or "https://t.me/animedekho"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("• ⚡ MAIN CHANNEL • ↗", url=main_chan)],
            [
                InlineKeyboardButton("• ABOUT •", callback_data="start:about"),
                InlineKeyboardButton("HELP •", callback_data="start:help"),
            ]
        ])
        text = (
            f"Bᴀᴋᴀᴀᴀ!!!.....{user_mention}\n\n"
            f"<blockquote><b>I AM FILE STORE + AUTO ANIME BOT, I CAN STORE PRIVATE FILES IN SPECIFIED CHANNEL AND OTHER USERS CAN ACCESS IT FROM SPECIAL LINK.</b></blockquote>"
        )

    try:
        if query.message.photo:
            await query.message.edit_caption(caption=text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        else:
            await query.message.edit_text(text=text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        await query.answer()
    except Exception as e:
        log.debug("Start callback edit error: %s", e)
        await query.answer()


@require_approved
async def cmd_help(client: Client, message: Message):
    is_owner_user = message.from_user and message.from_user.id == settings.bot.owner_id
    owner_help = (
        "\n\n<b>Owner & Admin Commands:</b>\n"
        "• <b>/settings</b> — Interactive control panel & live toggles\n"
        "• <b>/commands</b> — Interactive categorized commands guide\n"
        "/stats — View real-time VPS stats and net speed\n"
        "/health — System health & bot diagnostics\n"
        "/users — View total network users\n"
        "/broadcast — Broadcast text message\n"
        "/pbroadcast — Broadcast photo\n"
        "/dbroadcast — Broadcast video/doc\n"
        "/ban &lt;id&gt; — Ban a user\n"
        "/uban &lt;id&gt; — Unban a user\n"
        "/fsub — Manage Force Subscribe channel\n"
        "/fsub_mod — Toggle FSub 2-min timer link mode\n"
        "/dlt_time — Set file/video auto-delete timer\n"
        "/startstyle — Switch /start menu style (classic/modern)\n"
        "/schedstyle — Switch /schedule style (classic/modern)\n"
        "/epstyle — Switch episode post style (classic/modern)\n"
        "/poststyle — Switch channel card style (classic/modern)\n"
        "/startpic — Set custom banner for /start\n"
        "/tutorial — Full system guide\n"
        "/ai &lt;query&gt; — Chat with Autonomous AI Agent\n"
        "/setai — View & change AI model/provider\n"
        "/addbot — Add child worker bot\n"
        "/delete — Delete a series or file"
    ) if is_owner_user else ""

    await message.reply_text(
        "📖 <b>Commands</b>\n\n"
        "/start — Main menu\n"
        "/search &lt;name&gt; — Search anime or movies\n"
        "/schedule — Anime airing schedule\n"
        "/commands — Interactive categorized commands guide\n"
        "/help — This message\n\n"
        "Just type any anime name in chat to search!"
        f"{owner_help}",
        parse_mode=enums.ParseMode.HTML,
    )


@require_approved
async def cmd_search(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            "🔍 Usage: <code>/search &lt;anime name&gt;</code>\n"
            "Or simply type the anime name directly in chat!",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    from bot.handlers.messages import do_search
    query = parts[1].strip()
    await do_search(client, message, query)


@require_owner
async def cmd_autosearch(client: Client, message: Message):
    """Toggle direct anime name typing search in chat (Issue #9)."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database is not connected.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        arg = parts[1].strip().lower()
        if arg in ("on", "enable", "true", "yes", "1"):
            await db.set_auto_search(True)
            await message.reply_text(
                "✅ <b>Direct Anime Name Chat Search:</b> <code>ENABLED</code>\n"
                "Users can search by typing anime names directly in chat.",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        elif arg in ("off", "disable", "false", "no", "0"):
            await db.set_auto_search(False)
            await message.reply_text(
                "❌ <b>Direct Anime Name Chat Search:</b> <code>DISABLED</code>\n"
                "Chat typing will not trigger search. Users must use <code>/search &lt;name&gt;</code>.",
                parse_mode=enums.ParseMode.HTML,
            )
            return

    # Toggle if no argument provided
    curr = await db.get_auto_search()
    new_val = not curr
    await db.set_auto_search(new_val)
    status_str = "ENABLED ✅ (Direct typing triggers search)" if new_val else "DISABLED ❌ (Search only via /search)"
    await message.reply_text(
        f"⚙️ <b>Auto Chat Search is now:</b> <code>{status_str}</code>\n"
        f"Usage: <code>/autosearch on</code> or <code>/autosearch off</code>",
        parse_mode=enums.ParseMode.HTML,
    )


async def _handle_channel_join_request(client: Client, message: Message, series_slug: str):
    """Generate a 2-minute temporary invite link to the mapped series channel."""
    from bot.database import db
    import html as htmlmod
    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    user = message.from_user
    user_id = user.id if user else 0

    from bot.fsub import check_fsub, create_timer_invite_link
    is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=f"join_{series_slug}")
    if not is_sub:
        await message.reply_text(f_text, parse_mode=enums.ParseMode.HTML, reply_markup=f_markup)
        return

    mapping = await db.get_channel_mapping(series_slug)
    if not mapping or not mapping.get("channel_id"):
        await message.reply_text("⚠️ No dedicated channel found for this series.")
        return

    channel_id = mapping["channel_id"]
    series_title = mapping.get("series_title", series_slug)

    # Determine retry URL
    bot_username = getattr(getattr(client, "me", None), "username", None)
    if not bot_username:
        try:
            me = await client.get_me()
            bot_username = me.username or ""
        except Exception:
            bot_username = ""
    retry_url = f"https://t.me/{bot_username}?start=join_{series_slug}" if bot_username else ""

    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # Generate 2-minute timer link (Anti-Copyright Protection)
    timer_link = await create_timer_invite_link(
        client,
        channel_id,
        expire_seconds=120,
        member_limit=1,
        name=f"Join {series_slug[:15]}",
    )
    if not timer_link:
        buttons = []
        if retry_url:
            buttons.append([InlineKeyboardButton("🔄 Try Again", url=retry_url)])
        await message.reply_text(
            "⚠️ <b>Failed to generate temporary invite link.</b>\n\n"
            "Unable to generate a 2-minute expiring link for this series channel right now.\n"
            "Please click the <b>Try Again</b> button below to re-generate your link!",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
        return

    buttons = [
        [InlineKeyboardButton("🚀 Join Series Channel", url=timer_link)],
    ]
    if retry_url:
        buttons.append([InlineKeyboardButton("🔄 Try Again", url=retry_url)])
    sent_msg = await message.reply_text(
        f"📺 <b>Dedicated Series Channel:</b> {htmlmod.escape(series_title)}\n\n"
        f"⏳ <b>Temporary Invite Link:</b>\n"
        f"This invite link will automatically expire in <b>2 minutes</b>!\n\n"
        f"Click the button below to join:",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )

    # Schedule deletion of this invite link notice after 2 minutes
    from bot.auto_delete import auto_delete_service
    await auto_delete_service.schedule_deletion(
        client=client,
        chat_id=message.chat.id,
        message_id=sent_msg.id,
        custom_seconds=120,
    )


async def _handle_file_request(client: Client, message: Message, param: str):
    """Handle deep link file requests from main channel.

    Format: get_<slug>_<quality>_<episode_key>
    Example: get_naruto-shippuden_720p_S1E01
    """
    from bot.library import library_manager
    from bot.database import db

    if not library_manager:
        await message.reply_text("⚠️ Library not initialized.")
        return

    user = message.from_user
    user_id = user.id if user else 0

    # FSub verification (with timer link support)
    from bot.fsub import check_fsub
    is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=param)
    if not is_sub:
        await message.reply_text(f_text, parse_mode=enums.ParseMode.HTML, reply_markup=f_markup)
        return

    # Parse: get_<slug>_<quality>_<ep_key>
    raw = param[4:]  # strip "get_"

    # Match episode key at the end (S\d+E\d+|movie|all)
    m = re.match(
        r"^(.+?)_(480p|720p|1080p|1080p\s*hq|1080p\s*hq\s*x265|4k|2160p|\d+p|auto)_(s\d+e\d+|movie|all)$",
        raw,
        re.IGNORECASE,
    )
    if not m:
        await message.reply_text("⚠️ Invalid file link format.")
        return

    series_slug = m.group(1)
    quality = m.group(2)
    episode_key = m.group(3)

    import html as htmlmod
    from utils.helpers import slug_to_title
    title = slug_to_title(series_slug)
    bot_me = getattr(client, "me", None)
    b_name = bot_me.username if bot_me and bot_me.username else "bot"

    # Handle fetching ALL episodes
    if episode_key.lower() == "all":
        query = {"series_slug": series_slug}
        if quality.lower() in ("4k", "2160p", "2160"):
            query["quality"] = {
                "$in": [
                    "4K", "4k", "2160p", "2160P", "2160",
                    "1080p HQ", "1080p HQ x265", "1080p 10-Bit", "1080p 10bit",
                    "1080p x265", "1080p HEVC",
                ]
            }
        else:
            query["quality"] = {"$regex": f"^{re.escape(quality)}$", "$options": "i"}

        cursor = db.files.find(query)
        all_files = await cursor.to_list(length=None)
        if not all_files:
            all_files = await db.files.find({"series_title": {"$regex": f"^{re.escape(title)}$", "$options": "i"}}).to_list(length=None)

        if not all_files:
            await message.reply_text("❌ No files found for this series.")
            return

        from bot.library import _ep_sort_key
        all_files.sort(key=lambda x: _ep_sort_key(x["episode_key"]))

        status_msg = await message.reply_text(f"📤 Sending {len(all_files)} episodes...")
        from bot.auto_delete import auto_delete_service

        for f in all_files:
            ep_key = f["episode_key"]
            file_id = f["file_id"]
            q_label = f.get("quality", quality)
            caption = f"📺 {htmlmod.escape(title)} [{q_label}] — {ep_key}"
            sent_msg = None
            try:
                sent_msg = await message.reply_video(video=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            except Exception:
                try:
                    sent_msg = await message.reply_document(document=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                except Exception as e:
                    log.error("Failed to send %s: %s", ep_key, e)

            if sent_msg:
                await auto_delete_service.schedule_deletion(
                    client=client,
                    chat_id=message.chat.id,
                    message_id=sent_msg.id,
                    get_file_link=f"https://t.me/{b_name}?start={param}",
                    file_title=title,
                )
            await asyncio.sleep(0.4)

        await status_msg.delete()
        return

    # Normal single-file logic
    cached = await db.find_cached_file(series_slug, episode_key, quality)
    file_id = cached.get("file_id") if cached else await library_manager.get_file(series_slug, quality, episode_key)
    if not file_id:
        await message.reply_text("❌ File not found in library.")
        return

    # Send the file
    sent_msg = None
    try:
        caption = f"📺 {htmlmod.escape(title)} [{quality}]"
        if episode_key.lower() != "movie":
            caption += f" — {episode_key}"

        try:
            sent_msg = await message.reply_video(
                video=file_id,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            sent_msg = await message.reply_document(
                document=file_id,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )

        # Log download
        if db and user:
            await db.log_download(
                user_id=user.id,
                series_slug=series_slug,
                episode=episode_key,
                quality=quality,
                file_id=file_id,
            )

        # Auto-delete scheduling
        if sent_msg:
            from bot.auto_delete import auto_delete_service
            await auto_delete_service.schedule_deletion(
                client=client,
                chat_id=message.chat.id,
                message_id=sent_msg.id,
                get_file_link=f"https://t.me/{b_name}?start={param}",
                file_title=title,
            )

    except Exception as e:
        log.error("Failed to send library file: %s", e)
        await message.reply_text("⚠️ Could not deliver file. Please try again.")
