"""System Health, Bot Diagnostics, Environment Uptime, and Error Logging."""

from __future__ import annotations
import asyncio
import logging
import os
import platform
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
from bot.telegram import Client, enums
from bot.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import settings


def _get_db():
    """V3 #13: dynamic db access — never cache the module-level reference.

    ``bot.database.db`` is assigned at startup; a stale ``from bot.database
    import db`` binding stays None forever. Always resolve via module attr.
    """
    try:
        import bot.database as _dbmod
        return _dbmod.db
    except Exception:
        return None

log = logging.getLogger(__name__)

# Boot timestamp
_boot_time: float = time.time()


def set_boot_time():
    """Set the boot start timestamp."""
    global _boot_time
    _boot_time = time.time()


def get_uptime() -> tuple[float, str]:
    """Return uptime in seconds and human-readable string."""
    seconds = time.time() - _boot_time
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return seconds, " ".join(parts)


class RingBufferLogHandler(logging.Handler):
    """Retains the last N log records in memory for health inspections and debugging."""

    def __init__(self, capacity: int = 150):
        super().__init__()
        self.capacity = capacity
        self.buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S"),
                "created": record.created,
                "level": record.levelname,
                "name": record.name.split(".")[-1],
                "message": msg,
            }
            with self._lock:
                self.buffer.append(entry)
                if len(self.buffer) > self.capacity:
                    self.buffer.pop(0)
        except Exception:
            self.handleError(record)

    def get_logs(self, limit: int = 30, level: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            logs = list(self.buffer)
        if level:
            lvl = level.upper()
            logs = [l for l in logs if l["level"] == lvl]
        return logs[-limit:]

    def get_formatted_text(self, limit: int = 30, level: str | None = None) -> str:
        logs = self.get_logs(limit=limit, level=level)
        if not logs:
            return "No logs available."
        lines = []
        for l in logs:
            lines.append(f"[{l['timestamp']}] [{l['level'][:4]}] [{l['name'][:12]}] {l['message']}")
        return "\n".join(lines)


# Global log buffer handler
ring_buffer_handler = RingBufferLogHandler(capacity=150)


def setup_health_logging():
    """Attach the ring buffer handler to the root logger."""
    formatter = logging.Formatter("%(message)s")
    ring_buffer_handler.setFormatter(formatter)
    logging.getLogger().addHandler(ring_buffer_handler)
    log.info("System health logging buffer initialized (capacity: 150)")


async def check_main_bot(client: Client) -> dict[str, Any]:
    """Check connectivity and latency of the Main Bot."""
    res = {
        "is_connected": False,
        "ping_ms": None,
        "username": None,
        "bot_id": None,
        "first_name": None,
        "error": None,
    }
    if not client:
        res["error"] = "Client instance is None"
        return res
    if not client.is_connected:
        res["error"] = "Client is disconnected"
        return res

    try:
        start_t = time.perf_counter()
        me = await client.get_me()
        latency = (time.perf_counter() - start_t) * 1000
        res["is_connected"] = True
        res["ping_ms"] = round(latency, 1)
        res["username"] = me.username
        res["bot_id"] = me.id
        res["first_name"] = me.first_name
    except Exception as e:
        res["error"] = str(e)[:100]

    return res


async def check_all_bots(main_client: Client) -> dict[str, Any]:
    """Check health and ping of Main Bot, Userbot, and all Child Worker Bots."""
    # 1. Main Bot
    main_res = await check_main_bot(main_client)

    # 2. Userbot
    from bot.userbot import userbot_manager
    userbot_res = None
    if userbot_manager:
        userbot_res = await userbot_manager.check_health()
    else:
        userbot_res = {"is_active": False, "is_connected": False, "error": "Not initialized"}

    # 3. Child Bots
    from bot.child_bots import child_bot_manager
    child_bots_res = []
    if child_bot_manager:
        child_bots_res = await child_bot_manager.check_bots_health()

    return {
        "main_bot": main_res,
        "userbot": userbot_res,
        "child_bots": child_bots_res,
    }


def get_system_stats() -> dict[str, Any]:
    """Collect OS, CPU, RAM, Disk and process resource metrics."""
    try:
        cpu_pct = psutil.cpu_percent(interval=0.05)
        vm = psutil.virtual_memory()
        ram_used_gb = round(vm.used / (1024 ** 3), 2)
        ram_total_gb = round(vm.total / (1024 ** 3), 2)
        ram_pct = vm.percent

        du = psutil.disk_usage("/")
        disk_used_gb = round(du.used / (1024 ** 3), 2)
        disk_total_gb = round(du.total / (1024 ** 3), 2)
        disk_pct = du.percent

        proc = psutil.Process()
        proc_mem_mb = round(proc.memory_info().rss / (1024 ** 2), 1)

        from bot.telegram import FRAMEWORK_NAME, FRAMEWORK_VERSION

        return {
            "cpu_pct": cpu_pct,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "ram_pct": ram_pct,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "disk_pct": disk_pct,
            "proc_mem_mb": proc_mem_mb,
            "python_ver": platform.python_version(),
            "tg_framework": FRAMEWORK_NAME,
            "tg_framework_ver": FRAMEWORK_VERSION,
            "pyrogram_ver": FRAMEWORK_VERSION,
            "os_info": f"{platform.system()} {platform.release()}",
        }
    except Exception as e:
        log.warning("Failed to collect system stats: %s", e)
        return {
            "cpu_pct": 0,
            "ram_used_gb": 0,
            "ram_total_gb": 0,
            "ram_pct": 0,
            "disk_used_gb": 0,
            "disk_total_gb": 0,
            "disk_pct": 0,
            "proc_mem_mb": 0,
            "python_ver": sys.version.split()[0],
            "tg_framework": "WZGram",
            "tg_framework_ver": "3.1.2",
            "pyrogram_ver": "unknown",
            "os_info": "Linux",
        }


async def get_database_health() -> dict[str, Any]:
    """Check MongoDB latency and collection counts."""
    res = {
        "is_connected": False,
        "ping_ms": None,
        "files_count": 0,
        "series_count": 0,
        "users_count": 0,
        "child_bots_count": 0,
        "channels_count": 0,
        "errors_24h": 0,
        "error": None,
    }
    db = _get_db()  # V3 #13: dynamic access, no stale None
    if not db:
        res["error"] = "Database instance is None"
        return res

    try:
        ping = await db.ping_database()
        res["is_connected"] = True
        res["ping_ms"] = ping
        res["files_count"] = await db.files.count_documents({})
        res["series_count"] = await db.library.count_documents({"type": "album"})
        res["users_count"] = await db.users.count_documents({})
        res["child_bots_count"] = await db.child_bots.count_documents({})
        res["channels_count"] = await db.channel_mappings.count_documents({})
        res["errors_24h"] = await db.count_download_errors(hours=24)
    except Exception as e:
        res["error"] = str(e)[:100]

    return res


async def format_health_dashboard(main_client: Client) -> tuple[str, InlineKeyboardMarkup]:
    """Build the complete HTML status report and interactive keyboard."""
    _, uptime_str = get_uptime()
    started_iso = datetime.fromtimestamp(_boot_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Run health checks concurrently
    bots_task = asyncio.create_task(check_all_bots(main_client))
    db_task = asyncio.create_task(get_database_health())
    system_stats = get_system_stats()

    bots_data, db_data = await asyncio.gather(bots_task, db_task)

    # 1. Main Bot formatting
    mb = bots_data["main_bot"]
    if mb["is_connected"]:
        main_bot_line = f"• 👑 <b>Main Bot:</b> 🟢 <b>Online</b> (ping: <code>{mb['ping_ms']}ms</code>)\n  └ @{mb['username']} (ID: <code>{mb['bot_id']}</code>)"
    else:
        main_bot_line = f"• 👑 <b>Main Bot:</b> 🔴 <b>Error:</b> {mb.get('error', 'Disconnected')}"

    # 2. Userbot formatting
    ub = bots_data["userbot"]
    if ub and ub.get("is_connected"):
        userbot_line = f"• 👤 <b>Userbot:</b> 🟢 <b>Online</b> (ping: <code>{ub['ping_ms']}ms</code>)\n  └ Account: {ub.get('first_name')} (@{ub.get('username') or 'None'}, ID: <code>{ub.get('user_id')}</code>)"
    elif ub and ub.get("is_active"):
        userbot_line = f"• 👤 <b>Userbot:</b> ⚠️ <b>Active but disconnected:</b> {ub.get('error')}"
    else:
        userbot_line = "• 👤 <b>Userbot:</b> ⚪ <i>Not logged in (use /login)</i>"

    # 3. Child Bots formatting
    cb_list = bots_data["child_bots"]
    if not cb_list:
        child_bots_lines = "• 👷 <b>Child Worker Bots:</b> <i>None configured (use /addbot)</i>"
    else:
        cb_entries = []
        for c in cb_list:
            icon = "🟢" if c["is_connected"] else "🔴"
            ping_str = f"ping: {c['ping_ms']}ms" if c["ping_ms"] else c.get("error", "offline")
            cb_entries.append(
                f"  ├ @{c['username']} [{c['quality'].upper()}]: {icon} (<code>{ping_str}</code>, served: <code>{c['files_served']}</code>)"
            )
        # Fix last branch symbol
        if cb_entries:
            cb_entries[-1] = cb_entries[-1].replace("  ├", "  └")
        child_bots_lines = f"• 👷 <b>Child Worker Bots ({len(cb_list)} configured):</b>\n" + "\n".join(cb_entries)

    # 4. Environment & Database stats
    db_status = "🟢 <b>Connected</b>" if db_data["is_connected"] else f"🔴 <b>Error:</b> {db_data.get('error')}"
    db_ping = f" (ping: <code>{db_data['ping_ms']}ms</code>)" if db_data["ping_ms"] else ""

    # 5. Download errors
    errors_count_24h = db_data["errors_24h"]
    errors_icon = "🟢" if errors_count_24h == 0 else "⚠️"
    errors_summary = f"{errors_icon} <b>{errors_count_24h} failed in last 24h</b>"

    # Fetch last 3 errors for quick glance
    recent_errors = []
    _dbg = _get_db()  # V3 #13: dynamic access
    if _dbg:
        recent_errors = await _dbg.get_recent_download_errors(limit=3)

    recent_err_text = ""
    if recent_errors:
        err_lines = []
        for e in recent_errors:
            t = e.get("timestamp", "")
            time_part = t[11:19] if len(t) >= 19 else t
            title = e.get("title", "Unknown")[:30]
            q = f" [{e.get('quality')}]" if e.get("quality") else ""
            err_msg = e.get("error", "")[:70]
            err_lines.append(f"  • <code>{time_part}</code> - <b>{title}{q}</b>\n    └ <i>{err_msg}</i>")
        recent_err_text = "\n<b>Recent Failures:</b>\n" + "\n".join(err_lines) + "\n"
    else:
        recent_err_text = "\n<i>No recent download failures recorded.</i>\n"

    # 6. Recent log entries
    recent_logs = ring_buffer_handler.get_logs(limit=4)
    if recent_logs:
        log_lines = []
        for l in recent_logs:
            lvl_color = "🔴" if l["level"] == "ERROR" else ("⚠️" if l["level"] == "WARNING" else "ℹ️")
            msg_snippet = l["message"][:75].replace("<", "&lt;").replace(">", "&gt;")
            log_lines.append(f"  • {lvl_color} <code>{l['timestamp']}</code> [{l['name']}] {msg_snippet}")
        recent_logs_text = "\n".join(log_lines)
    else:
        recent_logs_text = "  • <i>No environment logs buffered yet.</i>"

    caption = (
        f"🩺 <b>System Health & Bot Diagnostics</b>\n"
        f"⟐━━━━━━━━━━━━━━━━━⟐\n\n"
        f"🤖 <b>BOT NETWORK:</b>\n"
        f"{main_bot_line}\n"
        f"{userbot_line}\n"
        f"{child_bots_lines}\n\n"
        f"🖥️ <b>ENVIRONMENT & RESOURCES:</b>\n"
        f"• ⏱️ <b>Uptime:</b> <code>{uptime_str}</code> (since {started_iso})\n"
        f"• 🧠 <b>RAM:</b> <code>{system_stats['ram_used_gb']} GB / {system_stats['ram_total_gb']} GB</code> ({system_stats['ram_pct']}% | Bot: {system_stats['proc_mem_mb']}MB)\n"
        f"• ⚡ <b>CPU Load:</b> <code>{system_stats['cpu_pct']}%</code>\n"
        f"• 💾 <b>Disk:</b> <code>{system_stats['disk_used_gb']} GB / {system_stats['disk_total_gb']} GB</code> ({system_stats['disk_pct']}%)\n"
        f"• 🐍 <b>Runtime:</b> Python <code>{system_stats['python_ver']}</code> | {system_stats.get('tg_framework', 'WZGram')} <code>{system_stats.get('tg_framework_ver', system_stats.get('pyrogram_ver', '3.1.2'))}</code>\n"
        f"• 🍃 <b>MongoDB:</b> {db_status}{db_ping}\n"
        f"  └ Files: <code>{db_data['files_count']}</code> | Series: <code>{db_data['series_count']}</code> | Users: <code>{db_data['users_count']}</code> | Channels: <code>{db_data['channels_count']}</code>\n\n"
        f"⚠️ <b>DOWNLOAD FAILURES:</b> {errors_summary}\n"
        f"{recent_err_text}\n"
        f"📜 <b>RECENT ENVIRONMENT LOGS:</b>\n"
        f"{recent_logs_text}\n\n"
        f"⟐━━━━━━━━━━━━━━━━━⟐"
    )

    buttons = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="health:refresh"),
            InlineKeyboardButton("⚠️ Download Errors", callback_data="health:errors"),
        ],
        [
            InlineKeyboardButton("📜 View Logs", callback_data="health:logs"),
            InlineKeyboardButton("🧹 Clear Errors", callback_data="health:clear_errors"),
        ],
    ]
    return caption, InlineKeyboardMarkup(buttons)


