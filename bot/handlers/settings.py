"""
Interactive /commands Guide and /settings Control Panel Dashboard.

Addresses Issue #8 (Point 1: Command overload & Visual Settings Panel).
- /commands: Interactive categorized menu showing all 60+ commands cleanly.
- /settings: Visual interactive dashboard with live toggle buttons for all bot features:
  * FSub Timer Link Mode (ON / OFF)
  * File Auto-Delete Timer (Off / 5m / 10m / 30m / 1h)
  * Start Menu UI Style (Classic / Modern)
  * Schedule UI Style (Classic / Modern)
  * Episode Post UI Style (Classic / Modern)
  * Channel Card UI Style (Classic / Modern)
  * Auto Thumbnail Generator (ON / OFF)
  * Daily 12:00 AM IST Schedule Post (ON / OFF)
  * AI Autonomous Assistant (ON / OFF)
"""

from __future__ import annotations

import logging
from bot.telegram import Client, enums
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.auth import is_owner, require_owner

log = logging.getLogger(__name__)


# ── Commands Menu Categories ──────────────────────────────────────────


COMMANDS_MENU_TEXT = (
    "📖 <b>AnimeDekho Bot — Commands Navigator</b>\n\n"
    "Explore and manage all bot commands by selecting a category below:"
)

COMMAND_CATEGORIES = {
    "cmd_cat:user": (
        "👤 <b>User & Search Commands:</b>\n\n"
        "• <code>/start</code> — Open main start menu\n"
        "• <code>/search &lt;name&gt;</code> — Search anime series & movies\n"
        "• <code>/schedule</code> — Today's anime airing schedule & countdowns\n"
        "• <code>/help</code> — Quick instructions and usage guide\n"
        "• <code>/commands</code> — Interactive command categories menu\n\n"
        "<i>Tip: You can also search by typing any anime name directly in chat!</i>"
    ),
    "cmd_cat:owner": (
        "👑 <b>Owner & Management Commands:</b>\n\n"
        "• <code>/stats</code> — Real-time VPS performance & network speed card\n"
        "• <code>/health</code> — Full system health check & diagnostics\n"
        "• <code>/users</code> — Total registered users & bot statistics\n"
        "• <code>/broadcast &lt;msg&gt;</code> — Send global text broadcast\n"
        "• <code>/pbroadcast</code> — Reply to photo to broadcast with caption\n"
        "• <code>/dbroadcast</code> — Reply to video/document to broadcast\n"
        "• <code>/ban &lt;user_id&gt;</code> — Ban user from the entire bot\n"
        "• <code>/uban &lt;user_id&gt;</code> — Unban user\n"
        "• <code>/addbot</code> — Register child worker bot token\n"
        "• <code>/delbot &lt;id&gt;</code> — Remove child worker bot\n"
        "• <code>/bots</code> — View list of all active child worker bots"
    ),
    "cmd_cat:channels": (
        "📢 <b>Channels & Force Subscribe:</b>\n\n"
        "• <code>/fsub</code> — Manage Force Subscribe channel\n"
        "• <code>/fsub_mod</code> — Toggle 2-minute expiring FSub timer links\n"
        "• <code>/setchannellink &lt;url&gt;</code> — Set main channel invite link\n"
        "• <code>/mapchannel &lt;slug&gt; &lt;cid&gt;</code> — Map series to dedicated channel\n"
        "• <code>/unmapchannel &lt;slug&gt;</code> — Remove series channel mapping\n"
        "• <code>/channels</code> — View all mapped dedicated series channels\n"
        "• <code>/setdump &lt;cid&gt;</code> — Configure storage dump channel\n"
        "• <code>/autochannel &lt;on|off&gt;</code> — Toggle automatic channel creation"
    ),
    "cmd_cat:styles": (
        "🎨 <b>UI Styles & Customization:</b>\n\n"
        "• <code>/settings</code> — Interactive visual control panel\n"
        "• <code>/startstyle &lt;classic|modern&gt;</code> — Set /start UI design\n"
        "• <code>/startpic &lt;url|reply|reset&gt;</code> — Set /start banner photo\n"
        "• <code>/schedstyle &lt;classic|modern&gt;</code> — Set /schedule design\n"
        "• <code>/epstyle &lt;classic|modern&gt;</code> — Set episode post design\n"
        "• <code>/poststyle &lt;classic|modern&gt;</code> — Set channel card design\n"
        "• <code>/setthumb &lt;reply&gt;</code> — Set custom manual thumbnail\n"
        "• <code>/delthumb</code> — Delete custom thumbnail\n"
        "• <code>/viewthumb</code> — View currently configured thumbnail"
    ),
    "cmd_cat:features": (
        "⚡ <b>Automation, AI & Storage:</b>\n\n"
        "• <code>/autosearch &lt;on|off&gt;</code> — Toggle direct name typing search\n"
        "• <code>/dlt_time &lt;seconds&gt;</code> — Set file auto-delete timer (e.g. 600)\n"
        "• <code>/automonitor &lt;on|off&gt;</code> — Automated episode release monitor\n"
        "• <code>/ai &lt;query&gt;</code> — Ask Autonomous AI Agent to find anime\n"
        "• <code>/setai</code> — Configure AI Agent provider & model\n"
        "• <code>/delete &lt;slug&gt;</code> — Delete series from library\n"
        "• <code>/refreshalbums</code> — Re-sync channel posts with active style\n"
        "• <code>/logs</code> — Download latest bot log file\n"
        "• <code>/errors</code> — View recent failed download errors"
    ),
}


