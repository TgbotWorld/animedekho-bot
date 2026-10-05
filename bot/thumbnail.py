"""
Auto Thumbnail Generator for AnimeDekho Bot — Modular Multi-Template Architecture.

Supports 5 distinct visual designs + random mode:
1. 'modern': Stylized modern glass — gradient accent cap & title, ambient
   glow field, feature chips, giant quality watermark and brand lockup.
2. 'cinematic': Moody widescreen theatrical master with silver frame & cinematic bars.
3. 'movie_gold': Luxury obsidian & champagne gold VIP aesthetic (tailored for movies).
4. 'neon_cyber': Futuristic cyberpunk with electric cyan & hot magenta neon glow.
5. 'minimal': Clean frosted studio matte card with refined minimalist typography.

Also includes `enhance_custom_thumbnail` to fix low-quality / blurry custom thumbnails,
ensuring razor-sharp text, proper 16:9 framing, and Telegram video player optimization.
"""

from __future__ import annotations

import logging
import math
import os
import random
import re
from pathlib import Path
from tempfile import gettempdir
from typing import Dict, Type

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

log = logging.getLogger(__name__)

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720

ASSETS_FONT_DIR = Path(__file__).parent.parent / "assets" / "fonts"

# System and bundled fonts priority
FONT_PATHS = [
    # Bundled Montserrat in repo
    str(ASSETS_FONT_DIR / "Montserrat-Bold.ttf"),
    str(ASSETS_FONT_DIR / "Montserrat-ExtraBold.ttf"),
    str(ASSETS_FONT_DIR / "Montserrat-SemiBold.ttf"),
    str(ASSETS_FONT_DIR / "Montserrat-Regular.ttf"),
    # Linux system fonts
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]


def _get_font(size: int, bold: bool = True, weight: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load bundled or system font with proper weights, falling back gracefully."""
    weight_map = {
        "extrabold": "Montserrat-ExtraBold.ttf",
        "bold": "Montserrat-Bold.ttf",
        "semibold": "Montserrat-SemiBold.ttf",
        "medium": "Montserrat-Medium.ttf",
        "regular": "Montserrat-Regular.ttf",
    }
    
    # Check bundled font first
    if weight and weight.lower() in weight_map:
        bundled = ASSETS_FONT_DIR / weight_map[weight.lower()]
        if bundled.exists():
            try:
                return ImageFont.truetype(str(bundled), size)
            except Exception:
                pass
    elif bold:
        bundled = ASSETS_FONT_DIR / "Montserrat-Bold.ttf"
        if bundled.exists():
            try:
                return ImageFont.truetype(str(bundled), size)
            except Exception:
                pass
    else:
        bundled = ASSETS_FONT_DIR / "Montserrat-Medium.ttf"
        if bundled.exists():
            try:
                return ImageFont.truetype(str(bundled), size)
            except Exception:
                pass

    # Fallback to system font paths
    for p in FONT_PATHS:
        if not bold and ("Bold" in p or "bd" in p):
            continue
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass

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


def _clean_text(text: str) -> str:
    """Strip emojis and unsupported unicode symbols to avoid broken square glyphs."""
    if not text:
        return ""
    # Strip high unicode emojis (U+1F000..U+1FFFF), misc symbols (U+2600..U+27BF), etc.
    cleaned = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50\u2b55\u200d\ufe0f]", "", text)
    return " ".join(cleaned.split()).strip()


def _draw_vector_star(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r_outer: float,
    r_inner: float,
    fill: tuple[int, int, int, int] | tuple[int, int, int],
    points: int = 5,
) -> None:
    """Draw a mathematically antialiased vector star directly on canvas."""
    coords = []
    angle = -math.pi / 2
    step = math.pi / points
    for i in range(points * 2):
        r = r_outer if i % 2 == 0 else r_inner
        coords.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        angle += step
    draw.polygon(coords, fill=fill)


def _round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Round the corners of an image with smooth antialiasing."""
    mask = Image.new("L", (img.width * 2, img.height * 2), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img.width * 2, img.height * 2), radius * 2, fill=255)
    mask = mask.resize((img.width, img.height), Image.Resampling.LANCZOS)
    output = img.copy().convert("RGBA")
    output.putalpha(mask)
    return output


def _prepare_blurred_bg(poster_path: str, blur_radius: int = 34) -> Image.Image | None:
    """Scale, center-crop, and blur a poster to cover the 1280x720 canvas."""
    target_path = poster_path
    if not target_path or not os.path.exists(target_path):
        banner_fallback = Path(__file__).parent.parent / "assets" / "banner.png"
        if banner_fallback.exists():
            target_path = str(banner_fallback)
        else:
            return None

    try:
        with Image.open(target_path) as p_img:
            p_img = p_img.convert("RGBA")
            scale = max(CANVAS_WIDTH / p_img.width, CANVAS_HEIGHT / p_img.height)
            nw = int(p_img.width * scale)
            nh = int(p_img.height * scale)
            scaled = p_img.resize((nw, nh), Image.Resampling.LANCZOS)
            x1 = (nw - CANVAS_WIDTH) // 2
            y1 = (nh - CANVAS_HEIGHT) // 2
            cropped = scaled.crop((x1, y1, x1 + CANVAS_WIDTH, y1 + CANVAS_HEIGHT))
            return cropped.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    except Exception as e:
        log.debug("Failed preparing blurred background: %s", e)
        return None


def _resolve_poster_image(poster_path: str) -> Image.Image | None:
    """Load poster image with fallback to assets/banner.png if missing."""
    target = poster_path
    if not target or not os.path.exists(target):
        banner = Path(__file__).parent.parent / "assets" / "banner.png"
        if banner.exists():
            target = str(banner)
        else:
            return None
    try:
        return Image.open(target).convert("RGBA")
    except Exception as e:
        log.debug("Failed loading poster image: %s", e)
        return None


def _wrap_and_fit_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    max_w: int,
    base_size: int = 46,
    min_size: int = 32,
    max_lines: int = 2,
) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont, int]:
    """Dynamically wrap and scale font size so title fits neatly within max_w and max_lines."""
    clean = _clean_text(re.sub(r"\[.*?\]|\(.*?\)", "", title).strip())
    if not clean:
        clean = title[:40]
    words = clean.split()

    for size in range(base_size, min_size - 1, -2):
        font = _get_font(size, bold=True, weight="extrabold")
        lines = []
        curr = []
        fits = True
        for w in words:
            cand = " ".join(curr + [w])
            bb = draw.textbbox((0, 0), cand, font=font)
            if (bb[2] - bb[0]) <= max_w:
                curr.append(w)
            else:
                if curr:
                    lines.append(" ".join(curr))
                curr = [w]
                if len(lines) >= max_lines:
                    fits = False
                    break
        if curr and fits:
            lines.append(" ".join(curr))

        if fits and len(lines) <= max_lines:
            line_height = int(size * 1.22)
            return lines[:max_lines], font, line_height

    # Final fallback with min_size
    font = _get_font(min_size, bold=True, weight="extrabold")
    lines = []
    curr = []
    for w in words:
        cand = " ".join(curr + [w])
        bb = draw.textbbox((0, 0), cand, font=font)
        if (bb[2] - bb[0]) <= max_w:
            curr.append(w)
        else:
            if curr:
                lines.append(" ".join(curr))
            curr = [w]
    if curr:
        lines.append(" ".join(curr))
    return lines[:max_lines], font, int(min_size * 1.22)


