"""Stage 1.5: decide whether the *underlying track* is electronic/EDM.

Genre is a fact about the song, not about the MuseScore arrangement. An
orchestral cover of a Kygo single is still electronic; a piano reduction of a
Tina Turner song is not. So nothing here looks at MIDI programs, instrument
names, or anything else the arranger chose — it reads the title and artist off
the score, normalizes them, and asks public music metadata what the track is.

`What's Love Got to Do with It` in this collection is credited to
"Kygo feat. Tina Turner": the Kygo remix, which *is* electronic. It is excluded
later by the structural stage for being a one-part piano reduction, not here.
That separation is the point.

Output is ELECTRONIC_EDM, NON_ELECTRONIC, or AMBIGUOUS. Ambiguous is never
forced into a decision; it goes to review with its evidence attached.
"""

import html
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import HOME

MB_ROOT = "https://musicbrainz.org/ws/2"
USER_AGENT = "musescore-midi/0.1 (https://github.com/browser-use/jev-ultrafast)"
MB_MIN_INTERVAL = 1.1  # MusicBrainz asks for one request per second
CACHE_FILE = Path(HOME / ".musescore_genre_cache.json")
# Explicit, human-asserted labels keyed by score id. These win over any lookup:
# the person who picked the track knows what it is.
OVERRIDE_FILE = Path(HOME / ".musescore_genre_overrides.json")
# Tags scraped from a score's musescore.com page, keyed by score id. These are
# curated per score by the uploader and the site, and say "Electronic" outright.
TAGS_FILE = Path(HOME / ".musescore_score_tags.json")

ELECTRONIC_EDM, NON_ELECTRONIC, AMBIGUOUS = "ELECTRONIC_EDM", "NON_ELECTRONIC", "AMBIGUOUS"

# Substrings, matched against lowercased labels. "house" catches deep/tropical/
# progressive/electro/future/bass house; "step" catches dubstep and brostep.
ELECTRONIC_PATTERNS = (
    "edm", "electronic", "electronica", "electro", "house", "techno", "trance",
    "dubstep", "step", "drum and bass", "drum & bass", "dnb", "d&b", "jungle",
    "breakbeat", "breaks", "big room", "future bass", "hardstyle", "hardcore",
    "gabber", "happy hardcore", "synthwave", "chillwave", "vaporwave", "idm",
    "garage", "moombahton", "glitch", "eurodance", "nightcore", "downtempo",
    "ambient", "trip hop", "dance", "club", "rave", "psytrance", "complextro",
    "synth-pop", "synthpop", "dance-pop", "electropop", "bass music", "trap",
)

# Labels that are positively *not* electronic. Used only to tell a genuine
# non-electronic result apart from having found nothing at all.
ACOUSTIC_PATTERNS = (
    "rock", "pop", "soul", "jazz", "blues", "classical", "country", "folk",
    "metal", "punk", "r&b", "rnb", "hip hop", "rap", "reggae", "gospel",
    "orchestral", "opera", "baroque", "romantic", "singer-songwriter",
    "soundtrack", "musical", "indie",
)

# Arrangement noise in MuseScore titles, stripped before lookup.
_NOISE = re.compile(
    r"\b(piano|orchestral?|string|brass|band|choir|guitar|violin|flute|sax\w*)?\s*"
    r"(cover|arrangement|arr\.?|transcription|version|tutorial|sheet\s*music|"
    r"full\s*score|solo|duet|quartet|reduction|remake|midi)\b",
    re.I,
)
# MuseScore ships these as field placeholders; uploaders leave them in.
PLACEHOLDERS = {
    "composer", "composer / arranger", "composer/arranger", "arranger",
    "title", "subtitle", "untitled score", "untitled", "score", "temp",
    "your name", "unknown", "n/a", "-",
    # MuseScore localises its default title, and uploaders leave it in.
    "partitura sin título", "partitura sin titulo", "sin título", "sin titulo",
    "partitura sem título", "partitura senza titolo", "senza titolo",
    "subtítulo", "subtitulo", "sottotitolo", "sous-titre", "untertitel",
    "compositor", "compositor / arreglista", "compositeur", "komponist",
    "partition sans titre", "sans titre", "unbenannt", "titel", "無題のスコア",
    "无标题乐谱", "제목 없는 악보", "безымянная партитура",
}

