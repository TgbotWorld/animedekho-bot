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
    m = re.match(r"(.+)-\d+x\d+", ep_slug)
    return m.group(1) if m else None


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

