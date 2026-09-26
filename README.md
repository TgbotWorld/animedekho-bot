━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<h2 align="center">
    ──「 ᴀɴɪᴍᴇ ᴅᴇᴋʜᴏ ʙᴏᴛ 」──
</h2>

<p align="center">
  <img src="https://images.unsplash.com/photo-1578632767115-351597cf2477?w=1000&auto=format&fit=crop" width="700" alt="AnimeDekho Bot Banner">
</p>

<p align="center">
  <b>High-performance Telegram bot to stream, browse, and download Hindi, Tamil & Telugu dubbed anime from AnimeDekho with MTProto 2GB uploads, AniList HD posters, multi-server failover, and interactive settings.</b>
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12%20|%203.14-blue?style=for-the-badge&logo=python" alt="Python"></a>
  <a href="https://github.com/TgbotWorld/animedekho-bot"><img src="https://img.shields.io/badge/WZGram-MTProto%202GB-orange?style=for-the-badge&logo=telegram" alt="WZGram"></a>
  <a href="https://github.com/TgbotWorld/animedekho-bot/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License"></a>
</p>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<h3 align="center">
    ─「 ᴅᴇᴘʟᴏʏᴍᴇɴᴛ ᴍᴇᴛʜᴏᴅs 」─
</h3>

<p align="center">
  <a href="https://dashboard.heroku.com/new?template=https://github.com/TgbotWorld/animedekho-bot"><img src="https://img.shields.io/badge/Deploy%20On%20Heroku-black?style=for-the-badge&logo=heroku" height="38"/></a>&nbsp;
  <a href="https://app.koyeb.com/deploy?type=git&repository=https://github.com/TgbotWorld/animedekho-bot&branch=main&name=animedekho-bot"><img src="https://img.shields.io/badge/Deploy%20On%20Koyeb-black?style=for-the-badge&logo=koyeb" height="38"/></a>&nbsp;
  <a href="https://render.com/deploy"><img src="https://img.shields.io/badge/Deploy%20On%20Render-black?style=for-the-badge&logo=render" height="38"/></a>
</p>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<details open>
<summary><b> - ғᴇᴀᴛᴜʀᴇs :</b></summary>

### ⚡ ᴋᴇʏ ғᴇᴀᴛᴜʀᴇs
- [x] **AniList Official HD Posters** — Primary authoritative source using AniList GraphQL API (`coverImage.extraLarge`), filtering out website banners and placeholders.
- [x] **Thumbnail Fallbacks** — Automatic fallback to `DEFAULT_ANIME_THUMB` (Series) and `DEFAULT_MOVIE_THUMB` (Movies) if poster fetching or uploading fails. Zero unhandled errors.
- [x] **Dual Configuration Support** — Configure via direct editing in `config.py` OR environment variables in `.env`.
- [x] **Configurable Auto-Search** — Toggle direct anime name typing search via `config.py`, `/settings`, or `/autosearch <on|off>`.
- [x] **VPS Temp File Cleanup** — Automatic background deletion of downloaded video files, chunks, and thumbnails after upload to save VPS storage.
- [x] **Multi-Server Streaming Failover** — AnimeDekho (Primary) ➔ AnimeDrive (Secondary) ➔ ToonFlix (Tertiary) with 4K-tier detection.
- [x] **2GB File Uploads** — Powered by WZGram/Pyrogram MTProto engine with accelerated cryptographic hashing.
- [x] **Interactive `/settings` Control Panel** — Visual dashboard with real-time toggle buttons for UI styles, auto-delete, auto-thumb, and AI.
- [x] **Interactive `/commands` Guide** — Clean categorized command navigator with user, owner, channel, style, and feature sections.
- [x] **1-Month Upcoming Schedule** — AnimeDubHindi release scraper with interactive pagination and optional 12:00 AM IST auto-post.
- [x] **Branded Auto-Thumbnail Generator** — Generates 1280x720 HD thumbnails with title, episode badges, and dub tags.
- [x] **Child Worker Bot Network** — Add unlimited child bots (`/addbot`) to balance download traffic and bypass Telegram rate limits.
- [x] **MTProto Userbot Login** — Interactive `/login` wizard with phone/OTP/2FA to auto-create private per-anime channels.
- [x] **Anti-Copyright 2-Min Timer Links** — Auto-expiring single-use invite links (`expire_date = now + 120s`).
- [x] **Media Auto-Delete System** — Automatically deletes delivered files from user chats after a configurable timer (`/dlt_time`).
- [x] **Autonomous AI Agent** — Natural language search assistant (`/ai`) supporting OpenAI, Gemini, and OpenRouter.

