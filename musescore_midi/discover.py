"""Find the right score behind a song, artist, set, or profile URL.

Several of the links in a wishlist are not score pages at all: a /song/ page
lists every arrangement of one track, an /artist/ page lists an act's whole
catalogue, a /sets/ page is a collection, and a bare /user/ page is a profile.
Handing any of those to the fetcher fails, because there is no "Edit on
desktop" button to click.

So they are resolved first, for free: the listing is read, its score links are
pulled out with whatever part count and duration the site shows, and the
candidate matching the requested arrangement is chosen. Only then does a
download get spent.
"""

import re

# Cards on a listing expose the score link, and usually a part count and a
# duration next to it. The DOM class names are generated and change often, so
# the text around each link is what gets parsed.
# Each score is linked twice: an empty thumbnail anchor and a text anchor
# carrying the title. Taking the first match yields an empty title on search
# pages, so merge every anchor for a score id and keep the longest text.
LISTING_JS = """(() => {
  const byId = new Map();
  for (const a of document.querySelectorAll('a[href*="/scores/"]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\\/scores\\/(\\d+)/);
    if (!m) continue;
    const card = a.closest('article, li, [class*="card"], [class*="Card"]') || a.parentElement;
    const text = (card ? card.innerText : a.textContent || '').replace(/\\s+/g, ' ').trim();
    const label = (a.textContent || '').trim();
    const prev = byId.get(m[1]);
    if (!prev) byId.set(m[1], {id: m[1], href: href, title: label, text: text});
    else {
      if (label.length > prev.title.length) { prev.title = label; prev.href = href; }
      if (text.length > prev.text.length) prev.text = text;
    }
  }
  return [...byId.values()].slice(0, 60)
    .map(r => ({...r, title: r.title.slice(0, 80), text: r.text.slice(0, 300)}));
})()"""

_PARTS = re.compile(r"\b(\d{1,2})\s*(?:parts?|instruments?|voices?)\b", re.I)
_DURATION = re.compile(r"\b(\d{1,2}):([0-5]\d)\b")


def parse_candidates(rows):
    """Normalize raw listing rows into candidates with parts and seconds."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        text = row.get("text") or ""
        parts = _PARTS.search(text)
        duration = _DURATION.search(text)
        out.append({
            "id": str(row["id"]),
            "title": (row.get("title") or "").strip(),
            "url": _absolute(row.get("href") or ""),
            "parts": int(parts.group(1)) if parts else None,
            "seconds": (int(duration.group(1)) * 60 + int(duration.group(2))) if duration else None,
            "text": text,
        })
    return out


def _absolute(href):
    if href.startswith("http"):
        return href
    return "https://musescore.com" + (href if href.startswith("/") else "/" + href)


def score(candidate, want_parts=None, want_seconds=None, want_title=None):
    """How well a candidate matches the requested arrangement. Higher is better."""
    points = 0.0
    if want_title:
        wanted = re.sub(r"[^a-z0-9]", "", want_title.lower())
        got = re.sub(r"[^a-z0-9]", "", (candidate["title"] + " " + candidate["text"]).lower())
        if wanted and wanted in got:
            points += 3
    if want_parts is not None and candidate["parts"] is not None:
        # An exact part count is the strongest signal a wishlist gives.
        points += 4 if candidate["parts"] == want_parts else max(0, 2 - abs(candidate["parts"] - want_parts))
    if want_seconds is not None and candidate["seconds"] is not None:
        delta = abs(candidate["seconds"] - want_seconds)
        points += 4 if delta <= 3 else max(0, 2 - delta / 30)
    return points


def choose(candidates, want_parts=None, want_seconds=None, want_title=None):
    """Best candidate and the ranked list, so a weak match stays visible."""
    ranked = sorted(
        ((score(c, want_parts, want_seconds, want_title), c) for c in candidates),
        key=lambda pair: -pair[0],
    )
    if not ranked:
        return None, []
    return ranked[0][1] if ranked[0][0] > 0 else None, ranked


def parse_spec(text):
    """Pull '7-part, 3:12' style requirements out of a wishlist line."""
    parts = re.search(r"(\d{1,2})[- ]part", text or "", re.I)
    duration = _DURATION.search(text or "")
    return (
        int(parts.group(1)) if parts else None,
        (int(duration.group(1)) * 60 + int(duration.group(2))) if duration else None,
    )


# --- picking usable scores off a profile -----------------------------------

# A listing gives only a part count and a duration, so "usable" here is a cheap
# pre-filter, not the real verdict: the structural classifier still runs after
# download. The point is to avoid spending a download on something the listing
# already shows is a solo or a fragment.
USABLE_MIN_PARTS = 4
USABLE_MIN_SECONDS = 60


def usable(candidate, min_parts=USABLE_MIN_PARTS, min_seconds=USABLE_MIN_SECONDS):
    """Whether a listing entry is worth a download."""
    parts, secs = candidate.get("parts"), candidate.get("seconds")
    if parts is not None and parts < min_parts:
        return False
    if secs is not None and secs < min_seconds:
        return False
    # An unknown part count is not disqualifying; the listing often omits it.
    return parts is not None or secs is not None


def pick_usable(candidates, *, have_ids=(), have_songs=(), limit=None,
                min_parts=USABLE_MIN_PARTS, min_seconds=USABLE_MIN_SECONDS):
    """Usable candidates we do not already hold, richest arrangement first.

    `have_ids` are score ids already cached; `have_songs` are canonical song
    keys already accepted, so a different upload of a song already in the set
    is skipped too.
    """
    from .songs import song_key

    have_ids = {str(i) for i in have_ids}
    have_songs = {k for k in have_songs if k}
    out, seen_songs = [], set()
    for c in sorted(candidates, key=lambda c: -(c.get("parts") or 0)):
        if c["id"] in have_ids:
            continue
        if not usable(c, min_parts, min_seconds):
            continue
        # The listing title is usually "Title" or "Artist - Title"; without a
        # reliable artist this only catches same-title repeats within a profile.
        key = song_key("x", c["title"]) or c["title"].strip().lower()
        if key in have_songs or key in seen_songs:
            continue
        seen_songs.add(key)
        out.append(c)
        if limit and len(out) >= limit:
            break
    return out
