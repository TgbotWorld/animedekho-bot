"""Automatic Episode Monitor Service — background watcher for new episode releases (OFF by default)."""

from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from bot.telegram import Client, enums
from config.settings import settings

log = logging.getLogger(__name__)


class EpisodeMonitorService:
    """Monitors anime series for new episode releases and uploads them automatically to mapped channels."""

    def __init__(self, client: Client | None = None):
        self.client = client
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run_time: float | None = None

    def start(self, client: Client | None = None):
        """Start background monitoring loop."""
        if client:
            self.client = client
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._monitor_loop())
            log.info("EpisodeMonitorService: Background watcher initialized (OFF by default).")

    async def stop(self):
        """Stop background monitoring loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("EpisodeMonitorService: Stopped.")

    async def _monitor_loop(self):
        """Main periodic loop."""
        # Initial sleep on boot
        await asyncio.sleep(30)

        while self._running:
            try:
                from bot.database import db
                if db:
                    enabled = await db.get_auto_monitor_enabled()
                    if enabled:
                        log.info("EpisodeMonitorService: Starting check cycle...")
                        await self.run_check_cycle()
                        self._last_run_time = time.time()
                    else:
                        log.debug("EpisodeMonitorService: Auto-monitor is OFF by default. Skipping cycle.")

                    interval_mins = await db.get_auto_monitor_interval()
                    sleep_seconds = max(300, interval_mins * 60)
                else:
                    sleep_seconds = 300
            except Exception as e:
                log.warning("EpisodeMonitorService loop error: %s", e)
                sleep_seconds = 300

            await asyncio.sleep(sleep_seconds)

    async def run_check_cycle(self) -> dict[str, Any]:
        """Execute a full check cycle across all monitored series."""
        from bot.database import db
        if not db or not self.client:
            return {"checked": 0, "new_episodes": 0, "errors": 0}

        # 1. Gather series to check: either dedicated watchlist or all mapped channels
        monitored = await db.list_monitored_series()
        if not monitored:
            mapped_channels = await db.list_channel_mappings()
            series_list = [
                {
                    "series_slug": m["series_slug"],
                    "series_title": m.get("series_title", m["series_slug"]),
                    "channel_id": m["channel_id"],
                    "quality": "",
                }
                for m in mapped_channels if m.get("channel_id")
            ]
        else:
            series_list = monitored

        if not series_list:
            log.info("EpisodeMonitorService: No series registered to monitor.")
            return {"checked": 0, "new_episodes": 0, "errors": 0}

        default_quality = await db.get_auto_monitor_quality()
        checked_count = 0
        new_episodes_count = 0
        errors_count = 0

        for item in series_list:
            slug = item["series_slug"]
            chan_id = item.get("channel_id")
            q_target = item.get("quality") or default_quality

            try:
                found_new = await self._check_single_series(slug, chan_id, q_target)
                checked_count += 1
                new_episodes_count += found_new
            except Exception as se:
                log.warning("EpisodeMonitorService error checking %s: %s", slug, se)
                errors_count += 1

            # Friendly rate-limiting pause between series
            await asyncio.sleep(3)

        return {
            "checked": checked_count,
            "new_episodes": new_episodes_count,
            "errors": errors_count,
        }

    async def _check_single_series(self, series_slug: str, channel_id: int | None, quality: str) -> int:
        """Check a single series for newly released episodes and process upload."""
        from bot.database import db
        from api.client import api_client

        if channel_id is not None and db:
            is_monitored = await db.get_monitored_channel_status(channel_id)
            if is_monitored is False:
                log.debug("EpisodeMonitorService: Channel %s is disabled for monitoring. Skipping %s.", channel_id, series_slug)
                return 0

        series_data = await api_client.get_series(series_slug)
        if not series_data or not series_data.seasons:
            return 0

        # Calculate total episodes currently available on site
        all_episodes = []
        for s in series_data.seasons.values():
            for ep in s.episodes:
                all_episodes.append((s.number, ep))

        # Update last known count in DB
        await db.update_monitored_series_check(series_slug, len(all_episodes))

        # Check which episodes are missing from DB cache
        new_uploaded = 0
        for s_num, ep in all_episodes:
            ep_key = f"S{s_num:01d}E{ep.number:02d}"
            cached = await db.get_cached_file(series_slug, quality, ep_key)
            if not cached:
                log.info("EpisodeMonitorService: Detected new episode %s %s! Processing auto-upload...", series_slug, ep_key)
                uploaded = await self._download_and_post_episode(series_data, s_num, ep, quality, channel_id)
                if uploaded:
                    new_uploaded += 1

        return new_uploaded

    async def _download_and_post_episode(
        self, series, season_num: int, ep, quality: str, channel_id: int | None
    ) -> bool:
        """Fetch stream, download, and upload episode automatically to channel."""
        from api.client import api_client
        from bot.downloader import download_and_upload, make_episode_filename
        from bot.database import db

        dest_chan = channel_id or settings.bot.main_channel
        if not dest_chan:
            log.warning("No destination channel for auto-monitor upload of %s", series.title)
            return False

        # Route notices to Dump Channel (fallback to owner if not set) - Issue #12
        dump_chan = await db.get_dump_channel() if db else None
        notify_chat = dump_chan if dump_chan else settings.bot.owner_id

        try:
            # 1. Resolve episode stream
            episode_detail = await api_client.get_episode(ep.slug)
            if not episode_detail or not episode_detail.servers:
                log.warning("No servers found for %s %s", series.title, ep.slug)
                return False

            best_quality = None
            best_stream_url = ""
            for srv in episode_detail.servers:
                for q in srv.qualities:
                    if q.resolution.lower() == quality.lower() and q.url:
                        best_quality = q
                        best_stream_url = q.url
                        break
                if best_quality:
                    break

            if not best_quality:
                # Fall back to first available quality
                for srv in episode_detail.servers:
                    if srv.qualities and srv.qualities[0].url:
                        best_quality = srv.qualities[0]
                        best_stream_url = best_quality.url
                        break

            if not best_stream_url:
                log.warning("No stream URL for %s %s", series.title, ep.slug)
                return False

            # 2. Progress notice sent to Dump Channel (or owner)
            progress_msg = None
            if notify_chat and self.client:
                try:
                    progress_msg = await self.client.send_message(
                        chat_id=notify_chat,
                        text=f"🔄 <b>Auto-Monitor Download Started:</b>\n{series.title} S{season_num}E{ep.number} [{best_quality.resolution}]",
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception as ne:
                    log.warning("Could not send progress notice to %s: %s", notify_chat, ne)

            filename = make_episode_filename(series.title, season_num, ep.number, best_quality.resolution)

            # Retry loop (2-3 retries) on failure before giving up - Issue #12
            max_retries = 3
            success = False
            sent_msg = None
            for attempt in range(1, max_retries + 1):
                try:
                    success, sent_msg = await download_and_upload(
                        chat_id=notify_chat or settings.bot.owner_id,
                        stream_url=best_stream_url,
                        quality=best_quality.resolution,
                        filename=filename,
                        title=f"{series.title} S{season_num}E{ep.number}",
                        progress_msg=progress_msg,
                        client=self.client,
                        poster_url=series.poster,
                        destination_channel_id=dest_chan,
                        series_slug=series.slug,
                    )
                    if success and sent_msg:
                        break
                    log.warning("Auto-Monitor download attempt %d/%d failed for %s", attempt, max_retries, filename)
                    await asyncio.sleep(5)
                except Exception as attempt_err:
                    log.warning("Auto-Monitor download attempt %d/%d error for %s: %s", attempt, max_retries, filename, attempt_err)
                    await asyncio.sleep(5)

            if success and sent_msg:
                # Delete download notice upon success - Issue #12
                if progress_msg:
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass

                # Cache file
                fid = sent_msg.video.file_id if sent_msg.video else (sent_msg.document.file_id if sent_msg.document else "")
                f_uid = sent_msg.video.file_unique_id if sent_msg.video else (sent_msg.document.file_unique_id if sent_msg.document else "")
                f_size = (sent_msg.video.file_size if sent_msg.video else (sent_msg.document.file_size if sent_msg.document else None)) if sent_msg else None
                ep_key = f"S{season_num:01d}E{ep.number:02d}"
                await db.save_file(
                    series_slug=series.slug,
                    series_title=series.title,
                    quality=best_quality.resolution,
                    episode_key=ep_key,
                    file_id=fid,
                    file_unique_id=f_uid,
                    storage_channel_id=dest_chan,
                    storage_message_id=sent_msg.id,
                    file_size=f_size,
                )

                # Update main channel album
                from bot.library import library_manager
                if library_manager:
                    try:
                        await library_manager.update_album_for_series(
                            series.slug,
                            series.title,
                            series.poster,
                            new_episode_key=ep_key,
                            quality=best_quality.resolution,
                        )
                    except Exception as le:
                        log.warning("Failed updating library album in monitor: %s", le)

                # Log completion to Dump Channel (not owner DM) or log channel
                if dump_chan and self.client:
                    try:
                        await self.client.send_message(
                            chat_id=dump_chan,
                            text=(
                                f"🤖 <b>Auto-Monitor New Episode:</b>\n"
                                f"📺 <b>{series.title}</b> S{season_num}E{ep.number} [{best_quality.resolution}]\n"
                                f"📢 Uploaded to channel: <code>{dest_chan}</code>"
                            ),
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception as de:
                        log.warning("Failed sending monitor completion to dump channel: %s", de)
                elif bot_logger := getattr(settings, "bot_logger", None):
                    await bot_logger.log_info(
                        f"🤖 <b>Auto-Monitor New Episode:</b>\n"
                        f"📺 {series.title} S{season_num}E{ep.number} [{best_quality.resolution}]\n"
                        f"📢 Uploaded to channel: <code>{dest_chan}</code>"
                    )
                return True
            else:
                # Delete progress notice on final failure
                if progress_msg:
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass

                # Send failure notice and auto-delete after 120 hours - Issue #12
                if notify_chat and self.client:
                    try:
                        from bot.auto_delete import auto_delete_service
                        fail_msg = await self.client.send_message(
                            chat_id=notify_chat,
                            text=(
                                f"⚠️ <b>Auto-Monitor Download Failed:</b>\n"
                                f"📺 {series.title} S{season_num}E{ep.number} [{best_quality.resolution}]\n"
                                f"❌ Failed after {max_retries} attempts.\n"
                                f"<i>(This notice will auto-delete in 120 hours)</i>"
                            ),
                            parse_mode=enums.ParseMode.HTML,
                        )
                        if fail_msg and auto_delete_service and self.client:
                            await auto_delete_service.schedule_deletion(
                                client=self.client,
                                chat_id=fail_msg.chat.id,
                                message_id=fail_msg.id,
                                custom_seconds=120 * 3600,
                            )
                    except Exception as fe:
                        log.warning("Failed sending monitor failure notice: %s", fe)
                return False

        except Exception as e:
            log.error("Failed auto-uploading episode %s: %s", ep.slug, e)

        return False


monitor_service = EpisodeMonitorService()
