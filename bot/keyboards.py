"""Inline keyboard builders — keeps handlers clean."""

from __future__ import annotations
from bot.telegram.types import InlineKeyboardButton, InlineKeyboardMarkup

from api.models import (
    SearchResult, Series, Season, Episode, VideoServer,
    Category, PaginatedResult, Quality,
)
from config.settings import settings
from utils.helpers import short_slug

S = settings.bot


def _safe_cb(data: str) -> str:
    """Ensure callback_data is within Telegram's 64-byte limit."""
    encoded = data.encode("utf-8")
    if len(encoded) <= 64:
        return data
    return data[:64]


def _safe_url_btn(label: str, url: str) -> InlineKeyboardButton | None:
    """Create a URL button only if the URL is valid. Returns None if invalid."""
    if url and url.startswith("https://"):
        return InlineKeyboardButton(label, url=url)
    return None


def main_menu(invite_link: str | None = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📺 Recent Series", callback_data="rp:1")],
        [InlineKeyboardButton("📂 Browse Genres", callback_data="m:genres")],
    ]
    if invite_link:
        buttons.append([InlineKeyboardButton("📢 Join the Channel ↗", url=invite_link)])
    return InlineKeyboardMarkup(buttons)


def search_results(results: list[SearchResult]) -> InlineKeyboardMarkup:
    buttons = []
    for r in results[: S.max_search_results]:
        prefix = "sr" if r.is_series else "mr"
        emoji = "📺" if r.is_series else "🎬"
        cb = _safe_cb(f"{prefix}:{short_slug(r.slug)}")
        buttons.append([InlineKeyboardButton(f"{emoji} {r.title[:45]}", callback_data=cb)])
    buttons.append([_menu_btn()])
    return InlineKeyboardMarkup(buttons)


def listing_page(
    items: list[SearchResult],
    page: int,
    max_page: int,
    prefix: str,
    item_prefix: str,
    emoji: str,
) -> InlineKeyboardMarkup:
    buttons = []
    for it in items[: S.items_per_page]:
        cb = _safe_cb(f"{item_prefix}:{short_slug(it.slug)}")
        buttons.append([InlineKeyboardButton(f"{emoji} {it.title[:45]}", callback_data=cb)])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}:{page - 1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([_menu_btn()])
    return InlineKeyboardMarkup(buttons)


def season_picker(series: Series) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    ss = short_slug(series.slug, 30)
    for sn in sorted(series.seasons.keys()):
        ep_count = series.seasons[sn].episode_count
        cb = _safe_cb(f"se:{ss}:{sn}")
        row.append(InlineKeyboardButton(f"S{sn} ({ep_count}ep)", callback_data=cb))
        if len(row) >= S.seasons_per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Batch download buttons per season
    for sn in sorted(series.seasons.keys()):
        cb = _safe_cb(f"bat:{ss}:{sn}")
        buttons.append([InlineKeyboardButton(
            f"📥 Batch Download S{sn}", callback_data=cb
        )])
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="rp:1"),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def episode_picker(
    series_slug: str,
    season: int,
    episodes: list[Episode],
) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for ep in episodes:
        cb = _safe_cb(f"ep:{short_slug(ep.slug, 60)}")
        row.append(InlineKeyboardButton(f"Ep {ep.number}", callback_data=cb))
        if len(row) >= S.episodes_per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    ss = short_slug(series_slug, 30)
    cb = _safe_cb(f"bat:{ss}:{season}")
    buttons.append([InlineKeyboardButton(
        f"📥 Batch Download S{season}", callback_data=cb
    )])
    buttons.append([
        InlineKeyboardButton("🔙 Seasons", callback_data=_safe_cb(f"sr:{short_slug(series_slug)}")),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def batch_quality_picker(series_slug: str, season: int) -> InlineKeyboardMarkup:
    """Quality selection for batch download — uses default 3 qualities."""
    buttons = []
    ss = short_slug(series_slug, 28)
    for q in settings.site.default_qualities:  # ["480p", "720p", "1080p"]
        cb = _safe_cb(f"bq:{ss}:{season}:{q}")
        buttons.append([InlineKeyboardButton(f"📥 {q}", callback_data=cb)])
    buttons.append([
        InlineKeyboardButton("🔙 Cancel", callback_data=_safe_cb(f"se:{short_slug(series_slug, 30)}:{season}")),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def quality_picker(
    qualities: set[str],
    slug: str,
    back_cb: str,
    is_movie: bool = False,
) -> InlineKeyboardMarkup:
    """Show quality buttons. Marks available ones with ✅, unavailable with ⚡ (will use closest)."""
    buttons = []
    prefix = "mdl" if is_movie else "dl"
    ss = short_slug(slug)

    default_q = settings.site.default_qualities  # ["480p", "720p", "1080p"]

    # Normalize detected qualities for comparison
    detected = {q.lower() for q in qualities} if qualities else set()

    row: list[InlineKeyboardButton] = []
    for q in default_q:
        cb = _safe_cb(f"{prefix}:{q}:{ss}")
        available = q in detected or "auto" in detected
        if q == "1080p":
            label = f"{'📥' if available else '⚡'} 1080p (FHD)"
        elif q == "720p":
            label = f"{'📥' if available else '⚡'} 720p (HD)"
        elif q == "480p":
            label = f"{'📥' if available else '⚡'} 480p (SD)"
        elif q.lower() in ("4k", "2160p"):
            label = f"{'📥' if available else '✨'} 4K (UHD)"
        else:
            label = f"{'📥' if available else '⚡'} {q}"
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) >= 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=_safe_cb(back_cb)), _menu_btn()])
    return InlineKeyboardMarkup(buttons)


def genre_list(categories: list[Category]) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for c in categories:
        cb = _safe_cb(f"ct:{short_slug(c.slug, 50)}:1")
        row.append(InlineKeyboardButton(f"{c.name} ({c.count})", callback_data=cb))
        if len(row) >= 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([_menu_btn()])
    return InlineKeyboardMarkup(buttons)


def category_page(
    items: list[SearchResult],
    cat_slug: str,
    page: int,
    max_page: int,
) -> InlineKeyboardMarkup:
    buttons = []
    for it in items[: S.items_per_page]:
        prefix = "sr" if it.is_series else "mr"
        emoji = "📺" if it.is_series else "🎬"
        cb = _safe_cb(f"{prefix}:{short_slug(it.slug)}")
        buttons.append([InlineKeyboardButton(f"{emoji} {it.title[:45]}", callback_data=cb)])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=_safe_cb(f"ct:{short_slug(cat_slug, 50)}:{page - 1}")))
    if page < max_page:
        nav.append(InlineKeyboardButton("➡️", callback_data=_safe_cb(f"ct:{short_slug(cat_slug, 50)}:{page + 1}")))
    if nav:
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton("🔙 Genres", callback_data="m:genres"),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


# ── Helpers ───────────────────────────────────────────────────────

def _menu_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 Menu", callback_data="m:main")
