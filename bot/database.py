"""MongoDB integration using motor (async driver)."""

from __future__ import annotations
import logging
import re
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger(__name__)


def _sanitize_upsert(set_doc: dict, set_on_insert: dict) -> tuple[dict, dict]:
    """V3 #1: MongoDB rejects an upsert when the same path appears in both
    $set and $setOnInsert ("Updating the path ... would create a conflict").
    Strip any overlap from $setOnInsert — $set already covers the path on
    both insert and update. Returns the (possibly mutated) pair."""
    for k in list(set_on_insert.keys()):
        if k in set_doc:
            set_on_insert.pop(k, None)
    return set_doc, set_on_insert


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
                # V2 #19: strict 4K — only true UHD satisfies a 4K request.
                # 1080p HQ tiers must NOT satisfy 4K; they trigger fallback instead.
                q_condition = {
                    "$in": [
                        "4K", "4k", "2160p", "2160P", "2160", "UHD",
                        "4K UHD", "2160p UHD",
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
            set_doc = {
                "token": token,
                "username": username.lstrip("@"),
                "bot_id": bot_id,
                "first_name": first_name,
                "quality": quality.strip().lower(),
                "is_active": True,
                "updated_at": now,
            }
            set_on_insert = {
                "created_at": now,
                "files_served": 0,
            }
            _sanitize_upsert(set_doc, set_on_insert)  # V3 #1 audit
            await self.child_bots.update_one(
                {"bot_id": bot_id},
                {"$set": set_doc, "$setOnInsert": set_on_insert},
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

    async def get_channel_mapping(
        self,
        series_slug: str,
        language: str | None = None,
        is_movie: bool = False,
        title: str = "",
    ) -> dict | None:
        """
        Get mapped channel information for a series slug, with optional language-specific routing.
        If is_movie is True or no direct mapping exists for a movie, intelligently resolves
        to the parent anime series channel mapping (Issue #12).
        """
        try:
            doc = await self.channel_mappings.find_one({"series_slug": series_slug})

            # Intelligent Series Season & Movie -> Existing Anime Channel Routing (Issues #12 & #16)
            if not doc:
                import re
                candidate_slugs = []
                clean_s = series_slug
                # Iteratively strip season, part, cour, audio, and movie suffixes
                for _ in range(3):
                    prev = clean_s
                    clean_s = re.sub(
                        r"-(?:season-\d+|s\d+|part-\d+|cour-\d+|hindi|english|japanese|multi|dubbed|subbed|the-movie|movie|film|the-final-chapter|final-season|0)$",
                        "",
                        clean_s,
                        flags=re.IGNORECASE,
                    ).strip("-")
                    clean_s = re.sub(
                        r"-(?:mugen-train|shippuden-the-movie|super-hero|red|stampede|gold|resurrection|battle-of-gods|zero).*$",
                        "",
                        clean_s,
                        flags=re.IGNORECASE,
                    ).strip("-")
                    if clean_s == prev:
                        break
                if clean_s and clean_s != series_slug and clean_s not in candidate_slugs:
                    candidate_slugs.append(clean_s)

                if title:
                    from utils.anilist import clean_anime_title
                    cands = clean_anime_title(title)
                    for c in cands:
                        slug_c = re.sub(r"[^\w\s-]", "", c.lower())
                        slug_c = re.sub(r"[\s_]+", "-", slug_c).strip("-")
                        if slug_c and slug_c not in candidate_slugs and slug_c != series_slug:
                            candidate_slugs.append(slug_c)

                # 1. Direct candidate slug match
                for cand in candidate_slugs:
                    doc = await self.channel_mappings.find_one({"series_slug": cand})
                    if doc:
                        log.info("Series/Movie '%s' successfully routed to mapped channel '%s' (ID: %s)", series_slug, cand, doc.get("channel_id"))
                        break

                # 2. Case-insensitive title match
                if not doc and title:
                    clean_t = re.sub(r"(?i)\s*(?:season\s*\d+|s\d+|part\s*\d+|cour\s*\d+|hindi|dubbed|subbed|movie|film).*$", "", title).strip()
                    if clean_t:
                        doc = await self.channel_mappings.find_one({"series_title": {"$regex": f"^{re.escape(clean_t)}$", "$options": "i"}})
                        if doc:
                            log.info("Series '%s' routed via title '%s' to channel (ID: %s)", series_slug, clean_t, doc.get("channel_id"))

                # 3. Fuzzy prefix match
                if not doc:
                    for cand in candidate_slugs:
                        if "-" in cand:
                            prefix = cand.split("-", 2)[:2]
                            prefix_pattern = f"^{'-'.join(prefix)}"
                            doc = await self.channel_mappings.find_one({"series_slug": {"$regex": prefix_pattern, "$options": "i"}})
                            if doc:
                                log.info("Series/Movie '%s' routed to channel via prefix '%s' (ID: %s)", series_slug, doc.get("series_slug"), doc.get("channel_id"))
                                break

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
            # V2 #18: atomic upsert — no find_one→insert_one race.
            # Two concurrent creators can no longer duplicate-insert on
            # unique series_slug; the second becomes an update instead.
            try:
                set_doc: dict = {
                    f"language_routes.{lang_clean}": route_entry,
                    "updated_at": now,
                }
                if series_title:
                    set_doc["series_title"] = series_title
                if poster_url:
                    set_doc["poster_url"] = poster_url
                set_on_insert: dict = {
                    "series_slug": series_slug,
                    "channel_id": channel_id,
                    "invite_link": invite_link,
                    "language": lang_clean,
                    "auto_created": auto_created,
                    "created_by": created_by,
                    "created_at": now,
                }
                # V3 #1: same path must never appear in both $set and
                # $setOnInsert — MongoDB rejects the upsert with
                # "Updating the path ... would create a conflict".
                # $set already covers series_title/poster_url on insert,
                # so strip any overlap from $setOnInsert defensively.
                _sanitize_upsert(set_doc, set_on_insert)
                await self.channel_mappings.update_one(
                    {"series_slug": series_slug},
                    {
                        "$set": set_doc,
                        "$setOnInsert": set_on_insert,
                    },
                    upsert=True,
                )
            except Exception as e:
                # DuplicateKey can still occur on a racing insert; fall back to update.
                if "duplicate" in str(e).lower() or "E11000" in str(e):
                    await self.channel_mappings.update_one(
                        {"series_slug": series_slug},
                        {"$set": {
                            f"language_routes.{lang_clean}": route_entry,
                            "updated_at": now,
                        }},
                    )
                else:
                    raise

            doc = await self.channel_mappings.find_one({"series_slug": series_slug})
            return doc or {}
        else:
            # V2 #18: atomic upsert for the non-language branch as well.
            # V3 #1: $set/$setOnInsert overlap audited — no shared paths.
            try:
                set_doc2: dict = {
                    "series_title": series_title or series_slug,
                    "channel_id": channel_id,
                    "invite_link": invite_link,
                    "auto_created": auto_created,
                    "created_by": created_by,
                    "updated_at": now,
                    **({"poster_url": poster_url} if poster_url else {}),
                }
                set_on_insert2: dict = {
                    "series_slug": series_slug,
                    "created_at": now,
                }
                _sanitize_upsert(set_doc2, set_on_insert2)
                await self.channel_mappings.update_one(
                    {"series_slug": series_slug},
                    {
                        "$set": set_doc2,
                        "$setOnInsert": set_on_insert2,
                    },
                    upsert=True,
                )
            except Exception as e:
                if "duplicate" in str(e).lower() or "E11000" in str(e):
                    await self.channel_mappings.update_one(
                        {"series_slug": series_slug},
                        {"$set": {
                            "channel_id": channel_id,
                            "invite_link": invite_link,
                            "updated_at": now,
                        }},
                    )
                else:
                    raise
            doc = await self.channel_mappings.find_one({"series_slug": series_slug})
            return doc or {}

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

    async def prune_old_data(
        self,
        downloads_days: int = 30,
        errors_days: int = 14,
        max_downloads: int = 5000,
    ) -> dict:
        """Issue #27: Periodic MongoDB storage cleanup.

        Keeps main data (library, files, users, channel_mappings,
        monitored_series, config) untouched. Only prunes junk:
        - downloads older than ``downloads_days`` (ISO-string compare works
          because timestamps are UTC ISO8601) + hard cap ``max_downloads``
        - download_errors older than ``errors_days`` (200/150 cap stays in
          log_download_failure as well)
        Returns {"downloads_deleted": N, "errors_deleted": M}.
        """
        from datetime import timedelta
        stats = {"downloads_deleted": 0, "errors_deleted": 0}
        try:
            dl_cutoff = (datetime.now(timezone.utc) - timedelta(days=downloads_days)).isoformat()
            res = await self.downloads.delete_many({"timestamp": {"$lt": dl_cutoff}})
            stats["downloads_deleted"] += int(res.deleted_count or 0)

            # Hard cap: keep only the newest max_downloads rows
            total = await self.downloads.count_documents({})
            if total > max_downloads:
                oldest = (
                    await self.downloads.find()
                    .sort("timestamp", 1)
                    .limit(total - max_downloads)
                    .to_list(length=None)
                )
                if oldest:
                    ids = [d["_id"] for d in oldest]
                    res2 = await self.downloads.delete_many({"_id": {"$in": ids}})
                    stats["downloads_deleted"] += int(res2.deleted_count or 0)
        except Exception as e:
            log.warning("Downloads prune failed: %s", e)
        try:
            err_cutoff = (datetime.now(timezone.utc) - timedelta(days=errors_days)).isoformat()
            res = await self.download_errors.delete_many({"timestamp": {"$lt": err_cutoff}})
            stats["errors_deleted"] = int(res.deleted_count or 0)
        except Exception as e:
            log.warning("Download-errors prune failed: %s", e)
        if stats["downloads_deleted"] or stats["errors_deleted"]:
            log.info("MongoDB prune: %s", stats)
        return stats

    # ── Bot User Network & Broadcasting ────────────────────────────

    async def track_bot_user(self, user_id: int, username: str = "", first_name: str = ""):
        """Record user activity for user count and broadcasting across bot fleet."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            set_doc = {
                "username": username or "",
                "first_name": first_name or "",
                "last_seen": now,
            }
            set_on_insert = {"first_seen": now}
            _sanitize_upsert(set_doc, set_on_insert)  # V3 #1 audit
            await self.bot_users.update_one(
                {"user_id": user_id},
                {"$set": set_doc, "$setOnInsert": set_on_insert},
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
        val = await self.get_config("fsub_mod", default=None)
        if val is not None:
            if isinstance(val, str):
                return val.lower() in ("on", "true", "1", "yes")
            return bool(val)
        try:
            from config import Config
            return getattr(Config, "FSUB_MOD", True)
        except Exception:
            return True

    async def set_fsub_mod(self, enabled: bool):
        """Set FSub timer link mode ('on' or 'off')."""
        await self.set_config("fsub_mod", "on" if enabled else "off")

    async def is_admin(self, user_id: int) -> bool:
        """Check if user_id is bot owner or an admin."""
        if not user_id:
            return False
        from bot.auth import is_owner
        if is_owner(user_id):
            return True
        from config import Config
        admins = getattr(Config, "ADMINS", []) or []
        return user_id in admins

    async def get_fsub_channel(self) -> int | str | None:
        """Get configured FSub channel (defaults to FSUB_CHANNEL or settings.bot.main_channel)."""
        val = await self.get_config("fsub_channel", default=None)
        if val is not None:
            return int(val) if str(val).lstrip("-").isdigit() else str(val)
        try:
            from config import Config
            fsub = getattr(Config, "FSUB_CHANNEL", None)
            if fsub is not None:
                return int(fsub) if str(fsub).lstrip("-").isdigit() else str(fsub)
        except Exception:
            pass
        return settings.bot.main_channel or None

    async def set_fsub_channel(self, channel: int | str | None):
        """Set custom FSub channel."""
        await self.set_config("fsub_channel", channel)

    async def get_dlt_time(self) -> int:
        """Get file auto-delete time in seconds (default 600s = 10 mins; 0 = disabled)."""
        val = await self.get_config("dlt_time", default=None)
        if val is not None:
            try:
                return max(0, int(val))
            except Exception:
                pass
        try:
            from config import Config
            if hasattr(Config, "AUTO_DELETE_TIME"):
                return max(0, int(Config.AUTO_DELETE_TIME))
        except Exception:
            pass
        return 600

    async def set_dlt_time(self, seconds: int):
        """Set file auto-delete time in seconds (0 = disabled)."""
        await self.set_config("dlt_time", max(0, int(seconds)))

    # ── Main Channel (posts must never silently disappear) ───────────

    async def get_main_channel(self) -> int | None:
        """Main channel id: explicit DB config, else settings fallback.

        Previously schedule.py/admin.py called this method which never
        existed → AttributeError swallowed → schedule posts never sent.
        """
        try:
            mc = await self.get_config("main_channel", default=0)
            if mc and str(mc).lstrip("-").isdigit() and int(mc):
                return int(mc)
        except Exception:
            pass
        try:
            from config.settings import settings
            if settings and settings.bot and settings.bot.main_channel:
                return int(settings.bot.main_channel)
        except Exception:
            pass
        return None

    # ── Dump / Storage Channel (OFF by default unless configured) ────

    async def get_dump_channel(self) -> int | None:
        """Get dump/storage channel ID if configured, else fallback to settings/config."""
        val = await self.get_config("dump_channel", default=None)
        if val is not None and str(val).lstrip("-").isdigit():
            return int(val)
        try:
            from config.settings import settings
            if settings and settings.bot and settings.bot.dump_channel:
                return int(settings.bot.dump_channel)
        except Exception:
            pass
        try:
            from config import Config
            c_val = getattr(Config, "DUMP_CHANNEL", None)
            if c_val and str(c_val).lstrip("-").isdigit():
                return int(c_val)
        except Exception:
            pass
        return None

    async def set_dump_channel(self, channel_id: int | None):
        """Set or disable dump/storage channel (None disables it)."""
        if channel_id:
            await self.set_config("dump_channel", int(channel_id))
        else:
            await self.set_config("dump_channel", None)

    # ── Upload Mode Configuration (Default: 'video') ─────────────────

    async def get_upload_mode(self) -> str:
        """Get media upload mode ('video' or 'document'). Default is 'video'."""
        val = await self.get_config("upload_mode", default="video")
        return str(val).lower() if val in ("video", "document") else "video"

    async def set_upload_mode(self, mode: str):
        """Set media upload mode ('video' or 'document')."""
        clean_mode = "document" if mode.strip().lower() == "document" else "video"
        await self.set_config("upload_mode", clean_mode)

    # ── Channel Monitor Controls ─────────────────────────────────────

    async def get_monitored_channel_status(self, channel_id: int) -> bool:
        """Check if monitoring is enabled for a specific channel (defaults to True)."""
        key = f"monitor_chan_{channel_id}"
        val = await self.get_config(key, default=None)
        if val is not None:
            return bool(val)
        return True

    async def set_monitored_channel_status(self, channel_id: int, enabled: bool):
        """Enable or disable monitoring for a specific channel."""
        key = f"monitor_chan_{channel_id}"
        await self.set_config(key, bool(enabled))

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

    # ── Post Style Configuration (V3 #7: modern-only, classic retired) ──

    async def get_post_style(self) -> str:
        """V3 #7: modern-only. Always returns 'modern'; legacy 'classic' auto-migrates."""
        try:
            val = await self.get_config("post_style", default="modern")
            if str(val).lower() == "classic":
                await self.set_config("post_style", "modern")
        except Exception:
            pass
        return "modern"

    async def set_post_style(self, style: str):
        """V3 #7: classic retired — any value persists as 'modern'."""
        await self.set_config("post_style", "modern")

    # ── Start Style & Banner Configuration (V3 #7: modern-only) ──────

    async def get_start_style(self) -> str:
        """V3 #7: modern-only. Always returns 'modern'."""
        try:
            val = await self.get_config("start_style", default="modern")
            if str(val).lower() == "classic":
                await self.set_config("start_style", "modern")
        except Exception:
            pass
        return "modern"

    async def set_start_style(self, style: str):
        """V3 #7: classic retired — any value persists as 'modern'."""
        await self.set_config("start_style", "modern")

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

    async def get_enable_custom_emoji(self) -> bool:
        """Check if Telegram Premium Custom Emojis are enabled."""
        from config import Config
        def_val = getattr(Config, "ENABLE_CUSTOM_EMOJI", False)
        val = await self.get_config("enable_custom_emoji", default="on" if def_val else "off")
        if isinstance(val, str):
            return val.lower() in ("on", "true", "1", "yes")
        return bool(val)

    async def set_enable_custom_emoji(self, enabled: bool):
        """Set Telegram Premium Custom Emojis toggle."""
        await self.set_config("enable_custom_emoji", "on" if enabled else "off")
        try:
            from bot.emojis import set_custom_emoji_runtime_state
            set_custom_emoji_runtime_state(enabled)
        except Exception:
            pass

    async def get_thumb_template(self) -> str:
        """Get selected thumbnail template (modern, cinematic, movie_gold, neon_cyber, minimal, random)."""
        from config import Config
        def_val = getattr(Config, "THUMB_TEMPLATE", "modern")
        val = await self.get_config("thumb_template", default=def_val)
        return str(val).lower() if val else "modern"

    async def set_thumb_template(self, template: str):
        """Set selected thumbnail template."""
        await self.set_config("thumb_template", template.strip().lower())

    async def get_random_thumb_template(self) -> bool:
        """Check if random thumbnail template selection mode is enabled."""
        from config import Config
        def_val = getattr(Config, "RANDOM_THUMB_TEMPLATE", False)
        val = await self.get_config("random_thumb_template", default="on" if def_val else "off")
        if isinstance(val, str):
            return val.lower() in ("on", "true", "1", "yes")
        return bool(val)

    async def set_random_thumb_template(self, enabled: bool):
        """Set random thumbnail template selection mode."""
        await self.set_config("random_thumb_template", "on" if enabled else "off")

    async def get_ongoing_channel(self) -> int | None:
        """Get ongoing updates channel ID."""
        from config import Config
        def_val = getattr(Config, "ONGOING_CHANNEL", None)
        val = await self.get_config("ongoing_channel", default=def_val)
        try:
            return int(val) if val else None
        except (ValueError, TypeError):
            return None

    async def set_ongoing_channel(self, channel_id: int | None):
        """Set ongoing updates channel ID."""
        await self.set_config("ongoing_channel", channel_id)


    # ── Episode Post Style Configuration (V3 #7: modern-only) ────────

    async def get_ep_style(self) -> str:
        """V3 #7: modern-only. Always returns 'modern'."""
        try:
            val = await self.get_config("ep_style", default="modern")
            if str(val).lower() == "classic":
                await self.set_config("ep_style", "modern")
        except Exception:
            pass
        return "modern"

    async def set_ep_style(self, style: str):
        """V3 #7: classic retired — any value persists as 'modern'."""
        await self.set_config("ep_style", "modern")

    # ── Schedule Style Configuration (V3 #7: modern-only) ────────────

    async def get_sched_style(self) -> str:
        """V3 #7: modern-only. Always returns 'modern'."""
        try:
            val = await self.get_config("sched_style", default="modern")
            if str(val).lower() == "classic":
                await self.set_config("sched_style", "modern")
        except Exception:
            pass
        return "modern"

    async def set_sched_style(self, style: str):
        """V3 #7: classic retired — any value persists as 'modern'."""
        await self.set_config("sched_style", "modern")

    async def migrate_classic_styles_to_modern(self) -> dict:
        """V3 #7: migrate any stored 'classic' style values to 'modern'."""
        migrated = []
        for key in ("post_style", "start_style", "ep_style", "sched_style"):
            try:
                val = await self.get_config(key, default="modern")
                if str(val or "").lower() == "classic":
                    await self.set_config(key, "modern")
                    migrated.append(key)
            except Exception:
                continue
        return {"migrated": migrated}

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
