# AnimeDekho Telegram Bot 🎌

Telegram bot to search, browse, and download anime from [AnimeDekho](https://animedekho.app/) — Hindi, Tamil & Telugu dubbed.

Built with **WZGram (High-Performance MTProto Fork with WarpCrypto)** for **2GB upload/download support**, multi-server fallbacks, child worker bots load balancing, userbot per-anime channel creation, autonomous AI agent, and real-time health monitoring.

---

> [!NOTE]
> **Graceful Fallback / Zero-Config Architecture**:
> All newly added advanced capabilities (**Userbot**, **Child Worker Bots**, **Per-Anime Dedicated Channels**, and **Autonomous AI Agent**) are **completely optional**.
> - If you do not log in to a userbot, the bot will upload directly to user chats and your main library channel as before.
> - If you do not register child bots, all downloads and deep links route through your main bot.
> - If no AI key is configured, standard search and keyboard navigation operate normally.
> 
> Everything works out-of-the-box in standard standalone mode if you only provide the core credentials!

---

## Features

### 🔍 Core Search & Browsing
- **Instant Search** — Send any anime name in chat to search.
- **Seasons & Episodes** — Complete pagination with interactive inline buttons.
- **Multi-Server Streaming** — Extracts real direct CDN video player streams from multiple providers.
- **Dual Formats** — Supports anime series (episodes grouped by season) and standalone movies.
- **Quality Selection** — 360p, 480p, 720p, 1080p, and enhanced 4K tiers.
- **Batch Downloads** — Download entire seasons sequentially with automatic server fallbacks.
- **Duplicate Prevention** — Downloaded files are indexed in MongoDB by `file_unique_id`; identical files are delivered in milliseconds from cache without re-downloading.

### 🌐 Multi-Source & Enhanced 4K Resolution
- **Multi-Source Fallback Cascade**:
  1. Primary: **AnimeDekho** (multi-server CDN streams)
  2. Secondary: **AnimeDrive** (automatic fallback for missing episodes)
  3. Tertiary: **ToonFlix** (tertiary fallback)
- **4K Tier Scoring** — Automatically identifies and downloads the highest available quality above 1080p (e.g. 1080p HQ x265, 1080p 10-bit, 2160p).

### 🎨 AniList Official HD Poster & Key Visuals
- **Authoritative AniList Integration** — Queries the **AniList GraphQL API** to retrieve official, high-resolution anime key visuals and cover art (`coverImage.extraLarge`).
- **Smart Title Normalization** — Automatically cleans dub tags (e.g. `(Hindi Dubbed)`, `[Multi Audio]`, `Dual Audio`), quality labels (`1080p`, `720p`), and season tags into progressive query candidates.
- **Junk & Banner Filtering** — Automatically detects and filters out generic website banners (e.g. `banner-112.webp`), header logos, and placeholders.
- **Graceful Fallback** — If AniList has no match or is unreachable, seamlessly falls back to valid scraped artwork without blocking downloads.
- **Channel Avatars & Album Cards** — The official AniList visual is automatically applied to dedicated per-anime channels, main channel album cards, and video upload thumbnails.

### 👷 Child Worker Bot Network (Load Balancing)
- **Multi-Bot Worker Fleet** — Connect unlimited child worker bots (`/addbot`) to distribute user downloads and avoid single-bot Telegram rate limits.
- **Quality-Tier Assignment** — Assign dedicated child bots to specific resolutions (e.g., Worker 1 for `1080p`, Worker 2 for `720p`, Worker 3 for `480p`).
- **Dynamic Deep Links** — Channel posters automatically route user download clicks to the assigned worker bots via round-robin balancing.
- **Instant Batch Refresh** — Use `/refreshalbums` to retroactively update all existing channel album posts with new worker bot links.

### 👤 Userbot & Dedicated Per-Anime Channels
- **MTProto Userbot Login**:
  - Interactive wizard (`/login`) with step-by-step phone number, OTP code, and 2FA cloud password handling.
  - Or direct login (`/login <string_session>`) using an existing WZGram/Pyrogram string session.
- **Per-Anime Dedicated Channels**:
  - Automatically creates a dedicated private Telegram channel per anime series.
  - Sets the anime poster as the channel's profile photo.
  - Promotes the Main Bot and all active Child Bots as administrators with full posting privileges.
  - Generates permanent invite links.
- **Channel Mapping & Routing**:
  - Automatically routes episode uploads into the dedicated anime channel.
  - Direct delivery to users in private chat via cached `file_id` (0 bandwidth overhead).
- **Poster Album Display Modes (`/albummode`)**:
  - `channel` *(default)*: Main channel poster album displays a prominent `[📢 Watch / Episodes Channel]` button linking directly to the series channel.
  - `both`: Displays the channel button at the top, followed by individual quality buttons.
  - `direct`: Traditional direct download buttons only.

### 🤖 Autonomous AI Agent
- **Natural Language Assistant** (`/ai <prompt>`): Ask the AI to find anime, inspect streams, check episodes, or trigger downloads autonomously.
- **Configurable Models (`/setai`)**: Supports OpenAI (`gpt-4o`, `gpt-4o-mini`), Google Gemini (`gemini-2.0-flash`), OpenRouter, and custom endpoints.
- **Long-Term Memory**: Autonomous memory tools (`remember_fact`, `recall_facts`) persist user preferences across sessions.
- **Channel Tools**: AI can check channel mappings and create dedicated anime channels.

### 🩺 System Health & Diagnostics
- **Network Health Dashboard (`/health`)**:
  - Tests connectivity and measures round-trip ping latency for the **Main Bot**, **Userbot**, and all **Child Worker Bots**.
  - Displays hardware metrics: CPU load %, RAM usage, Disk space, and MongoDB ping latency.
  - Reports process uptime (e.g. `2d 4h 12m`).
- **Download Error Tracker (`/errors`)**:
  - Automatically logs failed downloads to MongoDB with timestamps, series title, quality, source attempted, and error messages.
  - 24-hour error counters and recent failure inspection.
- **Environment Log Buffer (`/logs`)**:
  - In-memory ring buffer keeps the latest 150 log events.
  - Filter by error logs or export full logs as a `.txt` document file (`/logs export`).

### ⏳ 2-Minute Timer Links (Anti-Copyright Protection)
- **Zero Permanent Link Leakage** — Channel album cards no longer expose permanent invite links to scrapers or bad actors.
- **On-Demand Generation** — All channel buttons route to `https://t.me/{bot}?start=join_{slug}`, generating a fresh Telegram-enforced **2-minute auto-expiring link** (`expire_date = now + 120s`, `member_limit = 1`).
- **Telegram Server-Side Expiration** — Because expiration is handled natively by Telegram servers, links immediately invalidate even if the bot is stopped, killed, or restarted.
- **Auto-Purged Notice** — The bot deletes the generated invite message from the chat after 120 seconds.

### 🗑️ Persistent Auto-Delete System (`/dlt_time`)
- **Automatic Media Cleanup** — Videos and documents delivered to users in private chat are automatically deleted after a configurable timer (default: 10 minutes).
- **Restart & Crash Resilient** — Pending deletion jobs are persisted in MongoDB (`auto_delete_jobs`). When the bot restarts, past-due jobs are executed immediately and pending jobs resume without loss.
- **One-Click File Recovery** — Replaces deleted files with an elegant notification card containing a `[GET FILE AGAIN! ↗]` deep-link button and a `[CLOSE]` dismissal button.

### 🔒 Advanced Force Subscribe (FSub) & Timer Mode
- **Dual FSub Modes (`/fsub_mod`)**:
  - `ON`: Force-subscribe invite links are generated as **2-minute auto-expiring timer links** for enhanced privacy and strike prevention.
  - `OFF`: Uses standard permanent channel links.
- **Quick Controls** — `/fsub <channel_id_or_username>`, `/fsub off`, or toggle via interactive buttons.

### 📊 Real-Time VPS Stats & Worker Fleet Analytics
- **Live VPS Stats Card (`/stats`)** — Displays formatted ASCII/Unicode metrics including CPU %, RAM (used/total), Disk (used/free), real-time Download/Upload network speeds, and system uptime.
- **Network User Analytics (`/users`)** — Tracks and counts all users across the main bot and all child workers.
- **Broadcast System** — Broadcast text (`/broadcast`), photos (`/pbroadcast`), or files/videos (`/dbroadcast`) with real-time live progress edits.
- **Global User Moderation** — Ban (`/ban <id> [reason]`) and unban (`/uban <id>` or `/unban <id>`) users network-wide.

### 🔄 Automatic New Episode Monitoring (OFF by default)
- **Background Watcher** — Periodically checks provider streams for newly released episodes without any manual user intervention.
- **Auto-Pipeline** — Detect new episode → Select configured resolution → Download → Cache in Dump Channel (if set) → Upload to mapped anime channel → Update album poster.
- **Completely OFF by Default** — Disabled until the owner turns it on using `/automonitor on`.
- **Configurable Settings** — Adjust check interval (`/automonitor interval <minutes>`), target quality (`/automonitor quality <quality>`), and watchlist (`/automonitor add/del`).

### 📅 Anime Airing Schedule System
- **Real-Time Airing Timetable** — Built-in schedule viewer (`/schedule`) powered by the AniList AiringSchedule GraphQL engine with release countdowns and Indian Standard Time (IST) formatting.
- **Three Viewing Modes**:
  - 📌 **Today's Anime**: Releases airing today.
  - 📆 **Weekly Timetable**: Browse day-by-day (Monday through Sunday) with interactive inline day buttons.
  - 🔜 **Upcoming Anime**: Releases scheduled for the next 48 hours.
- **Rich Anime Cards** — High-resolution cover artwork, title (Romaji & English), airing episode number, exact countdown (e.g. `in 3h 15m`), IST time, genres, and community score.

### 📺 Advanced Multi-Channel Mapping & Language Routing
- **Multi-Channel Distribution** — Route the same anime to different channels based on language (e.g. Demon Slayer Hindi → Hindi Channel, Demon Slayer Tamil → Tamil Channel, Demon Slayer Multi → Multi Channel).
- **Flexible Mapping** — `/mapchannel <slug> <channel_id> [language]` to bind language-specific channels.
- **Fallback to Default** — If no language-specific route is configured, seamlessly uploads to the primary series channel as before.

### 💾 Dump / Storage Channel (OFF by default)
- **Centralized Media Cache** — Configure a master storage/dump channel (`/setdump <channel_id>`).
- **Zero Re-Downloading** — Downloaded video files are stored in the dump channel first. Target mapped channels and private user chats are delivered instantly via cached Telegram `file_id` without downloading the same file multiple times.
- **OFF by Default** — Disabled unless explicitly set with `/setdump`.

### 🖼️ Custom Thumbnail System (OFF by default)
- **Multi-Level Thumbnail Precedence**:
  1. Per-anime language thumbnail: `/setthumb <series_slug> <language>` (reply to image)
  2. Per-anime thumbnail: `/setthumb <series_slug>` (reply to image)
  3. Global thumbnail: `/setthumb` (reply to image)
  4. Automatic fallback: Official AniList HD key visual / scraped poster (existing default behavior).
- **Inspection & Cleanup** — `/viewthumb [slug] [language]` to preview and `/delthumb` to remove.

### ⚙️ Dual Configuration System (`config.py` & `.env`)
- **Direct Python Editing (`config.py`)** — Configure credentials, channel IDs, styles, and defaults directly in `config.py` (Codeflix FileStore style).
- **Environment Variable Fallback (`.env`)** — If a `.env` file exists or variables are set in your VPS environment/Docker/Heroku, they are automatically loaded seamlessly without conflict.

### 🛡️ Video Integrity Validation & 480p Optimization
- **Pre-Upload Integrity Check** — Uses `ffprobe` to validate stream properties, codecs, and durations before sending videos.
- **Corrupt File Discard & Auto-Retry** — If a download produces a broken container, corrupt stub, or 0-byte file, it is automatically discarded and deleted, and the bot cascades to an alternative server mirror.
- **M3U8 Variant Matching** — When downloading 480p/720p streams, the bot resolves the exact sub-playlist URL, preventing bloated 1080p stream downloads for 480p selections.

### 🖼️ Automatic 1280x720 HD Thumbnail Generator (Auto Thumb)
- **Zero-Manual Effort** — Automatically detects the anime/movie title, grabs official HD artwork via AniList, and composites a branded 16:9 HD thumbnail (1280x720).
- **Rich Elements** — Features a dark blurred background, crisp foreground poster with rounded borders, bold anime typography, gold Season/Episode badges, audio/quality tags, and watermark.

### 🎛️ Interactive `/commands` Guide & `/settings` Control Panel
- **`/commands`** — Categorized interactive menu grouping User, Admin, Owner, Channel, and Automation commands.
- **`/settings`** — Visual interactive dashboard with real-time inline toggle buttons (FSub Timer, File Auto-Delete, UI Styles, Auto Thumbnails, 12 AM Schedule Post, AI Agent).

### ⏰ Daily 12:00 AM IST Schedule Auto-Publisher
- **Automated Midnight Posting** — Automatically publishes or updates the daily anime airing schedule in the main channel every midnight at 12:00 AM IST.
- **Extended Schedule Window** — Supports 1-month upcoming schedules and real-time Hindi dub release schedules from `animedubhindi.link/schedule.php`.


## Bot Commands

### 👥 User Commands
| Command | Description |
| :--- | :--- |
| Any text | Search for anime series or movies |
| `/start` | Open the main menu |
| `/search <query>` | Search anime by title |
| `/schedule` | View Today's, Weekly, and Upcoming anime release schedule |
| `/commands` | Interactive categorized commands navigator |
| `/help` | Show user help message |
| `/tutorial` | View complete bot guide and tutorials |

### 👑 Owner & Admin Commands

#### Settings & Commands Navigator (Issue #8)
| Command | Description |
| :--- | :--- |
| `/settings` | Open visual interactive control panel with live toggle buttons |
| `/commands` | Open categorized commands guide (User, Owner, Channel, Styles, AI) |

#### Monitoring, Storage Dump & UI Customization (Issue #4)
| Command | Description |
| :--- | :--- |
| `/automonitor <on\|off>` | Toggle automatic episode monitoring ON or OFF (OFF by default) |
| `/automonitor status` | View monitoring status, interval, quality, and watchlist count |
| `/automonitor interval <m>` | Set check interval in minutes (default 30m, min 5m) |
| `/automonitor quality <q>` | Set target download quality for auto-monitoring (default 720p) |
| `/automonitor add <slug> [q]` | Add anime series to auto-monitor watchlist |
| `/automonitor del <slug>` | Remove anime series from auto-monitor watchlist |
| `/automonitor list` | List all watched anime series |
| `/automonitor check` | Run an immediate check cycle manually |
| `/setdump <channel_id>` | Set dump/storage channel (`/setdump off` to disable) |
| `/setthumb [slug] [lang]` | Set global, anime-specific, or language-specific custom thumbnail (reply to photo) |
| `/delthumb [slug] [lang]` | Remove custom thumbnail |
| `/viewthumb [slug] [lang]` | View active custom thumbnail |
| `/poststyle <classic\|modern>` | Toggle channel poster caption style (classic default / modern card) |
| `/startstyle <classic\|modern>` | Toggle `/start` menu layout (classic default / modern anime card with About/Help) |
| `/startpic <url\|reset>` | Configure custom banner photo for `/start` modern menu (or reply to photo) |
| `/schedstyle <classic\|modern>` | Toggle `/schedule` layout (classic default / modern double-line box cards) |
| `/epstyle <classic\|modern>` | Toggle episode upload post style (classic default / modern card with quality buttons) |

#### VPS Stats & Analytics
| Command | Description |
| :--- | :--- |
| `/stats` | Real-time VPS performance card (CPU, RAM, DISK, DL/UL speed, Uptime) |
| `/users` | Bot network user analytics (registered users, whitelist, banned) |
| `/ban <id> [reason]` | Ban a user network-wide across main and child bots |
| `/uban <id>` (or `/unban`) | Unban a user from the bot network |
| `/broadcast <text>` | Broadcast text announcement to all bot users with live progress |
| `/pbroadcast` | Broadcast photo to all users (reply to a photo) |
| `/dbroadcast` | Broadcast document/video to all users (reply to a file) |

#### Timer Links & Auto-Delete
| Command | Description |
| :--- | :--- |
| `/dlt_time [duration]` | Configure auto-delete timer (e.g. `10m`, `600`, `1h`, `off`) with quick presets |
| `/fsub <channel>` | Set Force Subscribe channel ID or `@username` (`/fsub off` to disable) |
| `/fsub_mod <on\|off>` | Toggle 2-minute expiring timer links for Force Subscribe |

#### System Health & Diagnostics
| Command | Description |
| :--- | :--- |
| `/health` (or `/status`) | Interactive health dashboard with bot pings, uptime, RAM/CPU/Disk metrics, and error summary |
| `/health errors` (or `/errors`) | View recent download failures with exact error reasons |
| `/health logs` (or `/logs`) | View recent environment log events in Telegram |
| `/logs export` | Export the latest 150 log records as a `.txt` file document |
| `/logs errors` | View only error-level environment logs |
| `/clearerrors` | Clear all logged download error history from the database |

#### Child Worker Bots (Load Balancing)
| Command | Description |
| :--- | :--- |
| `/addbot <token> [quality]` | Add a child worker bot with optional quality tier (`1080p`, `720p`, `480p`, `all`) |
| `/delbot <username or id>` | Remove a child worker bot |
| `/bots` | List all child worker bots, assigned qualities, and files served |
| `/setbotquality <bot> <quality>` | Update a child bot's assigned quality tier |
| `/refreshalbums` | Update all main channel album posts with new child bot links |

#### Userbot & Per-Anime Channels
| Command | Description |
| :--- | :--- |
| `/login` | Start interactive phone login wizard or `/login <string_session>` |
| `/logout` | Disconnect userbot and remove session from database |
| `/userbot` | View userbot session status and channel mapping settings |
| `/cancel` | Cancel an ongoing interactive login wizard |
| `/autochannel <on\|off>` | Toggle automatic channel creation during series downloads |
| `/albummode <channel\|both\|direct>` | Set poster album display mode in main channel |
| `/createchannel <slug or title>` | Manually create and map a dedicated channel for an anime |
| `/mapchannel <slug> <channel_id> [link]` | Manually map an existing Telegram channel to an anime slug |
| `/unmapchannel <slug>` | Remove channel mapping for an anime slug |
| `/channels` | List all mapped anime series channels with invite links |

#### Autonomous AI Agent
| Command | Description |
| :--- | :--- |
| `/ai <prompt>` | Query the autonomous AI agent |
| `/setai` | View or configure AI provider, API key, model, and system persona |

#### Access & User Management
| Command | Description |
| :--- | :--- |
| `/adduser <id>` | Approve a user to access the bot |
| `/removeuser <id>` | Revoke user access |
| `/approvedusers` | List approved whitelist user IDs |
| `/setchannellink <url>` | Set channel invite link fallback |
| `/delete` | Interactive menu to delete files or entire series from library |

---

## Environment Variables

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `BOT_TOKEN` | ✅ | — | Telegram Bot API token from [@BotFather](https://t.me/BotFather) |
| `API_ID` | ✅ | — | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | ✅ | — | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `OWNER_ID` | ✅ | — | Your numeric Telegram user ID |
| `MONGO_URI` | ✅ | — | MongoDB connection string (local or MongoDB Atlas) |
| `MAIN_CHANNEL` | ❌ | `0` | Telegram Channel ID for album library posts (e.g. `-1001234567890`) |
| `LOG_CHANNEL` | ❌ | `0` | Telegram Channel ID for live event and audit logs |
| `LOG_LEVEL` | ❌ | `INFO` | Console logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `AI_API_KEY` | ❌ | `""` | Optional API key for OpenAI, Gemini, or OpenRouter |
| `AI_MODEL` | ❌ | `gpt-4o-mini` | Optional default AI model |

---

## Project Structure

```
├── main.py                     # Application entrypoint
├── setup.sh                    # Automated VPS/Docker installer
├── config/settings.py          # Pydantic environment configuration
├── api/
│   ├── models.py               # Anime, Series, Episode & Server data models
│   ├── parser.py               # HTML parsers for AnimeDekho
│   └── client.py               # Async API client with session management
├── extractors/
│   ├── resolver.py             # CDN player extractors & m3u8 parser
│   ├── animedrive.py           # AnimeDrive multi-server stream extractor
│   ├── toonflix.py             # ToonFlix stream extractor
│   └── shortener.py            # Link shortener bypass (gplinks, vshort, cuty)
├── bot/
│   ├── app.py                  # WZGram app factory & lifecycle hooks
│   ├── telegram.py             # Unified Telegram MTProto client provider (WZGram / Pyrogram)
│   ├── auth.py                 # User authorization & owner guard
│   ├── child_bots.py           # Child worker bots manager & load balancer
│   ├── userbot.py              # MTProto userbot session & channel creator
│   ├── health.py               # System diagnostics, uptime & log buffer
│   ├── database.py             # MongoDB async driver (motor)
│   ├── downloader.py           # Video download engine & MTProto uploader
│   ├── forcesub.py             # Force-subscribe verification
│   ├── keyboards.py            # Inline keyboard builders
│   ├── library.py              # Main channel poster album manager
│   ├── logger.py               # Telegram log channel dispatcher
│   ├── ai/                     # Autonomous AI Agent engine & function tools
│   │   ├── agent.py            # ReAct autonomous loop
│   │   ├── config.py           # AI model & persona configuration
│   │   └── tools.py            # Function calling tools
│   └── handlers/               # Command, callback, and message handlers
│       ├── admin.py            # Owner commands & health callbacks
│       ├── admin_ai.py         # AI administration commands
│       ├── callbacks.py        # Inline button router & download handlers
│       ├── commands.py         # Basic user commands (/start, /help)
│       └── messages.py         # Search query & login wizard input handler
└── utils/
    ├── cache.py                # In-memory TTL cache
    ├── http.py                 # Async HTTP client with connection pooling
    └── helpers.py              # Formatting & slug helpers
```

---

## Deployment Guides

### Option 1: Docker / VPS (Recommended)

Run on any Ubuntu/Debian VPS (AWS EC2, DigitalOcean, Hetzner, etc.):

```bash
# Clone the repository
git clone https://github.com/jrodr254/animedekho-bot.git
cd animedekho-bot

# Run the automated setup script
bash setup.sh
```

The script will:
1. Install Docker Engine and Docker Compose.
2. Prompt for environment variables (`BOT_TOKEN`, `API_ID`, `API_HASH`, `OWNER_ID`, etc.) and write `.env`.
3. Build the container with Python 3.11, ffmpeg, and MongoDB.
4. Launch the bot daemon.

**Manage containers:**
```bash
docker compose logs -f          # Live logs
docker compose restart bot      # Restart bot
docker compose down             # Stop containers
docker compose up -d --build    # Rebuild & start
```

---

### Option 2: Railway Deployment

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

1. Fork this repository.
2. Create a project on [Railway](https://railway.app) and select your fork.
3. Add a **MongoDB** service (one-click in Railway).
4. Set required environment variables (`BOT_TOKEN`, `API_ID`, `API_HASH`, `OWNER_ID`, `MONGO_URI`).
5. Deploy!

---

### Option 3: Local Development

```bash
# 1. Clone repository
git clone https://github.com/Tgbotworld/animedekho-bot.git
cd animedekho-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install ffmpeg
# Ubuntu/Debian:
sudo apt install ffmpeg
# macOS:
brew install ffmpeg

# 4. Set environment variables
export BOT_TOKEN="your_bot_token"
export API_ID="123456"
export API_HASH="your_api_hash"
export OWNER_ID="your_telegram_id"
export MONGO_URI="mongodb://localhost:27017"

# 5. Run the bot
python main.py
```

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
