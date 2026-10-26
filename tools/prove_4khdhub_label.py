"""Prove 4KHDHub labels its 4K proxy pick as '4K', not '1080p'."""
import warnings
warnings.filterwarnings("ignore")
from bs4 import BeautifulSoup
from extractors.fourkhdhub import fourkhdhub


def page(rows):
    items = ""
    for title, link in rows:
        items += (f'<div class="episode-download-item">'
                  f'<div class="episode-file-title">{title}</div>'
                  f'<div class="episode-file-info">info</div>'
                  f'<a href="{link}">HubCloud</a></div>')
    return BeautifulSoup(f"<html>{items}</html>", "html.parser")


CASES = [
    # (desc, rows, expected_label)
    ("H.264 1.4GB (preferred 4K proxy)", [
        ("Anime.S01E01.1080p.WEB-DL.H.264 [1.4 GB]", "https://m/hc264"),
        ("Anime.S01E01.1080p.WEB-DL.H.265 [1.9 GB]", "https://m/hc265"),
    ], "4K"),
    ("H.265 only 1.5GB (4K proxy fallback)", [
        ("Anime.S01E01.1080p.WEB-DL.H.265 [1.5 GB]", "https://m/hc265b"),
    ], "4K"),
    ("native 2160p stays 4K", [
        ("Anime.S01E01.2160p.WEB-DL.H.265 [1.8 GB]", "https://m/hc4k"),
    ], "4K"),
    ("H.264 5GB (over cap) not chosen as 4K proxy", [
        ("Anime.S01E01.1080p.WEB-DL.H.264 [5.0 GB]", "https://m/big"),
        ("Anime.S01E01.1080p.WEB-DL.H.265 [1.5 GB]", "https://m/ok"),
    ], "4K"),
]

ok = True
for desc, rows, want in CASES:
    link, label, size = fourkhdhub._pick_mediator(page(rows), 1, 1, "4K")
    good = (label == want) and bool(link)
    ok &= good
    print(f"{'PASS' if good else 'FAIL'}  {desc:46} -> label={label!r} size={size} link={link}")

# Non-4K request must stay labelled by its real quality.
link, label, size = fourkhdhub._pick_mediator(
    page([("Anime.S01E01.1080p.WEB-DL.H.264 [1.4 GB]", "https://m/x")]), 1, 1, "1080p")
good = label == "1080p"
ok &= good
print(f"{'PASS' if good else 'FAIL'}  {'1080p request keeps real label':46} -> label={label!r}")

print("\nALL PASS" if ok else "\nSOME FAILED")