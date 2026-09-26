"""
Custom Emoji and Visual Styling Helpers.

Supports Telegram Premium Custom Emojis (HTML <emoji id="...">)
with automatic graceful fallback to standard Unicode emojis so free bots never break.
Addresses Issue #8 (Point 3: Custom Emoji Support).
"""

from __future__ import annotations
import os
from typing import Dict, Tuple

# Mapping of semantic emoji names to (custom_emoji_id, unicode_fallback)
# You can customize these emoji IDs using @PremiumemojiID_bot
EMOJI_REGISTRY: Dict[str, Tuple[str, str]] = {
    "fire": ("546546541234567890", "🔥"),
    "star": ("546546541234567891", "⭐"),
    "sparkles": ("546546541234567892", "✨"),
    "rocket": ("546546541234567893", "🚀"),
    "check": ("546546541234567894", "✅"),
    "cross": ("546546541234567895", "❌"),
    "tv": ("546546541234567896", "📺"),
    "clapper": ("546546541234567897", "🎬"),
    "download": ("546546541234567898", "📥"),
    "upload": ("546546541234567899", "📤"),
    "search": ("546546541234567900", "🔍"),
    "gear": ("546546541234567901", "⚙️"),
    "timer": ("546546541234567902", "⏳"),
    "lock": ("546546541234567903", "🔒"),
    "unlock": ("546546541234567904", "🔓"),
    "pin": ("546546541234567905", "📌"),
    "calendar": ("546546541234567906", "📅"),
    "audio": ("546546541234567907", "🎙️"),
}


def get_emoji(name: str, fallback: str = "") -> str:
    """
    Get formatted emoji. If custom emojis are enabled, outputs HTML <emoji id="...">
    otherwise returns standard unicode fallback.
    """
    enable_custom = os.environ.get("ENABLE_CUSTOM_EMOJI", "false").lower() in ("true", "1", "yes")

    entry = EMOJI_REGISTRY.get(name)
    if not entry:
        return fallback or "•"

    emoji_id, default_unicode = entry
    if enable_custom and emoji_id:
        return f'<emoji id="{emoji_id}">{fallback or default_unicode}</emoji>'
    return fallback or default_unicode
