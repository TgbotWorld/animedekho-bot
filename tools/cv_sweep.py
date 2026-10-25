"""Sweep overlay/grade variants; target the TMDB source on luminance,
saturation AND highlight retention, while keeping the copy rail legible."""
import warnings
import bot.thumbnail as th
from PIL import Image
from tools.cv_probe import stats, source_crop, ART_X, W, H

warnings.filterwarnings("ignore")

RAIL = (30, 300, 360, 690)  # copy-rail text area


def render(out):
    th.generate_auto_thumbnail(
        title="Solo Leveling", episode_info="S2 E1", quality="1080p",
        audio="Hindi Dub", poster_path="/tmp/tmdb_solo.jpg",
        output_path=out, template_name="streaming",
    )
    return Image.open(out)


def rail_p50(im):
    small = im.convert("L").crop(RAIL)
    px = sorted(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
    return px[len(px) // 2]


def show(tag, out):
    im = render(out)
    r, g, b, sat, hi, _lo = stats(im.crop((ART_X, 0, W, H)))
    p50 = rail_p50(im)
    print("%-18s mean=(%5.1f,%5.1f,%5.1f) sat=%.3f hi=%4.1f%%  rail_p50=%3d" % (tag, r, g, b, sat, hi, p50))
    return (r, g, b, sat, hi), p50


def apply(contrast, sat, split, bloom, edge, low, warm, cool, scrim):
    th.GRADE_CONTRAST, th.GRADE_SATURATION, th.GRADE_SPLIT_TONE = contrast, sat, split
    th.GRADE_BLOOM = bloom
    th.EDGE_VIGNETTE_ALPHA, th.LOWER_VIGNETTE_ALPHA = edge, low
    th.WARM_GLOW_ALPHA, th.COOL_GLOW_ALPHA = warm, cool
    th.SCRIM_STOPS = [(p, (5, 6, 13, a)) for p, a in scrim]


CUR = [(0.0, 214), (0.30, 196), (0.40, 120), (0.52, 40), (0.66, 8), (0.80, 0)]
# Clear the rail before the art box starts (art_x=359 -> 0.28W), so no scrim
# lands on the artwork.
CLEAR = [(0.0, 210), (0.16, 190), (0.22, 110), (0.27, 30), (0.30, 4), (0.34, 0)]

if __name__ == "__main__":
    r, g, b, sat, hi, _lo = stats(source_crop())
    print("TARGET (source)   mean=(%5.1f,%5.1f,%5.1f) sat=%.3f hi=%4.1f%%" % (r, g, b, sat, hi))
    print()
    apply(1.06, 1.06, 0.05, 0.10, 34, 56, 34, 18, CUR)
    show("current", "/tmp/sw_cur.jpg")
    apply(1.06, 1.10, 0.05, 0.10, 20, 40, 30, 16, CLEAR)
    show("A clear+soft-vig", "/tmp/sw_a.jpg")
    apply(1.04, 1.12, 0.04, 0.08, 12, 30, 26, 14, CLEAR)
    show("B clear+min-vig", "/tmp/sw_b.jpg")
    apply(1.06, 1.08, 0.05, 0.10, 24, 44, 32, 16, CUR)
    show("C scrim-only", "/tmp/sw_c.jpg")