</details>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### ⚙️ ᴄᴏɴғɪɢᴜʀᴀᴛɪᴏɴ ᴠᴀʀɪᴀʙʟᴇs

You can configure the bot by editing `config.py` directly OR by creating a `.env` file:

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `API_ID` | **Yes** | — | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | **Yes** | — | Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | **Yes** | — | Main Bot Token from [@BotFather](https://t.me/BotFather) |
| `OWNER_ID` | **Yes** | — | Telegram User ID of the bot owner |
| `ADMINS` | No | `""` | Additional Admin User IDs separated by space |
| `MAIN_CHANNEL` | No | `0` | Main Channel ID for anime album posts (e.g. `-1001234567890`) |
| `LOG_CHANNEL` | No | `0` | Log Channel ID for error reports and admin notifications |
| `DUMP_CHANNEL` | No | `0` | Storage dump channel ID for file caching |
| `FSUB_CHANNEL` | No | `None` | Force Subscribe channel ID (defaults to `MAIN_CHANNEL`) |
| `MONGO_URI` | No | `mongodb://localhost:27017` | MongoDB connection URI (Atlas or local) |
| `DEFAULT_ANIME_THUMB` | No | *Unsplash Anime* | Default fallback image URL for anime series thumbnails |
| `DEFAULT_MOVIE_THUMB` | No | *Unsplash Cinema* | Default fallback image URL for movie thumbnails |
| `AUTO_SEARCH` | No | `on` | Toggle direct anime name search in chat (`on` / `off`) |
| `AUTO_THUMB` | No | `on` | Automatically generate branded 1280x720 HD thumbnails |
| `AUTO_SCHEDULE_POST` | No | `off` | Post daily schedule to `MAIN_CHANNEL` at 12:00 AM IST |
| `AUTO_DELETE_TIME` | No | `600` | File auto-delete delay in seconds (`0` to disable, `600` = 10m) |
| `FSUB_MOD` | No | `on` | Use 2-minute expiring invite links for Force Subscribe |
| `START_STYLE` | No | `classic` | `/start` menu layout style (`classic` or `modern`) |
| `START_PIC` | No | `""` | Custom banner photo URL or Telegram file_id for `/start` |
| `SCHED_STYLE` | No | `classic` | Schedule card style (`classic` or `modern`) |
| `EP_STYLE` | No | `classic` | Upload episode post style (`classic` or `modern`) |
| `POST_STYLE` | No | `classic` | Channel library card style (`classic` or `modern`) |
| `AI_API_KEY` | No | `""` | AI API Key for `/ai` assistant (OpenAI, Gemini, OpenRouter) |
| `AI_MODEL` | No | `gpt-4o` | AI Model name (e.g. `gpt-4o`, `gemini-2.0-flash`) |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 📖 ʙᴏᴛ ᴄᴏᴍᴍᴀɴᴅs

#### 👤 User Commands
- `/start` — Open main menu and browse recent releases
- `/search <name>` — Search for anime series and movies
- `/schedule` — View today's and upcoming anime release schedule
- `/commands` — Open interactive categorized commands guide
- `/help` — View help and instructions

#### 👑 Owner & Admin Commands
- `/settings` — Open interactive visual control panel (toggle features in real-time)
- `/autosearch <on|off>` — Toggle direct chat text search trigger
- `/automonitor <on|off>` — Toggle automated episode release scraper
- `/stats` — Real-time VPS CPU, RAM, and network statistics card
- `/health` — Full diagnostic check for Main Bot, Userbot, Child Bots & MongoDB
- `/users` — Total registered bot users and analytics
- `/broadcast <msg>` — Send global announcement broadcast
- `/setthumb` — Reply to photo to set custom upload thumbnail
- `/delthumb` — Remove custom upload thumbnail
- `/mapchannel <slug> <cid>` — Route specific anime uploads to dedicated channel
- `/addbot` — Register child worker bot token for download load-balancing
- `/login` — Interactive MTProto userbot login wizard
- `/ai <prompt>` — Ask autonomous AI assistant to find anime and streams

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 🚀 ǫᴜɪᴄᴋ ᴠᴘs sᴇᴛᴜᴘ

```bash
# 1. Clone repository
git clone https://github.com/TgbotWorld/animedekho-bot.git
cd animedekho-bot

# 2. Install dependencies & FFmpeg
sudo apt update && sudo apt install -y ffmpeg
pip install -r requirements.txt

# 3. Configure credentials
cp sample.env .env
nano .env   # (or edit config.py directly)

# 4. Start the bot
python3 main.py
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<p align="center">
  <b>Made with ❤️ for Anime Lovers • Powered by WZGram & AniList</b>
</p>
