"""
Auto Thumbnail Generator for AnimeDekho Bot — Modular Multi-Template Architecture.

Issue #33 retired the five older styles in favour of ONE Netflix/streaming
key-art card (issue #33 reference screenshots):
- 'streaming': brand lockup (optional PNG logo + channel handle), wide-tracked
  eyebrow, oversized title, metadata bullets, DOWNLOAD + quality pills, and a
  handle watermark over feathered key art.

Legacy template names ('modern', 'cinematic', 'movie_gold', 'neon_cyber',
'minimal') are aliased to 'streaming' so old configs keep rendering.

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
        brand_username: str = "",
        logo_path: str = "",
    ) -> str | None:
        raise NotImplementedError


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


def _tracked_text(draw, xy, text: str, font, fill, tracking: int = 3) -> int:
    """Letter-spaced text (the reference art uses wide-tracked eyebrows)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textbbox((0, 0), ch, font=font)[2] + tracking
    return x - xy[0]


def _tracked_width(draw, text: str, font, tracking: int = 3) -> int:
    w = 0
    for ch in text:
        w += draw.textbbox((0, 0), ch, font=font)[2] + tracking
    return max(0, w - tracking)


def _load_logo_image(logo_path: str) -> Image.Image | None:
    """Load the admin-supplied PNG/JPG brand logo (issue #33 optional extra)."""
    if not logo_path or not os.path.exists(str(logo_path)):
        return None
    try:
        with Image.open(logo_path) as im:
            return im.convert("RGBA")
    except Exception as e:
        log.debug("Failed loading brand logo: %s", e)
        return None


