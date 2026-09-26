"""MongoDB integration using motor (async driver)."""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger(__name__)


class Database:
    def __init__(self, mongo_uri: str, db_name: str = "animedekho"):
        self.client = AsyncIOMotorClient(mongo_uri)
        self.db = self.client[db_name]

        # Collections
        self.users = self.db["users"]
        self.library = self.db["library"]
        self.files = self.db["files"]
        self.downloads = self.db["downloads"]
        self.config = self.db["config"]
        self.ai_history = self.db["ai_history"]
        self.ai_facts = self.db["ai_facts"]
        self.child_bots = self.db["child_bots"]
        self.channel_mappings = self.db["channel_mappings"]
        self.download_errors = self.db["download_errors"]
        self.bot_users = self.db["bot_users"]
        self.banned_users = self.db["banned_users"]
        self.auto_delete_jobs = self.db["auto_delete_jobs"]
        self.custom_thumbnails = self.db["custom_thumbnails"]
        self.monitored_series = self.db["monitored_series"]

    async def init_indexes(self):
        """Create necessary indexes."""
        await self.users.create_index("user_id", unique=True)
        await self.library.create_index(
            [("series_slug", 1), ("quality", 1), ("part", 1)],
            unique=True,
        )
        await self.files.create_index("file_unique_id", unique=True)
        await self.files.create_index(
            [("series_slug", 1), ("quality", 1), ("episode_key", 1)],
            unique=True,
        )
        await self.downloads.create_index("user_id")
        await self.downloads.create_index("timestamp")
        await self.ai_history.create_index([("chat_id", 1), ("timestamp", 1)])
        await self.ai_facts.create_index([("chat_id", 1), ("key", 1)], unique=True)
        await self.child_bots.create_index("bot_id", unique=True)
        await self.child_bots.create_index("username")
        await self.channel_mappings.create_index("series_slug", unique=True)
        await self.channel_mappings.create_index("channel_id")
        await self.download_errors.create_index([("timestamp", -1)])
        await self.download_errors.create_index("series_slug")
        await self.bot_users.create_index("user_id", unique=True)
        await self.banned_users.create_index("user_id", unique=True)
        await self.auto_delete_jobs.create_index([("delete_at", 1)])
        await self.auto_delete_jobs.create_index([("chat_id", 1), ("message_id", 1)], unique=True)
        await self.custom_thumbnails.create_index([("thumb_type", 1), ("key", 1)], unique=True)
        await self.monitored_series.create_index("series_slug", unique=True)
        log.info("MongoDB indexes created")

    # ── User management ───────────────────────────────────────────

    async def add_user(self, user_id: int, username: str = "", added_by: int = 0) -> bool:
        """Add approved user. Returns True if newly added."""
        try:
            await self.users.insert_one({
                "user_id": user_id,
                "username": username,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "added_by": added_by,
            })
            return True
        except Exception:
            # Duplicate key = already exists
            return False

    async def remove_user(self, user_id: int) -> bool:
        """Remove user. Returns True if removed."""
        result = await self.users.delete_one({"user_id": user_id})
        return result.deleted_count > 0

    async def is_approved(self, user_id: int) -> bool:
        """Check if user is approved."""
        doc = await self.users.find_one({"user_id": user_id})
        return doc is not None

    async def get_users(self) -> list[int]:
        """Get all approved user IDs."""
        cursor = self.users.find({}, {"user_id": 1})
        return sorted([doc["user_id"] async for doc in cursor])

    # ── Config (channel invite link, etc.) ────────────────────────

    async def get_config(self, key: str, default=None):
        """Get a config value."""
        doc = await self.config.find_one({"_id": key})
        return doc["value"] if doc else default

    async def set_config(self, key: str, value):
        """Set a config value."""
        await self.config.update_one(
            {"_id": key},
            {"$set": {"value": value}},
            upsert=True,
        )

    # ── File cache (duplicate prevention) ────────────────────────

    async def find_cached_file(
        self,
        series_identifier: str,
        episode_key: str = "",
        quality: str = "",
    ) -> dict | None:
        """
        Flexible lookup for a cached anime file in DB.
        Matches series by slug, clean slug, or title (case-insensitive regex),
        normalizes episode keys (e.g. S1E1 == S01E01, movie),
        and matches quality (or any quality if quality='auto' or empty).
        Returns dict with file_id, quality, episode_key, series_title, series_slug or None.
        """
        import re

        clean_id = (series_identifier or "").strip()
        if not clean_id:
            return None

        # Build episode query condition if specified
        ep_condition = None
        if episode_key:
            ep_clean = episode_key.strip()
            if ep_clean.lower() in ("movie", "film"):
                ep_condition = {"$in": ["movie", "Movie", "MOVIE"]}
            else:
                m = re.search(r"S(\d+)E(\d+)", ep_clean, re.I)
                if m:
                    s_num, ep_num = int(m.group(1)), int(m.group(2))
                    variants = {
                        f"S{s_num:02d}E{ep_num:02d}",
                        f"S{s_num}E{ep_num}",
                        f"S{s_num:02d}E{ep_num}",
                        f"S{s_num}E{ep_num:02d}",
                        f"s{s_num:02d}e{ep_num:02d}",
                    }
                    ep_condition = {"$in": list(variants)}
                else:
                    ep_condition = {"$regex": f"^{re.escape(ep_clean)}$", "$options": "i"}

        # Build quality condition
        q_condition = None
        if quality and quality.lower() not in ("auto", "any", ""):
            q_clean = quality.strip()
            if q_clean.lower() in ("4k", "2160p", "2160"):
                q_condition = {
                    "$in": [
                        "4K", "4k", "2160p", "2160P", "2160",
                        "1080p HQ", "1080p HQ x265", "1080p 10-Bit", "1080p 10bit",
                        "1080p x265", "1080p HEVC", "4K (1080p HQ)",
                    ]
                }
            elif q_clean.lower() in ("1080p", "1080"):
                q_condition = {
                    "$in": [
                        "1080p", "1080P", "1080",
                        "1080p HQ", "1080p HQ x265", "1080p 10-Bit", "1080p 10bit",
                        "1080p x265", "1080p HEVC",
                    ]
                }
            else:
                q_condition = {"$regex": f"^{re.escape(q_clean)}$", "$options": "i"}

        # Build series matching criteria
        slug_candidate = re.sub(r'[^a-zA-Z0-9]+', '-', clean_id).strip('-').lower()
        core_title = re.sub(r'(?i)\s*(season\s*\d+|s\d+|hindi|dubbed|multi-audio|tamil|telugu).*$', '', clean_id).strip()
        core_slug = re.sub(r'[^a-zA-Z0-9]+', '-', core_title).strip('-').lower()

        series_clauses = [
            {"series_slug": clean_id},
            {"series_slug": slug_candidate},
            {"series_slug": {"$regex": f"^{re.escape(slug_candidate)}", "$options": "i"}},
            {"series_title": {"$regex": f"^{re.escape(clean_id)}$", "$options": "i"}},
        ]
        if core_title and core_title != clean_id:
            series_clauses.append({"series_title": {"$regex": f"^{re.escape(core_title)}", "$options": "i"}})
        if core_slug and core_slug != slug_candidate:
            series_clauses.append({"series_slug": {"$regex": f"^{re.escape(core_slug)}", "$options": "i"}})

        query: dict = {"$or": series_clauses}
        if ep_condition:
            query["episode_key"] = ep_condition
        if q_condition:
            query["quality"] = q_condition

        # 1. Try matching with exact criteria
        doc = await self.files.find_one(query)
        if doc:
            return doc

        # 2. Try direct series_slug match if not matched above
        direct_query = {"series_slug": clean_id}
        if ep_condition:
            direct_query["episode_key"] = ep_condition
        if q_condition:
            direct_query["quality"] = q_condition
        doc = await self.files.find_one(direct_query)
        if doc:
            return doc

        return None

    async def get_cached_file(
        self, series_slug: str, quality: str, episode_key: str
    ) -> str | None:
        """
        Check if this series+quality+episode was already downloaded.
        Returns file_id if cached, None otherwise.
        """
        cached = await self.find_cached_file(series_slug, episode_key, quality)
        return cached["file_id"] if cached else None

    async def save_file(
        self,
        series_slug: str,
        series_title: str,
        quality: str,
        episode_key: str,
        file_id: str,
        file_unique_id: str,
        storage_channel_id: int | None = None,
        storage_message_id: int | None = None,
    ):
        """Save a downloaded file reference for future cache lookups."""
        try:
            update_data = {
                "series_title": series_title,
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if storage_channel_id:
                update_data["storage_channel_id"] = storage_channel_id
            if storage_message_id:
                update_data["storage_message_id"] = storage_message_id

            await self.files.update_one(
                {
                    "series_slug": series_slug,
                    "quality": quality,
                    "episode_key": episode_key,
                },
                {"$set": update_data},
                upsert=True,
            )
        except Exception as e:
            log.warning("Failed to save file cache: %s", e)

    # ── Download logging ──────────────────────────────────────────

    async def log_download(
        self,
        user_id: int,
        series_slug: str,
        episode: str,
        quality: str,
        file_id: str,
    ):
        """Log a download to history."""
        await self.downloads.insert_one({
            "user_id": user_id,
            "series_slug": series_slug,
            "episode": episode,
            "quality": quality,
            "file_id": file_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── AI Persistent Memory & Conversation History ───────────────

    async def save_ai_message(self, chat_id: int, role: str, content: str):
        """Save a message to persistent AI conversation history for a chat."""
        if not content:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.ai_history.insert_one({
                "chat_id": chat_id,
                "role": role,
                "content": content,
                "timestamp": now,
            })
            # Keep latest 50 messages per chat to keep database bounded
            count = await self.ai_history.count_documents({"chat_id": chat_id})
            if count > 50:
                oldest = await self.ai_history.find({"chat_id": chat_id}).sort("timestamp", 1).limit(count - 40).to_list(length=None)
                if oldest:
                    ids = [doc["_id"] for doc in oldest]
                    await self.ai_history.delete_many({"_id": {"$in": ids}})
        except Exception as e:
            log.warning("Failed to save AI message: %s", e)

    async def get_ai_history(self, chat_id: int, limit: int = 20) -> list[dict[str, str]]:
        """Retrieve recent conversation history in chronological order (oldest to newest)."""
        try:
            cursor = self.ai_history.find(
                {"chat_id": chat_id},
                {"_id": 0, "role": 1, "content": 1},
            ).sort("timestamp", -1).limit(limit)
            docs = await cursor.to_list(length=limit)
            docs.reverse()  # Oldest to newest
            return docs
        except Exception as e:
            log.warning("Failed to retrieve AI history: %s", e)
            return []

    async def clear_ai_history(self, chat_id: int | None = None):
        """Clear conversation history for a specific chat or all chats."""
        try:
            query = {"chat_id": chat_id} if chat_id is not None else {}
            await self.ai_history.delete_many(query)
        except Exception as e:
            log.warning("Failed to clear AI history: %s", e)

    async def save_ai_fact(self, chat_id: int, key: str, value: str):
        """Save or update a persistent long-term memory fact / preference."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.ai_facts.update_one(
                {"chat_id": chat_id, "key": key.strip().lower()},
                {"$set": {
                    "chat_id": chat_id,
                    "key": key.strip().lower(),
                    "value": value.strip(),
                    "updated_at": now,
                }},
                upsert=True,
            )
        except Exception as e:
            log.warning("Failed to save AI memory fact: %s", e)

    async def get_ai_facts(self, chat_id: int) -> list[dict]:
        """Retrieve all stored permanent memory facts for a chat."""
        try:
            cursor = self.ai_facts.find({"chat_id": chat_id}, {"_id": 0, "key": 1, "value": 1, "updated_at": 1})
            return await cursor.to_list(length=100)
        except Exception as e:
            log.warning("Failed to retrieve AI memory facts: %s", e)
            return []

    async def delete_ai_fact(self, chat_id: int, key: str) -> bool:
        """Delete a specific permanent memory fact."""
        try:
            res = await self.ai_facts.delete_one({"chat_id": chat_id, "key": key.strip().lower()})
            return res.deleted_count > 0
        except Exception as e:
            log.warning("Failed to delete AI memory fact: %s", e)
            return False

    # ── Child Bot Management ──────────────────────────────────────

    async def add_child_bot(
        self,
        token: str,
        username: str,
        bot_id: int,
        quality: str = "all",
        first_name: str = "",
    ) -> bool:
        """Add or update a child worker bot in MongoDB."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.child_bots.update_one(
                {"bot_id": bot_id},
                {"$set": {
                    "token": token,
                    "username": username.lstrip("@"),
                    "bot_id": bot_id,
                    "first_name": first_name,
                    "quality": quality.strip().lower(),
                    "is_active": True,
                    "updated_at": now,
                }, "$setOnInsert": {
                    "created_at": now,
                    "files_served": 0,
                }},
                upsert=True,
            )
            return True
        except Exception as e:
            log.warning("Failed to add child bot %s (%d): %s", username, bot_id, e)
            return False

    async def remove_child_bot(self, identifier: str) -> bool:
        """Remove a child bot by username or bot_id."""
        clean = identifier.strip().lstrip("@")
        query_clauses: list[dict] = [
            {"username": {"$regex": f"^{re.escape(clean)}$", "$options": "i"}},
            {"token": clean},
        ]
        if clean.isdigit():
            query_clauses.append({"bot_id": int(clean)})
        query = {"$or": query_clauses}
        try:
            res = await self.child_bots.delete_one(query)
            return res.deleted_count > 0
        except Exception as e:
            log.warning("Failed to remove child bot %s: %s", identifier, e)
            return False

    async def get_child_bots(self, active_only: bool = False) -> list[dict]:
        """Get all child bots from database."""
        query = {"is_active": True} if active_only else {}
        try:
            cursor = self.child_bots.find(query).sort("created_at", 1)
            return await cursor.to_list(length=100)
        except Exception as e:
            log.warning("Failed to list child bots: %s", e)
            return []

    async def get_child_bot(self, identifier: str) -> dict | None:
        """Get a single child bot by username or bot_id."""
        clean = identifier.strip().lstrip("@")
        query_clauses: list[dict] = [
            {"username": {"$regex": f"^{re.escape(clean)}$", "$options": "i"}},
            {"token": clean},
        ]
        if clean.isdigit():
            query_clauses.append({"bot_id": int(clean)})
        query = {"$or": query_clauses}
        try:
            return await self.child_bots.find_one(query)
        except Exception as e:
            log.warning("Failed to get child bot %s: %s", identifier, e)
            return None

    async def update_child_bot(self, identifier: str, update_data: dict) -> bool:
        """Update fields for a child bot."""
        clean = identifier.strip().lstrip("@")
        query_clauses: list[dict] = [
            {"username": {"$regex": f"^{re.escape(clean)}$", "$options": "i"}},
        ]
        if clean.isdigit():
            query_clauses.append({"bot_id": int(clean)})
        query = {"$or": query_clauses}
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            res = await self.child_bots.update_one(query, {"$set": update_data})
            return res.modified_count > 0
        except Exception as e:
            log.warning("Failed to update child bot %s: %s", identifier, e)
            return False

    async def increment_child_bot_stats(self, bot_id: int):
        """Increment files served counter for a child bot."""
        try:
            await self.child_bots.update_one(
                {"bot_id": bot_id},
                {"$inc": {"files_served": 1}}
            )
        except Exception:
            pass

    # ── Channel Mappings & Userbot Session ──────────────────────────

    async def get_channel_mapping(self, series_slug: str, language: str | None = None) -> dict | None:
        """Get mapped channel information for a series slug, with optional language-specific routing."""
        try:
            doc = await self.channel_mappings.find_one({"series_slug": series_slug})
            if not doc:
                return None
            if language:
                lang_clean = language.strip().lower()
                routes = doc.get("language_routes", {})
                if lang_clean in routes:
                    r = routes[lang_clean]
                    return {
                        **doc,
                        "channel_id": r.get("channel_id", doc.get("channel_id")),
                        "invite_link": r.get("invite_link", doc.get("invite_link")),
                        "language": lang_clean,
                    }
            return doc
        except Exception as e:
            log.warning("Failed to get channel mapping for %s: %s", series_slug, e)
            return None

    async def set_channel_mapping(
        self,
        series_slug: str,
        channel_id: int,
        invite_link: str,
        series_title: str = "",
        poster_url: str = "",
        auto_created: bool = False,
        created_by: int = 0,
        language: str = "",
    ) -> dict:
        """Upsert a channel mapping for an anime series, supporting optional language routing."""
        now = datetime.now(timezone.utc).isoformat()
        lang_clean = language.strip().lower() if language else ""

        if lang_clean:
            route_entry = {
                "channel_id": channel_id,
                "invite_link": invite_link,
                "language": lang_clean,
                "updated_at": now,
            }
            await self.channel_mappings.update_one(
                {"series_slug": series_slug},
                {
                    "$set": {
                        f"language_routes.{lang_clean}": route_entry,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "series_slug": series_slug,
                        "series_title": series_title or series_slug,
                        "channel_id": channel_id,
                        "invite_link": invite_link,
                        "poster_url": poster_url,
                        "auto_created": auto_created,
                        "created_by": created_by,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
            doc = await self.channel_mappings.find_one({"series_slug": series_slug})
            return doc or {}
        else:
            doc = {
                "series_slug": series_slug,
                "series_title": series_title or series_slug,
                "channel_id": channel_id,
                "invite_link": invite_link,
                "poster_url": poster_url,
                "auto_created": auto_created,
                "created_by": created_by,
                "updated_at": now,
            }
            await self.channel_mappings.update_one(
                {"series_slug": series_slug},
                {
                    "$set": doc,
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            return doc

    async def list_channel_mappings(self) -> list[dict]:
        """List all mapped channels."""
        try:
            cursor = self.channel_mappings.find().sort("series_title", 1)
            return await cursor.to_list(length=500)
        except Exception as e:
            log.warning("Failed to list channel mappings: %s", e)
            return []

    async def delete_channel_mapping(self, series_slug: str, language: str = "") -> bool:
        """Remove a channel mapping or a specific language route."""
        try:
            lang_clean = language.strip().lower() if language else ""
            if lang_clean:
                res = await self.channel_mappings.update_one(
                    {"series_slug": series_slug},
                    {"$unset": {f"language_routes.{lang_clean}": ""}}
                )
                return res.modified_count > 0
            res = await self.channel_mappings.delete_one({"series_slug": series_slug})
            return res.deleted_count > 0
        except Exception as e:
            log.warning("Failed to delete channel mapping for %s: %s", series_slug, e)
            return False

    async def get_userbot_session(self) -> dict | None:
        """Retrieve stored userbot session."""
        try:
            return await self.get_config("userbot_session")
        except Exception as e:
            log.warning("Failed to retrieve userbot session: %s", e)
            return None

    async def save_userbot_session(self, session_string: str, user_data: dict | None = None) -> bool:
        """Store userbot string session and user metadata."""
        try:
            data = {
                "session_string": session_string,
                "user": user_data or {},
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            await self.set_config("userbot_session", data)
            return True
        except Exception as e:
            log.warning("Failed to save userbot session: %s", e)
            return False

    async def delete_userbot_session(self) -> bool:
        """Delete stored userbot session."""
        try:
            await self.config.delete_one({"_id": "userbot_session"})
            return True
        except Exception as e:
            log.warning("Failed to delete userbot session: %s", e)
            return False

    # ── Download Failure Tracking & Database Health ─────────────────

    async def log_download_failure(
        self,
        title: str,
        quality: str = "",
        source: str = "",
        error: str = "",
        series_slug: str = "",
        user_id: int = 0,
    ):
        """Record a failed download event for health monitoring and diagnostics."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self.download_errors.insert_one({
                "title": title,
                "quality": quality,
                "source": source,
                "error": str(error)[:500],
                "series_slug": series_slug,
                "user_id": user_id,
                "timestamp": now,
            })
            # Keep latest 200 error records
            count = await self.download_errors.count_documents({})
            if count > 200:
                oldest = await self.download_errors.find().sort("timestamp", 1).limit(count - 150).to_list(length=None)
                if oldest:
                    await self.download_errors.delete_many({"_id": {"$in": [d["_id"] for d in oldest]}})
        except Exception as e:
            log.warning("Failed to record download failure in DB: %s", e)

    async def get_recent_download_errors(self, limit: int = 10) -> list[dict]:
        """Get recent download failure records."""
        try:
            cursor = self.download_errors.find().sort("timestamp", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            log.warning("Failed to fetch download errors: %s", e)
            return []

    async def count_download_errors(self, hours: int = 24) -> int:
        """Count failed downloads within the last N hours."""
        try:
            from datetime import timedelta
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            return await self.download_errors.count_documents({"timestamp": {"$gte": since}})
        except Exception as e:
            log.warning("Failed to count download errors: %s", e)
            return 0

    async def clear_download_errors(self) -> int:
        """Clear all logged download errors."""
        try:
            res = await self.download_errors.delete_many({})
            return res.deleted_count
        except Exception as e:
            log.warning("Failed to clear download errors: %s", e)
            return 0

    # ── Bot User Network & Broadcasting ────────────────────────────

    async def track_bot_user(self, user_id: int, username: str = "", first_name: str = ""):
        """Record user activity for user count and broadcasting across bot fleet."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.bot_users.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "username": username or "",
                        "first_name": first_name or "",
                        "last_seen": now,
                    },
                    "$setOnInsert": {
                        "first_seen": now,
                    }
                },
                upsert=True,
            )
        except Exception as e:
            log.debug("Failed tracking bot user %d: %s", user_id, e)

    async def get_bot_users_count(self) -> int:
        """Total number of users tracked across bot fleet."""
        try:
            return await self.bot_users.count_documents({})
        except Exception as e:
            log.warning("Failed to get bot users count: %s", e)
            return 0

    async def get_all_bot_user_ids(self) -> list[int]:
        """Get list of all user IDs for broadcasting."""
        try:
            cursor = self.bot_users.find({}, {"user_id": 1})
            return [doc["user_id"] async for doc in cursor if "user_id" in doc]
        except Exception as e:
            log.warning("Failed to fetch all user IDs: %s", e)
            return []

    # ── Ban / Unban System ─────────────────────────────────────────

    async def ban_user(self, user_id: int, reason: str = "", banned_by: int = 0) -> bool:
        """Ban a user from all bots."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self.banned_users.update_one(
                {"user_id": user_id},
                {"$set": {
                    "user_id": user_id,
                    "reason": reason,
                    "banned_by": banned_by,
                    "banned_at": now,
                }},
                upsert=True,
            )
            return True
        except Exception as e:
            log.warning("Failed banning user %d: %s", user_id, e)
            return False

    async def unban_user(self, user_id: int) -> bool:
        """Unban a user."""
        try:
            res = await self.banned_users.delete_one({"user_id": user_id})
            return res.deleted_count > 0
        except Exception as e:
            log.warning("Failed unbanning user %d: %s", user_id, e)
            return False

    async def is_banned(self, user_id: int) -> bool:
        """Check if a user is banned."""
        if not user_id:
            return False
        try:
            doc = await self.banned_users.find_one({"user_id": user_id})
            return doc is not None
        except Exception:
            return False

    async def get_banned_users_count(self) -> int:
        """Total banned users count."""
        try:
            return await self.banned_users.count_documents({})
        except Exception:
            return 0

    # ── Auto Delete System ─────────────────────────────────────────

    async def add_auto_delete_job(
        self,
        chat_id: int,
        message_id: int,
        bot_id: int,
        delete_at: float,
        get_file_link: str = "",
        file_title: str = "",
    ):
        """Store scheduled auto-delete task in MongoDB."""
        try:
            await self.auto_delete_jobs.update_one(
                {"chat_id": chat_id, "message_id": message_id},
                {"$set": {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "bot_id": bot_id,
                    "delete_at": delete_at,
                    "get_file_link": get_file_link,
                    "file_title": file_title,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:
            log.warning("Failed adding auto-delete job: %s", e)

    async def get_pending_auto_delete_jobs(self, before_ts: float | None = None) -> list[dict]:
        """Fetch auto-delete jobs scheduled to be deleted."""
        try:
            query: dict = {}
            if before_ts is not None:
                query["delete_at"] = {"$lte": before_ts}
            cursor = self.auto_delete_jobs.find(query).sort("delete_at", 1)
            return await cursor.to_list(length=None)
        except Exception as e:
            log.warning("Failed fetching auto-delete jobs: %s", e)
            return []

    async def remove_auto_delete_job(self, chat_id: int, message_id: int):
        """Remove an auto-delete task from MongoDB."""
        try:
            await self.auto_delete_jobs.delete_one({"chat_id": chat_id, "message_id": message_id})
        except Exception as e:
            log.debug("Failed removing auto-delete job: %s", e)

    async def get_auto_delete_jobs_count(self) -> int:
        """Count pending auto-delete jobs."""
        try:
            return await self.auto_delete_jobs.count_documents({})
        except Exception:
            return 0

    # ── FSub & Auto-Delete Configuration ───────────────────────────

    async def get_fsub_mod(self) -> bool:
        """Get FSub timer link mode status (default True = timer links ON)."""
        val = await self.get_config("fsub_mod", default="on")
        if isinstance(val, str):
            return val.lower() in ("on", "true", "1", "yes")
        return bool(val)

    async def set_fsub_mod(self, enabled: bool):
        """Set FSub timer link mode ('on' or 'off')."""
        await self.set_config("fsub_mod", "on" if enabled else "off")

    async def get_fsub_channel(self) -> int | str | None:
        """Get configured FSub channel (defaults to settings.bot.main_channel)."""
        val = await self.get_config("fsub_channel", default=None)
        if val is not None:
            return int(val) if str(val).lstrip("-").isdigit() else str(val)
        return settings.bot.main_channel or None

    async def set_fsub_channel(self, channel: int | str | None):
        """Set custom FSub channel."""
        await self.set_config("fsub_channel", channel)

    async def get_dlt_time(self) -> int:
        """Get file auto-delete time in seconds (default 600s = 10 mins; 0 = disabled)."""
        val = await self.get_config("dlt_time", default=600)
        try:
            return max(0, int(val))
        except Exception:
            return 600

    async def set_dlt_time(self, seconds: int):
        """Set file auto-delete time in seconds (0 = disabled)."""
        await self.set_config("dlt_time", max(0, int(seconds)))

    # ── Dump / Storage Channel (OFF by default) ──────────────────────

    async def get_dump_channel(self) -> int | None:
        """Get dump/storage channel ID if configured, else None (OFF by default)."""
        val = await self.get_config("dump_channel", default=None)
        if val is not None and str(val).lstrip("-").isdigit():
            return int(val)
        return None

    async def set_dump_channel(self, channel_id: int | None):
        """Set or disable dump/storage channel (None disables it)."""
        if channel_id:
            await self.set_config("dump_channel", int(channel_id))
        else:
            await self.set_config("dump_channel", None)

    # ── Custom Thumbnail System (OFF by default, falls back to poster) ──

    async def set_custom_thumbnail(self, thumb_type: str, key: str, file_id: str):
        """Save a custom thumbnail (type: 'global', 'series', or 'language')."""
        await self.custom_thumbnails.update_one(
            {"thumb_type": thumb_type, "key": key.lower()},
            {"$set": {"file_id": file_id, "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )

    async def get_custom_thumbnail(self, series_slug: str | None = None, language: str | None = None) -> str | None:
        """
        Resolve custom thumbnail with precedence:
        1. language-specific: (series_slug, language)
        2. series-specific: series_slug
        3. global
        Returns file_id or None (OFF by default, falls back to poster).
        """
        try:
            if series_slug and language:
                doc = await self.custom_thumbnails.find_one({"thumb_type": "language", "key": f"{series_slug}_{language}".lower()})
                if doc:
                    return doc.get("file_id")
            if series_slug:
                doc = await self.custom_thumbnails.find_one({"thumb_type": "series", "key": series_slug.lower()})
                if doc:
                    return doc.get("file_id")
            doc = await self.custom_thumbnails.find_one({"thumb_type": "global"})
            if doc:
                return doc.get("file_id")
        except Exception as e:
            log.warning("Failed to get custom thumbnail: %s", e)
        return None

    async def delete_custom_thumbnail(self, thumb_type: str, key: str = "") -> bool:
        """Delete custom thumbnail."""
        res = await self.custom_thumbnails.delete_one({"thumb_type": thumb_type, "key": key.lower()})
        return res.deleted_count > 0

    async def list_custom_thumbnails(self) -> list[dict]:
        """List all configured custom thumbnails."""
        return await self.custom_thumbnails.find().to_list(length=100)

    # ── Post Style Configuration (Default: 'classic') ────────────────

    async def get_post_style(self) -> str:
        """Get poster post style ('classic' or 'modern'). Default is 'classic'."""
        val = await self.get_config("post_style", default="classic")
        return str(val) if val else "classic"

    async def set_post_style(self, style: str):
        """Set poster post style ('classic' or 'modern')."""
        clean_style = "modern" if style.strip().lower() == "modern" else "classic"
        await self.set_config("post_style", clean_style)

    # ── Start Style & Banner Configuration (Default: 'classic') ──────

    async def get_start_style(self) -> str:
        """Get /start UI style ('classic' or 'modern')."""
        from config import Config
        def_st = getattr(Config, "START_STYLE", "classic") or "classic"
        val = await self.get_config("start_style", default=def_st)
        return str(val) if val else def_st

    async def set_start_style(self, style: str):
        """Set /start UI style ('classic' or 'modern')."""
        clean_style = "modern" if style.strip().lower() == "modern" else "classic"
        await self.set_config("start_style", clean_style)

    async def get_start_pic(self) -> str | None:
        """Get custom image banner for /start UI."""
        from config import Config
        def_pic = getattr(Config, "START_PIC", "") or None
        return await self.get_config("start_pic", default=def_pic)

    async def set_start_pic(self, pic: str | None):
        """Set or remove custom image banner for /start UI."""
        await self.set_config("start_pic", pic)

    async def get_start_msg(self) -> str | None:
        """Get custom welcome message for /start."""
        from config import Config
        def_msg = getattr(Config, "START_MSG", "") or None
        return await self.get_config("start_msg", default=def_msg)

    async def set_start_msg(self, msg: str | None):
        """Set or remove custom welcome message for /start."""
        await self.set_config("start_msg", msg)

    async def get_fsub_pic(self) -> str | None:
        """Get custom banner picture for FSub prompt."""
        from config import Config
        def_pic = getattr(Config, "FSUB_PIC", "") or None
        return await self.get_config("fsub_pic", default=def_pic)

    async def set_fsub_pic(self, pic: str | None):
        """Set or remove custom banner picture for FSub prompt."""
        await self.set_config("fsub_pic", pic)

    async def get_fsub_msg(self) -> str | None:
        """Get custom text message for FSub prompt."""
        from config import Config
        def_msg = getattr(Config, "FSUB_MSG", "") or None
        return await self.get_config("fsub_msg", default=def_msg)

    async def set_fsub_msg(self, msg: str | None):
        """Set or remove custom text message for FSub prompt."""
        await self.set_config("fsub_msg", msg)

    async def get_auto_thumb(self) -> bool:
        """Get auto thumbnail generation toggle (default from Config.AUTO_THUMB)."""
        from config import Config
        def_val = getattr(Config, "AUTO_THUMB", True)
        val = await self.get_config("auto_thumb", default="on" if def_val else "off")
        if isinstance(val, str):
            return val.lower() in ("on", "true", "1", "yes")
        return bool(val)

    async def set_auto_thumb(self, enabled: bool):
        """Set auto thumbnail generation toggle."""
        await self.set_config("auto_thumb", "on" if enabled else "off")

    async def get_auto_schedule_post(self) -> bool:
        """Get auto 12:00 AM schedule channel post toggle."""
        from config import Config
        def_val = getattr(Config, "AUTO_SCHEDULE_POST", False)
        val = await self.get_config("auto_schedule_post", default="on" if def_val else "off")
        if isinstance(val, str):
            return val.lower() in ("on", "true", "1", "yes")
        return bool(val)

    async def set_auto_schedule_post(self, enabled: bool):
        """Set auto 12:00 AM schedule channel post toggle."""
        await self.set_config("auto_schedule_post", "on" if enabled else "off")

    async def get_auto_search(self) -> bool:
        """Get direct anime name chat search trigger toggle (Issue #9)."""
        from config import Config
        def_val = getattr(Config, "AUTO_SEARCH", True)
        val = await self.get_config("auto_search", default="on" if def_val else "off")
        if isinstance(val, str):
            return val.lower() in ("on", "true", "1", "yes")
        return bool(val)

    async def set_auto_search(self, enabled: bool):
        """Set direct anime name chat search trigger toggle."""
        await self.set_config("auto_search", "on" if enabled else "off")


    # ── Episode Post Style Configuration (Default: 'classic') ────────

    async def get_ep_style(self) -> str:
        """Get episode upload post style ('classic' or 'modern'). Default is 'classic'."""
        val = await self.get_config("ep_style", default="classic")
        return str(val) if val else "classic"

    async def set_ep_style(self, style: str):
        """Set episode upload post style ('classic' or 'modern')."""
        clean_style = "modern" if style.strip().lower() == "modern" else "classic"
        await self.set_config("ep_style", clean_style)

    # ── Schedule Style Configuration (Default: 'classic') ────────────

    async def get_sched_style(self) -> str:
        """Get schedule UI style ('classic' or 'modern'). Default is 'classic'."""
        val = await self.get_config("sched_style", default="classic")
        return str(val) if val else "classic"

    async def set_sched_style(self, style: str):
        """Set schedule UI style ('classic' or 'modern')."""
        clean_style = "modern" if style.strip().lower() == "modern" else "classic"
        await self.set_config("sched_style", clean_style)

    # ── Auto Episode Monitoring (OFF by default) ─────────────────────

    async def get_auto_monitor_enabled(self) -> bool:
        """Check if auto episode monitoring is enabled (OFF by default)."""
        val = await self.get_config("auto_monitor_enabled", default=False)
        return bool(val)

    async def set_auto_monitor_enabled(self, enabled: bool):
        """Toggle auto episode monitoring ON or OFF."""
        await self.set_config("auto_monitor_enabled", bool(enabled))

    async def get_auto_monitor_interval(self) -> int:
        """Get auto-monitor check interval in minutes (default 30 mins)."""
        val = await self.get_config("auto_monitor_interval", default=30)
        return int(val) if val else 30

    async def set_auto_monitor_interval(self, minutes: int):
        """Set auto-monitor interval in minutes (minimum 5 mins)."""
        await self.set_config("auto_monitor_interval", max(5, int(minutes)))

    async def get_auto_monitor_quality(self) -> str:
        """Get default download quality for auto-monitoring (default '720p')."""
        val = await self.get_config("auto_monitor_quality", default="720p")
        return str(val) if val else "720p"

    async def set_auto_monitor_quality(self, quality: str):
        """Set default download quality for auto-monitoring."""
        await self.set_config("auto_monitor_quality", quality.strip().lower())

    async def add_monitored_series(self, series_slug: str, series_title: str = "", quality: str = "", channel_id: int | None = None) -> dict:
        """Add an anime series to the auto-monitoring watchlist."""
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "series_slug": series_slug,
            "series_title": series_title or series_slug,
            "quality": quality,
            "channel_id": channel_id,
            "enabled": True,
            "last_checked": None,
            "last_episode_count": 0,
            "created_at": now,
        }
        await self.monitored_series.update_one(
            {"series_slug": series_slug},
            {"$set": doc},
            upsert=True,
        )
        return doc

    async def remove_monitored_series(self, series_slug: str) -> bool:
        """Remove a series from auto-monitoring."""
        res = await self.monitored_series.delete_one({"series_slug": series_slug})
        return res.deleted_count > 0

    async def list_monitored_series(self) -> list[dict]:
        """List all series in auto-monitoring watchlist."""
        return await self.monitored_series.find({"enabled": True}).to_list(length=200)

    async def update_monitored_series_check(self, series_slug: str, episode_count: int):
        """Update last check time and episode count for a monitored series."""
        await self.monitored_series.update_one(
            {"series_slug": series_slug},
            {"$set": {
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "last_episode_count": episode_count,
            }}
        )

    async def ping_database(self) -> float:
        """Ping MongoDB and return round-trip latency in milliseconds."""
        import time
        start = time.perf_counter()
        await self.db.command("ping")
        return round((time.perf_counter() - start) * 1000, 2)

    def close(self):
        """Close the MongoDB connection."""
        self.client.close()


# Singleton — set during post_init
db: Database | None = None
