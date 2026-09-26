from bot.telegram import Client, filters, MessageHandler, CallbackQueryHandler

from .commands import cmd_start, cmd_help, cmd_search, start_callback
from .callbacks import callback_router
from .messages import handle_text
from .admin import (
    cmd_adduser, cmd_removeuser, cmd_users,
    cmd_setchannellink,
    cmd_delete, delete_callback,
    cmd_addbot, cmd_delbot, cmd_bots, cmd_setbotquality, cmd_refreshalbums,
    cmd_login, cmd_logout, cmd_userbot, cmd_cancel, cmd_autochannel,
    cmd_albummode, cmd_createchannel, cmd_mapchannel, cmd_unmapchannel, cmd_channels,
    cmd_health, cmd_logs, cmd_errors, cmd_clearerrors, health_callback,
    cmd_setdump, cmd_setthumb, cmd_delthumb, cmd_viewthumb, cmd_automonitor, cmd_poststyle,
    cmd_startstyle, cmd_startpic, cmd_epstyle, cmd_schedstyle,
)
from .admin_ai import cmd_setai, cmd_ai
from .worker_admin import (
    cmd_stats, cmd_users_count, cmd_ban, cmd_unban,
    cmd_broadcast, cmd_pbroadcast, cmd_dbroadcast,
    cmd_fsub, cmd_fsub_mod, cmd_dlt_time, cmd_tutorial,
    dlt_time_callback, toggle_fsub_mod_callback,
)
from .schedule import cmd_schedule, schedule_callback
from .settings import cmd_commands, commands_callback, cmd_settings, settings_callback
from bot.auto_delete import handle_close_dlt_notice

__all__ = [
    "cmd_start", "cmd_help", "cmd_search", "callback_router", "handle_text",
    "cmd_adduser", "cmd_removeuser", "cmd_users",
    "cmd_setchannellink",
    "cmd_delete",
    "cmd_setai", "cmd_ai",
    "cmd_addbot", "cmd_delbot", "cmd_bots", "cmd_setbotquality", "cmd_refreshalbums",
    "cmd_login", "cmd_logout", "cmd_userbot", "cmd_cancel", "cmd_autochannel",
    "cmd_albummode", "cmd_createchannel", "cmd_mapchannel", "cmd_unmapchannel", "cmd_channels",
    "cmd_health", "cmd_logs", "cmd_errors", "cmd_clearerrors", "health_callback",
    "cmd_stats", "cmd_users_count", "cmd_ban", "cmd_unban",
    "cmd_broadcast", "cmd_pbroadcast", "cmd_dbroadcast",
    "cmd_fsub", "cmd_fsub_mod", "cmd_dlt_time", "cmd_tutorial",
    "cmd_schedule", "schedule_callback",
    "cmd_setdump", "cmd_setthumb", "cmd_delthumb", "cmd_viewthumb", "cmd_automonitor", "cmd_poststyle",
    "cmd_startstyle", "cmd_startpic", "cmd_epstyle", "cmd_schedstyle", "start_callback",
    "register_handlers",
]


