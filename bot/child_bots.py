"""Child Bot Manager — manages worker bots for load balancing & multi-quality deep linking."""

from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timezone
import html as htmlmod

from bot.telegram import Client, filters, enums
from bot.telegram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import settings

log = logging.getLogger(__name__)


class ChildBotManager:
    """Manages dynamic Pyrogram Client instances for child/worker bots."""

    def __init__(self, main_client: Client | None = None):
        self.main_client = main_client
        self.active_clients: dict[int, Client] = {}   # bot_id -> Pyrogram Client
        self.bot_info_cache: dict[int, dict] = {}     # bot_id -> metadata doc
        self._round_robin_indices: dict[str, int] = {} # quality -> current index
        self._running = False

    async def start(self):
        """Load and start all active child bots from the database on bot boot."""
        from bot.database import db
        if not db:
            log.warning("ChildBotManager: Database not ready, skipping child bot startup.")
            return

        bot_docs = await db.get_child_bots(active_only=True)
        log.info("ChildBotManager: Found %d registered child bot(s)", len(bot_docs))

        started_count = 0
        for doc in bot_docs:
            success = await self._start_single_bot(doc)
            if success:
                started_count += 1

        self._running = True
        log.info("ChildBotManager: Successfully initialized %d/%d child bot(s)", started_count, len(bot_docs))

    async def stop(self):
        """Gracefully stop all child bot client instances on shutdown."""
        self._running = False
        for bot_id, client in list(self.active_clients.items()):
            try:
                log.info("Stopping child bot ID %d...", bot_id)
                await client.stop()
            except Exception as e:
                log.warning("Error stopping child bot %d: %s", bot_id, e)
        self.active_clients.clear()
        self.bot_info_cache.clear()
        log.info("ChildBotManager: All child bots stopped.")

    async def add_bot(self, token: str, quality: str = "all") -> dict:
        """Validate token with Telegram, register in DB, and start the child bot."""
        from bot.database import db
        if not db:
            raise RuntimeError("Database not available.")

        token = token.strip()
        quality = quality.strip().lower()

        # Step 1: Validate token by testing get_me()
        temp_client = Client(
            name=f"temp_val_{int(datetime.now().timestamp())}",
            api_id=settings.bot.api_id,
            api_hash=settings.bot.api_hash,
            bot_token=token,
            in_memory=True,
        )
        try:
            await temp_client.start()
            me = await temp_client.get_me()
            await temp_client.stop()
        except Exception as e:
            raise ValueError(f"Invalid Telegram bot token or connection failure: {e}")

        bot_id = me.id
        username = me.username or f"bot_{bot_id}"
        first_name = me.first_name or "AnimeDekho Worker"

        # Step 2: Save to MongoDB
        await db.add_child_bot(
            token=token,
            username=username,
            bot_id=bot_id,
            quality=quality,
            first_name=first_name,
        )

        doc = await db.get_child_bot(str(bot_id))
        if not doc:
            doc = {
                "token": token,
                "username": username,
                "bot_id": bot_id,
                "first_name": first_name,
                "quality": quality,
                "is_active": True,
                "files_served": 0,
            }

        # Step 3: Launch live client
        await self._start_single_bot(doc)

        return {
            "bot_id": bot_id,
            "username": username,
            "first_name": first_name,
            "quality": quality,
        }

    async def remove_bot(self, identifier: str) -> bool:
        """Stop and remove a child bot by username, bot_id, or token prefix."""
        from bot.database import db
        if not db:
            return False

        doc = await db.get_child_bot(identifier)
        if not doc:
            return False

        bot_id = doc["bot_id"]
        if bot_id in self.active_clients:
            try:
                client = self.active_clients.pop(bot_id)
                await client.stop()
            except Exception as e:
                log.warning("Error stopping removed child bot %d: %s", bot_id, e)

        self.bot_info_cache.pop(bot_id, None)
        return await db.remove_child_bot(identifier)

    async def set_bot_quality(self, identifier: str, quality: str) -> bool:
        """Update assigned quality tier for an existing child bot."""
        from bot.database import db
        if not db:
            return False

        q_clean = quality.strip().lower()
        success = await db.update_child_bot(identifier, {"quality": q_clean})
        if success:
            doc = await db.get_child_bot(identifier)
            if doc and doc["bot_id"] in self.bot_info_cache:
                self.bot_info_cache[doc["bot_id"]]["quality"] = q_clean
        return success

    async def get_all_bots(self) -> list[dict]:
        """List all child bots with live status and metadata."""
        from bot.database import db
        if not db:
            return []

        docs = await db.get_child_bots()
        for d in docs:
            b_id = d.get("bot_id")
            d["is_online"] = b_id in self.active_clients
        return docs

    def get_bot_for_quality(self, quality: str) -> str | None:
        """
        Get the child bot username assigned to this quality tier.
        Supports round-robin load balancing when multiple bots are assigned to the same quality.
        Returns username without '@' or None (falls back to main bot).
        """
        q_norm = quality.strip().lower()
        is_4k = q_norm in ("4k", "2160p", "2160")

        # Find matching candidates
        matching_bots: list[dict] = []
        fallback_all_bots: list[dict] = []

        for b_id, doc in self.bot_info_cache.items():
            if b_id not in self.active_clients:
                continue
            bot_q = doc.get("quality", "all").lower()

            if is_4k and bot_q in ("4k", "2160p", "2160", "uhd"):
                matching_bots.append(doc)
            elif bot_q == q_norm or (q_norm.replace("p", "") == bot_q.replace("p", "")):
                matching_bots.append(doc)
            elif bot_q in ("all", "any"):
                fallback_all_bots.append(doc)

        pool = matching_bots if matching_bots else fallback_all_bots
        if not pool:
            return None

        # Round-robin
        idx = self._round_robin_indices.get(q_norm, 0) % len(pool)
        self._round_robin_indices[q_norm] = idx + 1
        return pool[idx].get("username")

    async def _start_single_bot(self, doc: dict) -> bool:
        """Launch a single child bot Pyrogram Client and attach message handlers."""
        bot_id = doc["bot_id"]
        token = doc["token"]
        username = doc["username"]
        quality = doc.get("quality", "all")

        # Stop existing client if any
        if bot_id in self.active_clients:
            try:
                await self.active_clients[bot_id].stop()
            except Exception:
                pass

        client = Client(
            name=f"child_bot_{bot_id}",
            api_id=settings.bot.api_id,
            api_hash=settings.bot.api_hash,
            bot_token=token,
            in_memory=True,
        )

        # ── Register Message & Callback Handlers for Child Bot ──────────
        from bot.handlers.worker_admin import (
            cmd_stats, cmd_users_count, cmd_ban, cmd_unban,
            cmd_broadcast, cmd_pbroadcast, cmd_dbroadcast,
            cmd_fsub, cmd_fsub_mod, cmd_dlt_time, cmd_tutorial,
            dlt_time_callback, toggle_fsub_mod_callback,
        )
        from bot.auto_delete import handle_close_dlt_notice

        @client.on_message(filters.command("start") & filters.private)
        async def _child_start(c: Client, m: Message):
            user = m.from_user
            user_id = user.id if user else 0

            from bot.database import db
            if db and user_id:
                if await db.is_banned(user_id):
                    await m.reply_text("⛔ You are banned from using this bot.")
                    return
                await db.track_bot_user(user_id, user.username or "", user.first_name or "")

            args = m.text.split(maxsplit=1)
            if len(args) > 1:
                param = args[1]
                if param.startswith("get_"):
                    await self._handle_child_file_request(c, m, param, doc)
                    return
                elif param.startswith("join_"):
                    slug = param[5:]
                    if db:
                        mapping = await db.get_channel_mapping(slug)
                        if mapping and mapping.get("channel_id"):
                            from bot.fsub import check_fsub, create_timer_invite_link
                            is_sub, f_text, f_markup = await check_fsub(c, user_id, retry_param=param)
                            if not is_sub:
                                await m.reply_text(f_text, parse_mode=enums.ParseMode.HTML, reply_markup=f_markup)
                                return

                            retry_url = f"https://t.me/{username}?start={param}"
                            t_link = await create_timer_invite_link(c, mapping["channel_id"], expire_seconds=120, name=f"Join {slug[:15]}")
                            if not t_link:
                                retry_btn = [InlineKeyboardButton("🔄 Try Again", url=retry_url)]
                                await m.reply_text(
                                    "⚠️ <b>Failed to generate temporary invite link.</b>\n\n"
                                    "Unable to generate a 2-minute expiring link for this series channel right now.\n"
                                    "Please click the <b>Try Again</b> button below to re-generate your link!",
                                    parse_mode=enums.ParseMode.HTML,
                                    reply_markup=InlineKeyboardMarkup([retry_btn]),
                                )
                                return

                            s_title = mapping.get("series_title", slug)
                            buttons = [
                                [InlineKeyboardButton("🚀 Join Channel", url=t_link)],
                                [InlineKeyboardButton("🔄 Try Again", url=retry_url)],
                            ]
                            t_msg = await m.reply_text(
                                f"📺 <b>Dedicated Series Channel:</b> {htmlmod.escape(s_title)}\n\n"
                                f"⏳ <b>Temporary Invite Link:</b>\n"
                                f"This link will automatically expire in <b>2 minutes</b>!\n\n"
                                f"Click below to join:",
                                parse_mode=enums.ParseMode.HTML,
                                reply_markup=InlineKeyboardMarkup(buttons),
                            )
                            from bot.auto_delete import auto_delete_service
                            await auto_delete_service.schedule_deletion(
                                client=c,
                                chat_id=m.chat.id,
                                message_id=t_msg.id,
                                custom_seconds=120,
                            )
                            return
                        else:
                            await m.reply_text("⚠️ No dedicated channel found for this series.")
                            return

            main_user = ""
            if self.main_client and hasattr(self.main_client, "me") and self.main_client.me:
                main_user = f"@{self.main_client.me.username}"
            else:
                from bot.library import library_manager
                if library_manager and library_manager.bot_username:
                    main_user = f"@{library_manager.bot_username}"

            welcome = (
                f"🤖 <b>AnimeDekho Worker Bot</b> (@{username})\n\n"
                f"⚡ <b>Assigned Tier:</b> <code>{quality.upper()}</code>\n"
                f"📥 I deliver requested anime episodes and movies directly to you with maximum download speed.\n\n"
                f"🔍 <b>To search and browse all anime, use our Main Bot:</b>\n"
                f"👉 {main_user or 'Main Controller Bot'}"
            )
            buttons = []
            if main_user:
                buttons.append([InlineKeyboardButton("🚀 Go to Main Bot", url=f"https://t.me/{main_user.lstrip('@')}")])
            await m.reply_text(welcome, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

        @client.on_message(filters.command("help") & filters.private)
        async def _child_help(c: Client, m: Message):
            main_user = ""
            if self.main_client and hasattr(self.main_client, "me") and self.main_client.me:
                main_user = f"@{self.main_client.me.username}"

            await m.reply_text(
                f"📖 <b>Worker Bot Help</b>\n\n"
                f"This bot is an automated file delivery worker for {main_user}.\n"
                f"Click on any episode or quality button in our channel or main bot to download.\n\n"
                "<b>Commands:</b>\n"
                "/start — Start bot\n"
                "/help — View help\n"
                "/tutorial — How it works",
                parse_mode=enums.ParseMode.HTML,
            )

        @client.on_message(filters.command("status") & filters.private)
        async def _child_status(c: Client, m: Message):
            user = m.from_user
            if user and not is_owner(user.id):
                await m.reply_text("⛔ Owner only command.")
                return

            from bot.health import get_uptime, get_system_stats
            _, uptime_str = get_uptime()
            sys_stats = get_system_stats()
            text = (
                f"🤖 <b>Child Worker Status</b> (@{username})\n\n"
                f"• <b>Assigned Tier:</b> <code>{quality.upper()}</code>\n"
                f"• <b>Files Delivered:</b> <code>{doc.get('files_served', 0)}</code>\n"
                f"• <b>Uptime:</b> <code>{uptime_str}</code>\n"
                f"• <b>RAM Usage:</b> <code>{sys_stats['ram_pct']}%</code> ({sys_stats['proc_mem_mb']} MB)\n"
                f"• <b>CPU Load:</b> <code>{sys_stats['cpu_pct']}%</code>\n"
                f"• <b>Status:</b> <code>ONLINE (Active)</code>"
            )
            await m.reply_text(text, parse_mode=enums.ParseMode.HTML)

        # Attach shared admin commands on child bot
        client.on_message(filters.command("users") & filters.private)(cmd_users_count)
        client.on_message(filters.command("ban") & filters.private)(cmd_ban)
        client.on_message(filters.command(["uban", "unban"]) & filters.private)(cmd_unban)
        client.on_message(filters.command("broadcast") & filters.private)(cmd_broadcast)
        client.on_message(filters.command("pbroadcast") & filters.private)(cmd_pbroadcast)
        client.on_message(filters.command("dbroadcast") & filters.private)(cmd_dbroadcast)
        client.on_message(filters.command("fsub") & filters.private)(cmd_fsub)
        client.on_message(filters.command("fsub_mod") & filters.private)(cmd_fsub_mod)
        client.on_message(filters.command("dlt_time") & filters.private)(cmd_dlt_time)
        client.on_message(filters.command("stats") & filters.private)(cmd_stats)
        client.on_message(filters.command("tutorial") & filters.private)(cmd_tutorial)

        # Attach callbacks on child bot
        client.on_callback_query(filters.regex(r"^dlt:"))(dlt_time_callback)
        client.on_callback_query(filters.regex(r"^toggle_fsub_mod"))(toggle_fsub_mod_callback)
        client.on_callback_query(filters.regex(r"^close_dlt_notice"))(handle_close_dlt_notice)

        try:
            await client.start()
            self.active_clients[bot_id] = client
            self.bot_info_cache[bot_id] = doc
            log.info("Child Bot @%s (ID: %d) successfully started for tier [%s]", username, bot_id, quality)
            return True
        except Exception as e:
            log.error("Failed to start child bot @%s (%d): %s", username, bot_id, e)
            return False

    async def _handle_child_file_request(
        self, client: Client, message: Message, param: str, bot_doc: dict
    ):
        """Deliver requested anime episodes / movies through this child worker bot."""
        from bot.database import db
        user = message.from_user
        user_id = user.id if user else 0

        # Force Subscribe verification (supports timer links when fsub_mod is on)
        from bot.fsub import check_fsub
        is_sub, f_text, f_markup = await check_fsub(client, user_id, retry_param=param)
        if not is_sub:
            await message.reply_text(f_text, parse_mode=enums.ParseMode.HTML, reply_markup=f_markup)
            return

        # Parse deep link: get_<slug>_<quality>_<episode_key>
        raw = param[4:]  # strip 'get_'
        m = re.match(
            r"^(.+?)_(480p|720p|1080p|1080p\s*hq|1080p\s*hq\s*x265|4k|2160p|auto)_(s\d+e\d+|movie|all)$",
            raw,
            re.IGNORECASE,
        )
        if not m:
            await message.reply_text("⚠️ Invalid or expired download link.")
            return

        series_slug = m.group(1)
        quality = m.group(2)
        episode_key = m.group(3)

        from utils.helpers import slug_to_title
        title = slug_to_title(series_slug)
        from bot.auto_delete import auto_delete_service

        # Batch download / Get All request
        if episode_key.lower() == "all":
            query: dict = {"series_slug": series_slug}
            is_4k = quality.lower() in ("4k", "2160p", "2160")
            if is_4k:
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
                await message.reply_text("❌ No downloaded files found in the library for this quality tier.")
                return

            from bot.library import _ep_sort_key
            all_files.sort(key=lambda x: _ep_sort_key(x.get("episode_key", "")))

            status_msg = await message.reply_text(f"⚡ <b>Worker @{bot_doc['username']}:</b> Delivering {len(all_files)} episode(s)...", parse_mode=enums.ParseMode.HTML)
            delivered = 0

            for f in all_files:
                ep_key = f.get("episode_key", "")
                q_label = f.get("quality", quality)
                caption = f"📦 <b>{htmlmod.escape(title)}</b> [{q_label}] — {ep_key}\n<i>⚡ Delivered via @{bot_doc['username']}</i>"

                sent_msg = await self._send_single_file(client, message.chat.id, f, caption)
                if sent_msg:
                    delivered += 1
                    get_file_link = f"https://t.me/{bot_doc['username']}?start={param}"
                    await auto_delete_service.schedule_deletion(
                        client=client,
                        chat_id=message.chat.id,
                        message_id=sent_msg.id,
                        get_file_link=get_file_link,
                        file_title=title,
                    )
                await asyncio.sleep(0.4)

            await status_msg.edit_text(f"✅ <b>Delivered {delivered}/{len(all_files)} episode(s)</b> via @{bot_doc['username']}!", parse_mode=enums.ParseMode.HTML)
            await db.increment_child_bot_stats(bot_doc["bot_id"])
            return

        # Single episode or movie
        cached_file = await db.find_cached_file(series_slug, episode_key, quality)
        if not cached_file:
            cached_file = await db.files.find_one({
                "series_slug": series_slug,
                "episode_key": episode_key,
            })

        if not cached_file:
            await message.reply_text("❌ This episode is not yet available in the library.")
            return

        q_label = cached_file.get("quality", quality)
        disp_ep = f" — {episode_key}" if episode_key.lower() != "movie" else " 🎬 Movie"
        caption = f"📦 <b>{htmlmod.escape(title)}</b> [{q_label}]{disp_ep}\n<i>⚡ Delivered via @{bot_doc['username']}</i>"

        sent_msg = await self._send_single_file(client, message.chat.id, cached_file, caption)
        if sent_msg:
            await db.increment_child_bot_stats(bot_doc["bot_id"])
            get_file_link = f"https://t.me/{bot_doc['username']}?start={param}"
            await auto_delete_service.schedule_deletion(
                client=client,
                chat_id=message.chat.id,
                message_id=sent_msg.id,
                get_file_link=get_file_link,
                file_title=title,
            )
        else:
            await message.reply_text("⚠️ Could not deliver file. Please try again or download via our main bot.")

    async def _send_single_file(
        self, child_client: Client, chat_id: int, file_doc: dict, caption: str
    ) -> Message | None:
        """
        Deliver a single file to user with fallback cascade:
        1. Copy from storage/main channel (instant & works across all bots).
        2. Send document/video using file_id via child client.
        3. Fallback to main client sending/copying directly.
        Returns the sent Message or None.
        """
        storage_cid = file_doc.get("storage_channel_id")
        storage_mid = file_doc.get("storage_message_id")
        file_id = file_doc.get("file_id")

        # 1. Try copy_message from storage/dump channel
        if storage_cid and storage_mid:
            try:
                msg = await child_client.copy_message(
                    chat_id=chat_id,
                    from_chat_id=storage_cid,
                    message_id=storage_mid,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
                return msg
            except Exception as e:
                log.debug("copy_message from storage channel failed: %s", e)

        # 2. Try sending directly with file_id
        if file_id:
            try:
                msg = await child_client.send_video(
                    chat_id=chat_id,
                    video=file_id,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
                return msg
            except Exception:
                try:
                    msg = await child_client.send_document(
                        chat_id=chat_id,
                        document=file_id,
                        caption=caption,
                        parse_mode=enums.ParseMode.HTML,
                    )
                    return msg
                except Exception as e2:
                    log.debug("Child bot direct send file_id failed: %s", e2)

        # 3. Fallback: Main Bot client delivers on behalf of child bot
        if self.main_client and file_id:
            try:
                msg = await self.main_client.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                )
                return msg
            except Exception as e3:
                log.warning("Main client fallback send failed: %s", e3)

        return None

    async def check_bots_health(self) -> list[dict]:
        """
        Check health and connection latency of all registered child worker bots.
        Returns list of status dicts with bot info, ping latency, and online/offline state.
        """
        import time
        from bot.database import db
        if not db:
            return []
        try:
            bot_docs = await db.get_child_bots()
        except Exception as e:
            log.warning("Failed to fetch child bots for health check: %s", e)
            return []

        results = []
        for doc in bot_docs:
            bot_id = doc.get("bot_id")
            username = doc.get("username", "")
            quality = doc.get("quality", "all")
            files_served = doc.get("files_served", 0)
            client = self.active_clients.get(bot_id)

            status = {
                "bot_id": bot_id,
                "username": username,
                "quality": quality,
                "files_served": files_served,
                "is_active_config": doc.get("is_active", True),
                "is_connected": False,
                "ping_ms": None,
                "first_name": username,
                "error": None,
            }

            if not client:
                status["error"] = "Client not running"
            elif not client.is_connected:
                status["error"] = "Client disconnected"
            else:
                try:
                    start_t = time.perf_counter()
                    me = await client.get_me()
                    latency = (time.perf_counter() - start_t) * 1000
                    status["is_connected"] = True
                    status["ping_ms"] = round(latency, 1)
                    status["first_name"] = me.first_name or username
                except Exception as e:
                    status["error"] = str(e)[:100]

            results.append(status)
        return results


# Singleton instance
child_bot_manager: ChildBotManager | None = None
