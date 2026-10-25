"""Ablate each overlay layer to find what still crushes the art region."""
import bot.thumbnail as th
from PIL import Image
from tools.cv_probe import stats, source_crop, render, line, ART_X, W

print("Baseline:")
line("REF source", stats(source_crop()))
line("render", stats(render("/tmp/ab_base.jpg")))
print()

# Layer 1: kill the two radial glows (they sit ON the art side)
th.WARM_GLOW_ALPHA = 0
th.COOL_GLOW_ALPHA = 0
line("-glows", stats(render("/tmp/ab_glow.jpg")))

# Layer 2: also remove the edge vignette
th.EDGE_VIGNETTE_ALPHA = 0
line("-glows-vign", stats(render("/tmp/ab_vig.jpg")))

# Layer 3: also remove the lower vignette
th.LOWER_VIGNETTE_ALPHA = 0
line("-glows-vig-low", stats(render("/tmp/ab_low.jpg")))

# Layer 4: also remove the grade entirely
th.GRADE_CONTRAST = 1.0
th.GRADE_SATURATION = 1.0
th.GRADE_SPLIT_TONE = 0.0
th.GRADE_BLOOM = 0.0
th.GRADE_SHARPNESS = 1.0
line("-grade too", stats(render("/tmp/ab_grade.jpg")))
