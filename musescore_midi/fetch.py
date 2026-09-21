"""Stage 1: drive musescore.com with the Jev agent up to the "Edit" handoff.

The browser agent's job ends the moment it clicks Edit. That fires a
musescore:// URL, MuseScore Studio opens the score and writes it into
cloud_scores/, and stage 2 takes over from the filesystem. Nothing here
automates the MuseScore desktop UI.

Two things happen before that click, and the order matters:

  budget    every Edit spends one of the day's limited downloads, and a spent
            download cannot be recovered, so the quota is checked first and
            recorded only once a score actually lands.
  genre     the track is screened from the search phrase alone, over the music
            metadata APIs, which costs no download. A track that is positively
            not electronic never gets downloaded at all.

Structure can only be judged after the score is on disk, so it stays in stage 2.
"""

import re
import time
from urllib.parse import quote_plus

from browser_harness.helpers import cdp

from jev_ultrafast import Agent

from . import budget, studio
from .config import CLOUD_SCORES, HANDOFF_TIMEOUT_S, MUSESCORE_SEARCH
from .convert import load_state
from .genre import ELECTRONIC_EDM, NON_ELECTRONIC, _Client, classify_page, classify_query, classify_tags, save_tags
from .songs import known_keys, song_key

OPEN_GOAL = (
    "Open the sheet music score that best matches '{query}'. "
    "Click the score's title or its thumbnail in the results list. "
    "Stop as soon as the individual score page is open."
)

# The score page carries three similar controls: "Edit on desktop" (the
# musescore:// handoff we want), a plain "Edit" (the online editor, which never
# writes to cloud_scores), and "Download". Naming the exact label matters --
# "click the Edit button" sent the agent to the uploader's profile instead.
EDIT_GOAL = (
    "Click the button labelled exactly 'Edit on desktop'. It opens this score in "
    "the MuseScore Studio desktop application. "
    "Do NOT click the plain 'Edit' button, 'Download', 'Print', 'Save to library', "
    "the uploader's name, or any other link on the page. "
    "Stop immediately after clicking 'Edit on desktop'."
)

SKIPPED_GENRE = "skipped-genre"
SKIPPED_HAVE = "skipped-have"
SKIPPED_BUDGET = "skipped-budget"
FAILED = "failed"
FETCHED = "fetched"


class Result:
    def __init__(self, query, status, path=None, genre=None, detail=""):
        self.query, self.status, self.path, self.genre, self.detail = (
            query, status, path, genre, detail,
        )

    @property
    def spent_download(self):
        return self.status == FETCHED

    def __str__(self):
        label = {FETCHED: "fetched", SKIPPED_GENRE: "skipped", SKIPPED_BUDGET: "no quota",
                 SKIPPED_HAVE: "have it", FAILED: "failed"}[self.status]
        return f"{label:<9} {self.query[:44]:<45} {self.detail}"


# musescore.com URLs come in three shapes:
#   /user/<uid>/scores/<id>, /<username>/scores/<id>, and /song/<slug>-<id>
# musescore.com serves localised subdomains (ja., de., fr., ...) whose buttons
# are translated: the Japanese page labels the handoff in Japanese, so an
# English label match finds nothing. The canonical .com host is used instead.
_LOCALE_HOST = re.compile(r"^https?://([a-z]{2}(?:-[a-z]{2})?)\.musescore\.com/", re.I)


def canonical_url(url):
    """Rewrite a localised musescore host to the English .com one."""
    return _LOCALE_HOST.sub("https://musescore.com/", url or "")


SCORE_URL_ID = re.compile(r"/scores/(\d+)|/song/[^/?#]*?-(\d+)(?:[/?#]|$)")


def cached_score_id(url):
    """The score id in a musescore.com URL, if it is already in cloud_scores."""
    match = SCORE_URL_ID.search(url or "")
    if not match:
        return None
    score_id = match.group(1) or match.group(2)
    return score_id if (CLOUD_SCORES / f"{score_id}.mscz").exists() else None


def cache_snapshot():
    """score_id -> mtime for everything currently cached."""
    return {p.stem: p.stat().st_mtime for p in CLOUD_SCORES.glob("*.mscz")}


