"""Application factory — builds and configures the WZGram/Pyrogram bot."""

import logging

from bot.telegram import Client

from config.settings import settings

log = logging.getLogger(__name__)

active_bot_client: Client | None = None


async def _on_start(client: Client):
    """Called after client starts — init HTTP client, DB & logger."""
    global active_bot_client
    active_bot_client = client

    # Init Health monitoring and memory log buffer
    from bot.health import set_boot_time, setup_health_logging
    set_boot_time()
    setup_health_logging()

    from utils.http import http_client
    await http_client.start()
    log.info("HTTP client started")

    # Init MongoDB
    from bot.database import Database
    import bot.database as db_mod
    db = Database(settings.bot.mongo_uri)
    await db.init_indexes()
    db_mod.db = db
    log.info("MongoDB connected")

    # Init AI config
    from bot.ai import ai_config
    await ai_config.load()
    log.info("AI Agent config loaded (model: %s)", ai_config.model)

    # Init bot logger
    from bot.logger import BotLogger
    import bot.logger as logger_mod
    logger_mod.bot_logger = BotLogger(client)
    log.info("Bot logger initialized")

    # Init Library Manager
    from bot.library import LibraryManager
    import bot.library as lib_mod
    me = await client.get_me()
    bot_username = me.username or ""
    lib_mod.library_manager = LibraryManager(
        client=client,
        db=db,
        main_channel=settings.bot.main_channel,
        bot_username=bot_username,
    )
    log.info("Library manager initialized (bot: @%s)", bot_username)

    # Resolve channel peers so Pyrogram can send to them
    # Try get_chat first, fall back to raw API (needed on fresh sessions)
    for name, cid in [("main", settings.bot.main_channel), ("log", settings.bot.log_channel)]:
        if cid:
            try:
                chat = await client.get_chat(cid)
                log.info("Resolved %s channel: %s (id: %d)", name, chat.title, chat.id)
            except Exception:
                # Raw API fallback for fresh sessions without cached peers
                try:
                    from bot.telegram import raw
                    GetChannels = raw.functions.channels.GetChannels
                    InputChannel = raw.types.InputChannel
                    raw_id = abs(cid) % (10 ** 10)  # Strip -100 prefix
                    peer = InputChannel(channel_id=raw_id, access_hash=0)
                    result = await client.invoke(GetChannels(id=[peer]))
                    if result.chats:
                        log.info("Resolved %s channel via raw API: %s", name, result.chats[0].title)
                    else:
                        log.warning("Could not resolve %s channel %d via raw API", name, cid)
                except Exception as e2:
                    log.warning("Could not resolve %s channel %d: %s", name, cid, e2)
    # Auto-generate channel invite link if not set
    if settings.bot.main_channel:
        try:
            from bot.database import db as app_db
            existing_link = await app_db.get_config("channel_invite_link") if app_db else None
            if not existing_link:
                chat = await client.get_chat(settings.bot.main_channel)
                if chat.invite_link:
                    invite_link = chat.invite_link
                else:
                    invite_link = (await client.create_chat_invite_link(settings.bot.main_channel)).invite_link
                if invite_link and app_db:
                    await app_db.set_config("channel_invite_link", invite_link)
                    log.info("Auto-set channel invite link: %s", invite_link)
        except Exception as e:
            log.warning("Could not auto-generate invite link: %s", e)

    # Init Child Bot Manager
    from bot.child_bots import ChildBotManager
    import bot.child_bots as child_mod
    child_mgr = ChildBotManager(main_client=client)
    await child_mgr.start()
    child_mod.child_bot_manager = child_mgr
    log.info("Child Bot Manager initialized")

    # Init Userbot Manager
    from bot.userbot import UserbotManager
    import bot.userbot as userbot_mod
    ub_mgr = UserbotManager(main_client=client)
    await ub_mgr.start()
    userbot_mod.userbot_manager = ub_mgr
    log.info("Userbot Manager initialized")

    # Init Auto-Delete Service
    from bot.auto_delete import auto_delete_service
    auto_delete_service.start(client)
    log.info("Auto-Delete service started")

    # Init Episode Monitor Service (OFF by default)
    from bot.monitor import monitor_service
    monitor_service.start(client)
    log.info("Episode Monitor Service started (OFF by default)")

    # Init Auto-Schedule Daily 12 AM Publisher (OFF by default)
    from bot.schedule import auto_schedule_service
    auto_schedule_service.start(client)
    log.info("Auto-Schedule 12 AM Publisher started (OFF by default)")

    # Set bot commands menu
    from bot.telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
    try:
        # Default commands for everyone
        await client.set_bot_commands([
            BotCommand("start", "Main menu"),
            BotCommand("search", "Search anime or movies"),
            BotCommand("schedule", "Anime airing schedule"),
            BotCommand("commands", "Interactive commands navigator"),
            BotCommand("help", "Show help message"),
        ], scope=BotCommandScopeDefault())

        # Owner commands (shown to the owner)
        if settings.bot.owner_id:
            await client.set_bot_commands([
                BotCommand("start", "Main menu"),
                BotCommand("settings", "Visual interactive control panel"),
                BotCommand("commands", "Categorized commands navigator"),
                BotCommand("search", "Search anime or movies"),
                BotCommand("schedule", "Anime airing schedule"),
                BotCommand("ai", "Autonomous AI Agent"),
                BotCommand("setai", "Configure AI model, key & persona"),
                BotCommand("help", "Show help message"),
                BotCommand("adduser", "Approve a user"),
                BotCommand("removeuser", "Remove a user"),
                BotCommand("users", "User analytics & registered count"),
                BotCommand("stats", "VPS stats & performance card"),
                BotCommand("fsub", "Manage Force Subscribe channel"),
                BotCommand("fsub_mod", "Toggle 2-min timer FSub links"),
                BotCommand("dlt_time", "Configure file auto-delete timer"),
                BotCommand("setdump", "Configure dump storage channel"),
                BotCommand("setthumb", "Set custom thumbnail for uploads"),
                BotCommand("delthumb", "Delete custom thumbnail"),
                BotCommand("automonitor", "Automatic episode monitoring"),
                BotCommand("poststyle", "Toggle channel post style (classic/modern)"),
                BotCommand("startstyle", "Toggle /start UI style (classic/modern)"),
                BotCommand("schedstyle", "Toggle /schedule UI style (classic/modern)"),
                BotCommand("epstyle", "Toggle episode post style (classic/modern)"),
                BotCommand("startpic", "Set banner photo for /start modern UI"),
                BotCommand("tutorial", "Bot network guide & tutorials"),
                BotCommand("broadcast", "Broadcast text to all users"),
                BotCommand("pbroadcast", "Broadcast photo to all users"),
                BotCommand("dbroadcast", "Broadcast file to all users"),
                BotCommand("ban", "Ban user from bot network"),
                BotCommand("unban", "Unban user from bot network"),
                BotCommand("setchannellink", "Set channel invite link"),
                BotCommand("delete", "Delete a series or file"),
                BotCommand("addbot", "Add a child worker bot"),
                BotCommand("delbot", "Remove a child worker bot"),
                BotCommand("bots", "List child worker bots"),
                BotCommand("setbotquality", "Set child bot quality tier"),
                BotCommand("refreshalbums", "Refresh channel album buttons"),
                BotCommand("login", "Login userbot session"),
                BotCommand("logout", "Logout userbot session"),
                BotCommand("userbot", "Userbot status & options"),
                BotCommand("autochannel", "Toggle auto channel creation"),
                BotCommand("albummode", "Configure album display mode"),
                BotCommand("channels", "List mapped anime channels"),
                BotCommand("createchannel", "Create channel for anime"),
                BotCommand("health", "System health & bot status"),
                BotCommand("logs", "Environment logs & export"),
                BotCommand("errors", "Recent download errors"),
            ], scope=BotCommandScopeChat(settings.bot.owner_id))
        log.info("Bot commands menu set successfully")
    except Exception as e:
        log.warning("Failed to set bot commands: %s", e)