def _get_quality_pill(quality: str) -> tuple[str, tuple[int, int, int, int]]:
    """Return stylized quality label and background color."""
    q_up = (quality or "720P").upper().strip()
    if "4K" in q_up or "2160" in q_up:
        return "4K • ULTRA HD", (139, 92, 246, 240)  # Royal Violet
    elif "1080" in q_up:
        return "1080P • FULL HD", (225, 29, 72, 240)  # Crimson Ruby
    elif "720" in q_up:
        return "720P • HD", (37, 99, 235, 240)  # Sapphire Blue
    elif "480" in q_up:
        return "480P • SD", (13, 148, 136, 240)  # Teal
    else:
        label = f"{q_up} • HD" if "HD" not in q_up else q_up
        return label, (13, 148, 136, 240)


def _format_audio_tag(audio: str) -> str:
    """Format audio tag cleanly without broken glyphs."""
    aud = _clean_text(audio).strip().upper()
    if not aud:
        return "HINDI DUBBED"
    if "HINDI" in aud and "DUB" not in aud:
        return "HINDI DUBBED"
    if "MULTI" in aud and "AUDIO" not in aud:
        return "MULTI AUDIO"
    return aud


def _format_episode_tag(episode_info: str, is_movie: bool) -> str:
    """Format episode or movie banner tag."""
    if is_movie and not episode_info:
        return "FEATURE FILM • OFFICIAL RELEASE"
    if episode_info:
        clean_ep = _clean_text(episode_info).strip().upper()
        return clean_ep
    return "COMPLETE SERIES • ALL EPISODES"


def _save_optimized_jpeg(img: Image.Image, output_path: str, max_kb: int = 285) -> str:
    """Save image with optimal quality, unsharp mask, and ensure size is strictly under Telegram's limit."""
    final = img.convert("RGB")
    final = ImageEnhance.Contrast(final).enhance(1.05)
    final = final.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=2))

    q = 95
    while q >= 75:
        final.save(output_path, "JPEG", quality=q, optimize=True, subsampling=0)
        try:
            if os.path.getsize(output_path) <= max_kb * 1024:
                break
        except Exception:
            break
        q -= 4
    return output_path


# ── Custom Thumbnail Enhancement Function ──────────────────────────────────

def enhance_custom_thumbnail(
    input_path: str,
    output_path: str | None = None,
    target_width: int = 1280,
    target_height: int = 720,
) -> str | None:
    """
    Transform ANY custom uploaded image into a high-end, razor-sharp 1280x720 video thumbnail.
    
    Solves low quality & unreadable text issues on custom thumbnails:
    - Aspect Ratio Fix: Non-16:9 images (portraits, squares) are framed onto a 16:9 canvas
      with a cinema-grade blurred ambient background and subtle drop shadow (no stretching/cropping).
    - Clarity Enhancement: UnsharpMask filtering and contrast/color boosting make all text glyphs,
      logos, and episode labels crisp and easily legible on both mobile and desktop screens.
    - Lossless Chroma Subsampling: Saved with `subsampling=0` (4:4:4) to eliminate JPEG edge-blur on text.
    - Size Optimization: Kept strictly under 285 KB to prevent Telegram server recompression artifacts.
    """
    if not input_path or not os.path.exists(input_path):
        return None

    if not output_path:
        output_path = os.path.join(
            gettempdir(),
            f"enhanced_thumb_{os.getpid()}_{random.randint(1000, 9999)}.jpg",
        )

    try:
        with Image.open(input_path) as im:
            im = im.convert("RGBA")
            w, h = im.size
            if w <= 0 or h <= 0:
                return None

            aspect = w / h

            # Case 1: Already widescreen (~16:9, between 1.65 and 1.90)
            if 1.65 <= aspect <= 1.90:
                resized = im.resize((target_width, target_height), Image.Resampling.LANCZOS)
                canvas = resized.convert("RGB")
            else:
                # Case 2: Portrait poster, square, or irregular aspect ratio
                # Ambient blurred backdrop framing (Netflix / Crunchyroll / Disney+ aesthetic)
                canvas = Image.new("RGBA", (target_width, target_height), (10, 12, 20, 255))

                # Background scaled & blurred
                scale_bg = max(target_width / w, target_height / h)
                bg_w, bg_h = int(w * scale_bg), int(h * scale_bg)
                bg = im.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
                bg_x = (bg_w - target_width) // 2
                bg_y = (bg_h - target_height) // 2
                bg_cropped = bg.crop((bg_x, bg_y, bg_x + target_width, bg_y + target_height))
                bg_blurred = bg_cropped.filter(ImageFilter.GaussianBlur(radius=36))

                # Atmospheric vignette overlay
                overlay = Image.new("RGBA", (target_width, target_height), (8, 10, 18, 140))
                bg_composite = Image.alpha_composite(bg_blurred, overlay)
                canvas.paste(bg_composite, (0, 0))

                # Sharp foreground image (fitted within bounds)
                pad = 24
                max_fg_h = target_height - (pad * 2)
                max_fg_w = target_width - (pad * 2)
                scale_fg = min(max_fg_w / w, max_fg_h / h)
                fg_w, fg_h = int(w * scale_fg), int(h * scale_fg)
                fg_resized = im.resize((fg_w, fg_h), Image.Resampling.LANCZOS)

                fg_x = (target_width - fg_w) // 2
                fg_y = (target_height - fg_h) // 2

                # Soft realistic ambient drop shadow
                shadow = Image.new("RGBA", (fg_w + 30, fg_h + 30), (0, 0, 0, 0))
                draw_sh = ImageDraw.Draw(shadow)
                draw_sh.rounded_rectangle((15, 15, fg_w + 15, fg_h + 15), radius=18, fill=(0, 0, 0, 210))
                shadow = shadow.filter(ImageFilter.GaussianBlur(radius=12))
                canvas.paste(shadow, (fg_x - 15, fg_y - 15), shadow)

                # Delicate outer glass border stroke
                draw = ImageDraw.Draw(canvas)
                draw.rounded_rectangle(
                    (fg_x - 3, fg_y - 3, fg_x + fg_w + 3, fg_y + fg_h + 3),
                    radius=16, outline=(255, 255, 255, 100), width=2
                )
                canvas.paste(fg_resized, (fg_x, fg_y), fg_resized)
                canvas = canvas.convert("RGB")

            # High-Impact Clarity & Readability Enhancements
            canvas = ImageEnhance.Contrast(canvas).enhance(1.08)
            canvas = ImageEnhance.Color(canvas).enhance(1.06)
            canvas = ImageEnhance.Sharpness(canvas).enhance(1.25)
            canvas = canvas.filter(ImageFilter.UnsharpMask(radius=1.6, percent=135, threshold=3))

            # Optimize JPEG file size strictly for Telegram
            q = 95
            while q >= 75:
                canvas.save(output_path, "JPEG", quality=q, optimize=True, subsampling=0)
                try:
                    if os.path.getsize(output_path) <= 285 * 1024:
                        break
                except Exception:
                    break
                q -= 4

            log.info("Enhanced custom thumbnail created: %s (%d bytes)", output_path, os.path.getsize(output_path))
            return output_path
    except Exception as e:
        log.error("Failed enhancing custom thumbnail: %s", e, exc_info=True)
        return None