def wait_for_new_score(before, timeout=HANDOFF_TIMEOUT_S, settle=1.5):
    """Block until MuseScore Studio writes a new or updated .mscz.

    Returns the path, or None if the handoff never completed. Waits for the
    file size to stop changing so stage 2 never reads a partial download.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for path in CLOUD_SCORES.glob("*.mscz"):
            if before.get(path.stem) != path.stat().st_mtime:
                size = -1
                while size != path.stat().st_size:
                    size = path.stat().st_size
                    time.sleep(settle)
                return path
        time.sleep(0.5)
    return None


# musescore.com renders the tag list as plain text under a "Tags" heading, not
# as links, and states the canonical track under "This score is an arrangement
# of". Both are far better genre evidence than anything in the .mscz, and
# reading them costs no download.
PAGE_JS = """(() => {
  const body = document.body.innerText || '';
  const section = (start, stops) => {
    const i = body.indexOf(start);
    if (i < 0) return null;
    let rest = body.slice(i + start.length);
    for (const stop of stops) {
      const j = rest.indexOf(stop);
      if (j >= 0) rest = rest.slice(0, j);
    }
    return rest;
  };
  const tagBlock = section('\\nTags\\n',
    ['Related courses', 'This score is an arrangement of', 'Score info', 'Credits']);
  const arrangedOf = section('This score is an arrangement of\\n',
    ['Score info', 'Credits', 'Related']);
  return {
    tags: tagBlock ? tagBlock.split('\\n').map(s => s.trim()).filter(Boolean).slice(0, 25) : [],
    arranged_of: arrangedOf ? arrangedOf.split('\\n').map(s => s.trim()).filter(Boolean).slice(0, 4) : [],
    title: document.title || '',
  };
})()"""

# "Edit" appears inside the arrangement block as a button label.
_ARRANGED_NOISE = {"edit", "view", "play", "open"}

# Prompts the site shows in place of real tags when metadata is missing.
# A score covering a whole album or a medley is not one composition, so it must
# not collide with its own tracks in the canonical-song registry. MuseScore
# still links it to a single song, which is why the document title decides.
_COMPILATION = re.compile(
    r"\b(full album|album transcription|medley|megamix|compilation|suite|"
    r"all songs|mashup|continuous mix)\b", re.I
)

# Cloudflare serves an interstitial under load. Its title parses as a perfectly
# ordinary score name, so without this guard a challenge overwrites good data
# with "Just a moment...".
_CHALLENGE = re.compile(
    r"just a moment|attention required|checking your browser|"
    r"access denied|verifying you are human|cloudflare", re.I
)

_TAG_UI_NOISE = re.compile(
    r"aren'?t listed|add the missing|show more|see all|report|sign in", re.I
)


def wait_for_page_ready(agent, timeout=40.0, poll=2.0):
    """Bring the agent's tab to the front and wait out Cloudflare's challenge.

    jev_ultrafast opens its tab with background=True and keeps it rendering via
    focus emulation, which is right for speed but not enough for Cloudflare:
    an interstitial served to a hidden tab never resolves, while the same page
    in a visible tab clears in about four seconds. So the tab is activated for
    real before anything is read from it.

    Returns True once the page is past the challenge.
    """
    try:
        cdp("Target.activateTarget", targetId=agent.browser.target)
    except Exception:
        pass  # worth trying without, in case activation is unavailable
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            title = (agent.browser.evaluate("document.title") or "")
        except Exception:
            title = ""
        if title and not _CHALLENGE.search(title):
            return True
        time.sleep(poll)
    return False


def read_page(agent):
    """Tags and canonical track from the score page. Costs no download."""
    try:
        data = agent.browser.evaluate(PAGE_JS)
    except Exception:
        return {"tags": [], "title": None, "artist": None, "pair": None,
                "compilation": False, "blocked": True}
    if not isinstance(data, dict):
        return {"tags": [], "title": None, "artist": None, "pair": None,
                "compilation": False, "blocked": True}

    doc = data.get("title") or ""
    if _CHALLENGE.search(doc) or not doc.strip():
        return {"tags": [], "title": None, "artist": None, "pair": None,
                "compilation": False, "blocked": True}

    title = artist = None
    parts = [p for p in data.get("arranged_of") or [] if p.lower() not in _ARRANGED_NOISE]
    for part in parts:
        if part.lower().startswith("by ") and not artist:
            artist = part[3:].strip()
        elif not title:
            title = part

    # The document title carries both, but the order is uploader-dependent:
    # "Martin Garrix & Dua Lipa - Scared To Be Lonely" and
    # "Fable - Robert Miles" are the same format in opposite orders. Never guess.
    # Hand both sides over unordered and let an artist lookup settle it.
    pair = None
    if not (title and artist):
        head = re.split(r"\s+Sheet [Mm]usic\b", data.get("title") or "")[0]
        head = re.sub(r"\s*\|\s*MuseScore\.com\s*$", "", head)
        head = re.sub(r"^[^\w(]+", "", head).strip()
        pieces = [p.strip() for p in re.split(r"\s+[-\u2013\u2014]\s+", head, maxsplit=1)]
        if len(pieces) == 2 and all(pieces):
            pair = pieces
        elif head and not title:
            title = head

    doc_title = data.get("title") or ""
    compilation = bool(_COMPILATION.search(doc_title))
    if compilation:
        # arranged_of names only one of the album's tracks; take the real name
        # from the document title and let the song registry skip this score.
        head = re.split(r"\s+Sheet [Mm]usic\b", doc_title)[0]
        head = re.sub(r"^[^\w(]+", "", head).strip()
        pieces = [p.strip() for p in re.split(r"\s+[-\u2013\u2014]\s+", head, maxsplit=1)]
        title = pieces[-1] if len(pieces) == 2 else (head or title)

    tags = [t for t in (data.get("tags") or []) if not _TAG_UI_NOISE.search(t)]
    return {"tags": tags, "title": title, "artist": artist, "pair": pair,
            "compilation": compilation, "blocked": False}



def read_tags(agent, page_text=""):
    """Backwards-compatible tag-only view of read_page."""
    return read_page(agent)["tags"]


# The score page mounts its toolbar only after the score viewer has loaded.
# jev-ultrafast's snapshot offers the model only *visible* elements, so acting
# before the toolbar appears leaves "Edit on desktop" out of the action space
# entirely -- the agent then clicks the best of what is left (the uploader's
# profile link, in the first run here) and reports success.
EDIT_VISIBLE_JS = """(() => {
  const b = [...document.querySelectorAll('button,a')]
    .find(e => /edit on desktop/i.test((e.textContent || '').trim()));
  return !!(b && (b.offsetWidth || b.offsetHeight));
})()"""


def wait_for_edit_button(agent, timeout=45.0, poll=1.0):
    """Block until 'Edit on desktop' is actually visible. True if it appeared."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if agent.browser.evaluate(EDIT_VISIBLE_JS) is True:
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


