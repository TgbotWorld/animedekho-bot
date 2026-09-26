"""
AnimeDekho Bot — Central Configuration.

You can configure this bot in TWO ways:
1. Direct Python Editing: Edit the variables directly in this config.py file.
2. Environment File (.env): Place your values in a .env file or VPS environment variables.
Both methods are fully supported! If a .env file exists, it will be loaded automatically.
"""

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Minimal fallback parser if python-dotenv is not installed
    if os.path.isfile(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _v = _line.split("=", 1)
                        _k = _k.strip()
                        _v = _v.strip().strip("'\"")
                        if _k and _k not in os.environ:
                            os.environ[_k] = _v
        except Exception:
            pass



class Config:
    # ── Telegram API Credentials ──────────────────────────────────────────
    # Get these from https://my.telegram.org
    API_ID = int(os.environ.get("API_ID", "0"))
    API_HASH = os.environ.get("API_HASH", "")

    # Main Bot Token from @BotFather
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

    # ── Bot Owner & Admins ────────────────────────────────────────────────
    # Telegram User ID of the primary owner
    OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

    # Additional Admin IDs separated by space, e.g. "12345678 87654321"
    ADMINS = [
        int(x.strip()) for x in os.environ.get("ADMINS", "").split() if x.strip().isdigit()
    ]
    if OWNER_ID and OWNER_ID not in ADMINS:
        ADMINS.append(OWNER_ID)

    # ── Telegram Channels ────────────────────────────────────────────────
    # Main public/private channel ID for series albums (e.g. -1001234567890)
    MAIN_CHANNEL = int(os.environ.get("MAIN_CHANNEL", "0"))

    # Log channel ID for admin notifications and error reporting
    LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0"))

    # Dedicated dump/storage channel ID (optional, 0 = disabled)
    DUMP_CHANNEL = int(os.environ.get("DUMP_CHANNEL", "0"))

    # Force Subscribe channel ID (defaults to MAIN_CHANNEL if unset)
    FSUB_CHANNEL = os.environ.get("FSUB_CHANNEL", None)
    if FSUB_CHANNEL and str(FSUB_CHANNEL).lstrip("-").isdigit():
        FSUB_CHANNEL = int(FSUB_CHANNEL)

    # ── Database (MongoDB) ────────────────────────────────────────────────
    # MongoDB connection URI (Atlas or local)
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    DATABASE_NAME = os.environ.get("DATABASE_NAME", "animedekho_bot")

    # ── UI & Message Customization ────────────────────────────────────────
    # Custom banner image URL or Telegram file_id for /start menu
    # Example: "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=1000"
    START_PIC = os.environ.get("START_PIC", "")

    # Custom welcome message for /start menu (HTML format supported)
    # Available placeholder: {mention}
    START_MSG = os.environ.get("START_MSG", "")

    # Custom banner image URL or Telegram file_id for Force Subscribe alert
    FSUB_PIC = os.environ.get("FSUB_PIC", "")

    # Custom text message for Force Subscribe alert (HTML format supported)
    FSUB_MSG = os.environ.get("FSUB_MSG", "")

    # Default Fallback Thumbnails (Issue #9)
    # Used automatically if AnimeDekho / AniList poster fetching or uploading fails
    DEFAULT_ANIME_THUMB = os.environ.get(
        "DEFAULT_ANIME_THUMB",
        "https://images.unsplash.com/photo-1578632767115-351597cf2477?w=1000",
    )
    DEFAULT_MOVIE_THUMB = os.environ.get(
        "DEFAULT_MOVIE_THUMB",
        "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=1000",
    )

    # UI Styles: "classic" or "modern"
    START_STYLE = os.environ.get("START_STYLE", "classic").lower()
    SCHED_STYLE = os.environ.get("SCHED_STYLE", "classic").lower()
    EP_STYLE = os.environ.get("EP_STYLE", "classic").lower()
    POST_STYLE = os.environ.get("POST_STYLE", "classic").lower()

    # ── Features & Security ───────────────────────────────────────────────
    # Auto Search: Automatically trigger search when typing anime name directly in chat
    # If "off", users must use /search <name> command (prevents chat clutter)
    AUTO_SEARCH = os.environ.get("AUTO_SEARCH", "on").lower() in ("on", "true", "1", "yes")

    # FSub Timer Link Mode: "on" (2-min expiring links) or "off" (standard links)
    FSUB_MOD = os.environ.get("FSUB_MOD", "on").lower() in ("on", "true", "1", "yes")

    # Auto-Delete Time in seconds for sent files/videos (0 = disabled, 600 = 10 min)
    AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "600"))

    # Auto Thumbnail: Automatically generate branded 1280x720 HD thumbnails with title & badges
    AUTO_THUMB = os.environ.get("AUTO_THUMB", "on").lower() in ("on", "true", "1", "yes")

    # Auto Schedule Channel Post: Automatically post/update schedule card at 12:00 AM IST
    AUTO_SCHEDULE_POST = os.environ.get("AUTO_SCHEDULE_POST", "off").lower() in ("on", "true", "1", "yes")

    # ── Autonomous AI Agent ───────────────────────────────────────────────
    AI_API_KEY = os.environ.get("AI_API_KEY", "")
    AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
    AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o")
    AI_ENABLED = os.environ.get("AI_ENABLED", "true").lower() in ("true", "1", "yes", "on")

    # ── Downloader & Streaming Servers ────────────────────────────────────
    PREFERRED_SERVERS = [
        "VidStream", "Omega", "VidSrc", "Vidmoly", "StreamWish", "FileMoon", "VidCloud", "Strmup", "HydraX"
    ]
    DEFAULT_QUALITIES = ["480p", "720p", "1080p", "4K"]

    # ── System & Logging ──────────────────────────────────────────────────
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


# Export Config directly
__all__ = ["Config"]
