"""Live probe: resolve 'Sword of the Demon Hunter' S1E2 from 4KHDHub."""
import logging, sys, asyncio
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
from extractors.fourkhdhub import fourkhdhub, _get_scraper

title = sys.argv[1] if len(sys.argv) > 1 else "Sword of the Demon Hunter (Hindi Dubbed)"
season = int(sys.argv[2]) if len(sys.argv) > 2 else 1
ep = int(sys.argv[3]) if len(sys.argv) > 3 else 2
qual = sys.argv[4] if len(sys.argv) > 4 else "4K"

print("=== search ===")
res = fourkhdhub._sync_search(title)
for r in res[:10]:
    print(" ", r)
if not res:
    print("NO SEARCH RESULTS")
    sys.exit(1)

# fetch first candidate page and list files
from bs4 import BeautifulSoup
from utils.anime_match import is_confident_match
url = None
for r in res:
    if is_confident_match(title, r.get("title",""), r.get("url","")):
        url = r["url"]; print("MATCH:", r["title"], url); break
if not url:
    url = res[0]["url"]; print("no confident match; using", url)

s = _get_scraper()
r = s.get(url, timeout=25)
soup = BeautifulSoup(r.text, "html.parser")
items = soup.select(".episode-download-item")
print(f"=== {len(items)} download items ===")
for it in items:
    t = it.select_one(".episode-file-title")
    inf = it.select_one(".episode-file-info")
    print("  FILE:", t.get_text(' ',strip=True) if t else None, "|", inf.get_text(' ',strip=True) if inf else None)

med, q, sz = fourkhdhub._pick_mediator(soup, season, ep, qual)
print("=== pick ===", med, q, sz)

if med:
    final = fourkhdhub._unlock_mediator(med)
    print("=== unlock ===", final)
    if final and any(x in final.lower() for x in ("hubcloud","gamerxyt")):
        from extractors.animedrive import animedrive
        st = animedrive._resolve_hubcloud(_get_scraper(), final)
        print("=== hubcloud stream ===", st)
