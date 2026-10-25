"""Build a before/after proof strip: TMDB source vs old render vs new render."""
import warnings
warnings.filterwarnings("ignore")
import bot.thumbnail as th
from PIL import Image, ImageDraw
from tools.cv_probe import source_crop
from tools.cv_sweep import render as r_new, apply, CLEAR, CUR


def render_old():
    apply(1.06, 1.06, 0.05, 0.10, 34, 56, 34, 18, CUR)
    return r_new("/tmp/ba_old.jpg")


new = r_new("/tmp/ba_new.jpg")
old = render_old()
ref = source_crop().resize((1280, 720), Image.LANCZOS)

strip = Image.new("RGB", (1280, 720 * 3 + 60), (8, 8, 12))
d = ImageDraw.Draw(strip)
try:
    from PIL import ImageFont
    f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
except Exception:
    f = None
for i, (label, img) in enumerate([("TMDB SOURCE (reference)", ref),
                                  ("BEFORE  (grade 1.06 / heavy overlays)", old),
                                  ("AFTER  (grade calmed / overlays cleared)", new)]):
    y = i * 740
    strip.paste(img.convert("RGB"), (0, y))
    d.rectangle((0, y, 1280, y + 44), fill=(8, 8, 12))
    d.text((22, y + 6), label, fill=(240, 240, 245), font=f)
strip.save("/tmp/colour_before_after.jpg", quality=88)
print("wrote /tmp/colour_before_after.jpg", strip.size)