@register_template("streaming")
class StreamingCardTemplate(BaseThumbnailTemplate):
    """Netflix-style key-art hero: art bleeds on the right, info rail on the left.

    Mirrors the reference art in issue #33: brand lockup + wide-tracked eyebrow
    on top, oversized title, metadata bullets, CTA + quality pills, handle
    watermark over the art.
    """
    name = "streaming"
    display_name = "Streaming Card"
    description = "Netflix-style key-art hero with info rail, brand lockup and quality pills."

    # Violet → pink → cyan signature, used for the underline, eyebrow and CTA.
    ACCENT = [
        (0.0, (167, 139, 250, 255)),
        (0.55, (236, 72, 153, 255)),
        (1.0, (34, 211, 238, 255)),
    ]

    # Left info rail geometry.
    RAIL_X = 72
    RAIL_W = 566
    PANEL = (13, 11, 24, 255)

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
        brand_username: str = "",
        logo_path: str = "",
    ) -> str | None:
        try:
            W, H = CANVAS_WIDTH, CANVAS_HEIGHT
            canvas = Image.new("RGBA", (W, H), self.PANEL)

            # ── 1. Key art on the right, feathered into the panel ──────────
            art = _resolve_poster_image(poster_path)
            if art:
                # cover-crop into the art box (x >= ART_X)
                ART_X = 500
                box_w, box_h = W - ART_X, H
                scale = max(box_w / art.width, box_h / art.height)
                art = art.resize((max(1, int(art.width * scale)), max(1, int(art.height * scale))),
                                 Image.Resampling.LANCZOS)
                ox = max(0, (art.width - box_w) // 2)
                oy = max(0, (art.height - box_h) // 2)
                art = art.crop((ox, oy, ox + box_w, oy + box_h))
                # Feather: art fully hidden under the rail, ramping in right.
                mask = Image.new("L", (W, H), 0)
                md = ImageDraw.Draw(mask)
                ramp_start, ramp_end = 560, 940
                for x in range(W):
                    if x < ramp_start:
                        a = 0
                    elif x > ramp_end:
                        a = 255
                    else:
                        a = int(255 * ((x - ramp_start) / (ramp_end - ramp_start)) ** 0.85)
                    md.line([(x, 0), (x, H - 1)], fill=a)
                layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                layer.paste(art, (ART_X, 0))
                canvas.paste(layer, (0, 0), mask)
            else:
                # No art — keep the panel and add a soft glow so it isn't flat.
                canvas.alpha_composite(_radial_glow((W, H), (1030, 360), 470, (167, 139, 250), 44))

            # Vertical scrim on the art so the bottom watermark stays legible.
            scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(scrim)
            for y in range(int(H * 0.55), H):
                a = int(150 * ((y - H * 0.55) / (H * 0.45)))
                sd.line([(0, y), (W, y)], fill=(5, 4, 12, a))
            canvas.alpha_composite(scrim)

            # Panel edge shade: keep the rail readable where art bleeds in.
            edge = _multi_gradient((W, H), [
                (0.0, (13, 11, 24, 255)),
                (0.44, (13, 11, 24, 255)),
                (0.68, (13, 11, 24, 0)),
                (1.0, (13, 11, 24, 0)),
            ])
            canvas.alpha_composite(edge)

            draw = ImageDraw.Draw(canvas)
            # Translucent strokes go on this layer: ImageDraw writes alpha
            # INTO the RGBA canvas (it doesn't blend), and _save_optimized_jpeg
            # flattens straight to RGB — so a 26%-white pill would land as solid
            # white. Compositing the layer afterwards preserves the intent.
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            x0 = self.RAIL_X
            right = x0 + self.RAIL_W

            def _flush_overlay() -> None:
                """Composite translucent strokes, then start a fresh layer."""
                nonlocal overlay, od
                canvas.alpha_composite(overlay)
                overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)

            brand = (brand_username or bot_username or "AnimeDekhoBot").lstrip("@").strip()
            handle = f"@{brand}" if brand else "@AnimeDekhoBot"

            # ── 2. Brand lockup: logo tile (or monogram) + wide-tracked name ─
            by = 52
            logo = _load_logo_image(logo_path)
            if logo:
                try:
                    side = 60
                    lg = logo.copy()
                    # cover-crop to a square, then round the corners
                    s = max(side / lg.width, side / lg.height)
                    lg = lg.resize((max(1, int(lg.width * s)), max(1, int(lg.height * s))),
                                   Image.Resampling.LANCZOS)
                    lg = lg.crop(((lg.width - side) // 2, (lg.height - side) // 2,
                                  (lg.width - side) // 2 + side, (lg.height - side) // 2 + side))
                    lg = _round_corners(lg, radius=14)
                    sh = Image.new("RGBA", (side + 30, side + 30), (0, 0, 0, 0))
                    ImageDraw.Draw(sh).rounded_rectangle(
                        (15, 15, side + 15, side + 15), radius=16, fill=(0, 0, 0, 190))
                    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(radius=10)), (x0 - 15, by - 15))
                    canvas.paste(lg, (x0, by), lg)
                    draw.rounded_rectangle((x0, by, x0 + side, by + side), radius=14,
                                           outline=(255, 255, 255, 80), width=2)
                    tx_logo = x0 + side + 20
                except Exception as le:
                    log.debug("Logo paste failed: %s", le)
                    tx_logo = x0
            else:
                # Monogram tile — same footprint as the logo version.
                side = 60
                canvas.alpha_composite(_rounded_gradient_layer(
                    (W, H), (x0, by, x0 + side, by + side), 14, self.ACCENT))
                draw.rounded_rectangle((x0, by, x0 + side, by + side), radius=14,
                                       outline=(255, 255, 255, 90), width=2)
                mono = (brand[:2] or "AD").upper()
                font_mono = _get_font(26, bold=True, weight="extrabold")
                mb = draw.textbbox((0, 0), mono, font=font_mono)
                draw.text(
                    (x0 + side // 2 - (mb[2] - mb[0]) // 2 - mb[0],
                     by + side // 2 - (mb[3] - mb[1]) // 2 - mb[1]),
                    mono, font=font_mono, fill=(255, 255, 255, 255),
                )
                tx_logo = x0 + side + 20

            font_brand = _get_font(27, bold=True, weight="extrabold")
            nb = draw.textbbox((0, 0), brand.upper(), font=font_brand)
            draw.text((tx_logo, by + 6), brand.upper(), font=font_brand, fill=(255, 255, 255, 255))
            # Gradient underline beneath the brand name.
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (tx_logo, by + 44, tx_logo + max(40, nb[2] - nb[0]), by + 49), 3, self.ACCENT))

            # ── 3. Rule + wide-tracked eyebrow ─────────────────────────────
            ry = by + 76
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (x0, ry, right, ry + 2), 1,
                [(0.0, (255, 255, 255, 90)), (1.0, (255, 255, 255, 12))],
            ))

            eyebrow = ("MOVIE" if is_movie else "ANIME") + "  •  " + _format_audio_tag(audio)
            font_eye = _get_font(19, bold=True, weight="bold")
            _tracked_text(draw, (x0, ry + 24), eyebrow, font_eye,
                          (196, 181, 253, 255), tracking=4)

            # ── 4. Oversized title ─────────────────────────────────────────
            ty = ry + 66
            title_lines, font_title, line_h = _wrap_and_fit_title(
                draw, title, max_w=self.RAIL_W, base_size=64, min_size=40, max_lines=3,
            )
            for line in title_lines:
                # White with a soft shadow — the reference title is flat white.
                bb = draw.textbbox((0, 0), line, font=font_title)
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
                tmask = Image.new("L", (tw + 20, th + 20), 0)
                ImageDraw.Draw(tmask).text((10 - bb[0], 10 - bb[1]), line, font=font_title, fill=255)
                sh = Image.new("RGBA", tmask.size, (0, 0, 0, 255))
                sh.putalpha(tmask.point(lambda p: p * 170 // 255))
                canvas.alpha_composite(sh, (x0 - 10 + 3, ty - 10 + 5))
                txt = Image.new("RGBA", tmask.size, (0, 0, 0, 0))
                txt.paste(Image.new("RGBA", tmask.size, (255, 255, 255, 255)), (0, 0), tmask)
                canvas.alpha_composite(txt, (x0 - 10, ty - 10))
                ty += line_h
            ty += 20

            # ── 5. Metadata bullets (matches the channel post format) ───────
            font_meta = _get_font(21, bold=False, weight="medium")
            meta_lines = []
            if is_movie:
                meta_lines.append(("QUALITY", (quality or "HD").upper()))
            else:
                if episode_info:
                    # episode_info often already reads "Episodes: 12 | S01"
                    # (the channel-post format) — don't prefix a second label.
                    raw_ep = _clean_text(episode_info).strip()
                    if re.match(r"(?i)^episodes?\b", raw_ep):
                        meta_lines.append((None, raw_ep.upper()))
                    else:
                        meta_lines.append(("EPISODE", raw_ep.upper()))
                meta_lines.append(("AUDIO TRACK", _format_audio_tag(audio)))
                meta_lines.append(("QUALITY", (quality or "HD").upper()))
            for label, value in meta_lines:
                dot_x = x0
                draw.ellipse((dot_x, ty + 8, dot_x + 8, ty + 16), fill=(236, 72, 153, 255))
                if label is None:
                    draw.text((dot_x + 20, ty), value, font=font_meta, fill=(226, 232, 240, 255))
                    ty += 34
                    continue
                lb = draw.textbbox((0, 0), f"{label}: ", font=font_meta)
                draw.text((dot_x + 20, ty), f"{label}:", font=font_meta, fill=(148, 163, 184, 255))
                draw.text((dot_x + 20 + (lb[2] - lb[0]) + 4, ty), value,
                          font=font_meta, fill=(226, 232, 240, 255))
                ty += 34
            ty += 16

            # ── 6. CTA + quality pills ─────────────────────────────────────
            font_pill = _get_font(20, bold=True, weight="bold")
            # Primary "DOWNLOAD" pill (gradient fill).
            cta = "DOWNLOAD"
            cb = draw.textbbox((0, 0), cta, font=font_pill)
            cw, chh = (cb[2] - cb[0]) + 56, 54
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (x0, ty, x0 + cw, ty + chh), 14,
                [(0.0, _shade_rgba(self.ACCENT[0][1], 1.0)), (1.0, _shade_rgba(self.ACCENT[1][1], 1.0))],
            ))
            draw.text((x0 + 28, ty + 15), cta, font=font_pill, fill=(255, 255, 255, 255))
            # Secondary outline pill: quality tier.
            q_label, q_color = _get_quality_pill(quality)
            qb = draw.textbbox((0, 0), q_label, font=font_pill)
            qx = x0 + cw + 16
            qw = (qb[2] - qb[0]) + 44
            # Translucent fill → overlay (see note above), then flush so the
            # label is painted on top of it rather than under it.
            od.rounded_rectangle((qx, ty, qx + qw, ty + chh), radius=14,
                                 fill=(255, 255, 255, 26), outline=(255, 255, 255, 130), width=2)
            _flush_overlay()
            draw.text((qx + 22, ty + 15), q_label, font=font_pill, fill=(241, 245, 249, 255))
            # Dot accent in the pill's tier color.
            draw.ellipse((qx + qw - 20, ty + chh // 2 - 5, qx + qw - 10, ty + chh // 2 + 5),
                         fill=q_color[:3] + (255,))

            # ── 7. Watermarks: handle bottom-right, brand note bottom-left ──
            font_wm = _get_font(22, bold=True, weight="bold")
            wm = handle.upper()
            wb = draw.textbbox((0, 0), wm, font=font_wm)
            ww = wb[2] - wb[0]
            wmx, wmy = W - 64 - ww, H - 58
            # Shadow pass on the overlay so it stays a soft shadow (26-170 alpha
            # written straight onto RGBA would flatten to solid black).
            od.text((wmx + 2, wmy + 3), wm, font=font_wm, fill=(0, 0, 0, 170))
            _flush_overlay()
            draw.text((wmx, wmy), wm, font=font_wm, fill=(255, 255, 255, 235))
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (wmx, wmy + 30, wmx + ww, wmy + 33), 2, self.ACCENT))

            # Small HD/quality tag on the art, top-right.
            tag = q_label.split(" • ")[0]
            font_tag = _get_font(20, bold=True, weight="extrabold")
            tb = draw.textbbox((0, 0), tag, font=font_tag)
            tw = (tb[2] - tb[0]) + 30
            tx = W - 64 - tw
            od.rounded_rectangle((tx, 52, tx + tw, 52 + 42), radius=11,
                                 fill=(10, 9, 18, 210), outline=(255, 255, 255, 110), width=2)
            _flush_overlay()
            draw.text((tx + 15, 62), tag, font=font_tag, fill=(255, 255, 255, 240))

            _flush_overlay()  # nothing pending — guarantees no stroke is lost

            return _save_optimized_jpeg(canvas, output_path)
        except Exception as e:
            log.error("StreamingCardTemplate generation error: %s", e, exc_info=True)
            return None


