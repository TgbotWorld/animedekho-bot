"""Colour/contrast probe: compare rendered art box against the TMDB source crop."""
import sys
import bot.thumbnail as th
from PIL import Image

W, H = 1280, 720
BOX_W = int(W * 0.72)
ART_X = W - BOX_W  # 359


def source_crop():
    src = Image.open("/tmp/tmdb_solo.jpg").convert("RGB")
    s = max(BOX_W / src.width, H / src.height)
    nw, nh = int(src.width * s), int(src.height * s)
    sh = src.resize((nw, nh), Image.LANCZOS)
    ox = max(0, (nw - BOX_W) // 2)
    oy = max(0, int((nh - H) * 0.20))
    return sh.crop((ox, oy, ox + BOX_W, oy + H))


def stats(im):
    im = im.convert("RGB").resize((230, 180))
    px = im.load(); n = 230 * 180
    r = g = b = 0; sat = 0; hi = lo = 0
    for y in range(180):
        for x in range(230):
            R, G, B = px[x, y]
            r += R; g += G; b += B
            mx = max(R, G, B); mn = min(R, G, B)
            sat += (mx - mn) / mx if mx else 0
            if mx >= 250: hi += 1
            if mx <= 4: lo += 1
    return (r / n, g / n, b / n, sat / n, 100 * hi / n, 100 * lo / n)


def render(out):
    th.generate_auto_thumbnail(
        title="Solo Leveling", episode_info="S2 E1", quality="1080p",
        audio="Hindi Dub", poster_path="/tmp/tmdb_solo.jpg",
        output_path=out, template_name="streaming",
    )
    return Image.open(out).crop((ART_X, 0, W, H))


def line(name, s):
    print("%-14s mean=(%5.1f,%5.1f,%5.1f) sat=%.3f hi=%4.1f%% lo=%4.1f%%" % (name, *s))


if __name__ == "__main__":
    line("REF source", stats(source_crop()))
    line("render", stats(render("/tmp/probe.jpg")))