# ── Modular Template Base & Registry ───────────────────────────────────────

TEMPLATES: Dict[str, Type["BaseThumbnailTemplate"]] = {}


def register_template(name: str):
    """Decorator to register a thumbnail template class."""
    def decorator(cls: Type["BaseThumbnailTemplate"]):
        TEMPLATES[name.lower().strip()] = cls
        return cls
    return decorator


class BaseThumbnailTemplate:
    """Abstract base class for all auto-thumbnail templates."""
    name: str = "base"
    display_name: str = "Base Template"
    description: str = "Base thumbnail template"

    def generate(
        self,
        title: str,
        episode_info: str = "",
        quality: str = "720p",
        audio: str = "Hindi Dub",
        poster_path: str = "",
        output_path: str = "",
        bot_username: str = "AnimeDekhoBot",
        is_movie: bool = False,
    ) -> str | None:
        raise NotImplementedError


# ── Template 1: Modern Glass (v2 — stylized gradient design) ────────────────

def _grad_color(stops: list, t: float) -> tuple:
    """Interpolate RGBA across gradient stops [(pos, rgba), ...]."""
    if t <= stops[0][0]:
        return stops[0][1]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            f = (t - p0) / max(1e-6, p1 - p0)
            return tuple(int(round(c0[i] + (c1[i] - c0[i]) * f)) for i in range(len(c0)))
    return stops[-1][1]


def _multi_gradient(size, stops, horizontal: bool = True) -> Image.Image:
    """Linear RGBA gradient image of the given (w, h) size."""
    w, h = max(1, size[0]), max(1, size[1])
    grad = Image.new("RGBA", (w, h), stops[0][1])
    gd = ImageDraw.Draw(grad)
    n = w if horizontal else h
    for i in range(n):
        col = _grad_color(stops, i / (n - 1) if n > 1 else 0)
        if horizontal:
            gd.line([(i, 0), (i, h - 1)], fill=col)
        else:
            gd.line([(0, i), (w - 1, i)], fill=col)
    return grad


def _rounded_gradient_layer(canvas_size, box, radius: int, stops, horizontal: bool = True) -> Image.Image:
    """Full-canvas RGBA layer whose rounded rectangle is filled by a gradient."""
    x1, y1, x2, y2 = (int(v) for v in box)
    grad = _multi_gradient((max(1, x2 - x1), max(1, y2 - y1)), stops, horizontal)
    mask = Image.new("L", canvas_size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=255)
    padded = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    padded.paste(grad, (x1, y1))
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    layer.paste(padded, (0, 0), mask)
    return layer