_FEAT = re.compile(r"\s*[\(\[]?\s*(?:ft|feat|featuring)[.:]?\s+[^)\]]*[\)\]]?", re.I)
_BRACKETS = re.compile(r"[\(\[][^)\]]*[\)\]]")
# A bare instrument word trailing a search phrase ("Kygo Firestone piano") is
# describing the arrangement, not the track.
_TRAILING = re.compile(
    r"\s+(piano|orchestral?|strings?|brass|band|choir|guitar|violin|flute|synth|"
    r"midi|sheet|music|cover|remix|edit|mix)\s*$", re.I
)


def _drop_placeholder(text):
    return "" if (text or "").strip().lower() in PLACEHOLDERS else text


def _clean(text):
    text = html.unescape(text or "")
    text = _FEAT.sub(" ", text)
    text = _BRACKETS.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[_*]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—,.")
    previous = None
    while previous != text:
        previous = text
        text = _TRAILING.sub("", text).strip(" -–—,.")
    return text


def parse_metadata(mscz):
    """Title and artist as written in the score, before normalization."""
    try:
        with zipfile.ZipFile(mscz) as z:
            members = [n for n in z.namelist() if n.endswith(".mscx")]
            if not members:
                return {}
            xml = z.read(members[0]).decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError):
        return {}
    tags = dict(re.findall(r'<metaTag name="([^"]+)">(.*?)</metaTag>', xml, re.S))
    return {k: v.strip() for k, v in tags.items() if v.strip()}


def normalize(meta):
    """Best guess at (title, artist) for a metadata lookup.

    MuseScore titles routinely carry the artist inline as "Artist - Title", and
    the composer field is where uploaders put the recording artist.
    """
    title = _clean(_drop_placeholder(meta.get("workTitle")) or _drop_placeholder(meta.get("subtitle")))
    artist = _clean(
        _drop_placeholder(meta.get("composer"))
        or _drop_placeholder(meta.get("lyricist"))
        or _drop_placeholder(meta.get("arranger"))
    )

    if not artist and " - " in title:
        left, _, right = title.partition(" - ")
        artist, title = _clean(left), _clean(right)
    elif artist and title.lower().startswith(artist.lower() + " -"):
        title = _clean(title[len(artist) + 2 :])

    # "Kygo feat. Tina Turner" -> the lead artist carries the genre
    artist = re.split(r"\s+(?:feat|ft|featuring|&|x|vs\.?)\.?\s+", artist, flags=re.I)[0].strip()
    return title or None, artist or None


class _Client:
    """Cached, rate-limited MusicBrainz access."""

    def __init__(self, cache_file=CACHE_FILE, offline=False):
        self.cache_file = Path(cache_file)
        self.offline = offline
        self._last = 0.0
        try:
            self.cache = json.loads(self.cache_file.read_text())
        except (OSError, json.JSONDecodeError):
            self.cache = {}

    def save(self):
        try:
            self.cache_file.write_text(json.dumps(self.cache, indent=2, sort_keys=True))
        except OSError:
            pass

    def get(self, path, params):
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if key in self.cache:
            return self.cache[key]
        if self.offline:
            return None
        wait = MB_MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        data = None
        for attempt in range(3):
            try:
                response = httpx.get(
                    f"{MB_ROOT}/{path}",
                    params={**params, "fmt": "json"},
                    headers={"User-Agent": USER_AGENT},
                    timeout=20.0,
                )
                self._last = time.monotonic()
                if response.status_code == 200:
                    data = response.json()
                    break
                if response.status_code not in (429, 503):
                    break
            except (httpx.HTTPError, json.JSONDecodeError):
                self._last = time.monotonic()
            time.sleep(MB_MIN_INTERVAL * (attempt + 1))
        # Never cache a failure: MusicBrainz rate-limits hard, and caching one
        # 503 would poison that lookup for every future run.
        if data is not None:
            self.cache[key] = data
        return data


