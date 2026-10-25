"""
Auto Thumbnail Generator for AnimeDekho Bot — Modular Multi-Template Architecture.

 Issue #33 retired the five older styles in favour of one full-bleed anime
 hero card: compact channel mark, poster-forward artwork, smaller title, short
 synopsis, and one red WATCH NOW action.

Legacy template names ('modern', 'cinematic', 'movie_gold', 'neon_cyber',
'minimal') are aliased to 'streaming' so old configs keep rendering.

Also includes `enhance_custom_thumbnail` to fix low-quality / blurry custom thumbnails,
ensuring razor-sharp text, proper 16:9 framing, and Telegram video player optimization.
"""

from __future__ import annotations

import logging
import html
import math
import os
import random
import re
import shutil
from pathlib import Path
from tempfile import gettempdir
from typing import Dict, Type

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

log = logging.getLogger(__name__)

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
PHOTO_EXPORT_MAX_KB = 1800
VIDEO_THUMB_MAX_KB = 190

#: Art narrower than this is explicitly upscaled (see :func:`_upscale_art`).
#: Metadata providers cap covers near 460px (AniList extraLarge), so on a
#: 1280px canvas the art is interpolated regardless; doing it once here with a
#: sharpening pass beats letting each template resize blur it a second time.
ART_UPSCALE_MIN_WIDTH = CANVAS_WIDTH

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


def _upscale_art(im: Image.Image, min_width: int = ART_UPSCALE_MIN_WIDTH) -> Image.Image:
    """Upscale small poster art toward the canvas with a sharpening pass.

    AniList (and similar metadata providers) cap covers near 460px, so a
    1280px canvas force-scales them ~2x. Doing that here with a stepped
    LANCZOS resize plus a restrained unsharp mask yields crisper edges than a
    single large resize. NOTE: this is interpolation — it cannot recover detail
    the source never had; it only avoids a softer second resample.
    """
    if im.width >= min_width:
        return im
    step = im
    # Doubling in stages keeps LANCZOS ringing low on big ratios.
    while step.width * 2 <= min_width:
        step = step.resize((step.width * 2, step.height * 2), Image.Resampling.LANCZOS)
    if step.width != min_width:
        ratio = min_width / step.width
        step = step.resize(
            (min_width, max(1, int(round(step.height * ratio)))),
            Image.Resampling.LANCZOS,
        )
    return step.filter(ImageFilter.UnsharpMask(radius=1.4, percent=85, threshold=3))


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
        return _upscale_art(Image.open(target).convert("RGBA"))
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


def _save_optimized_jpeg(img: Image.Image, output_path: str, max_kb: int = PHOTO_EXPORT_MAX_KB) -> str:
    """Save a high-quality photo JPEG within the configured export budget."""
    final = img.convert("RGB")
    final = ImageEnhance.Contrast(final).enhance(1.05)
    final = ImageEnhance.Color(final).enhance(1.04)
    final = ImageEnhance.Sharpness(final).enhance(1.20)
    final = final.filter(ImageFilter.UnsharpMask(radius=1.2, percent=125, threshold=2))

    # Progressive JPEG with 4:4:4 chroma subsampling (subsampling=0) and 300 DPI metadata
    q = 96
    while q >= 75:
        final.save(
            output_path,
            "JPEG",
            quality=q,
            optimize=True,
            progressive=True,
            subsampling=0,
            dpi=(300, 300),
        )
        try:
            if os.path.getsize(output_path) <= max_kb * 1024:
                break
        except Exception:
            break
        q -= 3
    return output_path


# ── Custom Thumbnail Enhancement Function ──────────────────────────────────