def _run(agent, label, verbose):
    state = None
    for state in agent.run():
        if verbose:
            print(f"    {label} {state['elapsed_ms']:>6} ms  {state['status']}")
    return state


def screen(query, *, client=None, allow_ambiguous=False):
    """Genre check that costs no download. Returns (may_download, verdict)."""
    verdict = classify_query(query, client=client)
    if verdict.status == NON_ELECTRONIC:
        return False, verdict
    if verdict.status == ELECTRONIC_EDM:
        return True, verdict
    return allow_ambiguous, verdict


def _page_gate(page, verdict, screen_genre, allow_ambiguous, collected, force, verbose,
               client=None):
    """Last free check before the Edit click. Returns (detail, verdict) to stop."""
    if page.get("blocked"):
        return "score page could not be read (blocked)", verdict
    if not screen_genre or force:
        return None
    from_page = classify_page(page, client=client)
    if from_page.status == ELECTRONIC_EDM:
        if verbose:
            who = " — ".join(x for x in (from_page.artist, from_page.title) if x)
            print(f"    {who or 'page'}: {from_page.status} "
                  f"[{', '.join(from_page.electronic[:4])}]")
        return None
    if allow_ambiguous and from_page.status != NON_ELECTRONIC:
        return None
    who = " — ".join(x for x in (from_page.artist, from_page.title) if x) or "unknown track"
    detail = f"{from_page.status}: {who}"
    if from_page.status != NON_ELECTRONIC:
        detail += " (pass --allow-ambiguous to fetch it anyway)"
    if verbose:
        print(f"    {detail}; no download spent")
    return detail, from_page