def _gradient_text(
    canvas: Image.Image,
    xy,
    text: str,
    font,
    stops: list,
    shadow_alpha: int = 170,
    shadow_offset=(3, 5),
    pad: int = 8,
) -> tuple[int, int]:
    """Draw gradient-filled text (soft drop shadow) directly on the canvas.

    Returns the (width, height) of the rendered text block.
    """
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    bbox = probe.textbbox((0, 0), text, font=font)
    tw, th = max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])
    size = (tw + pad * 2, th + pad * 2)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=255)
    x, y = xy
    if shadow_alpha:
        sh = Image.new("RGBA", size, (0, 0, 0, 255))
        sh.putalpha(mask.point(lambda p: p * shadow_alpha // 255))
        canvas.alpha_composite(sh, (x - pad + shadow_offset[0], y - pad + shadow_offset[1]))
    txt = Image.new("RGBA", size, (0, 0, 0, 0))
    txt.paste(_multi_gradient(size, stops), (0, 0), mask)
    canvas.alpha_composite(txt, (x - pad, y - pad))
    return tw, th


def _radial_glow(canvas_size, center, radius: int, color, max_alpha: int = 60) -> Image.Image:
    """Soft radial glow layer (concentric falloff + blur) for ambient fields."""
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = center
    steps = 40
    for i in range(steps, 0, -1):
        t = i / steps
        r = radius * t
        a = int(max_alpha * ((1 - t) ** 1.6))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(color) + (a,))
    return layer.filter(ImageFilter.GaussianBlur(radius=28))


def _shade_rgba(color, factor: float) -> tuple:
    """Lighten (>1.0) or darken (<1.0) an RGBA color toward white/black."""
    if len(color) == 3:
        r, g, b, a = (*color, 255)
    else:
        r, g, b, a = color
    if factor >= 1.0:
        k = min(1.0, factor - 1.0)
        ch = lambda c: int(round(c + (255 - c) * k))  # noqa: E731
    else:
        ch = lambda c: int(round(c * factor))  # noqa: E731
    return (ch(r), ch(g), ch(b), a)


@register_template("modern")
class ModernGradientTemplate(BaseThumbnailTemplate):
    """Stylized modern glass — gradient title, ambient glow field, feature chips."""
    name = "modern"
    display_name = "Modern Glass"
    description = "Stylized glass card with gradient typography, ambient glow and feature chips."

    # Brand accent gradient shared across cap, borders and monogram tile.
    ACCENT = [
        (0.0, (34, 211, 238, 255)),
        (0.5, (59, 130, 246, 255)),
        (1.0, (139, 92, 246, 255)),
    ]

    def generate(
        self,
        title: str,
        episode_info: str = "",
        quality: str = "720p",
        audio: str = "Hindi Dub",
        poster_path: str = "",
        output_path: str = "",
        bot_username: str = "AnimeDekhoBot",
        is_movie: bool = False,
    ) -> str | None:
        try:
            W, H = CANVAS_WIDTH, CANVAS_HEIGHT
            canvas = Image.new("RGBA", (W, H), (8, 10, 18, 255))

            # ── 1. Ambient background: blurred poster + graded darks + glows
            bg = _prepare_blurred_bg(poster_path, blur_radius=38)
            if bg:
                canvas.paste(bg, (0, 0))
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dov = ImageDraw.Draw(overlay)
            for y in range(H):
                dov.line([(0, y), (W, y)], fill=(8, 10, 18, int(120 + (y / H) * 80)))
            for x in range(360, W):
                dov.line([(x, 0), (x, H)], fill=(6, 8, 16, int(((x - 360) / (W - 360)) * 90)))
            canvas = Image.alpha_composite(canvas, overlay)
            for center, radius, color, alpha in (
                ((1010, 60), 420, (34, 211, 238), 55),
                ((240, 680), 430, (139, 92, 246), 50),
                ((640, 380), 560, (59, 130, 246), 26),
            ):
                canvas.alpha_composite(_radial_glow((W, H), center, radius, color, alpha))

            draw = ImageDraw.Draw(canvas)

            # ── 2. Poster: ambient glow + drop shadow + gradient hairline ──
            px, py, pw, ph = 56, 84, 360, 540
            p_img = _resolve_poster_image(poster_path)
            glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(glow).rounded_rectangle(
                (px - 14, py - 14, px + pw + 14, py + ph + 14), radius=26, fill=(56, 189, 248, 110)
            )
            canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(radius=30)))
            if p_img:
                try:
                    p_resized = p_img.resize((pw, ph), Image.Resampling.LANCZOS)
                    p_rounded = _round_corners(p_resized, radius=18)
                    shadow = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
                    ImageDraw.Draw(shadow).rounded_rectangle(
                        (20, 20, pw + 20, ph + 20), radius=24, fill=(0, 0, 0, 225)
                    )
                    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=14))
                    canvas.alpha_composite(shadow, (px - 20, py - 20))
                    canvas.paste(p_rounded, (px, py), p_rounded)
                    # gradient hairline border
                    bmask = Image.new("L", (W, H), 0)
                    ImageDraw.Draw(bmask).rounded_rectangle(
                        (px - 2, py - 2, px + pw + 2, py + ph + 2), radius=20, outline=255, width=3
                    )
                    border = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    border.paste(_multi_gradient((W, H), self.ACCENT), (0, 0), bmask)
                    canvas.alpha_composite(border)
                    # bottom readability shade inside poster
                    shade_y1 = py + ph - 150
                    smask = Image.new("L", (W, H), 0)
                    ImageDraw.Draw(smask).rounded_rectangle(
                        (px, shade_y1, px + pw, py + ph), radius=18, fill=255
                    )
                    sg = _multi_gradient((pw, 150), [(0.0, (0, 0, 0, 0)), (1.0, (0, 0, 0, 155))], horizontal=False)
                    slayer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    slayer.paste(sg, (px, shade_y1))
                    sout = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    sout.paste(slayer, (0, 0), smask)
                    canvas.alpha_composite(sout)
                except Exception as pe:
                    log.debug("Modern poster error: %s", pe)
            else:
                # No poster available — elegant placeholder panel.
                draw.rounded_rectangle(
                    (px, py, px + pw, py + ph), radius=18, fill=(13, 20, 38, 255),
                    outline=(71, 85, 105, 200), width=2,
                )
                wcx, wcy = px + pw // 2, py + ph // 2
                draw.ellipse((wcx - 70, wcy - 70, wcx + 70, wcy + 70),
                             fill=(34, 211, 238, 30), outline=(34, 211, 238, 180), width=3)
                draw.polygon([(wcx - 22, wcy - 34), (wcx - 22, wcy + 34), (wcx + 40, wcy)],
                             fill=(34, 211, 238, 220))

            # ── 3. Glass card with gradient accent cap ─────────────────────
            cx1, cy1, cx2, cy2 = 448, 52, 1232, 664
            card_mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(card_mask).rounded_rectangle((cx1, cy1, cx2, cy2), radius=26, fill=255)
            card_fill = Image.new("RGBA", (W, H), (11, 17, 32, 232))
            cf = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            cf.paste(card_fill, (0, 0))
            cout = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            cout.paste(cf, (0, 0), card_mask)
            canvas.alpha_composite(cout)
            draw.rounded_rectangle((cx1, cy1, cx2, cy2), radius=26, outline=(255, 255, 255, 60), width=1)

            # Gradient accent cap (top 32px band, top corners rounded).
            band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            band.paste(_multi_gradient((cx2 - cx1, 32), self.ACCENT), (cx1, cy1))
            bm = Image.new("L", (W, H), 0)
            bmd = ImageDraw.Draw(bm)
            bmd.rounded_rectangle((cx1, cy1, cx2, cy1 + 58), radius=26, fill=255)
            bmd.rectangle((cx1, cy1 + 32, cx2, cy2), fill=0)
            bout = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            bout.paste(band, (0, 0), bm)
            canvas.alpha_composite(bout)

            # Frosted sheen across the upper card.
            sheen_mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(sheen_mask).rectangle((cx1, 110, cx2, 344), fill=255)
            sheen = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sheen.paste(_multi_gradient((W, 234), [(0.0, (255, 255, 255, 24)), (1.0, (255, 255, 255, 0))], horizontal=False), (0, 110))
            sout = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sout.paste(sheen, (0, 0), sheen_mask)
            canvas.alpha_composite(sout)

            # Big translucent play watermark (behind the content) — composed
            # on a layer so its low alpha survives the RGB flatten.
            wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            wdraw = ImageDraw.Draw(wm)
            wcx, wcy, wr = 1108, 302, 92
            wdraw.ellipse((wcx - wr, wcy - wr, wcx + wr, wcy + wr),
                          fill=(255, 255, 255, 14), outline=(255, 255, 255, 34), width=3)
            wdraw.polygon([(wcx - 26, wcy - 40), (wcx - 26, wcy + 40), (wcx + 46, wcy)],
                          fill=(255, 255, 255, 40))
            canvas.alpha_composite(wm)

            # ── 4. Content ─────────────────────────────────────────────────
            tx, right = cx1 + 40, cx2 - 40
            avail = right - tx
            y = 118

            font_pill = _get_font(20, bold=True, weight="bold")

            # Quality pill (shaded gradient of its tier color).
            q_label, q_color = _get_quality_pill(quality)
            qb = draw.textbbox((0, 0), q_label, font=font_pill)
            qw = (qb[2] - qb[0]) + 34
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (tx, y, tx + qw, y + 40), 10,
                [(0.0, _shade_rgba(q_color, 0.82)), (1.0, _shade_rgba(q_color, 1.18))],
            ))
            draw.text((tx + 17, y + 9), q_label, font=font_pill, fill=(255, 255, 255, 255))

            # Audio pill (glass).
            audio_text = _format_audio_tag(audio)
            ab = draw.textbbox((0, 0), audio_text, font=font_pill)
            aw = (ab[2] - ab[0]) + 34
            ax = tx + qw + 14
            draw.rounded_rectangle((ax, y, ax + aw, y + 40), radius=10,
                                   fill=(30, 41, 59, 235), outline=(100, 116, 139, 170), width=1)
            draw.text((ax + 17, y + 9), audio_text, font=font_pill, fill=(241, 245, 249, 255))
            y += 40 + 24

            # Main title — gradient fill + soft shadow (auto-wrapped).
            title_lines, font_title, line_h = _wrap_and_fit_title(
                draw, title, max_w=avail, base_size=48, min_size=34, max_lines=2,
            )
            for line in title_lines:
                _gradient_text(canvas, (tx, y), line, font_title,
                               [(0.0, (255, 255, 255, 255)), (1.0, (165, 215, 255, 255))])
                y += line_h
            y += 14

            # Episode / movie ribbon (amber gradient).
            ep_tag = _format_episode_tag(episode_info, is_movie)
            font_ep = _get_font(23, bold=True, weight="extrabold")
            eb = draw.textbbox((0, 0), ep_tag, font=font_ep)
            ew = (eb[2] - eb[0]) + 44
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (tx, y, tx + ew, y + 48), 12,
                [(0.0, (251, 191, 36, 255)), (1.0, (245, 158, 11, 255))],
            ))
            draw.text((tx + 22, y + 11), ep_tag, font=font_ep, fill=(15, 23, 42, 255))
            y += 48 + 24

            # Gradient divider.
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (tx, y, right, y + 3), 2,
                [(0.0, (34, 211, 238, 220)), (0.55, (59, 130, 246, 150)), (1.0, (139, 92, 246, 60))],
            ))

            # ── 5. Feature chips (flow directly after the divider) ──────────
            chips_y = int(y + 30)
            font_chip = _get_font(17, bold=True, weight="semibold")
            chip_x = tx
            for label, icon in (
                ("FAST DIRECT PLAY", "bolt"),
                ("HD MASTER", "play"),
                ("MULTI-AUDIO", "wave"),
            ):
                cb = draw.textbbox((0, 0), label, font=font_chip)
                cw = (cb[2] - cb[0]) + 62
                draw.rounded_rectangle((chip_x, chips_y, chip_x + cw, chips_y + 44), radius=22,
                                       fill=(20, 30, 52, 210), outline=(71, 85, 105, 150), width=1)
                icx, icy = chip_x + 20, chips_y + 22
                if icon == "bolt":
                    draw.polygon([(icx + 7, icy - 11), (icx - 4, icy + 2), (icx + 2, icy + 2),
                                  (icx - 3, icy + 12), (icx + 9, icy - 2), (icx + 3, icy - 2)],
                                 fill=(34, 211, 238, 255))
                elif icon == "play":
                    draw.ellipse((icx - 10, icy - 10, icx + 10, icy + 10),
                                 fill=(34, 211, 238, 60), outline=(34, 211, 238, 200), width=2)
                    draw.polygon([(icx - 3, icy - 6), (icx - 3, icy + 6), (icx + 7, icy)],
                                 fill=(255, 255, 255, 255))
                else:
                    for i, bh in enumerate((9, 16, 12)):
                        bx = icx - 7 + i * 7
                        draw.rounded_rectangle((bx, icy - bh // 2, bx + 4, icy + bh // 2),
                                               radius=2, fill=(139, 92, 246, 255))
                draw.text((chip_x + 38, chips_y + 12), label, font=font_chip, fill=(226, 232, 240, 255))
                chip_x += cw + 14

            # Giant translucent quality watermark (bottom-right, brand-tinted)
            # — fills the lower card zone with a stylized, modern signature.
            q_short = (quality or "HD").upper().strip().replace(" ", "")
            if "2160" in q_short or "UHD" in q_short:
                q_short = "4K"
            font_giant = _get_font(86, bold=True, weight="extrabold")
            gb = draw.textbbox((0, 0), q_short, font=font_giant)
            _gradient_text(
                canvas, (right - (gb[2] - gb[0]), chips_y + 74), q_short, font_giant,
                [(0.0, (34, 211, 238, 95)), (1.0, (139, 92, 246, 95))],
                shadow_alpha=0,
            )

            # ── 6. Brand lockup ────────────────────────────────────────────
            by = 576
            canvas.alpha_composite(_rounded_gradient_layer((W, H), (tx, by, tx + 54, by + 54), 14, self.ACCENT))
            draw.rounded_rectangle((tx, by, tx + 54, by + 54), radius=14,
                                   outline=(255, 255, 255, 90), width=2)
            font_mono = _get_font(25, bold=True, weight="extrabold")
            mb = draw.textbbox((0, 0), "AD", font=font_mono)
            draw.text(
                (tx + 27 - (mb[2] - mb[0]) // 2 - mb[0], by + (54 - (mb[3] - mb[1])) // 2 - mb[1]),
                "AD", font=font_mono, fill=(255, 255, 255, 255),
            )
            font_brand = _get_font(26, bold=True, weight="extrabold")
            nb = draw.textbbox((0, 0), "ANIMEDEKHO", font=font_brand)
            draw.text((tx + 70, by), "ANIMEDEKHO", font=font_brand, fill=(255, 255, 255, 255))
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (tx + 70, by + 38, tx + 70 + max(1, nb[2] - nb[0]), by + 41), 2, self.ACCENT,
            ))
            font_handle = _get_font(18, bold=False, weight="medium")
            draw.text((tx + 70, by + 47), f"@{bot_username.lstrip('@')}",
                      font=font_handle, fill=(125, 211, 252, 255))

            return _save_optimized_jpeg(canvas, output_path)
        except Exception as e:
            log.error("ModernGradientTemplate generation error: %s", e, exc_info=True)
            return None


