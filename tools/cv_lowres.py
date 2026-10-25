"""Prove the low-res portrait path (AniList-backed) still reads after the
overlay/grade change: art must not be crushed *further* than the source."""
import warnings
warnings.filterwarnings("ignore")
import bot.thumbnail as th
from PIL import Image
from tools.cv_probe import stats

# Synthetic low-res portrait cover (mimics an AniList 460px extraLarge poster).
src = Image.open("/tmp/tmdb_solo.jpg").convert("RGB").resize((460, 690), Image.LANCZOS)
src.save("/tmp/anilist_low.jpg")
print("low-res source size:", src.size)


def render(out):
    th.generate_auto_thumbnail(
        title="Solo Leveling", episode_info="S2 E1", quality="1080p",
        audio="Hindi Dub", poster_path="/tmp/anilist_low.jpg",
        output_path=out, template_name="streaming",
    )
    return Image.open(out)


im = render("/tmp/anilist_out.jpg")
r, g, b, sat, hi, lo = stats(im.crop((359, 0, 1280, 720)))
print("render art  mean=(%5.1f,%5.1f,%5.1f) sat=%.3f hi=%4.1f%% lo=%4.1f%%" % (r, g, b, sat, hi, lo))
print("rendered ->", im.size, "bytes", __import__("os").path.getsize("/tmp/anilist_out.jpg"))
