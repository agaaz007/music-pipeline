"""Fast scout: reads Electronic-genre listing pages over one browser tab.

Replaces the Jev-agent scout for crawling, which hung on page loads. Uses the
same filters as team/scout.py and writes to the same pool, so the fetcher and
the genre review work unchanged. Run through browser-harness:

    uv run browser-harness < team/crawl.py

Stops after CRAWL_PAGES listing pages per base URL, or when a base URL returns
no new score ids (the listing has run out).
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path("/Users/Agaaz/code/jev-ultrafast")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "team"))
from musescore_midi.discover import LISTING_JS, parse_candidates  # noqa: E402
from scout import BROWSE, COMP, POOL, electronic_ratio, held_ids, load_pool, roles  # noqa: E402

CRAWL_PAGES = 60


def ev(expr):
    return cdp("Runtime.evaluate", expression=expr, returnByValue=True)["result"].get("value")  # noqa: F821


pool = {c["id"]: c for c in load_pool()}
have = held_ids()
tab = new_tab("about:blank")  # noqa: F821
try:
    for base in BROWSE:
        seen_here = set()
        for page in range(1, CRAWL_PAGES + 1):
            url = f"{base}?page={page}"
            try:
                goto_url(url)  # noqa: F821
                rows = None
                for _ in range(20):
                    time.sleep(0.5)
                    rows = ev(LISTING_JS)
                    if rows:
                        break
            except Exception as exc:
                print(f"  {type(exc).__name__} on {url}", flush=True)
                continue
            cands = parse_candidates(rows)
            ids = {c["id"] for c in cands}
            if not ids or ids <= seen_here:
                print(f"{base} ran out at page {page}", flush=True)
                break
            seen_here |= ids
            fresh = 0
            for c in cands:
                if c["id"] in pool or c["id"] in have:
                    continue
                if (c.get("parts") or 0) < 4 or not (150 <= (c.get("seconds") or 0) <= 900):
                    continue
                if COMP.search(c.get("title") or ""):
                    continue
                ratio, ins = electronic_ratio(c.get("text") or "")
                if ratio < 0.4 or roles(c.get("text") or "") < 3:
                    continue
                pool[c["id"]] = {**c, "ratio": ratio, "roles": 3, "instruments": ins[:110],
                                 "via": "electronic-genre"}
                fresh += 1
            POOL.write_text(json.dumps(list(pool.values()), indent=1))
            total = sum(c.get("seconds") or 0 for c in pool.values() if c["id"] not in have)
            print(f"{url.split('sheetmusic/')[1]:<40} +{fresh:<2} pool {len(pool)} ({total / 3600:.2f} h)", flush=True)
finally:
    close_tab(tab if isinstance(tab, str) else tab.get("targetId"))  # noqa: F821