def register_handlers(app: Client):
    """Register all handlers on the Pyrogram Client."""
    # Commands
    app.add_handler(MessageHandler(cmd_start, filters.command("start") & filters.private))
    app.add_handler(MessageHandler(cmd_help, filters.command("help") & filters.private))
    app.add_handler(MessageHandler(cmd_search, filters.command("search") & filters.private))

    # Owner AI commands
    app.add_handler(MessageHandler(cmd_setai, filters.command("setai") & filters.private))
    app.add_handler(MessageHandler(cmd_ai, filters.command("ai") & filters.private))

    # Admin commands (owner-only, checked inside each handler)
    app.add_handler(MessageHandler(cmd_adduser, filters.command("adduser") & filters.private))
    app.add_handler(MessageHandler(cmd_removeuser, filters.command("removeuser") & filters.private))
    app.add_handler(MessageHandler(cmd_users_count, filters.command("users") & filters.private))
    app.add_handler(MessageHandler(cmd_users, filters.command(["approvedusers", "whitelist"]) & filters.private))
    app.add_handler(MessageHandler(cmd_setchannellink, filters.command("setchannellink") & filters.private))
    app.add_handler(MessageHandler(cmd_delete, filters.command("delete") & filters.private))
    app.add_handler(MessageHandler(cmd_addbot, filters.command("addbot") & filters.private))
    app.add_handler(MessageHandler(cmd_delbot, filters.command("delbot") & filters.private))
    app.add_handler(MessageHandler(cmd_bots, filters.command("bots") & filters.private))
    app.add_handler(MessageHandler(cmd_setbotquality, filters.command("setbotquality") & filters.private))
    app.add_handler(MessageHandler(cmd_refreshalbums, filters.command("refreshalbums") & filters.private))

    # Schedule command (public)
    app.add_handler(MessageHandler(cmd_schedule, filters.command("schedule") & filters.private))

    # Worker Admin & Fleet Management commands
    app.add_handler(MessageHandler(cmd_stats, filters.command("stats") & filters.private))
    app.add_handler(MessageHandler(cmd_ban, filters.command("ban") & filters.private))
    app.add_handler(MessageHandler(cmd_unban, filters.command(["uban", "unban"]) & filters.private))
    app.add_handler(MessageHandler(cmd_broadcast, filters.command("broadcast") & filters.private))
    app.add_handler(MessageHandler(cmd_pbroadcast, filters.command("pbroadcast") & filters.private))
    app.add_handler(MessageHandler(cmd_dbroadcast, filters.command("dbroadcast") & filters.private))
    app.add_handler(MessageHandler(cmd_fsub, filters.command("fsub") & filters.private))
    app.add_handler(MessageHandler(cmd_fsub_mod, filters.command("fsub_mod") & filters.private))
    app.add_handler(MessageHandler(cmd_dlt_time, filters.command("dlt_time") & filters.private))
    app.add_handler(MessageHandler(cmd_tutorial, filters.command("tutorial") & filters.private))

    # Storage Dump, Custom Thumbnails, Auto-Monitor & UI Style Configurations
    app.add_handler(MessageHandler(cmd_setdump, filters.command(["setdump", "dumpchannel"]) & filters.private))
    app.add_handler(MessageHandler(cmd_setthumb, filters.command("setthumb") & filters.private))
    app.add_handler(MessageHandler(cmd_delthumb, filters.command("delthumb") & filters.private))
    app.add_handler(MessageHandler(cmd_viewthumb, filters.command("viewthumb") & filters.private))
    app.add_handler(MessageHandler(cmd_automonitor, filters.command(["automonitor", "monitor"]) & filters.private))
    app.add_handler(MessageHandler(cmd_poststyle, filters.command("poststyle") & filters.private))
    app.add_handler(MessageHandler(cmd_startstyle, filters.command("startstyle") & filters.private))
    app.add_handler(MessageHandler(cmd_startpic, filters.command("startpic") & filters.private))
    app.add_handler(MessageHandler(cmd_epstyle, filters.command("epstyle") & filters.private))
    app.add_handler(MessageHandler(cmd_schedstyle, filters.command("schedstyle") & filters.private))

    # Userbot & Channel mapping commands
    app.add_handler(MessageHandler(cmd_login, filters.command("login") & filters.private))
    app.add_handler(MessageHandler(cmd_logout, filters.command("logout") & filters.private))
    app.add_handler(MessageHandler(cmd_userbot, filters.command("userbot") & filters.private))
    app.add_handler(MessageHandler(cmd_cancel, filters.command("cancel") & filters.private))
    app.add_handler(MessageHandler(cmd_autochannel, filters.command("autochannel") & filters.private))
    app.add_handler(MessageHandler(cmd_albummode, filters.command("albummode") & filters.private))
    app.add_handler(MessageHandler(cmd_createchannel, filters.command("createchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_mapchannel, filters.command("mapchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_unmapchannel, filters.command("unmapchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_channels, filters.command("channels") & filters.private))

    # Health, Diagnostics & System Monitoring
    app.add_handler(MessageHandler(cmd_health, filters.command(["health", "status"]) & filters.private))
    app.add_handler(MessageHandler(cmd_logs, filters.command("logs") & filters.private))
    app.add_handler(MessageHandler(cmd_errors, filters.command("errors") & filters.private))
    app.add_handler(MessageHandler(cmd_clearerrors, filters.command("clearerrors") & filters.private))

    # Commands Navigator & Interactive Settings Panel
    app.add_handler(MessageHandler(cmd_commands, filters.command("commands") & filters.private))
    app.add_handler(MessageHandler(cmd_settings, filters.command("settings") & filters.private))

    # Specific callbacks (before general router)
    app.add_handler(CallbackQueryHandler(start_callback, filters.regex(r"^start:")))
    app.add_handler(CallbackQueryHandler(commands_callback, filters.regex(r"^(cmd_cat:|open_settings)")))
    app.add_handler(CallbackQueryHandler(settings_callback, filters.regex(r"^(set_toggle:|settings_action:)")))
    app.add_handler(CallbackQueryHandler(health_callback, filters.regex(r"^health:")))
    app.add_handler(CallbackQueryHandler(delete_callback, filters.regex(r"^del:")))
    app.add_handler(CallbackQueryHandler(dlt_time_callback, filters.regex(r"^dlt:\d+$")))
    app.add_handler(CallbackQueryHandler(toggle_fsub_mod_callback, filters.regex(r"^toggle_fsub_mod$")))
    app.add_handler(CallbackQueryHandler(handle_close_dlt_notice, filters.regex(r"^close_dlt_notice$")))
    app.add_handler(CallbackQueryHandler(schedule_callback, filters.regex(r"^sch:")))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Text messages (search) — must be last to avoid catching commands
    # Note: filters.regex matches non-command text (doesn't start with /)
    app.add_handler(MessageHandler(handle_text, filters.text & filters.private & filters.regex(r"^[^/]")))