async def _on_stop(client: Client):
    """Called on shutdown — cleanup."""
    from bot.monitor import monitor_service
    await monitor_service.stop()
    from bot.schedule import auto_schedule_service
    auto_schedule_service.stop()
    from bot.auto_delete import auto_delete_service
    await auto_delete_service.stop()
    from bot.userbot import userbot_manager
    if userbot_manager:
        await userbot_manager.stop()
    from bot.child_bots import child_bot_manager
    if child_bot_manager:
        await child_bot_manager.stop()
    from utils.http import http_client
    await http_client.close()
    from bot.database import db
    if db:
        db.close()
    log.info("HTTP client, Userbot, Child Bots, Auto-Delete & MongoDB closed")


def create_app() -> Client:
    """Build the Pyrogram Client with all handlers registered."""
    if not settings.bot.token:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if not settings.bot.owner_id:
        raise RuntimeError("OWNER_ID environment variable is required")
    if not settings.bot.api_id:
        raise RuntimeError("API_ID environment variable is required")
    if not settings.bot.api_hash:
        raise RuntimeError("API_HASH environment variable is required")

    app = Client(
        "animedekho_bot",
        api_id=settings.bot.api_id,
        api_hash=settings.bot.api_hash,
        bot_token=settings.bot.token,
    )

    # Register startup/shutdown hooks
    app.on_start = _on_start
    app.on_stop = _on_stop

    # Register all handlers
    from bot.handlers import register_handlers
    register_handlers(app)

    return app
