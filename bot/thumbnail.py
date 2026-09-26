"""
Auto Thumbnail Generator for AnimeDekho Bot.

Automatically generates professional, high-definition 1280x720 (16:9) thumbnails
for anime episodes and movies using Pillow (PIL), combining:
- High-resolution poster / artwork (from AniList/TMDB)
- Blurred background with dark cinematic gradient
- Crisp foreground poster with rounded border & shadow
- Bold anime title typography
- Season & Episode badge (e.g. S01 • EP02)
- Audio and Quality badges (e.g. Hindi Dub • 1080p FHD)
- Bot watermark / branding (@AnimeDekhoBot)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

log = logging.getLogger(__name__)

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720

# System fonts priority
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load system font if available, fallback to default."""
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Round the corners of an image with smooth antialiasing."""
    mask = Image.new("L", (img.width * 2, img.height * 2), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img.width * 2, img.height * 2), radius * 2, fill=255)
    mask = mask.resize((img.width, img.height), Image.Resampling.LANCZOS)
    output = img.copy().convert("RGBA")
    output.putalpha(mask)
    return output


def generate_auto_thumbnail(
    title: str,
    episode_info: str = "",
    quality: str = "720p",
    audio: str = "Hindi Dub",
    poster_path: str = "",
    output_path: str = "",
    bot_username: str = "AnimeDekhoBot",
) -> str | None:
    """
    Generate a 1280x720 professional YouTube/Telegram video thumbnail.
    Returns the absolute path of the generated JPEG thumbnail.
    """
    try:
        if not output_path:
            from tempfile import gettempdir
            output_path = os.path.join(gettempdir(), f"thumb_auto_{os.getpid()}_{int(os.times().elapsed * 1000)}.jpg")

        # 1. Base Canvas
        canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (15, 17, 24, 255))

        # 2. Background: Blurred Artwork or Gradient
        bg_created = False
        if poster_path and os.path.exists(poster_path):
            try:
                with Image.open(poster_path) as p_img:
                    p_img = p_img.convert("RGBA")
                    # Scale to fill canvas
                    scale = max(CANVAS_WIDTH / p_img.width, CANVAS_HEIGHT / p_img.height)
                    nw = int(p_img.width * scale)
                    nh = int(p_img.height * scale)
                    scaled = p_img.resize((nw, nh), Image.Resampling.LANCZOS)
                    # Center crop
                    x1 = (nw - CANVAS_WIDTH) // 2
                    y1 = (nh - CANVAS_HEIGHT) // 2
                    cropped = scaled.crop((x1, y1, x1 + CANVAS_WIDTH, y1 + CANVAS_HEIGHT))
                    # Heavy blur
                    blurred = cropped.filter(ImageFilter.GaussianBlur(radius=28))
                    canvas.paste(blurred, (0, 0))
                    bg_created = True
            except Exception as e:
                log.debug("Failed creating blurred background from poster: %s", e)

        # 3. Dark Cinematic Gradient Overlay (gives rich contrast for text)
        overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)
        for y in range(CANVAS_HEIGHT):
            # Gradient: darker at the bottom and right
            alpha = int(140 + (y / CANVAS_HEIGHT) * 90)
            draw_ov.line([(0, y), (CANVAS_WIDTH, y)], fill=(10, 12, 18, alpha))
        # Extra left-to-right gradient so the right text side is easily readable
        for x in range(CANVAS_WIDTH):
            if x > 400:
                alpha = int(((x - 400) / (CANVAS_WIDTH - 400)) * 90)
                draw_ov.line([(x, 0), (x, CANVAS_HEIGHT)], fill=(6, 8, 14, alpha))
        canvas = Image.alpha_composite(canvas, overlay)

        draw = ImageDraw.Draw(canvas)

        # 4. Foreground Poster (Left Side)
        poster_w, poster_h = 360, 520
        poster_x, poster_y = 60, 100

        if poster_path and os.path.exists(poster_path):
            try:
                with Image.open(poster_path) as p_img:
                    p_img = p_img.convert("RGBA")
                    p_resized = p_img.resize((poster_w, poster_h), Image.Resampling.LANCZOS)
                    p_rounded = _round_corners(p_resized, radius=18)

                    # Shadow / Border around poster
                    draw.rounded_rectangle(
                        (poster_x - 3, poster_y - 3, poster_x + poster_w + 3, poster_y + poster_h + 3),
                        radius=21,
                        fill=(255, 255, 255, 30),
                        outline=(255, 255, 255, 120),
                        width=2,
                    )
                    canvas.paste(p_rounded, (poster_x, poster_y), p_rounded)
            except Exception as pe:
                log.debug("Could not paste foreground poster: %s", pe)

        # 5. Right Side Content
        text_x = 470
        curr_y = 110

        # Fonts
        font_pill = _get_font(22)
        font_title = _get_font(48)
        font_sub = _get_font(26)
        font_brand = _get_font(22)

        # A. Top Badges (Quality & Audio)
        # Quality Pill (e.g. "1080p FHD" or "720p HD")
        q_label = quality.upper()
        if "1080" in q_label:
            q_text = "1080P • FULL HD"
            pill_color = (220, 38, 38, 230)  # Red accent
        elif "720" in q_label:
            q_text = "720P • HD"
            pill_color = (37, 99, 235, 230)  # Blue accent
        elif "480" in q_label:
            q_text = "480P • SD"
            pill_color = (13, 148, 136, 230)  # Teal accent
        elif "4K" in q_label or "2160" in q_label:
            q_text = "4K • ULTRA HD"
            pill_color = (147, 51, 234, 230)  # Purple accent
        else:
            q_text = q_label
            pill_color = (75, 85, 99, 230)

        # Draw Quality Pill
        pw = 190
        ph = 42
        draw.rounded_rectangle((text_x, curr_y, text_x + pw, curr_y + ph), radius=10, fill=pill_color)
        draw.text((text_x + 16, curr_y + 9), q_text, font=font_pill, fill=(255, 255, 255, 255))

        # Audio Pill
        audio_text = f"🎙️ {audio}" if audio else "🎙️ Hindi Dub"
        aw = 220
        draw.rounded_rectangle(
            (text_x + pw + 15, curr_y, text_x + pw + 15 + aw, curr_y + ph),
            radius=10,
            fill=(30, 41, 59, 220),
            outline=(100, 116, 139, 180),
            width=1,
        )
        draw.text((text_x + pw + 28, curr_y + 9), audio_text, font=font_pill, fill=(241, 245, 249, 255))

        curr_y += 75

        # B. Anime Title (Clean, Auto-wrapped)
        # Clean title
        clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', title).strip()
        if not clean_title:
            clean_title = title[:40]

        # Wrap title to max ~24 characters per line
        words = clean_title.split()
        lines = []
        cur_line = []
        for w in words:
            if len(" ".join(cur_line + [w])) <= 24:
                cur_line.append(w)
            else:
                if cur_line:
                    lines.append(" ".join(cur_line))
                cur_line = [w]
        if cur_line:
            lines.append(" ".join(cur_line))
        lines = lines[:2]  # Max 2 lines for title

        for line in lines:
            # Subtle drop shadow
            draw.text((text_x + 2, curr_y + 2), line, font=font_title, fill=(0, 0, 0, 180))
            draw.text((text_x, curr_y), line, font=font_title, fill=(255, 255, 255, 255))
            curr_y += 58

        curr_y += 15

        # C. Season / Episode Highlight Badge
        if episode_info:
            ep_box_w = 340
            ep_box_h = 56
            draw.rounded_rectangle(
                (text_x, curr_y, text_x + ep_box_w, curr_y + ep_box_h),
                radius=12,
                fill=(245, 158, 11, 240),  # Amber gold
            )
            # Black bold text for high contrast on gold
            draw.text((text_x + 22, curr_y + 13), episode_info.upper(), font=_get_font(28), fill=(17, 24, 39, 255))
            curr_y += 85
        else:
            curr_y += 30

        # D. Divider Line
        draw.line([(text_x, curr_y), (CANVAS_WIDTH - 60, curr_y)], fill=(255, 255, 255, 50), width=2)
        curr_y += 30

        # E. Features bullet line
        tagline = "⚡ Multi-Audio • Dual Audio • High Speed Direct Play"
        draw.text((text_x, curr_y), tagline, font=font_sub, fill=(148, 163, 184, 255))
        curr_y += 45

        # F. Branding Footer
        brand_text = f"✦ Powered by @{bot_username.lstrip('@')}"
        draw.text((text_x, curr_y), brand_text, font=font_brand, fill=(96, 165, 250, 255))

        # 6. Save as crisp, optimized JPEG
        final_img = canvas.convert("RGB")
        final_img.save(output_path, "JPEG", quality=92, optimize=True)
        log.info("Generated auto thumbnail: %s (%dx%d)", output_path, CANVAS_WIDTH, CANVAS_HEIGHT)
        return output_path

    except Exception as e:
        log.error("Failed generating auto thumbnail: %s", e, exc_info=True)
        return None