def _labels(node):
    out = {}
    for field_name in ("genres", "tags"):
        for entry in node.get(field_name) or []:
            name = entry.get("name", "").lower().strip()
            if name:
                out[name] = max(out.get(name, 0), entry.get("count") or 1)
    return out


@dataclass
class GenreVerdict:
    status: str = AMBIGUOUS
    title: str = None
    artist: str = None
    matched_artist: str = None
    electronic: list = field(default_factory=list)
    other: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    unverified: list = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self):
        return self.status == ELECTRONIC_EDM

    def __str__(self):
        who = f"{self.artist or '?'} — {self.title or '?'}"
        found = ", ".join(self.electronic[:4]) or ", ".join(self.other[:4]) or "no labels"
        return f"{self.status:<16} {who[:46]:<47} [{found}]"


def _classify(labels):
    electronic = sorted({m for m in labels if any(p in m for p in ELECTRONIC_PATTERNS)})
    other = sorted({m for m in labels if m not in electronic
                    and any(p in m for p in ACOUSTIC_PATTERNS)})
    if electronic:
        return ELECTRONIC_EDM, electronic, other
    if other:
        return NON_ELECTRONIC, electronic, other
    return AMBIGUOUS, electronic, other


def lookup(title, artist, client=None):
    """Ask MusicBrainz what the underlying track is, widening until something answers."""
    client = client or _Client()
    verdict = GenreVerdict(title=title, artist=artist)
    if not artist and not title:
        verdict.reason = "no title or artist in the score metadata"
        return verdict

    labels, mbid = {}, None

    if artist:
        # The artist *search* result carries tags even when the lookup does not,
        # which is the only place some artists (Marshmello) are labelled at all.
        found = client.get("artist", {"query": artist, "limit": "5"})
        candidates = [a for a in (found or {}).get("artists", []) if a.get("score", 0) >= 90]
        for candidate in candidates[:3]:
            mbid = candidate.get("id")
            if verdict.matched_artist is None:
                verdict.matched_artist = candidate.get("name")
            got = _labels(candidate)
            if got:
                labels.update(got)
                verdict.evidence.append({"source": "musicbrainz:artist-search",
                                         "artist": candidate.get("name"), "labels": sorted(got)})
            detail = client.get(f"artist/{mbid}", {"inc": "genres+tags"}) if mbid else None
            got = _labels(detail or {})
            if got:
                labels.update(got)
                verdict.evidence.append({"source": "musicbrainz:artist", "mbid": mbid,
                                         "artist": candidate.get("name"), "labels": sorted(got)})
            if labels:
                break

    if not labels and title:
        query = f'recording:"{title}"' + (f' AND artist:"{artist}"' if artist else "")
        found = client.get("recording", {"query": query, "limit": "5"})
        recordings = (found or {}).get("recordings", [])
        # Recording tags only count when an artist from the score corroborates
        # the match. Otherwise `recording:"Avicii Levels"` happily returns a
        # classical cover and its "classical" tag decides the verdict.
        if artist:
            for rec in recordings[:3]:
                got = _labels(rec)
                if got:
                    labels.update(got)
                    verdict.evidence.append({"source": "musicbrainz:recording",
                                             "title": rec.get("title"), "labels": sorted(got)})
        # With no artist from the score there is nothing to verify a title match
        # against: "Avril" and "end of spring" both resolve confidently to
        # completely unrelated songs. Record the candidate as evidence and leave
        # the verdict ambiguous for a human, rather than guessing.
        if not labels and not artist:
            for rec in recordings[:3]:
                if rec.get("score", 0) < 90:
                    break
                credit = (rec.get("artist-credit") or [{}])[0]
                found_artist = credit.get("artist") or {}
                if found_artist.get("name"):
                    verdict.unverified.append(
                        f"{found_artist['name']} — {rec.get('title')}"
                    )
            if verdict.unverified:
                verdict.evidence.append({"source": "musicbrainz:title-only",
                                         "unverified_candidates": verdict.unverified})

    client.save()
    verdict.status, verdict.electronic, verdict.other = _classify(labels)
    if verdict.status == AMBIGUOUS:
        if verdict.unverified:
            verdict.reason = ("title matched only without an artist to verify it: "
                              + "; ".join(verdict.unverified[:2]))
        elif not labels:
            verdict.reason = "no genre labels found for this artist or track"
        else:
            verdict.reason = "labels found but none conclusive"
    else:
        verdict.reason = f"{len(labels)} label(s) from {len(verdict.evidence)} source(s)"
    return verdict