# ── Template 2: Cinematic Glow (Theatrical Letterbox) ──────────────────────

@register_template("cinematic")
class CinematicGlowTemplate(BaseThumbnailTemplate):
    """Moody widescreen letterbox with theatrical bars, silver frame, and cyan accents."""
    name = "cinematic"
    display_name = "Cinematic Glow"
    description = "Theatrical widescreen look with atmospheric indigo glow and silver metallic borders."

    def generate(
        self,
        title: str,
        episode_info: str = "",
        quality: str = "720p",
        audio: str = "Hindi Dub",
        poster_path: str = "",
        output_path: str = "",
        bot_username: str = "AnimeDekhoBot",
        is_movie: bool = False,
    ) -> str | None:
        try:
            canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (5, 7, 12, 255))
            bg = _prepare_blurred_bg(poster_path, blur_radius=38)
            if bg:
                canvas.paste(bg, (0, 0))

            # Deep moody overlay with letterbox bars
            overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            draw_ov = ImageDraw.Draw(overlay)
            for y in range(CANVAS_HEIGHT):
                alpha = int(160 + (y / CANVAS_HEIGHT) * 75)
                draw_ov.line([(0, y), (CANVAS_WIDTH, y)], fill=(6, 8, 16, alpha))

            # 42px Widescreen letterbox bars
            lb_h = 42
            draw_ov.rectangle([(0, 0), (CANVAS_WIDTH, lb_h)], fill=(2, 3, 6, 255))
            draw_ov.rectangle([(0, CANVAS_HEIGHT - lb_h), (CANVAS_WIDTH, CANVAS_HEIGHT)], fill=(2, 3, 6, 255))
            draw_ov.line([(0, lb_h), (CANVAS_WIDTH, lb_h)], fill=(56, 189, 248, 120), width=1)
            draw_ov.line([(0, CANVAS_HEIGHT - lb_h), (CANVAS_WIDTH, CANVAS_HEIGHT - lb_h)], fill=(56, 189, 248, 120), width=1)
            canvas = Image.alpha_composite(canvas, overlay)
            draw = ImageDraw.Draw(canvas)

            # Poster with double metallic silver frame
            poster_w, poster_h = 340, 490
            poster_x, poster_y = 75, 115
            p_img = _resolve_poster_image(poster_path)
            if p_img:
                try:
                    p_resized = p_img.resize((poster_w, poster_h), Image.Resampling.LANCZOS)
                    p_rounded = _round_corners(p_resized, radius=14)
                    draw.rounded_rectangle(
                        (poster_x - 4, poster_y - 4, poster_x + poster_w + 4, poster_y + poster_h + 4),
                        radius=18, outline=(226, 232, 240, 200), width=2,
                    )
                    canvas.paste(p_rounded, (poster_x, poster_y), p_rounded)
                except Exception as pe:
                    log.debug("Cinematic poster error: %s", pe)

            text_x = 470
            curr_y = 135
            available_w = CANVAS_WIDTH - text_x - 60

            # Theatrical Header with Vector Gold Stars
            font_hdr = _get_font(20, bold=True, weight="bold")
            hdr_text = "THEATRICAL MASTER • ULTRA HD STREAM"
            _draw_vector_star(draw, text_x + 8, curr_y + 11, r_outer=7, r_inner=3, fill=(245, 158, 11, 240))
            draw.text((text_x + 24, curr_y), hdr_text, font=font_hdr, fill=(148, 163, 184, 255))
            curr_y += 42

            # Title
            title_lines, font_title, line_height = _wrap_and_fit_title(
                draw, title, max_w=available_w, base_size=44, min_size=32, max_lines=2,
            )
            for line in title_lines:
                draw.text((text_x + 2, curr_y + 2), line, font=font_title, fill=(0, 0, 0, 240))
                draw.text((text_x, curr_y), line, font=font_title, fill=(255, 255, 255, 255))
                curr_y += line_height
            curr_y += 18

            # Cinema Badge
            badge_text = _format_episode_tag(episode_info, is_movie)
            font_badge = _get_font(22, bold=True, weight="extrabold")
            b_bb = draw.textbbox((0, 0), badge_text, font=font_badge)
            bw = (b_bb[2] - b_bb[0]) + 44
            bh = 48
            draw.rounded_rectangle(
                (text_x, curr_y, text_x + bw, curr_y + bh),
                radius=8, fill=(15, 23, 42, 240), outline=(56, 189, 248, 220), width=2,
            )
            draw.text((text_x + 22, curr_y + 11), badge_text, font=font_badge, fill=(224, 242, 254, 255))
            curr_y += bh + 28

            # Specs
            q_label = (quality or "1080P").upper()
            draw.text((text_x, curr_y), f"RESOLUTION : {q_label} ULTRA STREAM", font=_get_font(22, bold=True, weight="bold"), fill=(56, 189, 248, 255))
            curr_y += 34
            audio_text = _format_audio_tag(audio)
            draw.text((text_x, curr_y), f"AUDIO TRACK : {audio_text} (ORIGINAL)", font=_get_font(22, bold=False, weight="semibold"), fill=(226, 232, 240, 255))

            # Watermark in Letterbox bar
            wm_text = f"ANIMEDEKHO THEATRICAL • @{bot_username.lstrip('@')}"
            draw.text((CANVAS_WIDTH - 390, CANVAS_HEIGHT - 30), wm_text, font=_get_font(16, bold=True, weight="bold"), fill=(148, 163, 184, 255))

            return _save_optimized_jpeg(canvas, output_path)
        except Exception as e:
            log.error("CinematicGlowTemplate generation error: %s", e, exc_info=True)
            return None


