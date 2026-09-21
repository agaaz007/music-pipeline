"""Explore musescore.com for candidate scores, without spending downloads.

Reading a listing costs nothing; only the "Edit on desktop" click does. So the
way to build a large candidate list is to crawl the site's own browse surfaces
-- tag pages, genre pages and text search -- collect every score link with the
part count and duration the listing shows, and filter before any download.

Everything here is a *candidate*. The real verdicts still come from the genre
stage and the structural classifier after a score is on disk.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

# Text search is the only browse surface that actually works: /sheetmusic/tag/
# <tag> is a 404 on this site, which cost a crawl before it was checked. A
# search page returns about fifty score links.
SEARCH_URL = "https://musescore.com/sheetmusic?text={query}"
PAGED = "{url}{sep}page={page}"

# Genres worth crawling, using the site's own tag vocabulary.
# Genre words, searched as free text rather than as tag paths.
ELECTRONIC_TAGS = (
    "electronic", "edm", "electronic dance music", "house music", "techno",
    "trance", "dubstep", "drum and bass", "synthwave", "chiptune",
    "future bass", "electro house", "progressive house", "hardstyle",
    "big room", "melodic dubstep", "electro swing", "eurodance",
)

# Artists whose catalogues are heavily arranged on MuseScore and are
# unambiguously electronic; text search finds arrangements the tags miss.
ELECTRONIC_ARTISTS = (
    "TheFatRat", "Kygo", "Marshmello", "Martin Garrix", "Avicii", "Daft Punk",
    "Deadmau5", "Zedd", "Skrillex", "Porter Robinson", "Madeon", "Alan Walker",
    "Calvin Harris", "Swedish House Mafia", "Eric Prydz", "Justice", "Kavinsky",
    "The Midnight", "Carpenter Brut", "Perturbator", "Odesza", "Flume",
    "Disclosure", "Fred again", "Rufus Du Sol", "Above and Beyond", "Seven Lions",
    "Illenium", "Said The Sky", "Virtual Riot", "Knife Party", "Pendulum",
    "Camellia", "Sakuraburst", "Xtrullor", "Nitro Fun", "Pegboard Nerds",
)


def tag_urls(tags=ELECTRONIC_TAGS, pages=3):
    """Search URLs for each genre word, paginated."""
    return search_urls(tags, pages)


def search_urls(queries=ELECTRONIC_ARTISTS, pages=2):
    """Text-search URLs for each artist, paginated."""
    out = []
    for q in queries:
        base = SEARCH_URL.format(query=quote_plus(q))
        for page in range(1, pages + 1):
            out.append(base if page == 1 else PAGED.format(url=base, sep="&", page=page))
    return out


def crawl_urls(tags=ELECTRONIC_TAGS, artists=ELECTRONIC_ARTISTS,
               tag_pages=3, search_pages=2):
    """Every browse URL to visit, tags first (denser in multi-part scores)."""
    return tag_urls(tags, tag_pages) + search_urls(artists, search_pages)


_ARTIST_IN_TITLE = re.compile(r"^\s*(.+?)\s+[-–—]\s+(.+?)\s*$")


def split_title(text):
    """'Artist - Title' when the listing gives it, else (None, text)."""
    m = _ARTIST_IN_TITLE.match(text or "")
    return (m.group(1), m.group(2)) if m else (None, (text or "").strip())


def explore(urls=None, *, out_path="candidates.json", min_parts=4, min_seconds=60,
            pause=2.0, verbose=True, limit_urls=None):
    """Crawl browse pages and collect candidate scores. Spends no downloads.

    Returns the candidate list and writes it to `out_path`. Already-cached
    scores are excluded, and candidates are de-duplicated by score id across
    every page visited, so overlapping tag and search results collapse.
    """
    from jev_ultrafast import Agent

    from .config import CLOUD_SCORES
    from .discover import LISTING_JS, parse_candidates, usable
    from .fetch import read_page, wait_for_page_ready

    urls = list(urls if urls is not None else crawl_urls())
    if limit_urls:
        urls = urls[:limit_urls]
    have = {p.stem for p in CLOUD_SCORES.glob("*.mscz")}

    # Seed from a previous run so an interrupted crawl accumulates instead of
    # starting over, and skip browse pages that already contributed.
    found, done = {}, set()
    out = Path(out_path)
    if out.exists():
        for c in json.loads(out.read_text()):
            found[c["id"]] = c
            done.add(c.get("source"))
        if verbose and found:
            print(f"resuming from {out_path}: {len(found)} candidates, "
                  f"{len(done)} pages already contributed")

    def checkpoint():
        ranked = sorted(found.values(), key=lambda c: -(c.get("parts") or 0))
        out.write_text(json.dumps(ranked, indent=1))
        return ranked

    blocked = failed = 0
    for i, url in enumerate(urls, 1):
        try:
            with Agent(url, "Do nothing; only reading.") as agent:
                ready = wait_for_page_ready(agent)
                if not ready or read_page(agent).get("blocked"):
                    blocked += 1
                    if verbose:
                        print(f"[{i}/{len(urls)}] blocked: {url[:70]}")
                    time.sleep(pause * 3)
                    continue
                rows = agent.browser.evaluate(LISTING_JS)
        except Exception as exc:
            failed += 1
            if verbose:
                print(f"[{i}/{len(urls)}] {type(exc).__name__}: {url[:60]}")
            time.sleep(pause)
            continue

        fresh = 0
        for c in parse_candidates(rows):
            if c["id"] in have or c["id"] in found:
                continue
            if not usable(c, min_parts, min_seconds):
                continue
            artist, title = split_title(c["title"])
            found[c["id"]] = {**c, "artist": artist, "clean_title": title, "source": url}
            fresh += 1
        checkpoint()
        if verbose:
            print(f"[{i}/{len(urls)}] +{fresh} new (total {len(found)})  {url[:58]}")
        time.sleep(pause)

    candidates = checkpoint()
    if verbose:
        print(f"\n{len(candidates)} candidates -> {out_path}"
              f"  ({blocked} pages blocked, {failed} errored)")
    return candidates