# ── Public API & Generator ─────────────────────────────────────────────────

def list_available_templates() -> list[str]:
    """Return all registered template names."""
    return list(TEMPLATES.keys())


#: The single template new/legacy configs resolve to.
DEFAULT_TEMPLATE = "streaming"


def get_template(
    name: str = "",
    is_movie: bool = False,
    random_mode: bool = False,
) -> BaseThumbnailTemplate:
    """
    Resolve template instance.

    Issue #33 retired the old templates ('modern', 'cinematic', 'movie_gold',
    'neon_cyber', 'minimal') in favour of a single streaming-card style, so
    any legacy name a deployment still has stored resolves to it instead of
    silently regressing to a removed class.
    """
    del is_movie  # one style now serves both movies and series
    if random_mode or (name and name.lower() == "random"):
        cls = TEMPLATES.get(random.choice(list(TEMPLATES.keys())), StreamingCardTemplate)
        return cls()

    key = name.lower().strip() if name else ""
    cls = TEMPLATES.get(key) or TEMPLATES.get(DEFAULT_TEMPLATE) or StreamingCardTemplate
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
    brand_username: str = "",
    logo_path: str = "",
) -> str | None:
    """
    Generate a 1280x720 professional YouTube/Telegram video thumbnail using the selected template.
    Template resolution:
    1. Explicit `template_name` argument if provided.
    2. Config `RANDOM_THUMB_TEMPLATE` or DB `random_thumb_template` if enabled.
    3. Config `THUMB_TEMPLATE` or DB `thumb_template`.
    4. Fallback to 'streaming' (legacy names are aliased there too).

    brand_username / logo_path: optional admin-set channel handle and PNG logo
    for the lockup (issue #33 "optional" extra).
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
                resolved_template_name = getattr(Config, "THUMB_TEMPLATE", DEFAULT_TEMPLATE)
                is_random = getattr(Config, "RANDOM_THUMB_TEMPLATE", False)
            except Exception:
                resolved_template_name = DEFAULT_TEMPLATE

        if not brand_username or not logo_path:
            # Owner-set branding (persisted by /thumbuser + /thumblogo).
            try:
                from config import Config
                brand_username = brand_username or str(getattr(Config, "THUMB_BRAND_USERNAME", "") or "")
                logo_path = logo_path or str(getattr(Config, "THUMB_BRAND_LOGO", "") or "")
            except Exception:
                pass

        template = get_template(
            name=resolved_template_name or DEFAULT_TEMPLATE,
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
            brand_username=brand_username,
            logo_path=logo_path,
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
        **{k: kwargs[k] for k in ("brand_username", "logo_path") if k in kwargs},
    )
