"""Shared utility functions."""

from __future__ import annotations
import html as htmlmod
import os
import re

from config.settings import settings


def esc(text: str) -> str:
    return htmlmod.escape(text)


def resolve_photo_source(pic: str):
    """Turn a configured picture into something Telegram accepts.

    ``START_PIC`` / ``FSUB_PIC`` may be a ``file_id``, an http(s) URL *or* a
    local path in ``config.py`` — only the first two are accepted by Telegram,
    so a local path is opened for upload (issue #35).
    """
    if not pic:
        return pic
    try:
        if os.path.isfile(pic):
            return open(pic, "rb")
    except Exception:
        pass
    return pic


def truncate(text: str, maxlen: int = 400) -> str:
    return text if len(text) <= maxlen else text[:maxlen - 1] + "…"


def short_slug(slug: str, maxlen: int | None = None) -> str:
    return slug[: maxlen or settings.bot.slug_max_len]


def clean_title(raw: str) -> str:
    for sep in ("–", "|", " - AnimeDekho", " - Watch"):
        raw = raw.split(sep)[0]
    return raw.strip()


def slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").title()


def extract_series_slug(ep_slug: str) -> str | None:
    if not ep_slug:
        return None
    if ep_slug.startswith("e_"):
        try:
            from bot.slug_store import resolve_slug
            resolved = resolve_slug(ep_slug)
            if resolved and resolved != ep_slug:
                ep_slug = resolved
        except Exception:
            pass
    m = re.match(r"^(.+)-\d+x\d+$", ep_slug)
    if m:
        return m.group(1)
    m2 = re.match(r"^(.+)-(?:season-\d+-)?episode-\d+$", ep_slug, re.IGNORECASE)
    if m2:
        return m2.group(1)
    m3 = re.match(r"^(.+)-s\d+e\d+$", ep_slug, re.IGNORECASE)
    if m3:
        return m3.group(1)
    m4 = re.match(r"^(.+)-\d+x\d+", ep_slug)
    if m4:
        return m4.group(1)
    return None


import base64


def encode_file_param(param: str) -> str:
    """Encode a deep link parameter (e.g. 'get_series_720p_S1E01') to a secure URL-safe base64 string."""
    if not param:
        return ""
    return base64.urlsafe_b64encode(param.encode("utf-8")).decode("utf-8").rstrip("=")


#: Deep-link parameter families this bot accepts (file requests, series-channel
#: joins, and issue #33's gated-download links).
_PARAM_PREFIXES = ("get_", "join_", "dl_")


def decode_file_param(param: str) -> str:
    """Decode a base64 URL-safe parameter. Returns original if already raw or if decoding fails."""
    if not param:
        return ""
    if param.startswith(_PARAM_PREFIXES):
        return param
    try:
        padded = param + "=" * ((4 - len(param) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        if decoded.startswith(_PARAM_PREFIXES):
            return decoded
    except Exception:
        pass
    return param