# ── Template 3: Movie Gold (Luxury VIP) ────────────────────────────────────

@register_template("movie_gold")
class MovieGoldTemplate(BaseThumbnailTemplate):
    """Luxury obsidian and gold theme designed specifically for movies and VIP releases."""
    name = "movie_gold"
    display_name = "Movie Gold VIP"
    description = "Luxury gold borders and warm obsidian tones tailored for movie premieres."

    def generate(
        self,
        title: str,
        episode_info: str = "",
        quality: str = "720p",
        audio: str = "Hindi Dub",
        poster_path: str = "",
        output_path: str = "",
        bot_username: str = "AnimeDekhoBot",
        is_movie: bool = True,
    ) -> str | None:
        try:
            canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (12, 10, 6, 255))
            bg = _prepare_blurred_bg(poster_path, blur_radius=32)
            if bg:
                canvas.paste(bg, (0, 0))

            # Warm dark gold ambient gradient
            overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            draw_ov = ImageDraw.Draw(overlay)
            for y in range(CANVAS_HEIGHT):
                alpha = int(160 + (y / CANVAS_HEIGHT) * 80)
                draw_ov.line([(0, y), (CANVAS_WIDTH, y)], fill=(12, 10, 8, alpha))
            # Outer Gold Border frame
            draw_ov.rectangle([(16, 16), (CANVAS_WIDTH - 16, CANVAS_HEIGHT - 16)], outline=(212, 175, 55, 140), width=2)
            canvas = Image.alpha_composite(canvas, overlay)
            draw = ImageDraw.Draw(canvas)

            # Poster with Gold Frame
            poster_w, poster_h = 350, 500
            poster_x, poster_y = 70, 110
            p_img = _resolve_poster_image(poster_path)
            if p_img:
                try:
                    p_resized = p_img.resize((poster_w, poster_h), Image.Resampling.LANCZOS)
                    p_rounded = _round_corners(p_resized, radius=14)
                    draw.rounded_rectangle(
                        (poster_x - 4, poster_y - 4, poster_x + poster_w + 4, poster_y + poster_h + 4),
                        radius=18, outline=(212, 175, 55, 230), width=3,
                    )
                    canvas.paste(p_rounded, (poster_x, poster_y), p_rounded)
                except Exception as pe:
                    log.debug("Movie gold poster error: %s", pe)

            text_x = 475
            curr_y = 115
            available_w = CANVAS_WIDTH - text_x - 65

            # Gold VIP Header
            header_text = "OFFICIAL VIP PREMIERE" if is_movie else "SPECIAL VIP BROADCAST"
            font_vip = _get_font(18, bold=True, weight="bold")
            v_bb = draw.textbbox((0, 0), header_text, font=font_vip)
            vw = (v_bb[2] - v_bb[0]) + 60
            vh = 38
            draw.rounded_rectangle((text_x, curr_y, text_x + vw, curr_y + vh), radius=6, fill=(212, 175, 55, 230))
            _draw_vector_star(draw, text_x + 16, curr_y + 19, r_outer=7, r_inner=3, fill=(24, 18, 5, 255))
            draw.text((text_x + 30, curr_y + 9), header_text, font=font_vip, fill=(24, 18, 5, 255))
            _draw_vector_star(draw, text_x + vw - 16, curr_y + 19, r_outer=7, r_inner=3, fill=(24, 18, 5, 255))
            curr_y += vh + 20

            # Title
            title_lines, font_title, line_height = _wrap_and_fit_title(
                draw, title, max_w=available_w, base_size=46, min_size=32, max_lines=2,
            )
            for line in title_lines:
                draw.text((text_x + 2, curr_y + 2), line, font=font_title, fill=(0, 0, 0, 240))
                draw.text((text_x, curr_y), line, font=font_title, fill=(255, 255, 255, 255))
                curr_y += line_height
            curr_y += 16

            # Golden Main Ribbon
            main_tag = _format_episode_tag(episode_info, is_movie)
            font_ribbon = _get_font(23, bold=True, weight="extrabold")
            m_bb = draw.textbbox((0, 0), main_tag, font=font_ribbon)
            mw = (m_bb[2] - m_bb[0]) + 44
            mh = 50
            draw.rounded_rectangle((text_x, curr_y, text_x + mw, curr_y + mh), radius=10, fill=(212, 175, 55, 240))
            draw.text((text_x + 22, curr_y + 12), main_tag, font=font_ribbon, fill=(24, 18, 5, 255))
            curr_y += mh + 26

            # Quality & Audio Pills
            q_label = (quality or "1080P").upper()
            draw.text((text_x, curr_y), f"QUALITY : {q_label} ULTRA HD MASTER", font=_get_font(22, bold=True, weight="bold"), fill=(245, 210, 100, 255))
            curr_y += 34
            audio_text = _format_audio_tag(audio)
            draw.text((text_x, curr_y), f"AUDIO : {audio_text} / DUAL AUDIO", font=_get_font(22, bold=False, weight="semibold"), fill=(243, 244, 246, 255))
            curr_y += 45

            # Gold Divider & Branding
            draw.line([(text_x, curr_y), (CANVAS_WIDTH - 65, curr_y)], fill=(212, 175, 55, 80), width=2)
            curr_y += 20
            draw.text((text_x, curr_y), f"VIP RELEASE BY @{bot_username.lstrip('@')}", font=_get_font(20, bold=True, weight="semibold"), fill=(212, 175, 55, 220))

            return _save_optimized_jpeg(canvas, output_path)
        except Exception as e:
            log.error("MovieGoldTemplate generation error: %s", e, exc_info=True)
            return None