def _build_commands_main_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 User Commands", callback_data="cmd_cat:user"),
            InlineKeyboardButton("👑 Owner Commands", callback_data="cmd_cat:owner"),
        ],
        [
            InlineKeyboardButton("📢 Channels & FSub", callback_data="cmd_cat:channels"),
            InlineKeyboardButton("🎨 Styles & UI", callback_data="cmd_cat:styles"),
        ],
        [
            InlineKeyboardButton("⚡ Automation & AI", callback_data="cmd_cat:features"),
        ],
        [
            InlineKeyboardButton("⚙️ Open /settings Dashboard", callback_data="open_settings"),
            InlineKeyboardButton("❌ Close", callback_data="settings_action:close"),
        ]
    ])


async def cmd_commands(client: Client, message: Message):
    """Display categorized /commands interactive navigator."""
    await message.reply_text(
        COMMANDS_MENU_TEXT,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_build_commands_main_markup(),
    )


async def commands_callback(client: Client, query: CallbackQuery):
    """Handle /commands navigation callbacks."""
    data = query.data

    if data == "cmd_cat:home":
        await query.message.edit_text(
            COMMANDS_MENU_TEXT,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_build_commands_main_markup(),
        )
        await query.answer()
        return

    if data in COMMAND_CATEGORIES:
        text = COMMAND_CATEGORIES[data]
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀ Back to Categories", callback_data="cmd_cat:home")],
            [InlineKeyboardButton("❌ Close", callback_data="settings_action:close")],
        ])
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        await query.answer()
        return

    if data == "open_settings":
        # Switch to /settings panel
        from bot.database import db
        user = query.from_user
        if not user or not is_owner(user.id):
            await query.answer("⛔ Settings panel is for Owner/Admins only.", show_alert=True)
            return
        settings_text, markup = await _render_settings_panel(db)
        await query.message.edit_text(settings_text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        await query.answer()
        return


# ── Interactive /settings Control Panel ───────────────────────────────


async def _render_settings_panel(db) -> tuple[str, InlineKeyboardMarkup]:
    """Fetch current state from DB and render interactive dashboard."""
    fsub_mod = await db.get_fsub_mod() if db else True
    dlt_time = await db.get_dlt_time() if db else 600
    start_style = await db.get_start_style() if db else "classic"
    sched_style = await db.get_sched_style() if db else "classic"
    ep_style = await db.get_ep_style() if db else "classic"
    post_style = await db.get_post_style() if db else "classic"
    auto_thumb = await db.get_auto_thumb() if db else True
    auto_sched = await db.get_auto_schedule_post() if db else False
    auto_search = await db.get_auto_search() if db else True

    from config.settings import settings
    ai_enabled = settings.ai.enabled if settings and settings.ai else True

    # Formatting helper badges
    def _badge(val: bool) -> str:
        return "ON ✅" if val else "OFF ❌"

    def _style_badge(style: str) -> str:
        return "Modern 🎨" if style == "modern" else "Classic 📜"

    def _dlt_badge(sec: int) -> str:
        if sec <= 0:
            return "Disabled ❌"
        if sec == 300:
            return "5m ⏳"
        if sec == 600:
            return "10m ⏳"
        if sec == 1800:
            return "30m ⏳"
        if sec == 3600:
            return "1h ⏳"
        return f"{sec}s ⏳"

    text = (
        "⚙️ <b>AnimeDekho Bot — Settings & Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Click the buttons below to toggle features and customize UI styles in real-time.\n\n"
        f"• 🔒 <b>FSub Timer Link:</b> <code>{_badge(fsub_mod)}</code> (2m Expire)\n"
        f"• ⏳ <b>File Auto-Delete:</b> <code>{_dlt_badge(dlt_time)}</code>\n"
        f"• 🎨 <b>Start Menu UI:</b> <code>{_style_badge(start_style)}</code>\n"
        f"• 📅 <b>Schedule UI:</b> <code>{_style_badge(sched_style)}</code>\n"
        f"• 📺 <b>Episode Post:</b> <code>{_style_badge(ep_style)}</code>\n"
        f"• 🖼️ <b>Channel Card:</b> <code>{_style_badge(post_style)}</code>\n"
        f"• 🖼️ <b>Auto Thumbnail:</b> <code>{_badge(auto_thumb)}</code> (1280x720 HD)\n"
        f"• ⏰ <b>12 AM Schedule:</b> <code>{_badge(auto_sched)}</code> (Main Channel)\n"
        f"• 🔍 <b>Auto Chat Search:</b> <code>{_badge(auto_search)}</code> (Name Trigger)\n"
        f"• 🤖 <b>AI Assistant:</b> <code>{_badge(ai_enabled)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔒 FSub: {_badge(fsub_mod)}", callback_data="set_toggle:fsub_mod"),
            InlineKeyboardButton(f"⏳ Delete: {_dlt_badge(dlt_time)}", callback_data="set_toggle:dlt_time"),
        ],
        [
            InlineKeyboardButton(f"🎨 Start: {_style_badge(start_style)}", callback_data="set_toggle:start_style"),
            InlineKeyboardButton(f"📅 Sched: {_style_badge(sched_style)}", callback_data="set_toggle:sched_style"),
        ],
        [
            InlineKeyboardButton(f"📺 Ep: {_style_badge(ep_style)}", callback_data="set_toggle:ep_style"),
            InlineKeyboardButton(f"🖼️ Post: {_style_badge(post_style)}", callback_data="set_toggle:post_style"),
        ],
        [
            InlineKeyboardButton(f"🖼️ AutoThumb: {_badge(auto_thumb)}", callback_data="set_toggle:auto_thumb"),
            InlineKeyboardButton(f"⏰ 12 AM Post: {_badge(auto_sched)}", callback_data="set_toggle:auto_sched"),
        ],
        [
            InlineKeyboardButton(f"🔍 AutoSearch: {_badge(auto_search)}", callback_data="set_toggle:auto_search"),
            InlineKeyboardButton(f"🤖 AI Agent: {_badge(ai_enabled)}", callback_data="set_toggle:ai_enabled"),
        ],
        [
            InlineKeyboardButton("📖 Open /commands Guide", callback_data="cmd_cat:home"),
            InlineKeyboardButton("🔄 Refresh", callback_data="set_toggle:refresh"),
        ],
        [
            InlineKeyboardButton("❌ Close Panel", callback_data="settings_action:close"),
        ],
    ])

    return text, markup