def classify_score(mscz, client=None):
    """Genre verdict for one cached .mscz."""
    meta = parse_metadata(mscz)
    title, artist = normalize(meta)
    verdict = lookup(title, artist, client=client)
    if verdict.status == AMBIGUOUS and not (title or artist) and meta.get("source"):
        verdict.reason = f"no title/artist in score; source page {meta['source']}"
    return verdict


# --- explicit assertions ---------------------------------------------------


def load_overrides(path=OVERRIDE_FILE):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def set_override(score_id, status, *, title=None, artist=None, note=None, path=OVERRIDE_FILE):
    """Record a human decision about one score's genre."""
    if status not in (ELECTRONIC_EDM, NON_ELECTRONIC, AMBIGUOUS):
        raise ValueError(f"status must be one of {ELECTRONIC_EDM}, {NON_ELECTRONIC}, {AMBIGUOUS}")
    data = load_overrides(path)
    data[str(score_id)] = {
        "status": status,
        "title": title,
        "artist": artist,
        "note": note,
        "set_at": time.strftime("%Y-%m-%d"),
    }
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))
    return data[str(score_id)]


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _resolve_split(text, client):
    """Split a free-form phrase into (artist, title) by longest exact artist match."""
    words = text.split()
    for cut in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:cut])
        found = client.get("artist", {"query": prefix, "limit": "3"})
        for candidate in (found or {}).get("artists", []):
            if candidate.get("score", 0) < 90:
                break
            names = [candidate.get("name", "")] + [
                a.get("name", "") for a in candidate.get("aliases") or []
            ]
            if any(_norm(n) == _norm(prefix) for n in names):
                return prefix, " ".join(words[cut:])
    return None, text


def classify_query(query, client=None):
    """Genre for a track the user asked for, from the search phrase itself.

    This is the strongest signal available: "Kygo Firestone" names the track
    directly, whereas the .mscz an uploader produced may carry no metadata at
    all. A bare musescore.com URL says nothing about the song, so it returns
    ambiguous rather than inventing a match.
    """
    if not query or query.startswith("http"):
        v = GenreVerdict()
        v.reason = "a score URL names no track; genre must come from the score or an override"
        return v
    text = _clean(query)
    client = client or _Client()

    if " - " in text:
        left, _, right = text.partition(" - ")
        artist, title = _clean(left), _clean(right)
    else:
        # A search phrase holds both artist and title with no marker between
        # them. Splitting on the first word makes "Martin Garrix Animals" into
        # the artist "Martin", who makes country pop; free-texting the whole
        # phrase matches a classical cover of "Levels". So try every split from
        # the longest prefix down and keep the first that names a real artist
        # exactly -- "Martin Garrix" matches, "Martin Garrix Animals" does not.
        artist, title = _resolve_split(text, client)

    verdict = lookup(title, artist, client=client)
    verdict.evidence.insert(0, {"source": "user-selection", "query": query,
                                "parsed": {"artist": artist, "title": title}})
    verdict = lookup(title, artist, client=client)
    verdict.evidence.insert(0, {"source": "user-selection", "query": query})
    return verdict


