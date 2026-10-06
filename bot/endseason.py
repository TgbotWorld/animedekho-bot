"""END OF SEASON sticker (issue #33, optional).

The owner installs a sticker with ``/endsticker`` (reply to a sticker). When a
season's batch download finishes completely, that sticker is posted to the
series' destination channel as the closing "END OF SEASON" card.

Nothing is configured ⇒ nothing is ever posted, so deployments that never run
``/endsticker`` behave exactly as before.
"""

from __future__ import annotations

import logging

from bot.telegram import Client

log = logging.getLogger(__name__)

#: DB config key shared with ``cmd_endsticker`` in bot/handlers/admin.py.
EOS_STICKER_KEY = "end_of_season_sticker"


async def get_end_of_season_sticker() -> str:
    """Stored sticker file_id, or ``""`` when unset/disabled/unavailable."""
    from bot.database import db
    if not db:
        return ""
    try:
        return str(await db.get_config(EOS_STICKER_KEY, default="") or "").strip()
    except Exception as e:
        log.debug("END OF SEASON sticker lookup failed: %s", e)
        return ""


async def post_end_of_season(
    client: Client,
    dest_channel_id: int | None,
    series_title: str,
    season: int,
) -> bool:
    """Post the END OF SEASON sticker. Returns True when one went out.

    Silently returns False when no sticker is set (the default) or the channel
    rejects it — a sticker must never surface as a batch error.
    """
    sticker = await get_end_of_season_sticker()
    if not sticker:
        return False
    if not dest_channel_id:
        log.debug("END OF SEASON skipped for '%s': no destination channel", series_title)
        return False
    try:
        await client.send_sticker(chat_id=dest_channel_id, sticker=sticker)
        log.info("END OF SEASON sticker posted for '%s' S%02d", series_title, season)
        return True
    except Exception as e:
        log.warning("END OF SEASON sticker failed for '%s' on %s: %s",
                    series_title, dest_channel_id, e)
        return False