async def format_download_errors_view(limit: int = 10) -> tuple[str, InlineKeyboardMarkup]:
    """Build detailed view of logged download failures."""
    db = _get_db()  # V3 #13: was referenced with no binding -> NameError on /errors
    if not db:
        return "⚠️ Database not initialized.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="health:back")]])

    errors = await db.get_recent_download_errors(limit=limit)
    total_24h = await db.count_download_errors(hours=24)

    if not errors:
        text = (
            "🎉 <b>No Download Errors Recorded!</b>\n\n"
            "All download pipelines and upstream resolvers are operating smoothly.\n"
            f"Errors in last 24h: <code>0</code>"
        )
    else:
        lines = []
        for i, e in enumerate(errors, 1):
            ts = e.get("timestamp", "")
            title = e.get("title", "Unknown Series")
            q = e.get("quality", "N/A")
            src = e.get("source", "N/A")
            err = e.get("error", "Unknown error")
            lines.append(
                f"<b>{i}. {title}</b> [{q}]\n"
                f"⏰ <code>{ts}</code> | Source: <code>{src}</code>\n"
                f"💔 <code>{err}</code>"
            )
        text = (
            f"⚠️ <b>Recent Download Failures ({len(errors)} shown | {total_24h} in 24h):</b>\n\n"
            + "\n\n".join(lines)
        )

    buttons = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="health:errors"),
            InlineKeyboardButton("🧹 Clear Error History", callback_data="health:clear_errors"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Health Dashboard", callback_data="health:back"),
        ],
    ]
    return text, InlineKeyboardMarkup(buttons)


def format_logs_view(limit: int = 25, level: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Build formatted text view of recent environment logs."""
    lvl_label = f"[{level.upper()}]" if level else "[ALL]"
    logs_text = ring_buffer_handler.get_formatted_text(limit=limit, level=level)

    # Escape HTML
    import html as htmlmod
    escaped = htmlmod.escape(logs_text)
    if len(escaped) > 3500:
        escaped = escaped[-3500:]

    text = (
        f"📜 <b>Environment Logs {lvl_label} (Latest {limit}):</b>\n\n"
        f"<pre>{escaped}</pre>"
    )

    buttons = [
        [
            InlineKeyboardButton("🔴 Errors Only", callback_data="health:logs_errors"),
            InlineKeyboardButton("📜 All Events", callback_data="health:logs_all"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Logs", callback_data="health:logs"),
            InlineKeyboardButton("🔙 Back to Dashboard", callback_data="health:back"),
        ],
    ]
    return text, InlineKeyboardMarkup(buttons)