def resolve(mscz, *, query=None, client=None, overrides=None, tags=None):
    """Genre for one score.

    Order of authority: an explicit human override, then the tags on the score's
    musescore.com page, then the track the user asked for, then whatever the
    uploader left in the .mscz.
    """
    score_id = Path(mscz).stem
    overrides = load_overrides() if overrides is None else overrides
    if score_id in overrides:
        entry = overrides[score_id]
        v = GenreVerdict(status=entry["status"], title=entry.get("title"),
                         artist=entry.get("artist"))
        v.evidence = [{"source": "override", **entry}]
        v.reason = entry.get("note") or "explicitly asserted"
        return v

    entry = (load_tags() if tags is None else tags).get(score_id)
    if entry:
        page_tags = _entry_tags(entry)
        if page_tags:
            v = classify_tags(page_tags)
            if v.status != AMBIGUOUS:
                v.title = (entry.get("title") if isinstance(entry, dict) else None) or v.title
                v.artist = (entry.get("artist") if isinstance(entry, dict) else None) or v.artist
                return v
        # The page also names the underlying track, which beats .mscz metadata.
        page_title, page_artist = None, None
        if isinstance(entry, dict):
            page_title, page_artist = entry.get("title"), entry.get("artist")
            if not (page_title and page_artist) and entry.get("pair"):
                page_title, page_artist = resolve_pair(entry["pair"], client=client)
        if page_title or page_artist:
            v = lookup(page_title, page_artist, client=client)
            v.evidence.insert(0, {"source": "musescore:page", "title": page_title,
                                  "artist": page_artist})
            if v.status != AMBIGUOUS:
                return v

    if query:
        v = classify_query(query, client=client)
        if v.status != AMBIGUOUS:
            return v

    return classify_score(mscz, client=client)


# --- musescore.com page tags ----------------------------------------------

# Tag vocabulary on musescore.com is a short curated list; "Electronic" and
# "Dance/Electronic" are the ones that matter, alongside explicit subgenres.
TAG_NOISE = {
    # instrument and ensemble tags carry no genre information
    "piano", "guitar", "violin", "flute", "trumpet", "trombone", "tuba", "drums",
    "drum set", "saxophone", "clarinet", "cello", "voice", "choir", "organ",
    "bass guitar", "mellophone", "brass band", "brass band (new orleans)",
    "orchestra", "string orchestra", "concert band", "marching band", "solo",
    "duet", "trio", "quartet", "ensemble", "easy", "intermediate", "advanced",
    "mixed ensemble", "alto", "snare drum", "bass guitar", "drum group",
    "strings group", "synthesizer", "woodwind group", "brass group", "vocals",
    "soprano", "tenor", "baritone", "percussion", "keyboard", "electric guitar",
}


def load_tags(path=TAGS_FILE):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_tags(score_id, tags, path=TAGS_FILE, title=None, artist=None, pair=None,
              compilation=False):
    """Record what a score's musescore.com page says: its tags and its track."""
    data = load_tags(path)
    entry = {"tags": sorted({t.strip() for t in tags if t.strip()})}
    if title:
        entry["title"] = title
    if artist:
        entry["artist"] = artist
    if pair:
        entry["pair"] = list(pair)
    if compilation:
        entry["compilation"] = True
    data[str(score_id)] = entry
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))
    return entry


def _entry_tags(entry):
    """Tag list from a store entry, tolerating the older bare-list format."""
    if isinstance(entry, dict):
        return entry.get("tags") or []
    return entry or []


def classify_tags(tags):
    """Genre verdict from musescore.com page tags alone.

    Tags can only *confirm* electronic, never rule it out. They often describe
    the arrangement rather than the song: an orchestral cover of Odesza's
    "Higher Ground" is tagged Classical, and rejecting on that would throw away
    exactly the orchestral-cover-of-an-EDM-track case this pipeline exists for.
    A non-electronic tag set therefore falls through to the artist lookup.
    """
    meaningful = [t for t in tags if t.strip().lower() not in TAG_NOISE]
    status, electronic, other = _classify({t.lower(): 1 for t in meaningful})
    if status == NON_ELECTRONIC:
        status = AMBIGUOUS
    verdict = GenreVerdict(status=status, electronic=electronic, other=other)
    verdict.evidence = [{"source": "musescore:tags", "tags": sorted(tags)}]
    verdict.reason = (
        f"musescore tag(s): {', '.join(electronic)}" if electronic
        else f"musescore tags describe the arrangement, not the song: "
             f"{', '.join(meaningful) or 'none'}"
    )
    return verdict