@require_owner
async def cmd_settings(client: Client, message: Message):
    """Launch interactive /settings visual control panel."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database is not connected.")
        return

    text, markup = await _render_settings_panel(db)
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def settings_callback(client: Client, query: CallbackQuery):
    """Handle settings button toggle clicks."""
    user = query.from_user
    if not user or not is_owner(user.id):
        await query.answer("⛔ Owner/Admin only action.", show_alert=True)
        return

    data = query.data
    from bot.database import db
    if not db:
        await query.answer("⚠️ Database not connected.", show_alert=True)
        return

    alert_msg = "Updated!"

    if data == "settings_action:close":
        try:
            await query.message.delete()
        except Exception:
            await query.message.edit_text("<i>Settings panel closed.</i>", parse_mode=enums.ParseMode.HTML)
        await query.answer()
        return

    if data == "set_toggle:refresh":
        alert_msg = "Refreshed!"

    elif data == "set_toggle:fsub_mod":
        cur = await db.get_fsub_mod()
        new_val = not cur
        await db.set_fsub_mod(new_val)
        alert_msg = f"FSub Timer Mode: {'ON (2-min links)' if new_val else 'OFF (Standard links)'}"

    elif data == "set_toggle:dlt_time":
        cur = await db.get_dlt_time()
        # Cycle through: 0 -> 300 -> 600 -> 1800 -> 3600 -> 0
        cycle = [0, 300, 600, 1800, 3600]
        try:
            curr_idx = cycle.index(cur)
            next_val = cycle[(curr_idx + 1) % len(cycle)]
        except ValueError:
            next_val = 600
        await db.set_dlt_time(next_val)
        alert_msg = f"Auto-Delete set to: {'Disabled' if next_val == 0 else f'{next_val // 60} Minutes'}"

    elif data == "set_toggle:start_style":
        cur = await db.get_start_style()
        new_style = "classic" if cur == "modern" else "modern"
        await db.set_start_style(new_style)
        alert_msg = f"Start Menu Style set to: {new_style.capitalize()}"

    elif data == "set_toggle:sched_style":
        cur = await db.get_sched_style()
        new_style = "classic" if cur == "modern" else "modern"
        await db.set_sched_style(new_style)
        alert_msg = f"Schedule Style set to: {new_style.capitalize()}"

    elif data == "set_toggle:ep_style":
        cur = await db.get_ep_style()
        new_style = "classic" if cur == "modern" else "modern"
        await db.set_ep_style(new_style)
        alert_msg = f"Episode Post Style set to: {new_style.capitalize()}"

    elif data == "set_toggle:post_style":
        cur = await db.get_post_style()
        new_style = "classic" if cur == "modern" else "modern"
        await db.set_post_style(new_style)
        alert_msg = f"Channel Card Style set to: {new_style.capitalize()}"

    elif data == "set_toggle:auto_thumb":
        cur = await db.get_auto_thumb()
        new_val = not cur
        await db.set_auto_thumb(new_val)
        alert_msg = f"Auto Thumbnail Generator: {'ENABLED (1280x720 HD)' if new_val else 'DISABLED'}"

    elif data == "set_toggle:auto_sched":
        cur = await db.get_auto_schedule_post()
        new_val = not cur
        await db.set_auto_schedule_post(new_val)
        alert_msg = f"Daily 12:00 AM IST Schedule Post: {'ENABLED' if new_val else 'DISABLED'}"

    elif data == "set_toggle:auto_search":
        cur = await db.get_auto_search()
        new_val = not cur
        await db.set_auto_search(new_val)
        alert_msg = f"Auto Chat Search: {'ENABLED (Direct typing ON)' if new_val else 'DISABLED (Use /search)'}"

    elif data == "set_toggle:ai_enabled":
        from config.settings import settings
        cur_ai = settings.ai.enabled
        new_ai = not cur_ai
        # Update in-memory settings
        object.__setattr__(settings.ai, "enabled", new_ai)
        await db.set_config("ai_enabled", "on" if new_ai else "off")
        alert_msg = f"AI Agent: {'ENABLED' if new_ai else 'DISABLED'}"

    # Render updated UI
    text, markup = await _render_settings_panel(db)
    try:
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception as ee:
        log.debug("Edit message in settings callback: %s", ee)

    await query.answer(alert_msg)