def enhance_custom_thumbnail(
    input_path: str,
    output_path: str | None = None,
    target_width: int = 320,
    target_height: int = 180,
) -> str | None:
    """
    Prepare a Telegram-sized video thumbnail, preserving valid JPEGs unchanged.

    Video thumbnail uploads are limited to 320 px per side and 200 KB. Full-size
    album cards use :func:`generate_thumbnail` and remain 1280×720.
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
            w, h = im.size
            if w <= 0 or h <= 0:
                return None

            # Avoid a second lossy encode when an input already matches the
            # platform's size, format, and file-size requirements.
            if (
                im.format == "JPEG"
                and im.size == (target_width, target_height)
                and os.path.getsize(input_path) < VIDEO_THUMB_MAX_KB * 1024
            ):
                if os.path.abspath(input_path) != os.path.abspath(output_path):
                    shutil.copyfile(input_path, output_path)
                return output_path

            im = im.convert("RGBA")

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

            # Keep color and edge treatment subtle to avoid over-processing.
            canvas = ImageEnhance.Contrast(canvas).enhance(1.03)
            canvas = ImageEnhance.Color(canvas).enhance(1.02)
            canvas = ImageEnhance.Sharpness(canvas).enhance(1.05)
            canvas = canvas.filter(ImageFilter.UnsharpMask(radius=0.8, percent=70, threshold=3))

            # Telegram video-thumbnail requirements: JPEG, <200 KB, <=320 px.
            q = 96
            while q >= 75:
                canvas.save(
                    output_path,
                    "JPEG",
                    quality=q,
                    optimize=True,
                    progressive=True,
                    subsampling=0,
                    dpi=(300, 300),
                )
                try:
                    if os.path.getsize(output_path) < VIDEO_THUMB_MAX_KB * 1024:
                        break
                except Exception:
                    break
                q -= 3

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
        synopsis: str = "",
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


def _cinematic_color_grade(image: Image.Image) -> Image.Image:
    """Boost contrast, split-tone shadows/highlights, and add restrained bloom."""
    graded = ImageEnhance.Contrast(image.convert("RGB")).enhance(1.30)
    graded = ImageEnhance.Color(graded).enhance(1.24)

    luminance = ImageOps.grayscale(graded)
    split_tone = ImageOps.colorize(luminance, black=(12, 18, 40), white=(255, 224, 196))
    graded = Image.blend(graded, split_tone, 0.24)

    luminance = ImageOps.grayscale(graded)
    highlight_mask = luminance.point(lambda p: max(0, min(255, (p - 178) * 255 // 77)))
    highlights = Image.new("RGB", graded.size, (0, 0, 0))
    highlights.paste(graded, mask=highlight_mask)
    bloom = highlights.filter(ImageFilter.GaussianBlur(radius=max(8, int(min(graded.size) * 0.026))))
    graded = Image.blend(graded, ImageChops.screen(graded, bloom), 0.28)
    return ImageEnhance.Sharpness(graded).enhance(1.10)


def _build_edge_vignette(size, max_alpha: int = 54) -> Image.Image:
    """Softly darken the outer frame while preserving a clear center for the poster."""
    w, h = max(1, size[0]), max(1, size[1])
    mask = Image.new("L", (w, h), max(0, min(255, max_alpha)))
    margin_x, margin_y = int(w * 0.12), int(h * 0.12)
    ImageDraw.Draw(mask).ellipse((margin_x, margin_y, w - margin_x, h - margin_y), fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(8, int(min(w, h) * 0.10))))
    layer = Image.new("RGBA", (w, h), (4, 6, 18, 0))
    layer.putalpha(mask)
    return layer


def _particle_overlay(size, seed: int) -> Image.Image:
    """Scatter restrained, repeatable light flecks over the artwork side only."""
    w, h = max(1, size[0]), max(1, size[1])
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if w < 24 or h < 32:
        return layer
    rng = random.Random(int(seed) & 0xFFFFFFFF)
    draw = ImageDraw.Draw(layer)
    palette = ((236, 244, 255), (255, 194, 211), (157, 221, 255), (255, 223, 169))
    count = max(20, int((w * h) / 7000))
    x_min = min(w - 16, int(w * 0.50))
    y_min, y_max = 16, max(17, h - 68)
    for index in range(count):
        x = rng.randint(x_min, w - 16)
        y = rng.randint(y_min, y_max)
        radius = rng.choices((1, 2, 3), weights=(52, 38, 10), k=1)[0]
        alpha = rng.randint(46, 120)
        color = rng.choice(palette) + (alpha,)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        if index % 15 == 0:
            draw.line((x - 7, y, x + 7, y), fill=color[:3] + (alpha // 2,), width=1)
            draw.line((x, y - 7, x, y + 7), fill=color[:3] + (alpha // 2,), width=1)
    return layer


def _apply_film_grain(image: Image.Image, seed: int, opacity: float = 0.10) -> Image.Image:
    """Blend a subtle deterministic grain texture into the image, not the typography."""
    rgb = image.convert("RGB")
    w, h = rgb.size
    grain_size = (max(1, w // 3), max(1, h // 3))
    rng = random.Random((int(seed) ^ 0x9E3779B9) & 0xFFFFFFFF)
    grain = Image.new("L", grain_size)
    grain.putdata([rng.randint(88, 168) for _ in range(grain_size[0] * grain_size[1])])
    grain = grain.resize((w, h), Image.Resampling.BICUBIC)
    grain_rgb = Image.merge("RGB", (grain, grain, grain))
    return Image.blend(rgb, grain_rgb, max(0.0, min(1.0, opacity)))


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
    """Poster-forward anime hero with a compact title and metadata rail."""
    name = "streaming"
    display_name = "Streaming Card"
    description = "Full-bleed anime art with a compact left rail and one watch action."

    # Violet → pink → cyan signature, used for the underline, eyebrow and CTA.
    ACCENT = [
        (0.0, (167, 139, 250, 255)),
        (0.55, (236, 72, 153, 255)),
        (1.0, (34, 211, 238, 255)),
    ]

    # Left info rail geometry; artwork remains visible beneath the scrim.
    RAIL_X = 72
    RAIL_W = 520
    ART_BOX_RATIO = 0.72
    TITLE_BASE_SIZE = 56
    TITLE_MIN_SIZE = 38
    EPISODE_SEASON_SIZE = 18
    EPISODE_COUNT_SIZE = 23
    EPISODE_CHIP_HEIGHT = 40
    SYNOPSIS_MAX_LINES = 2
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
        synopsis: str = "",
    ) -> str | None:
        try:
            W, H = CANVAS_WIDTH, CANVAS_HEIGHT
            canvas = Image.new("RGBA", (W, H), self.PANEL)
            seed_text = _clean_text(str(title or "")).casefold()
            effect_seed = sum((index + 1) * ord(char) for index, char in enumerate(seed_text)) & 0xFFFFFFFF

            # ── 1. Full-bleed key art with an editorial dark scrim ─────────
            art = _resolve_poster_image(poster_path)
            if art:
                art = _cinematic_color_grade(art)
                art = art.filter(ImageFilter.UnsharpMask(radius=1.2, percent=125, threshold=2))
                if art.width / max(1, art.height) < 1.30:
                    # Portrait cover art cannot fill 16:9 without losing faces.
                    # Blur it behind a sharper right-side crop for a full-bleed
                    # composition that still preserves the anime character.
                    backdrop = art.copy()
                    scale = max(W / backdrop.width, H / backdrop.height)
                    nw, nh = max(1, int(backdrop.width * scale)), max(1, int(backdrop.height * scale))
                    backdrop = backdrop.resize((nw, nh), Image.Resampling.LANCZOS)
                    bx = max(0, (backdrop.width - W) // 2)
                    by = max(0, int((backdrop.height - H) * 0.20)) if backdrop.height > H else 0
                    backdrop = backdrop.crop((bx, by, bx + W, by + H))
                    backdrop = ImageEnhance.Brightness(backdrop.filter(
                        ImageFilter.GaussianBlur(radius=22)
                    )).enhance(0.62)
                    canvas = backdrop.convert("RGBA")

                    box_w = int(W * self.ART_BOX_RATIO)
                    art_x = W - box_w
                    scale = max(box_w / art.width, H / art.height)
                    nw, nh = max(1, int(art.width * scale)), max(1, int(art.height * scale))
                    sharp = art.resize((nw, nh), Image.Resampling.LANCZOS)
                    ox = max(0, (sharp.width - box_w) // 2)
                    oy = max(0, int((sharp.height - H) * 0.20)) if sharp.height > H else 0
                    sharp = sharp.crop((ox, oy, ox + box_w, oy + H))
                    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    layer.paste(sharp, (art_x, 0))
                    mask = Image.new("L", (W, H), 0)
                    md = ImageDraw.Draw(mask)
                    ramp_start, ramp_end = art_x - 240, art_x + 160
                    for x in range(W):
                        if x < ramp_start:
                            alpha = 0
                        elif x > ramp_end:
                            alpha = 255
                        else:
                            alpha = int(255 * (x - ramp_start) / max(1, ramp_end - ramp_start))
                        md.line([(x, 0), (x, H - 1)], fill=alpha)
                    canvas.paste(layer, (0, 0), mask)
                else:
                    scale = max(W / art.width, H / art.height)
                    nw, nh = max(1, int(art.width * scale)), max(1, int(art.height * scale))
                    art = art.resize((nw, nh), Image.Resampling.LANCZOS)
                    ox = max(0, (art.width - W) // 2)
                    oy = max(0, int((art.height - H) * 0.20)) if art.height > H else 0
                    art = art.crop((ox, oy, ox + W, oy + H))
                    canvas = art.convert("RGBA")
            else:
                canvas.alpha_composite(_radial_glow((W, H), (1030, 360), 470, (167, 139, 250), 44))

            # Warm bloom, cool fill, and a restrained lens streak light the art side.
            canvas.alpha_composite(_radial_glow(
                (W, H), (int(W * 0.83), int(H * 0.22)), int(H * 0.31), (255, 185, 124), 78,
            ))
            canvas.alpha_composite(_radial_glow(
                (W, H), (int(W * 0.76), int(H * 0.62)), int(H * 0.23), (130, 164, 255), 42,
            ))
            flare = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            flare_draw = ImageDraw.Draw(flare)
            flare_x, flare_y = int(W * 0.82), int(H * 0.18)
            flare_draw.line(
                (int(W * 0.68), flare_y, int(W * 0.96), flare_y),
                fill=(255, 235, 207, 120), width=2,
            )
            flare_draw.ellipse(
                (flare_x - 3, flare_y - 3, flare_x + 3, flare_y + 3),
                fill=(255, 246, 223, 245),
            )
            canvas.alpha_composite(flare.filter(ImageFilter.GaussianBlur(radius=5)))

            # Keep the copy rail compact and let the artwork read through behind it.
            canvas.alpha_composite(_multi_gradient((W, H), [
                (0.0, (5, 6, 13, 232)),
                (0.32, (5, 6, 13, 220)),
                (0.46, (5, 6, 13, 184)),
                (0.64, (5, 6, 13, 82)),
                (0.82, (5, 6, 13, 20)),
                (1.0, (5, 6, 13, 0)),
            ]))

            # Gentle lower vignette keeps the watermark visible over busy art.
            scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(scrim)
            for y in range(int(H * 0.60), H):
                a = int(105 * ((y - H * 0.60) / (H * 0.40)))
                sd.line([(0, y), (W, y)], fill=(5, 4, 12, a))
            canvas.alpha_composite(scrim)
            canvas.alpha_composite(_build_edge_vignette((W, H), max_alpha=76))
            canvas = _apply_film_grain(canvas, effect_seed).convert("RGBA")
            canvas.alpha_composite(_particle_overlay((W, H), effect_seed))

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

            # ── 2. Small channel mark, kept out of the title’s way ─────────
            by = 38
            logo = _load_logo_image(logo_path)
            if logo:
                try:
                    side = 34
                    lg = logo.copy()
                    s = max(side / lg.width, side / lg.height)
                    lg = lg.resize((max(1, int(lg.width * s)), max(1, int(lg.height * s))),
                                   Image.Resampling.LANCZOS)
                    lg = lg.crop(((lg.width - side) // 2, (lg.height - side) // 2,
                                  (lg.width - side) // 2 + side, (lg.height - side) // 2 + side))
                    lg = _round_corners(lg, radius=7)
                    canvas.paste(lg, (x0, by), lg)
                    draw.rounded_rectangle((x0, by, x0 + side, by + side), radius=7,
                                           outline=(255, 255, 255, 100), width=1)
                    tx_logo = x0 + side + 11
                except Exception as le:
                    log.debug("Logo paste failed: %s", le)
                    tx_logo = x0
            else:
                side = 34
                canvas.alpha_composite(_rounded_gradient_layer(
                    (W, H), (x0, by, x0 + side, by + side), 7, self.ACCENT))
                draw.rounded_rectangle((x0, by, x0 + side, by + side), radius=7,
                                       outline=(255, 255, 255, 110), width=1)
                mono = (brand[:2] or "AD").upper()
                font_mono = _get_font(16, bold=True, weight="extrabold")
                mb = draw.textbbox((0, 0), mono, font=font_mono)
                draw.text(
                    (x0 + side // 2 - (mb[2] - mb[0]) // 2 - mb[0],
                     by + side // 2 - (mb[3] - mb[1]) // 2 - mb[1]),
                    mono, font=font_mono, fill=(255, 255, 255, 255),
                )
                tx_logo = x0 + side + 11

            font_brand = _get_font(18, bold=True, weight="bold")
            brand_label = brand.upper()
            if len(brand_label) > 22:
                brand_label = brand_label[:20] + "…"
            nb = draw.textbbox((0, 0), brand_label, font=font_brand)
            draw.text((tx_logo, by + 8), brand_label, font=font_brand, fill=(255, 255, 255, 235))
            draw.rounded_rectangle(
                (tx_logo, by + 30, tx_logo + max(34, nb[2] - nb[0]), by + 32),
                radius=1, fill=(236, 72, 153, 255),
            )

            # ── 3. Rule + wide-tracked eyebrow ─────────────────────────────
            ry = by + 66
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (x0, ry, right, ry + 2), 1,
                [(0.0, (255, 255, 255, 90)), (1.0, (255, 255, 255, 12))],
            ))

            eyebrow = ("MOVIE FEATURE" if is_movie else "ANIME SERIES")
            font_eye = _get_font(18, bold=True, weight="bold")
            _tracked_text(draw, (x0, ry + 17), eyebrow, font_eye,
                          (255, 255, 255, 235), tracking=2)

            # ── 4. Compact title; reserve the right side for key art ────────
            ty = ry + 53
            clean_title = _clean_text(title).upper()
            title_max_lines = 2 if len(clean_title) <= 46 else 3
            title_width = self.RAIL_W
            title_lines, font_title, line_h = _wrap_and_fit_title(
                draw, clean_title, max_w=title_width,
                base_size=self.TITLE_BASE_SIZE, min_size=self.TITLE_MIN_SIZE,
                max_lines=title_max_lines,
            )
            for line in title_lines:
                # White with smooth Gaussian blurred shadow for depth & legibility
                bb = draw.textbbox((0, 0), line, font=font_title)
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
                pad_s = 14
                tmask = Image.new("L", (tw + pad_s * 2, th + pad_s * 2), 0)
                ImageDraw.Draw(tmask).text((pad_s - bb[0], pad_s - bb[1]), line, font=font_title, fill=255)
                sh = Image.new("RGBA", tmask.size, (0, 0, 0, 255))
                sh.putalpha(tmask.point(lambda p: p * 200 // 255))
                sh = sh.filter(ImageFilter.GaussianBlur(radius=3))
                canvas.alpha_composite(sh, (x0 - pad_s + 2, ty - pad_s + 4))
                txt = Image.new("RGBA", tmask.size, (0, 0, 0, 0))
                txt.paste(Image.new("RGBA", tmask.size, (255, 255, 255, 255)), (0, 0), tmask)
                canvas.alpha_composite(txt, (x0 - pad_s, ty - pad_s))
                ty += line_h
            ty += 20
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (x0, ty - 2, x0 + 96, ty + 1), 2, self.ACCENT,
            ))
            ty += 7

            # ── 5. Compact episode line, audio/quality, and short synopsis ─
            q_label, _q_color = _get_quality_pill(quality)
            episode_line = _clean_text(episode_info).strip().upper()
            if not episode_line:
                episode_line = _format_episode_tag("", is_movie)
            episode_parts = re.split(r"\s*[·•]\s*", episode_line, maxsplit=1)
            split_badge = (
                len(episode_parts) == 2
                and episode_parts[0].startswith("SEASON ")
                and not re.search(r"\s/\s*SEASON\s", episode_parts[1])
            )

            def _measure(text, font):
                bbox = draw.textbbox((0, 0), text, font=font)
                return bbox[2] - bbox[0]

            if split_badge:
                season_label, episode_label = episode_parts
                episode_match = re.match(r"^(\d+)\s+(.+)$", episode_label)
                count_text, suffix_text = episode_match.groups() if episode_match else ("", "")
                season_size = self.EPISODE_SEASON_SIZE
                count_size = self.EPISODE_COUNT_SIZE
                while True:
                    font_season = _get_font(season_size, bold=True, weight="semibold")
                    season_width = _measure(season_label, font_season)
                    if count_text:
                        font_count = _get_font(count_size, bold=True, weight="extrabold")
                        font_suffix = _get_font(max(16, count_size - 4), bold=True, weight="semibold")
                        episode_width = _measure(count_text, font_count) + 5 + _measure(suffix_text, font_suffix)
                    else:
                        font_episode = _get_font(count_size, bold=True, weight="extrabold")
                        episode_width = _measure(episode_label, font_episode)
                    meta_width = season_width + 24 + episode_width
                    if meta_width <= self.RAIL_W - 30 or count_size <= 16:
                        break
                    count_size -= 1
                    season_size = max(16, season_size - 1)
            else:
                season_label = episode_label = count_text = suffix_text = ""
                meta_size = self.EPISODE_COUNT_SIZE
                font_meta = _get_font(meta_size, bold=True, weight="extrabold")
                meta_width = _measure(episode_line, font_meta)
                while meta_width > self.RAIL_W - 30 and meta_size > 13:
                    meta_size -= 1
                    font_meta = _get_font(meta_size, bold=True, weight="extrabold")
                    meta_width = _measure(episode_line, font_meta)

            chip_height = self.EPISODE_CHIP_HEIGHT
            chip_top, chip_bottom = ty - 5, ty + chip_height - 5
            chip_box = (x0, chip_top, x0 + min(self.RAIL_W, meta_width + 30), chip_bottom)
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), chip_box, 13,
                [(0.0, (18, 25, 47, 242)), (0.55, (35, 27, 54, 238)), (1.0, (15, 35, 49, 240))],
            ))
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (x0, chip_top, x0 + 5, chip_bottom), 2, self.ACCENT,
            ))
            od.rounded_rectangle(chip_box, radius=13, outline=(255, 255, 255, 110), width=1)

            def _center_y(text, font):
                bbox = draw.textbbox((0, 0), text, font=font)
                return chip_top + (chip_height - (bbox[3] - bbox[1])) // 2 - bbox[1]

            if split_badge:
                text_x = x0 + 15
                draw.text((text_x, _center_y(season_label, font_season)), season_label,
                          font=font_season, fill=(226, 237, 250, 255))
                divider_x = text_x + season_width + 10
                draw.line((divider_x, chip_top + 10, divider_x, chip_bottom - 10),
                          fill=(236, 72, 153, 235), width=2)
                episode_x = divider_x + 11
                if count_text:
                    count_width = _measure(count_text, font_count)
                    draw.text((episode_x, _center_y(count_text, font_count)), count_text,
                              font=font_count, fill=(111, 237, 255, 255))
                    draw.text((episode_x + count_width + 5, _center_y(suffix_text, font_suffix)),
                              suffix_text, font=font_suffix, fill=(255, 255, 255, 255))
                else:
                    draw.text((episode_x, _center_y(episode_label, font_episode)), episode_label,
                              font=font_episode, fill=(255, 255, 255, 255))
            else:
                draw.text((x0 + 15, _center_y(episode_line, font_meta)), episode_line,
                          font=font_meta, fill=(255, 255, 255, 255))
            ty += chip_height + 7

            details_line = f"{_format_audio_tag(audio)}  •  {(quality or 'HD').upper()}"
            font_details = _get_font(16, bold=False, weight="medium")
            draw.text((x0, ty), details_line, font=font_details, fill=(226, 232, 240, 235))
            ty += 24

            synopsis_text = re.sub(r"<[^>]+>", " ", str(synopsis or ""))
            synopsis_text = _clean_text(html.unescape(synopsis_text))
            if synopsis_text:
                synopsis_text = " ".join(synopsis_text.split())
                font_synopsis = _get_font(15, bold=False, weight="regular")
                synopsis_lines = []
                current_line = ""
                words = synopsis_text.split()
                truncated = False
                for word in words:
                    candidate = f"{current_line} {word}".strip()
                    width = draw.textbbox((0, 0), candidate, font=font_synopsis)[2]
                    if width <= self.RAIL_W:
                        current_line = candidate
                        continue
                    if current_line:
                        synopsis_lines.append(current_line)
                    current_line = word
                    if len(synopsis_lines) >= self.SYNOPSIS_MAX_LINES:
                        truncated = True
                        current_line = ""
                        break
                if current_line and len(synopsis_lines) < self.SYNOPSIS_MAX_LINES:
                    synopsis_lines.append(current_line)
                elif current_line:
                    truncated = True
                if truncated and synopsis_lines:
                    last = synopsis_lines[-1].rstrip(" .")
                    while last and draw.textbbox((0, 0), last + "…", font=font_synopsis)[2] > self.RAIL_W:
                        last = last.rsplit(" ", 1)[0] if " " in last else ""
                    synopsis_lines[-1] = (last + "…") if last else "…"
                for line in synopsis_lines[:self.SYNOPSIS_MAX_LINES]:
                    draw.text((x0, ty), line, font=font_synopsis, fill=(235, 238, 244, 225))
                    ty += 20
            ty += 12

            # ── 6. Single streaming action (visual treatment only) ──────────
            font_pill = _get_font(17, bold=True, weight="bold")
            # Primary "WATCH NOW" pill (gradient fill + ambient shadow).
            cta = "WATCH NOW"
            cb = draw.textbbox((0, 0), cta, font=font_pill)
            cw, chh = (cb[2] - cb[0]) + 54, 42
            sh_cta = Image.new("RGBA", (cw + 24, chh + 24), (0, 0, 0, 0))
            ImageDraw.Draw(sh_cta).rounded_rectangle((12, 12, cw + 12, chh + 12), radius=14, fill=(0, 0, 0, 160))
            canvas.alpha_composite(sh_cta.filter(ImageFilter.GaussianBlur(radius=8)), (x0 - 12, ty - 12 + 3))
            cta_glow = Image.new("RGBA", (cw + 36, chh + 36), (0, 0, 0, 0))
            ImageDraw.Draw(cta_glow).rounded_rectangle(
                (18, 18, cw + 18, chh + 18), radius=18, fill=(255, 18, 83, 98),
            )
            canvas.alpha_composite(cta_glow.filter(ImageFilter.GaussianBlur(radius=11)), (x0 - 18, ty - 18 + 2))
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), (x0, ty, x0 + cw, ty + chh), 14,
                [(0.0, (230, 0, 58, 255)), (1.0, (247, 28, 73, 255))],
            ))
            od.rounded_rectangle(
                (x0, ty, x0 + cw, ty + chh), radius=14,
                outline=(255, 255, 255, 88), width=1,
            )
            play_x, play_y = x0 + 18, ty + chh // 2
            draw.polygon(
                [(play_x, play_y - 7), (play_x, play_y + 7), (play_x + 12, play_y)],
                fill=(255, 255, 255, 255),
            )
            draw.text(
                (x0 + 38, ty + (chh - (cb[3] - cb[1])) // 2 - cb[1]),
                cta, font=font_pill, fill=(255, 255, 255, 255),
            )
            # ── 7. Watermarks: handle bottom-right, brand note bottom-left ──
            font_wm = _get_font(16, bold=True, weight="bold")
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
                (W, H), (wmx, wmy + 22, wmx + ww, wmy + 24), 1, self.ACCENT))

            # Small top-right quality badge; keep the label readable but quiet.
            tag = q_label.split(" • ")[0]
            font_tag = _get_font(16, bold=True, weight="bold")
            tb = draw.textbbox((0, 0), tag, font=font_tag)
            tag_width = tb[2] - tb[0]
            tx = W - 64 - tag_width
            tag_box = (tx - 10, 46, tx + tag_width + 10, 78)
            canvas.alpha_composite(_rounded_gradient_layer(
                (W, H), tag_box, 11,
                [(0.0, (14, 20, 38, 205)), (1.0, (43, 34, 61, 195))],
            ))
            od.rounded_rectangle(tag_box, radius=11, outline=(255, 255, 255, 72), width=1)
            draw.text((tx, 54), tag, font=font_tag, fill=(255, 255, 255, 225))

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
    synopsis: str = "",
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
            synopsis=synopsis,
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
        synopsis=str(kwargs.get("synopsis") or ""),
        **{k: kwargs[k] for k in ("brand_username", "logo_path") if k in kwargs},
    )
