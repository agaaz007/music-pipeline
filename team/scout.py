"""Scout: keeps the candidate pool stocked with electronic-instrumented scores.

Reads musescore.com listing pages, which print each score's instrument list, and
keeps only scores whose instruments are predominantly electronic. This is the
filter that lifted mean synth content from ~10% to 56%; it costs no downloads,
so the scout can run whenever the fetcher is not using the browser.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musescore_midi.team import browser_lease, log, write_state

ELECTRONIC = re.compile(r"\b(synth\w*|drum machine|drum group|electric piano|"
    r"electric guitar|electric bass|bass guitar|sampler|808)\b", re.I)
ACOUSTIC = re.compile(r"\b(clarinet|oboe|bassoon|flute|piccolo|trombone|trumpet|"
    r"saxophone|sax\b|french horn|tuba|euphonium|cornet|violin|viola|cello|harp|"
    r"timpani|marimba|xylophone|glockenspiel|organ|choir|recorder|sousaphone)\b", re.I)
# The approved samples each list a drum part, a bass part and a synth part.
# Requiring all three in the listing's instrument list costs no downloads.
ROLE_DRUMS = re.compile(r"\b(drum\w*|percussion|drumset|drum kit|808)\b", re.I)
ROLE_BASS = re.compile(r"\b(bass guitar|electric bass|synth bass|bass synth\w*|"
    r"fretless bass|bass)\b(?!\s*(?:clarinet|trombone|drum))", re.I)
ROLE_SYNTH = re.compile(r"\b(synth\w*|sampler|electric piano)\b", re.I)
COMP = re.compile(r"medley|mashup|full album|megamix|compilation", re.I)
POOL = Path(__file__).resolve().parent / "pool.json"
TERMS = ["future bass", "progressive house", "electro house", "dubstep", "trance",
         "synthwave", "big room", "melodic dubstep", "hardstyle", "drum and bass",
         "edm", "electronic dance", "house music", "techno", "bass house",
         "deep house", "trap edm", "chiptune", "eurodance", "electro swing"]
# Instrument browse pages filtered to the Electronic genre (genres=16). Text
# search matches titles, so it surfaces wind-band covers of EDM songs; these
# list scores by their instruments instead. Measured on page 1: 12 of 20 rows
# pass the three-role filter here, against 0-2 for a text search.
BROWSE = ["https://musescore.com/sheetmusic/synthesizer?genres=16",
          "https://musescore.com/sheetmusic/bass-guitar?genres=16",
          "https://musescore.com/sheetmusic/synthesizer"]
BROWSE_PAGES = 40
CURSOR = Path(__file__).resolve().parent / "scout_cursor.json"


def crawl_plan():
    """Browse pages first (interleaved, page by page), then text searches."""
    from musescore_midi.search import search_urls
    plan = [f"{base}&page={page}" if "?" in base else f"{base}?page={page}"
            for page in range(1, BROWSE_PAGES + 1) for base in BROWSE]
    return plan + search_urls(TERMS, 3)


def load_cursor():
    try:
        return int(json.loads(CURSOR.read_text())["next"])
    except Exception:
        return 0

def instruments(text):
    m = re.search(r"(?:Ensemble|Band|Orchestra|Solo|Group|Duet|Trio|Quartet|Quintet)\s+(.*)$", text)
    return m.group(1) if m else text[-160:]

def electronic_ratio(text):
    i = instruments(text)
    e = len({x.lower() for x in ELECTRONIC.findall(i)})
    a = len({x.lower() for x in ACOUSTIC.findall(i)})
    return (e / (e + a)) if (e + a) else 0.0, i

def roles(text):
    """How many of drums, bass, synth the listed instruments cover (0-3)."""
    i = instruments(text)
    return sum(bool(r.search(i)) for r in (ROLE_DRUMS, ROLE_BASS, ROLE_SYNTH))


def load_pool():
    try:
        return json.loads(POOL.read_text())
    except Exception:
        return []

def held_ids():
    """Score IDs already downloaded here or listed as held by other devices."""
    from musescore_midi.config import CLOUD_SCORES
    ids = {p.stem for p in CLOUD_SCORES.glob("*.mscz")}
    shared = Path(__file__).resolve().parent.parent / "handoff" / "held_score_ids.txt"
    if shared.exists():
        ids |= {line.strip() for line in shared.read_text().splitlines() if line.strip()}
    return ids

def run(rounds=99, per_round=3, min_ratio=0.4, min_parts=4):
    from jev_ultrafast import Agent
    from musescore_midi.discover import LISTING_JS, parse_candidates
    from musescore_midi.fetch import wait_for_page_ready

    pool = {c["id"]: c for c in load_pool()}
    plan = crawl_plan()
    cursor = load_cursor()
    for rnd in range(rounds):
        if cursor >= len(plan):
            log("scout", "crawl plan exhausted; starting over for new uploads")
            cursor = 0
        have = held_ids()
        fresh = 0
        with browser_lease("scout"):
            urls = plan[cursor:cursor + per_round]
            cursor += len(urls)
            CURSOR.write_text(json.dumps({"next": cursor}))
            log("scout", f"round {rnd+1}: crawling {len(urls)} pages from {urls[0][:70]}")
            for url in urls:
                try:
                    with Agent(url, "Do nothing; only reading.") as agent:
                        wait_for_page_ready(agent)
                        rows = agent.browser.evaluate(LISTING_JS)
                except Exception as exc:
                    log("scout", f"  {type(exc).__name__} on {url[:60]}")
                    continue
                for c in parse_candidates(rows):
                    if c["id"] in pool or c["id"] in have:
                        continue
                    if (c.get("parts") or 0) < min_parts:
                        continue
                    if not (150 <= (c.get("seconds") or 0) <= 900):
                        continue
                    if COMP.search(c.get("title") or ""):
                        continue
                    ratio, ins = electronic_ratio(c.get("text") or "")
                    if ratio < min_ratio:
                        continue
                    r = roles(c.get("text") or "")
                    if r < 3:
                        continue
                    pool[c["id"]] = {**c, "ratio": ratio, "roles": r, "instruments": ins[:110]}
                    fresh += 1
                time.sleep(1.0)
        POOL.write_text(json.dumps(list(pool.values()), indent=1))
        unclaimed = [c for c in pool.values() if c["id"] not in have]
        write_state(pool=len(unclaimed))
        log("scout", f"  +{fresh} new; pool now {len(unclaimed)} unfetched")
        time.sleep(5)

if __name__ == "__main__":
    run(rounds=int(sys.argv[1]) if len(sys.argv) > 1 else 99)
