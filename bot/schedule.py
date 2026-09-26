"""Anime Airing Schedule System (Today, Weekly, Upcoming, Release Times & Artwork)."""

from __future__ import annotations
import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.telegram import enums
from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"

AIRING_SCHEDULE_QUERY = """
query ($airingAt_greater: Int, $airingAt_lesser: Int, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    airingSchedules(
      airingAt_greater: $airingAt_greater,
      airingAt_lesser: $airingAt_lesser,
      sort: TIME
    ) {
      id
      airingAt
      timeUntilAiring
      episode
      media {
        id
        idMal
        title {
          romaji
          english
          native
        }
        coverImage {
          extraLarge
          large
        }
        bannerImage
        genres
        status
        episodes
        duration
        averageScore
        countryOfOrigin
      }
    }
  }
}
"""

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ScheduleService:
    """Fetches, formats, and manages anime release schedules."""

    def __init__(self):
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._cache_ttl = 300  # 5 minutes cache

    async def fetch_schedule(
        self,
        start_ts: int,
        end_ts: int,
        page: int = 1,
        per_page: int = 25,
    ) -> list[dict]:
        """Fetch airing schedules within a timestamp window from AniList."""
        cache_key = f"{start_ts}_{end_ts}_{page}_{per_page}"
        now = time.time()
        if cache_key in self._cache:
            ts, cached_data = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return cached_data

        payload = {
            "query": AIRING_SCHEDULE_QUERY,
            "variables": {
                "airingAt_greater": start_ts,
                "airingAt_lesser": end_ts,
                "page": page,
                "perPage": per_page,
            },
        }

        try:
            from utils.http import http_client
            # If http_client is available, use it, else aiohttp
            if hasattr(http_client, "session") and http_client.session:
                async with http_client.session.post(
                    ANILIST_GRAPHQL_URL,
                    json=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "AnimeDekho/1.0"},
                    timeout=10,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])
                        self._cache[cache_key] = (now, schedules)
                        return schedules
            else:
                import aiohttp
                async with aiohttp.ClientSession() as s:
                    async with s.post(
                        ANILIST_GRAPHQL_URL,
                        json=payload,
                        headers={"Content-Type": "application/json", "User-Agent": "AnimeDekho/1.0"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])
                            self._cache[cache_key] = (now, schedules)
                            return schedules
        except Exception as e:
            log.warning("Failed to fetch schedule from AniList: %s", e)

        return []

    async def fetch_animedubhindi_schedule(self) -> list[dict]:
        """Fetch Hindi dubbed anime schedule directly from animedubhindi.link/schedule.php."""
        def _scrape():
            try:
                import cloudscraper
                from bs4 import BeautifulSoup

                scraper = cloudscraper.create_scraper()
                resp = scraper.get("https://www.animedubhindi.link/schedule.php", timeout=8)
                if resp.status_code != 200:
                    return []

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select(".card")
                results = []

                for c in cards:
                    h2 = c.select_one("h2")
                    title = h2.get_text(strip=True) if h2 else ""
                    if not title:
                        continue

                    poster_el = c.select_one("img.poster")
                    poster = poster_el.get("src", "") if poster_el else ""

                    meta_boxes = [m.get_text(strip=True) for m in c.select(".meta-box")]
                    season_str = meta_boxes[0] if len(meta_boxes) > 0 else "Season 1"
                    ep_str = meta_boxes[1] if len(meta_boxes) > 1 else "EP 01"

                    ep_num = 1
                    ep_match = re.search(r"\d+", ep_str)
                    if ep_match:
                        ep_num = int(ep_match.group(0))

                    date_el = c.select_one(".date-block")
                    date_str = date_el.get_text(strip=True) if date_el else "Today"

                    time_el = c.select_one(".time-block")
                    time_str = time_el.get_text(strip=True) if time_el else ""

                    langs = [l.get_text(strip=True) for l in c.select(".lang span") if l.get_text(strip=True)]
                    audio_str = ", ".join(langs) if langs else "Hindi Dub"

                    results.append({
                        "title": title,
                        "season": season_str,
                        "episode": ep_num,
                        "poster": poster,
                        "day": date_str,
                        "time": time_str,
                        "audio": audio_str,
                        "source": "animedubhindi",
                    })

                return results
            except Exception as e:
                log.warning("AnimeDubHindi schedule scrape error: %s", e)
                return []

        return await asyncio.to_thread(_scrape)


    async def get_today_schedule(self) -> list[dict]:
        """Get anime schedule for today (UTC/IST midnight to midnight)."""
        now = int(time.time())
        # Align with start of day in UTC
        start_of_day = now - (now % 86400)
        end_of_day = start_of_day + 86400
        return await self.fetch_schedule(start_of_day, end_of_day, per_page=30)

    async def get_day_schedule(self, day_index: int) -> list[dict]:
        """
        Get anime schedule for a specific day of the current week.
        day_index: 0=Monday, 6=Sunday.
        """
        now_dt = datetime.now(timezone.utc)
        current_weekday = now_dt.weekday()  # 0=Monday
        days_diff = day_index - current_weekday

        target_dt = (now_dt + timedelta(days=days_diff)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_ts = int(target_dt.timestamp())
        end_ts = start_ts + 86400
        return await self.fetch_schedule(start_ts, end_ts, per_page=30)

    async def get_upcoming_schedule(self, days: int = 30) -> list[dict]:
        """Get anime schedule airing in the next N days (default 30 days = 1 month)."""
        now = int(time.time())
        end_ts = now + (days * 86400)
        return await self.fetch_schedule(now, end_ts, per_page=50)

    @staticmethod
    def format_countdown(target_ts: int) -> str:
        """Format countdown to release time nicely."""
        now = int(time.time())
        diff = target_ts - now
        if diff <= 0:
            return "Aired / Available"
        hours = diff // 3600
        mins = (diff % 3600) // 60
        if hours > 24:
            days = hours // 24
            rem_h = hours % 24
            return f"in {days}d {rem_h}h"
        elif hours > 0:
            return f"in {hours}h {mins}m"
        else:
            return f"in {mins}m"

    @staticmethod
    def format_ist_time(target_ts: int) -> str:
        """Format timestamp as Indian Standard Time (IST, UTC+5:30)."""
        ist = timezone(timedelta(hours=5, minutes=30))
        dt = datetime.fromtimestamp(target_ts, tz=ist)
        return dt.strftime("%I:%M %p IST (%d %b)")


schedule_service = ScheduleService()


class AutoScheduleService:
    """Automated daily schedule publisher for main channel at 12:00 AM IST."""

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_posted_date: str = ""

    def start(self, client: Client):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(client))
        log.info("AutoScheduleService: Started (12:00 AM IST auto-poster loop).")

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("AutoScheduleService: Stopped.")

    async def _loop(self, client: Client):
        await asyncio.sleep(10)
        ist = timezone(timedelta(hours=5, minutes=30))

        while self._running:
            try:
                from bot.database import db
                from config.settings import settings

                if db and client:
                    enabled = await db.get_auto_schedule_post()
                    main_chan = settings.bot.main_channel

                    if enabled and main_chan:
                        now_ist = datetime.now(ist)
                        today_str = now_ist.strftime("%Y-%m-%d")

                        if now_ist.hour == 0 and now_ist.minute <= 5 and self._last_posted_date != today_str:
                            log.info("AutoScheduleService: Triggering 12:00 AM IST daily schedule post for %s", main_chan)
                            ok = await self.post_daily_schedule(client, main_chan)
                            if ok:
                                self._last_posted_date = today_str

            except Exception as e:
                log.warning("AutoScheduleService loop error: %s", e)

            await asyncio.sleep(60)

    async def post_daily_schedule(self, client: Client, channel_id: int) -> bool:
        """Fetch today's schedule and post/update it in the channel."""
        try:
            from bot.database import db
            from bot.handlers.schedule import _format_modern_schedule_text, _format_classic_schedule_text, _build_modern_schedule_markup, _build_schedule_menu_markup

            schedules = await schedule_service.get_today_schedule()
            sched_style = await db.get_sched_style() if db else "classic"

            if sched_style == "modern":
                text, total_pages = _format_modern_schedule_text(schedules, "today", 1)
                markup = _build_modern_schedule_markup("today", 1, total_pages)
            else:
                text, total_pages = _format_classic_schedule_text(schedules, "today", 1)
                markup = _build_schedule_menu_markup("today", 1, total_pages)

            await client.send_message(
                chat_id=channel_id,
                text=text,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=markup,
            )
            log.info("AutoScheduleService: Successfully posted daily schedule to %s", channel_id)
            return True
        except Exception as e:
            log.error("AutoScheduleService: Failed posting daily schedule: %s", e)
            return False


auto_schedule_service = AutoScheduleService()

