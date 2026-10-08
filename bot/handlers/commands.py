"""Slash command handlers."""

import asyncio
import logging
import re

from bot.telegram import Client, enums
from bot.telegram.types import Message

from bot.keyboards import main_menu
from bot.auth import require_approved, require_owner
import bot.logger

log = logging.getLogger(__name__)


def _start_caption(user_mention: str) -> str:
    """Streaming-style welcome shared by /start and the Home callback."""
    return (
        f"🍿 <b>AnimeDekho</b> — {user_mention}\n\n"
        "<blockquote><b>Your anime, on demand.</b>\n"
        "Search any title, pick a quality and I'll deliver it — 480p → 4K, "
        "Hindi-dubbed series &amp; movies, one tidy post per show.</blockquote>"
    )


def _start_markup(main_channel: str):
    """The three buttons every /start screen shows."""
    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Main Channel ↗", url=main_channel)],
        [
            InlineKeyboardButton("✨ About", callback_data="start:about"),
            InlineKeyboardButton("📖 Help", callback_data="start:help"),
        ],
    ])


def _screen_markup(main_channel: str):
    """About / Help screens: a way back to the menu."""
    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Main Channel ↗", url=main_channel)],
        [InlineKeyboardButton("🔙 Menu", callback_data="start:home")],
    ])


async def _home_channel(db) -> str:
    """Channel link used by the /start screens (main link → invite → default)."""
    main_link = await db.get_config("main_channel_link") if db else None
    invite_link = await db.get_config("channel_invite_link") if db else None
    return main_link or invite_link or "https://t.me/animedekho"