def fetch_track(query, *, verbose=True, timeout=HANDOFF_TIMEOUT_S, client=None,
                allow_ambiguous=False, limit=budget.DAILY_LIMIT, screen_genre=True,
                collected=None, force=False, close_windows=True):
    """Screen, budget-check, then fetch one track. Returns a Result."""
    verdict = None
    is_url_query = query.startswith("http://") or query.startswith("https://")
    # A bare score URL names no track, so the phrase-level screen cannot judge
    # it. The page itself can, and the page is free, so the gate moves there.
    if screen_genre and not is_url_query:
        may, verdict = screen(query, client=client, allow_ambiguous=allow_ambiguous)
        if not may:
            detail = f"genre {verdict.status}"
            if verdict.status != NON_ELECTRONIC:
                detail += " (pass --allow-ambiguous to fetch it anyway)"
            if verbose:
                print(f"    {detail}; no download spent")
            return Result(query, SKIPPED_GENRE, genre=verdict, detail=detail)

    if query.startswith("http"):
        query = canonical_url(query)
    have = cached_score_id(query) if query.startswith("http") else None
    if have and not force:
        detail = f"score {have} already in cloud_scores; no download spent"
        if verbose:
            print(f"    {detail}")
        return Result(query, SKIPPED_HAVE, path=CLOUD_SCORES / f"{have}.mscz",
                      genre=verdict, detail=detail)

    if budget.remaining(limit) <= 0:
        detail = budget.describe(limit)
        if verbose:
            print(f"    {detail}")
        return Result(query, SKIPPED_BUDGET, genre=verdict, detail=detail)

    before = cache_snapshot()
    is_url = query.startswith("http://") or query.startswith("https://")
    page = {}
    try:
        if is_url:
            with Agent(query, EDIT_GOAL) as agent:
                wait_for_page_ready(agent)
                page = read_page(agent)
                blocked = _page_gate(page, verdict, screen_genre, allow_ambiguous,
                                     collected, force, verbose, client=client)
                if blocked:
                    return Result(query, SKIPPED_GENRE, genre=blocked[1], detail=blocked[0])
                if not wait_for_edit_button(agent):
                    return Result(query, FAILED, genre=verdict,
                                  detail="'Edit on desktop' never became visible")
                if verbose:
                    print("    'Edit on desktop' visible; clicking")
                _run(agent, "edit", verbose)
        else:
            # The search box is URL-addressable, so the agent only ever needs to
            # CLICK. That avoids TYPE_TEXT and its separate text-model API key.
            url = MUSESCORE_SEARCH.format(query=quote_plus(query))
            with Agent(url, OPEN_GOAL.format(query=query)) as agent:
                wait_for_page_ready(agent)
                state = _run(agent, "find", verbose)
                score_url = state["page"]["url"] if state else None
            if not score_url or "/user/" not in score_url or "/scores/" not in score_url:
                return Result(query, FAILED, genre=verdict, detail="no score page reached")
            have = cached_score_id(score_url)
            if have and not force:
                detail = f"score {have} already in cloud_scores; no download spent"
                if verbose:
                    print(f"    {detail}")
                return Result(query, SKIPPED_HAVE, path=CLOUD_SCORES / f"{have}.mscz",
                              genre=verdict, detail=detail)
            with Agent(score_url, EDIT_GOAL) as agent:
                wait_for_page_ready(agent)
                page = read_page(agent)
                # The page names the song, and the page is free. If that song is
                # already in the dataset, stop before spending a download.
                key = song_key(page.get("artist"), page.get("title"))
                if key and key in (collected or set()) and not force:
                    detail = f"already have this song ({key}); no download spent"
                    if verbose:
                        print(f"    {detail}")
                    return Result(query, SKIPPED_HAVE, genre=verdict, detail=detail)
                blocked = _page_gate(page, verdict, screen_genre, allow_ambiguous,
                                     collected, force, verbose, client=client)
                if blocked:
                    return Result(query, SKIPPED_GENRE, genre=blocked[1], detail=blocked[0])
                if not wait_for_edit_button(agent):
                    return Result(query, FAILED, genre=verdict,
                                  detail="'Edit on desktop' never became visible")
                if verbose:
                    print("    'Edit on desktop' visible; clicking")
                _run(agent, "edit", verbose)
    except Exception as exc:
        return Result(query, FAILED, genre=verdict, detail=f"{type(exc).__name__}: {exc}")

    if verbose:
        print("    waiting for MuseScore Studio handoff...")
    path = wait_for_new_score(before, timeout=timeout)
    if not path:
        # Nothing landed, so nothing was spent as far as we can tell.
        path = wait_for_new_score(before, timeout=45.0, settle=1.0)
        if not path:
            return Result(query, FAILED, genre=verdict, detail="handoff timed out")

    if not page.get("blocked") and (page.get("tags") or page.get("title") or page.get("pair")):
        save_tags(path.stem, page.get("tags") or [], title=page.get("title"),
                  artist=page.get("artist"), pair=page.get("pair"),
                  compilation=page.get("compilation", False))
        from_tags = classify_tags(page.get("tags") or [])
        if from_tags.status != "AMBIGUOUS":
            verdict = from_tags
        if verbose:
            who = " / ".join(x for x in (page.get("artist"), page.get("title")) if x)
            print(f"    page: {who}  tags: {', '.join((page.get('tags') or [])[:5])}")

    # Studio leaves every opened score on screen; a 20-score run would end
    # with 20 windows. Close it now that the .mscz is safely on disk.
    if close_windows:
        closed = studio.close_score_window()
        # Closing the window is not enough: each handoff starts its own
        # MuseScore process, so the instances pile up in the Dock too.
        # Keep one instance alive: quitting them all forces the next
        # musescore:// to cold-start a 184 MB app, and the handoff wait expires
        # before it finishes launching (6 of 17 timed out that way). One warm
        # instance still prevents the pile-up.
        quit_count = studio.quit_extras(keep=1)
        if verbose and (closed or quit_count):
            print(f"    closed {closed} window(s), quit {quit_count} Studio instance(s)")

    used = budget.record(path.stem, note=query, path=budget.BUDGET_FILE)
    detail = f"{path.name}  ({used}/{limit} used today)"
    if verbose:
        print(f"    {detail}")
    return Result(query, FETCHED, path=path, genre=verdict, detail=detail)


