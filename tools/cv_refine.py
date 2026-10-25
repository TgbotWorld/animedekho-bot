"""Refinement: recover saturation + highlight retention toward the source."""
import warnings
import bot.thumbnail as th
from PIL import Image
from tools.cv_probe import stats, source_crop, ART_X, W
from tools.cv_sweep import render, rail_p50, CLEAR

warnings.filterwarnings("ignore")

CLEAR2 = [(0.0, 206), (0.16, 186), (0.22, 104), (0.27, 26), (0.30, 3), (0.33, 0)]


def show(tag, out):
    im = render(out)
    r, g, b, sat, hi, _lo = stats(im.crop((ART_X, 0, W, 720)))
    p50 = rail_p50(im)
    print("%-20s mean=(%5.1f,%5.1f,%5.1f) sat=%.3f hi=%4.1f%%  rail_p50=%3d" % (tag, r, g, b, sat, hi, p50))


def apply(contrast, sat, split, bloom, edge, low, warm, cool, scrim):
    th.GRADE_CONTRAST, th.GRADE_SATURATION, th.GRADE_SPLIT_TONE = contrast, sat, split
    th.GRADE_BLOOM = bloom
    th.EDGE_VIGNETTE_ALPHA, th.LOWER_VIGNETTE_ALPHA = edge, low
    th.WARM_GLOW_ALPHA, th.COOL_GLOW_ALPHA = warm, cool
    th.SCRIM_STOPS = [(p, (5, 6, 13, a)) for p, a in scrim]


if __name__ == "__main__":
    r, g, b, sat, hi, _lo = stats(source_crop())
    print("TARGET (source)      mean=(%5.1f,%5.1f,%5.1f) sat=%.3f hi=%4.1f%%" % (r, g, b, sat, hi))
    print()
    apply(1.04, 1.13, 0.03, 0.06, 16, 36, 28, 14, CLEAR2)
    show("D low-grade", "/tmp/rf_d.jpg")
    apply(1.02, 1.16, 0.02, 0.05, 12, 30, 24, 12, CLEAR2)
    show("E lower-grade", "/tmp/rf_e.jpg")
    apply(1.05, 1.12, 0.04, 0.08, 20, 42, 30, 16, CLEAR2)
    show("F balanced", "/tmp/rf_f.jpg")