async def _welcome_caption(db, user_mention: str) -> str:
    """Owner-authored /start text (config ``START_MSG`` / DB ``start_msg``).

    config.py documents the ``{mention}`` placeholder; when nothing is set the
    default streaming welcome is used (issue #35: custom text never showed).
    """
    try:
        custom = await db.get_start_msg() if db else None
    except Exception as e:
        log.debug("start message lookup failed: %s", e)
        custom = None
    if custom and custom.strip():
        return custom.replace("{mention}", user_mention)
    return _start_caption(user_mention)


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
        raw_arg = args[1].strip()
        from utils.helpers import decode_file_param
        param = decode_file_param(raw_arg)
        if param.startswith("get_"):
            await _handle_file_request(client, message, param)
            return
        elif param.startswith("join_"):
            await _handle_channel_join_request(client, message, param[5:])
            return
        elif param.startswith("dl_"):
            # Issue #33: gated ⬇ DOWNLOAD button on channel posts.
            await _handle_gated_download(client, message, param)
            return

    if bot.logger.bot_logger and user:
        await bot.logger.bot_logger.log_bot_start(user.id, user.username or user.first_name)

    # Get channel invite link for the welcome message
    invite_link = None
    if db:
        invite_link = await db.get_config("channel_invite_link")

    start_style = await db.get_start_style() if db else "modern"

    if start_style == "modern":
        first_name = user.first_name if user else "Friend"
        user_mention = f"<a href='tg://user?id={user_id}'>{re.sub(r'[<>&]', '', first_name)}</a>" if user_id else (first_name or "Friend")

        main_chan = await _home_channel(db)
        modern_markup = _start_markup(main_chan)
        caption = await _welcome_caption(db, user_mention)

        start_pic = await db.get_start_pic() if db else None
        # Default stylish welcome banner fallback if user has not set a custom start picture
        from pathlib import Path
        import os
        welcome_asset = str(Path(__file__).resolve().parent.parent.parent / "assets" / "welcome.png")
        default_pic = welcome_asset if os.path.isfile(welcome_asset) else "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=1000&auto=format&fit=crop"
        from utils.helpers import resolve_photo_source

        sent = False
        for pic in dict.fromkeys(p for p in (start_pic, default_pic) if p):
            src = resolve_photo_source(pic)
            try:
                await message.reply_photo(
                    photo=src,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=modern_markup,
                )
                sent = True
                break
            except Exception as pe:
                # Issue #35: a START_PIC Telegram refuses (bad file_id, dead
                # URL, missing file) must degrade — never swallow /start.
                log.warning("Start banner %s failed (%s) — trying the next one", str(pic)[:80], pe)
            finally:
                if hasattr(src, "close"):
                    try:
                        src.close()
                    except Exception:
                        pass

        if not sent:
            await message.reply_text(
                caption,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=modern_markup,
            )
        return

    from bot.emojis import get_emoji
    tv_emoji = get_emoji("tv", "📺")

    welcome_text = (
        "🎌 <b>AnimeDekho Bot</b>\n\n"
        "Stream Hindi-dubbed anime on demand.\n\n"
        f"• {tv_emoji} <b>Recent Series</b> — what just dropped\n"
        "• 📂 <b>Browse Genres</b> — pick a mood\n\n"
        "Type any anime name and I'll find it for you."
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

    if data == "start:about":
        text = (
            "🍿 <b>ABOUT ANIMEDEKHO</b> 🍿\n\n"
            "<blockquote><b>🤖 Bot:</b> AnimeDekho\n"
            "<b>⚡ Engine:</b> V3 streaming · multi-audio HLS / DASH\n"
            "<b>🐍 Framework:</b> WZGram / Pyrogram\n\n"
            "<b>🎬 Watch</b>\n"
            "• Netflix-style auto thumbnail on every upload\n"
            "• 480p → 720p → 1080p → 4K, Hindi dub first\n"
            "• One master post per anime, updated in place\n\n"
            "<b>⚙️ Automate</b>\n"
            "• Auto episode monitor &amp; airing schedules\n"
            "• Channel auto-mapping &amp; dedicated channels\n"
            "• Anti-copyright 2-minute invite links &amp; auto-delete\n"
            "• Optional DOWNLOAD gate behind a second channel</blockquote>\n\n"
            "<i>Tap below to go back to the menu.</i>"
        )
        markup = _screen_markup(await _home_channel(db))
    elif data == "start:help":
        text = (
            "📖 <b>HELP &amp; COMMANDS</b> 📖\n\n"
            "<blockquote><b>🧭 Everyday</b>\n"
            "• /start — Open the menu\n"
            "• /search &lt;anime&gt; — Search series &amp; movies\n"
            "• /schedule — Today's release schedule\n"
            "• /help — This guide\n\n"
            "<b>🎛️ Owner &amp; Admin</b>\n"
            "• /settings — Interactive control panel\n"
            "• /commands — Categorized command catalog\n"
            "• /source — Switch the default download source\n"
            "• /automonitor — Auto-download new episodes\n"
            "• /mapchannel — Route a series to its own channel\n"
            "• /linkgate — Gate the post's DOWNLOAD button\n"
            "• /endsticker — END OF SEASON sticker\n"
            "• /thumbuser · /thumblogo — Thumbnail branding\n"
            "• /startpic · /setthumb — Banner &amp; file art\n"
            "• /setdump — Configure dump storage channel</blockquote>\n\n"
            "<i>Tap below to go back to the menu.</i>"
        )
        markup = _screen_markup(await _home_channel(db))
    else:  # start:home
        user = query.from_user
        user_id = user.id if user else 0
        first_name = user.first_name if user else "Friend"
        user_mention = f"<a href='tg://user?id={user_id}'>{re.sub(r'[<>&]', '', first_name)}</a>" if user_id else (first_name or "Friend")
        markup = _start_markup(await _home_channel(db))
        text = await _welcome_caption(db, user_mention)

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
    # Local import: `settings` was referenced without ever being imported,
    # which made /help raise NameError for every user (issue #33 finding).
    from config.settings import settings
    is_owner_user = message.from_user and message.from_user.id == settings.bot.owner_id
    owner_help = (
        "\n\n<b>🎛️ Owner &amp; Admin Commands</b>\n"
        "• <b>/settings</b> — Interactive control panel &amp; live toggles\n"
        "• <b>/commands</b> — Interactive categorized command guide\n"
        "• /stats — Real-time VPS stats &amp; network speed\n"
        "• /health — System health &amp; bot diagnostics\n"
        "• /users — Total registered users\n"
        "• /broadcast · /pbroadcast · /dbroadcast — Announce to everyone\n"
        "• /ban &lt;id&gt; · /uban &lt;id&gt; — Ban or unban a user\n"
        "• /fsub · /fsub_mod · /dlt_time — Force-subscribe &amp; auto-delete timers\n"
        "• /startstyle · /schedstyle · /epstyle · /poststyle — UI styles\n"
        "• /startpic — Custom banner for /start\n"
        "• /source — Show or change the default download source\n"
        "• /linkgate · /endsticker — Channel post extras (gate + sticker)\n"
        "• /thumbuser · /thumblogo — Thumbnail branding\n"
        "• /setdump — Dump storage channel\n"
        "• /tutorial — Full system guide\n"
        "• /ai &lt;query&gt; — Chat with the autonomous AI agent\n"
        "• /setai — View &amp; change AI model/provider\n"
        "• /addbot — Add a child worker bot\n"
        "• /delete — Delete a series or file"
    ) if is_owner_user else ""

    await message.reply_text(
        "📖 <b>AnimeDekho — Help</b>\n\n"
        "<b>🧭 Everyday</b>\n"
        "• /start — Main menu\n"
        "• /search &lt;name&gt; — Search anime or movies\n"
        "• /schedule — Today's airing schedule\n"
        "• /commands — Interactive categorized guide\n"
        "• /help — This message\n\n"
        "💡 <i>Just type any anime name in chat to search!</i>"
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


async def _handle_gated_download(client: Client, message: Message, param: str):
    """Issue #33: gate the ⬇ DOWNLOAD button behind a second channel.

    1. Force-subscribe check (existing timer/standard link flow).
    2. Link gate check — join-request / timer / plain link to the gate channel.
    3. Once through, reveal the real 480p · 720p · 1080p buttons.
    """
    from bot.database import db
    import html as htmlmod
    from bot.linkgate import decode_gate_param, gate_prompt
    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton

    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    user = message.from_user
    user_id = user.id if user else 0
    slug, season = decode_gate_param(param)
    if not slug:
        await message.reply_text("⚠️ Invalid download link.")
        return

    # 1. Force-subscribe (existing behaviour, unchanged). `retry_param` must be
    #    the *encoded* form — that's what lands in the ?start= URL.
    from bot.fsub import check_fsub, send_fsub_prompt
    from utils.helpers import encode_file_param
    is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=encode_file_param(param))
    if not is_sub:
        await send_fsub_prompt(message, f_text, f_markup)
        return

    # 2. Link gate (issue #33) — returns None once the user is through.
    prompt = await gate_prompt(client, user_id, slug, season)
    if prompt is not None:
        text, markup = prompt
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        return

    # 3. Through the gate → show the real quality buttons.
    from bot.library import qualities_for_series, build_quality_buttons
    qualities = await qualities_for_series(slug)
    if not qualities:
        await message.reply_text("❌ No files found for this series yet.")
        return

    is_movie = "movie" in slug.lower()
    try:
        doc = await db.files.find_one({"series_slug": slug})
        title = (doc or {}).get("series_title") or slug
    except Exception:
        title = slug

    bot_me = getattr(client, "me", None)
    b_name = bot_me.username if bot_me and bot_me.username else "bot"
    text = (
        f"📺 <b>{htmlmod.escape(title)}</b>\n"
        f"<b>──────────────────────</b>\n\n"
        f"<blockquote>• {'Movie' if is_movie else f'Season {season:02d}'} | "
        f"Quality - {', '.join(qualities)} | #Official</blockquote>\n\n"
        "📊 <b>Select quality to download:</b>"
    )
    markup = build_quality_buttons(
        qualities, slug, is_movie=is_movie, bot_username=b_name,
    )
    buttons = list(markup.inline_keyboard)
    buttons.append([InlineKeyboardButton("🔙 Menu", url=f"https://t.me/{b_name}?start=start")])

    try:
        await message.reply_text(
            text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        log.warning("Gated download reply failed for %s: %s", slug, e)
        await message.reply_text(f"❌ Could not open the download menu: <code>{htmlmod.escape(str(e)[:160])}</code>",
                                 parse_mode=enums.ParseMode.HTML)


async def _handle_channel_join_request(client: Client, message: Message, series_slug: str):
    """Generate a 2-minute temporary invite link to the mapped series channel."""
    from bot.database import db
    import html as htmlmod
    if not db:
        await message.reply_text("⚠️ Database not available.")
        return

    user = message.from_user
    user_id = user.id if user else 0

    from bot.fsub import check_fsub, create_timer_invite_link, send_fsub_prompt
    from utils.helpers import encode_file_param
    sec_retry_param = encode_file_param(f"join_{series_slug}")
    is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=sec_retry_param)
    if not is_sub:
        await send_fsub_prompt(message, f_text, f_markup)
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
    retry_url = f"https://t.me/{bot_username}?start={sec_retry_param}" if bot_username else ""

    from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # Generate 2-minute timer link (Anti-Copyright Protection)
    timer_link = await create_timer_invite_link(
        client,
        channel_id,
        expire_seconds=120,
        member_limit=1,
        name=f"Join {series_slug[:15]}",
    )
    if not timer_link and mapping.get("invite_link"):
        timer_link = mapping["invite_link"]

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
    from bot.fsub import check_fsub, send_fsub_prompt
    is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=param)
    if not is_sub:
        await send_fsub_prompt(message, f_text, f_markup)
        return

    # Parse: get_<slug>_<quality>_<ep_key>
    raw = param[4:]  # strip "get_"

    # Match episode key at the end (S\d+E\d+|movie|all|\d+) with extended quality formats (Issue #20 - Bug 19)
    m = re.match(
        r"^(.+?)_((?:480|720|1080|2160|\d{3,4})p?(?:[\s_.-]?(?:hq|hevc|x265|x264|10-?bit|web-?dl|hdrip|dvdrip))*|4k|auto)_(s\d+e\d+|movie|all|\d+)$",
        raw,
        re.IGNORECASE,
    )
    if m:
        series_slug = m.group(1)
        quality = m.group(2)
        episode_key = m.group(3)
    else:
        # Fallback to rsplit: <slug>_<quality>_<episode_key>
        parts = raw.rsplit("_", 2)
        if len(parts) == 3 and re.match(r"^(s\d+e\d+|movie|all|\d+)$", parts[2], re.IGNORECASE):
            series_slug, quality, episode_key = parts[0], parts[1], parts[2]
        else:
            await message.reply_text("⚠️ Invalid file link format.")
            return

    import html as htmlmod
    from utils.helpers import slug_to_title
    title = slug_to_title(series_slug)
    # Prefer the real stored title: slug_to_title mangles names like
    # "Ranma ½" (slug ranma-1-2) into "Ranma 1 2" (issue #30 caption).
    try:
        if db:
            _doc = await db.files.find_one({"series_slug": series_slug})
            _stored = (_doc or {}).get("series_title") or ""
            if _stored and len(_stored.strip()) >= 3:
                title = _stored.strip()
    except Exception:
        pass
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

        if quality.lower() in ("4k", "2160p", "2160") and all_files:
            from utils.anime_match import is_4k_satisfying
            all_files = [f for f in all_files if is_4k_satisfying(f.get("quality", ""), size=f.get("file_size"))]

        if not all_files:
            await message.reply_text("❌ No files found for this series.")
            return

        from bot.library import _ep_sort_key
        all_files.sort(key=lambda x: _ep_sort_key(x["episode_key"]))

        status_msg = await message.reply_text(f"📤 Sending {len(all_files)} episodes...")
        from bot.auto_delete import auto_delete_service
        from utils.helpers import encode_file_param
        upload_mode = await db.get_upload_mode() if db else "video"

        for f in all_files:
            ep_key = f["episode_key"]
            file_id = f["file_id"]
            q_label = f.get("quality", quality)
            caption = f"📺 {htmlmod.escape(title)} [{q_label}] — {ep_key}"
            sent_msg = None
            if upload_mode == "document":
                try:
                    sent_msg = await message.reply_document(document=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    try:
                        sent_msg = await message.reply_video(video=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                    except Exception as e:
                        log.error("Failed to send %s: %s", ep_key, e)
            else:
                try:
                    sent_msg = await message.reply_video(video=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    try:
                        sent_msg = await message.reply_document(document=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
                    except Exception as e:
                        log.error("Failed to send %s: %s", ep_key, e)

            if sent_msg:
                sec_param = encode_file_param(f"get_{series_slug}_{quality}_{ep_key}")
                await auto_delete_service.schedule_deletion(
                    client=client,
                    chat_id=message.chat.id,
                    message_id=sent_msg.id,
                    get_file_link=f"https://t.me/{b_name}?start={sec_param}",
                    file_title=title,
                )
            await asyncio.sleep(0.4)

        await status_msg.delete()

        # Send auto-delete notification note (Issue #15)
        dlt_seconds = await db.get_dlt_time() if db else 0
        if dlt_seconds > 0:
            dlt_mins = max(1, round(dlt_seconds / 60))
            notice_text = (
                f"<blockquote>‣ <b>ɴᴏᴛᴇ:</b> ᴛʜɪs ғɪʟᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ɪɴ "
                f"<b>{dlt_mins} mins</b>. ꜰᴏʀᴡᴀʀᴅ ɪᴛ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ɴᴏᴡ..!</blockquote>"
            )
            notice_msg = await message.reply_text(notice_text, parse_mode=enums.ParseMode.HTML)
            await auto_delete_service.schedule_deletion(
                client=client,
                chat_id=message.chat.id,
                message_id=notice_msg.id,
                custom_seconds=dlt_seconds,
            )
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
        from bot.emojis import get_emoji
        from utils.helpers import encode_file_param
        upload_mode = await db.get_upload_mode() if db else "video"
        is_mov = bool(episode_key.lower() == "movie")
        media_icon = get_emoji("movie", "🎬") if is_mov else get_emoji("tv", "📺")
        caption = f"{media_icon} {htmlmod.escape(title)} [{quality}]"
        if not is_mov:
            caption += f" — {episode_key}"

        if upload_mode == "document":
            try:
                sent_msg = await message.reply_document(
                    document=file_id,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                sent_msg = await message.reply_video(
                    video=file_id,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
        else:
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
            sec_param = encode_file_param(param)
            await auto_delete_service.schedule_deletion(
                client=client,
                chat_id=message.chat.id,
                message_id=sent_msg.id,
                get_file_link=f"https://t.me/{b_name}?start={sec_param}",
                file_title=title,
            )

            # Send auto-delete notification note (Issue #15)
            dlt_seconds = await db.get_dlt_time() if db else 0
            if dlt_seconds > 0:
                dlt_mins = max(1, round(dlt_seconds / 60))
                notice_text = (
                    f"<blockquote>‣ <b>ɴᴏᴛᴇ:</b> ᴛʜɪs ғɪʟᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ɪɴ "
                    f"<b>{dlt_mins} mins</b>. ꜰᴏʀᴡᴀʀᴅ ɪᴛ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ɴᴏᴡ..!</blockquote>"
                )
                notice_msg = await message.reply_text(notice_text, parse_mode=enums.ParseMode.HTML)
                await auto_delete_service.schedule_deletion(
                    client=client,
                    chat_id=message.chat.id,
                    message_id=notice_msg.id,
                    custom_seconds=dlt_seconds,
                )

    except Exception as e:
        log.error("Failed to send library file: %s", e)
        await message.reply_text("⚠️ Could not deliver file. Please try again.")