def fetch_all(queries, *, verbose=True, client=None, **kwargs):
    """Fetch a list of tracks, stopping cleanly when the daily quota runs out."""
    client = client or _Client()
    collected = kwargs.pop("collected", None)
    if collected is None:
        collected = known_keys(load_state())
    results = []
    for i, query in enumerate(queries, 1):
        if verbose:
            print(f"[{i}/{len(queries)}] {query}")
        result = fetch_track(query, verbose=verbose, client=client,
                             collected=collected, **kwargs)
        results.append(result)
        if result.status == SKIPPED_BUDGET:
            remaining = queries[i:]
            if remaining and verbose:
                print(f"    stopping: {len(remaining)} track(s) not attempted")
            break
    return results


def enrich(score_ids=None, *, verbose=True, pause=3.0):
    """Visit cached scores' musescore.com pages and store their tags and track.

    This spends **no downloads** — it only reads the public score page, which is
    what the "Edit" click would have cost. Use it to resolve the genre of scores
    already in cloud_scores/ whose .mscz carries no usable metadata.
    """
    from .genre import parse_metadata

    results = []
    paths = sorted(CLOUD_SCORES.glob("*.mscz"))
    if score_ids:
        wanted = {str(s) for s in score_ids}
        paths = [p for p in paths if p.stem in wanted]

    for i, mscz in enumerate(paths, 1):
        source = parse_metadata(mscz).get("source")
        if not source:
            if verbose:
                print(f"[{i}/{len(paths)}] {mscz.stem}: no source URL in the score")
            results.append((mscz.stem, None))
            continue
        url = source.replace("http://", "https://")
        try:
            with Agent(url, "Do nothing; this page is only being read.") as agent:
                wait_for_page_ready(agent)
                page = read_page(agent)
        except Exception as exc:
            if verbose:
                print(f"[{i}/{len(paths)}] {mscz.stem}: {type(exc).__name__}: {exc}")
            results.append((mscz.stem, None))
            continue
        if page.get("blocked"):
            if verbose:
                print(f"[{i}/{len(paths)}] {mscz.stem}: blocked by Cloudflare; left unchanged")
            results.append((mscz.stem, None))
            time.sleep(pause * 4)
            continue
        if page.get("tags") or page.get("title") or page.get("pair"):
            save_tags(mscz.stem, page.get("tags") or [], title=page.get("title"),
                      artist=page.get("artist"), pair=page.get("pair"),
                      compilation=page.get("compilation", False))
        if verbose:
            who = " / ".join(x for x in (page.get("artist"), page.get("title")) if x) or "?"
            print(f"[{i}/{len(paths)}] {mscz.stem}: {who}  "
                  f"[{', '.join((page.get('tags') or [])[:5])}]")
        results.append((mscz.stem, page))
        time.sleep(pause)
    return results
