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

    # Optional Ongoing Channel for latest episode updates & notifications (0 = disabled)
    ONGOING_CHANNEL = int(os.environ.get("ONGOING_CHANNEL", "0"))

    # Force Subscribe channel ID (defaults to MAIN_CHANNEL if unset)
    FSUB_CHANNEL = os.environ.get("FSUB_CHANNEL", None)
    if FSUB_CHANNEL and str(FSUB_CHANNEL).lstrip("-").isdigit():
        FSUB_CHANNEL = int(FSUB_CHANNEL)

    # V3 #15: quality-button worker routing fallback.
    # True (default) = when no active worker exists for a quality, fall back
    # to the main bot explicitly (logged, never silent). False = keep the
    # worker-only deep-link (delivery may fail until a worker is added).
    QUALITY_BUTTON_FALLBACK_TO_MAIN = os.environ.get("QUALITY_BUTTON_FALLBACK_TO_MAIN", "1") == "1"

    # Main / Network Channel Link (used for DOWNLOAD NETWORK button)
    NETWORK_CHANNEL_LINK = os.environ.get("NETWORK_CHANNEL_LINK", "https://t.me/animedekho")

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
    START_STYLE = os.environ.get("START_STYLE", "modern").lower()
    SCHED_STYLE = os.environ.get("SCHED_STYLE", "modern").lower()
    EP_STYLE = os.environ.get("EP_STYLE", "modern").lower()
    POST_STYLE = os.environ.get("POST_STYLE", "modern").lower()

    # ── Telegram Premium Custom Emojis (Issue #10) ────────────────────────
    # Enable rendering of Telegram Premium Custom Emojis (<emoji id="...">)
    # Automatically falls back to standard Unicode emojis if false/unsupported
    ENABLE_CUSTOM_EMOJI = os.environ.get("ENABLE_CUSTOM_EMOJI", "false").lower() in ("true", "1", "yes", "on")

    # Configurable Custom Emoji IDs (customize using @PremiumemojiID_bot)
    CUSTOM_EMOJIS = {
        "star": os.environ.get("EMOJI_STAR", "5368324170671202286"),
        "rating": os.environ.get("EMOJI_RATING", "5368324170671202286"),
        "movie": os.environ.get("EMOJI_MOVIE", "5443037926569253457"),
        "audio": os.environ.get("EMOJI_AUDIO", "5454157843477544062"),
        "quality": os.environ.get("EMOJI_QUALITY", "5427009714745328964"),
        "genres": os.environ.get("EMOJI_GENRES", "5472164874889714493"),
        "channel": os.environ.get("EMOJI_CHANNEL", "5465223707248387434"),
        "arrow": os.environ.get("EMOJI_ARROW", "5465223707248387434"),
        "check": os.environ.get("EMOJI_CHECK", "5445284980972591637"),
        "fire": os.environ.get("EMOJI_FIRE", "5467657928606459048"),
        "download": os.environ.get("EMOJI_DOWNLOAD", "5445284980972591637"),
        "upload": os.environ.get("EMOJI_UPLOAD", "5445284980972591637"),
    }

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

    # Thumbnail template (issue #33): every legacy name aliases to the single
    # streaming-card style; "random" is accepted for backwards compatibility.
    # Options: "streaming", "random", or any legacy name ("modern", "cinematic",
    # "movie_gold", "neon_cyber", "minimal") which now renders "streaming".
    THUMB_TEMPLATE = os.environ.get("THUMB_TEMPLATE", "streaming").lower()

    # Random Thumbnail Mode: When True, chooses a different random template on every upload
    RANDOM_THUMB_TEMPLATE = os.environ.get("RANDOM_THUMB_TEMPLATE", "off").lower() in ("on", "true", "1", "yes")

    # Thumbnail branding (issue #33): channel handle + PNG logo stamped on the
    # lockup. Overridden at runtime by /thumbuser and /thumblogo.
    THUMB_BRAND_USERNAME = os.environ.get("THUMB_BRAND_USERNAME", "")
    THUMB_BRAND_LOGO = os.environ.get("THUMB_BRAND_LOGO", "")

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