# ── Template 4: Neon Cyber (Cyberpunk Anime) ───────────────────────────────

@register_template("neon_cyber")
class NeonCyberTemplate(BaseThumbnailTemplate):
    """Cyberpunk neon aesthetic with electric cyan and hot magenta borders."""
    name = "neon_cyber"
    display_name = "Neon Cyber"
    description = "Cyberpunk glowing neon aesthetic with dual cyan and magenta accents."

    def generate(
        self,
        title: str,
        episode_info: str = "",
        quality: str = "720p",
        audio: str = "Hindi Dub",
        poster_path: str = "",
        output_path: str = "",
        bot_username: str = "AnimeDekhoBot",
        is_movie: bool = False,
    ) -> str | None:
        try:
            canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (7, 7, 15, 255))
            bg = _prepare_blurred_bg(poster_path, blur_radius=28)
            if bg:
                canvas.paste(bg, (0, 0))

            # Dark overlay with slight magenta tint
            overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            draw_ov = ImageDraw.Draw(overlay)
            for y in range(CANVAS_HEIGHT):
                alpha = int(160 + (y / CANVAS_HEIGHT) * 80)
                draw_ov.line([(0, y), (CANVAS_WIDTH, y)], fill=(8, 8, 18, alpha))
            canvas = Image.alpha_composite(canvas, overlay)
            draw = ImageDraw.Draw(canvas)

            # Poster with Cyber Neon Dual Border (Cyan top/left, Pink bottom/right)
            poster_w, poster_h = 350, 500
            poster_x, poster_y = 70, 110
            p_img = _resolve_poster_image(poster_path)
            if p_img:
                try:
                    p_resized = p_img.resize((poster_w, poster_h), Image.Resampling.LANCZOS)
                    p_rounded = _round_corners(p_resized, radius=10)
                    draw.rounded_rectangle(
                        (poster_x - 5, poster_y - 5, poster_x + poster_w + 5, poster_y + poster_h + 5),
                        radius=14, outline=(0, 240, 255, 230), width=2,
                    )
                    draw.rounded_rectangle(
                        (poster_x - 2, poster_y - 2, poster_x + poster_w + 2, poster_y + poster_h + 2),
                        radius=11, outline=(255, 0, 128, 200), width=2,
                    )
                    canvas.paste(p_rounded, (poster_x, poster_y), p_rounded)
                except Exception as pe:
                    log.debug("Neon cyber poster error: %s", pe)

            text_x = 475
            curr_y = 115
            available_w = CANVAS_WIDTH - text_x - 65

            # Cyber Header Badge
            draw.text((text_x, curr_y), "// CYBER DIRECT LINK // SPEED 10Gbps", font=_get_font(20, bold=True, weight="bold"), fill=(0, 240, 255, 255))
            curr_y += 44

            # Title
            title_lines, font_title, line_height = _wrap_and_fit_title(
                draw, title, max_w=available_w, base_size=46, min_size=32, max_lines=2,
            )
            for line in title_lines:
                draw.text((text_x + 2, curr_y + 2), line, font=font_title, fill=(0, 240, 255, 140))
                draw.text((text_x, curr_y), line, font=font_title, fill=(255, 255, 255, 255))
                curr_y += line_height
            curr_y += 18

            # Cyber Episode Tag
            ep_tag = _format_episode_tag(episode_info, is_movie)
            cyber_ep = f"// {ep_tag} //"
            font_ep = _get_font(23, bold=True, weight="extrabold")
            e_bb = draw.textbbox((0, 0), cyber_ep, font=font_ep)
            ew = (e_bb[2] - e_bb[0]) + 44
            eh = 50
            draw.rectangle((text_x, curr_y, text_x + ew, curr_y + eh), fill=(255, 0, 128, 220), outline=(0, 240, 255, 255), width=2)
            draw.text((text_x + 22, curr_y + 12), cyber_ep, font=font_ep, fill=(255, 255, 255, 255))
            curr_y += eh + 26

            # Badges
            q_label = (quality or "1080P").upper()
            draw.text((text_x, curr_y), f"QUALITY : [ {q_label} ]", font=_get_font(22, bold=True, weight="bold"), fill=(0, 240, 255, 255))
            curr_y += 34
            audio_text = _format_audio_tag(audio)
            draw.text((text_x, curr_y), f"AUDIO : [ {audio_text} ]", font=_get_font(22, bold=False, weight="semibold"), fill=(255, 0, 128, 255))
            curr_y += 46

            # Neon Divider
            draw.line([(text_x, curr_y), (CANVAS_WIDTH - 65, curr_y)], fill=(0, 240, 255, 140), width=2)
            curr_y += 20
            draw.text((text_x, curr_y), f"SYS.OP: @{bot_username.lstrip('@')}", font=_get_font(20, bold=True, weight="semibold"), fill=(148, 163, 184, 255))

            return _save_optimized_jpeg(canvas, output_path)
        except Exception as e:
            log.error("NeonCyberTemplate generation error: %s", e, exc_info=True)
            return None


# ── Template 5: Minimal Card (Frosted Matte Studio) ───────────────────────

