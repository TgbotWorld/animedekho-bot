"""Small, shared formatters for user-facing Telegram media captions."""

from __future__ import annotations

import html
import re


_AUDIO_HINT_RE = re.compile(
    r"\b(?:hindi|tamil|telugu|malayalam|bengali|english|japanese|"
    r"multi\s*audio|dub(?:bed)?|sub(?:bed)?)\b",
    re.IGNORECASE,
)
_EPISODE_KEY_RE = re.compile(r"^(?:S|SEASON)\s*(\d+)\s*E(?:P(?:ISODE)?)?\s*(\d+)$", re.IGNORECASE)


def format_episode_post_caption(
    title: str,
    quality: str,
    episode_key: str = "",
    *,
    is_movie: bool = False,
    media_icon: str | None = None,
) -> str:
    """Format an individual download as a compact, readable Telegram caption.

    A trailing language label such as ``(Hindi Dubbed)`` is moved out of the
    title and displayed alongside quality, so the episode identity is easier
    to scan beneath its thumbnail.
    """
    title_text = " ".join(str(title or "Anime episode").split()).strip()
    audio = ""
    suffix = re.search(r"\s*[\(\[]([^\)\]]{2,48})[\)\]]\s*$", title_text)
    if suffix:
        candidate = " ".join(suffix.group(1).split())
        if _AUDIO_HINT_RE.search(candidate):
            audio = candidate
            title_text = title_text[:suffix.start()].strip()

    raw_episode = " ".join(str(episode_key or "").split()).strip()
    episode_match = _EPISODE_KEY_RE.fullmatch(raw_episode.replace(" ", ""))
    if is_movie or raw_episode.lower() == "movie":
        episode_label = "FEATURE FILM"
        is_movie = True
    elif episode_match:
        season, episode = (int(value) for value in episode_match.groups())
        episode_label = f"S{season:02d} · EP {episode:02d}"
    else:
        episode_label = raw_episode.upper()

    quality_label = " ".join(str(quality or "HD").split()).upper()
    icon = media_icon or ("🎬" if is_movie else "📺")
    lines = [f"{icon} <b>{html.escape(title_text, quote=False)}</b>"]

    detail_items = []
    if audio:
        detail_items.append(f"🌐 <b>{html.escape(audio, quote=False)}</b>")
    if quality_label:
        detail_items.append(f"🎞 <b>{html.escape(quality_label, quote=False)}</b>")
    if detail_items:
        lines.append("  ·  ".join(detail_items))
    if episode_label:
        lines.append(f"📺 <code>{html.escape(episode_label, quote=False)}</code>")
    return "\n".join(lines)