def _artist_exists(name, client):
    """The exact-matching artist name, or None."""
    found = client.get("artist", {"query": name, "limit": "3"})
    for candidate in (found or {}).get("artists", []):
        if candidate.get("score", 0) < 90:
            break
        names = [candidate.get("name", "")] + [
            a.get("name", "") for a in candidate.get("aliases") or []
        ]
        if any(_norm(n) == _norm(name) for n in names):
            return candidate.get("name")
    return None


def _recording_exists(title, artist, client):
    """Whether MusicBrainz has this title recorded by this artist."""
    found = client.get(
        "recording", {"query": f'recording:"{title}" AND artist:"{artist}"', "limit": "3"}
    )
    for rec in (found or {}).get("recordings", []):
        if rec.get("score", 0) < 90:
            break
        if _norm(rec.get("title")) == _norm(title):
            return True
    return False


def resolve_pair(pair, client=None):
    """Settle an unordered (A, B) from a page title into (title, artist).

    musescore.com writes "Artist - Title" and "Title - Artist" with equal
    enthusiasm. Both sides can name a real artist -- "Fable" is a band as well
    as a Robert Miles single -- so an exact artist match alone picks the wrong
    order. The ordering that also has a matching *recording* is the right one.
    """
    client = client or _Client()
    candidates = []
    for artist_side, title_side in (pair, pair[::-1]):
        # "Martin Garrix & Dua Lipa" is a collaboration, not an artist; the
        # lead name is the one carrying the genre.
        lead = re.split(r"\s+(?:&|x|vs\.?|feat\.?|ft\.?|and|with)\s+",
                        artist_side, flags=re.I)[0].strip()
        matched = _artist_exists(lead, client)
        if matched:
            candidates.append((title_side, matched))

    # Both sides can name a real artist, so score each ordering: a matching
    # recording is strong evidence, and an artist carrying genre labels at all
    # is weak evidence. "FEELS" is a real band; "Calvin Harris" is the one with
    # a discography.
    best, best_score = None, -1
    for position, (title, artist) in enumerate(candidates):
        score = 0
        if _recording_exists(title, artist, client):
            score += 4
        found = client.get("artist", {"query": artist, "limit": "1"})
        for candidate in (found or {}).get("artists", [])[:1]:
            if _labels(candidate):
                score += 1
        score -= position * 0.1  # ties keep the page's own order
        if score > best_score:
            best, best_score = (title, artist), score
    return best or (None, None)


def classify_page(page, client=None):
    """Genre from a read_page() result: tags first, then the track it names.

    Most score pages tag only instruments -- "Everytime We Touch" carries
    Trombone, Flute, Saxophone and nothing about genre -- so stopping at the
    tags would refuse almost every page. The page also names the underlying
    track, and that resolves through the same artist lookup as everything else.
    """
    client = client or _Client()
    tags = page.get("tags") or []
    if tags:
        verdict = classify_tags(tags)
        if verdict.status == ELECTRONIC_EDM:
            return verdict

    title, artist = page.get("title"), page.get("artist")
    if not (title and artist) and page.get("pair"):
        title, artist = resolve_pair(page["pair"], client=client)
    if title or artist:
        verdict = lookup(title, artist, client=client)
        verdict.evidence.insert(0, {"source": "musescore:page", "title": title,
                                    "artist": artist, "tags": tags})
        return verdict

    verdict = GenreVerdict(title=title, artist=artist)
    verdict.reason = f"page names no track; tags: {', '.join(tags) or 'none'}"
    return verdict