@register_template("minimal")
class MinimalCardTemplate(BaseThumbnailTemplate):
    """Clean frosted glass card with refined minimalist typography and high-contrast badges."""
    name = "minimal"
    display_name = "Minimal Card"
    description = "Refined frosted glass card with modern, uncluttered typography."

    def generate(
        self,
        title: str,
        episode_info: str = "",
        quality: str = "720p",
        audio: str = "Hindi Dub",
        poster_path: str = "",
        output_path: str = "",
        bot_username: str = "AnimeDekhoBot",
        is_movie: bool = False,
    ) -> str | None:
        try:
            canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (15, 23, 42, 255))
            bg = _prepare_blurred_bg(poster_path, blur_radius=34)
            if bg:
                canvas.paste(bg, (0, 0))

            # Translucent Frosted Glass Card in Center
            card_x1, card_y1 = 45, 45
            card_x2, card_y2 = CANVAS_WIDTH - 45, CANVAS_HEIGHT - 45
            glass_card = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            draw_card = ImageDraw.Draw(glass_card)
            draw_card.rounded_rectangle(
                (card_x1, card_y1, card_x2, card_y2),
                radius=24, fill=(15, 23, 42, 220), outline=(255, 255, 255, 60), width=1,
            )
            canvas = Image.alpha_composite(canvas, glass_card)
            draw = ImageDraw.Draw(canvas)

            # Poster
            poster_w, poster_h = 340, 500
            poster_x, poster_y = 80, 110
            p_img = _resolve_poster_image(poster_path)
            if p_img:
                try:
                    p_resized = p_img.resize((poster_w, poster_h), Image.Resampling.LANCZOS)
                    p_rounded = _round_corners(p_resized, radius=16)
                    draw.rounded_rectangle(
                        (poster_x - 2, poster_y - 2, poster_x + poster_w + 2, poster_y + poster_h + 2),
                        radius=18, outline=(255, 255, 255, 90), width=1,
                    )
                    canvas.paste(p_rounded, (poster_x, poster_y), p_rounded)
                except Exception as pe:
                    log.debug("Minimal poster error: %s", pe)

            text_x = 470
            curr_y = 120
            available_w = card_x2 - text_x - 50

            # Minimal High-Contrast Dark Pill Badge (Prevents white-on-white washed out text)
            q_label, _ = _get_quality_pill(quality)
            audio_text = _format_audio_tag(audio)
            ep_tag = _format_episode_tag(episode_info, is_movie)
            pill_text = f"{q_label}  •  {audio_text}  •  {ep_tag}"

            font_pill = _get_font(20, bold=True, weight="bold")
            p_bb = draw.textbbox((0, 0), pill_text, font=font_pill)
            pw = (p_bb[2] - p_bb[0]) + 36
            ph = 40
            draw.rounded_rectangle(
                (text_x, curr_y, text_x + pw, curr_y + ph),
                radius=8, fill=(30, 41, 59, 240), outline=(56, 189, 248, 140), width=1,
            )
            draw.text((text_x + 18, curr_y + 9), pill_text, font=font_pill, fill=(56, 189, 248, 255))
            curr_y += ph + 24

            # Title
            title_lines, font_title, line_height = _wrap_and_fit_title(
                draw, title, max_w=available_w, base_size=46, min_size=32, max_lines=2,
            )
            for line in title_lines:
                draw.text((text_x, curr_y), line, font=font_title, fill=(255, 255, 255, 255))
                curr_y += line_height
            curr_y += 20

            # Subtle Divider
            draw.line([(text_x, curr_y), (card_x2 - 50, curr_y)], fill=(255, 255, 255, 30), width=1)
            curr_y += 30

            # Description features
            font_feat = _get_font(21, bold=False, weight="medium")
            draw.text((text_x, curr_y), "• Original Studio Quality Direct Stream", font=font_feat, fill=(148, 163, 184, 255))
            curr_y += 32
            draw.text((text_x, curr_y), "• Multi-Language Audio Tracks Included", font=font_feat, fill=(148, 163, 184, 255))
            curr_y += 32
            draw.text((text_x, curr_y), "• Instant Fast Telegram Playback", font=font_feat, fill=(148, 163, 184, 255))
            curr_y += 48

            # Branding
            draw.text((text_x, curr_y), f"@{bot_username.lstrip('@')}", font=_get_font(22, bold=True, weight="bold"), fill=(56, 189, 248, 255))

            return _save_optimized_jpeg(canvas, output_path)
        except Exception as e:
            log.error("MinimalCardTemplate generation error: %s", e, exc_info=True)
            return None


# ── Public API & Generator ─────────────────────────────────────────────────

def list_available_templates() -> list[str]:
    """Return all registered template names."""
    return list(TEMPLATES.keys())


def get_template(
    name: str = "",
    is_movie: bool = False,
    random_mode: bool = False,
) -> BaseThumbnailTemplate:
    """
    Resolve template instance:
    - If random_mode or name=='random', randomly choose from available templates.
    - If is_movie and no name provided, prefer 'movie_gold' or 'cinematic'.
    - Fallback to 'modern'.
    """
    if random_mode or (name and name.lower() == "random"):
        chosen_name = random.choice(list(TEMPLATES.keys()))
        cls = TEMPLATES.get(chosen_name, ModernGradientTemplate)
        return cls()

    key = name.lower().strip() if name else ""
    if not key and is_movie:
        key = "movie_gold"

    cls = TEMPLATES.get(key)
    if not cls:
        cls = ModernGradientTemplate
    return cls()


def generate_auto_thumbnail(
    title: str,
    episode_info: str = "",
    quality: str = "720p",
    audio: str = "Hindi Dub",
    poster_path: str = "",
    output_path: str = "",
    bot_username: str = "AnimeDekhoBot",
    template_name: str | None = None,
    is_movie: bool = False,
) -> str | None:
    """
    Generate a 1280x720 professional YouTube/Telegram video thumbnail using the selected template.
    Template resolution:
    1. Explicit `template_name` argument if provided.
    2. Config `RANDOM_THUMB_TEMPLATE` or DB `random_thumb_template` if enabled.
    3. Config `THUMB_TEMPLATE` or DB `thumb_template`.
    4. Fallback to 'modern' (or 'movie_gold' for movies).
    """
    try:
        if not output_path:
            output_path = os.path.join(
                gettempdir(),
                f"thumb_auto_{os.getpid()}_{int(os.times().elapsed * 1000)}_{random.randint(100, 999)}.jpg"
            )

        resolved_template_name = template_name
        is_random = False

        if not resolved_template_name:
            try:
                from config import Config
                resolved_template_name = getattr(Config, "THUMB_TEMPLATE", "modern")
                is_random = getattr(Config, "RANDOM_THUMB_TEMPLATE", False)
            except Exception:
                resolved_template_name = "modern"

        template = get_template(
            name=resolved_template_name or "modern",
            is_movie=is_movie,
            random_mode=is_random,
        )

        log.info("Generating auto-thumbnail for '%s' using template '%s' (is_movie=%s)", title, template.name, is_movie)
        result = template.generate(
            title=title,
            episode_info=episode_info,
            quality=quality,
            audio=audio,
            poster_path=poster_path,
            output_path=output_path,
            bot_username=bot_username,
            is_movie=is_movie,
        )
        return result
    except Exception as e:
        log.error("Failed generating auto thumbnail: %s", e, exc_info=True)
        return None


def generate_thumbnail(
    title: str,
    episode_info: str = "",
    quality: str = "720p",
    audio: str = "Hindi Dub",
    poster_path: str = "",
    output_path: str = "",
    bot_username: str = "AnimeDekhoBot",
    template: str | None = None,
    template_name: str | None = None,
    is_movie: bool = False,
    season: int = 1,
    episode: int = 1,
    **kwargs,
) -> str | None:
    """Compatibility wrapper for generate_auto_thumbnail."""
    if not episode_info and not is_movie:
        episode_info = f"S{season:02d} E{episode:02d}"
    return generate_auto_thumbnail(
        title=title,
        episode_info=episode_info,
        quality=quality,
        audio=audio,
        poster_path=poster_path,
        output_path=output_path,
        bot_username=bot_username,
        template_name=template or template_name,
        is_movie=is_movie,
    )
