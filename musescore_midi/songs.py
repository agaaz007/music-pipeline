"""Canonical-song identity, so the dataset does not pay twice for one composition.

Two MuseScore arrangements of Calvin Harris's "Feels" are both legitimate files
and both may be worth keeping, but they are one underlying song. Every score
gets a `song_key` derived from its canonical artist and title, so duplicates can
be reported, and — more usefully — so a fetch can decline to spend one of the
day's limited downloads on a song already in the dataset.

Nothing here deletes anything. It reports identity; a person decides.
"""

import re
import unicodedata

# Anything after these markers describes a rendition, not a different song.
_VARIANT = re.compile(
    r"\s*[\(\[]?\b(remix|edit|mix|version|cover|arrangement|arr|remaster(?:ed)?|"
    r"live|acoustic|instrumental|extended|radio|club|bootleg|mashup|vip|rework|"
    r"feat|ft|featuring|piano|orchestral?|strings?|brass|band|guitar|violin|"
    r"flute|sax\w*|choir|quartet|duet|solo|tutorial|sheet|midi|full\s*score)\b.*$",
    re.I,
)
_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)


def _fold(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _VARIANT.sub("", text)
    text = _ARTICLE.sub("", text.strip())
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def song_key(artist, title):
    """Stable identity for a composition, or None when it cannot be determined.

    A title alone is too weak: several unrelated songs are called "Alone". Both
    sides are required so two scores only collide when they agree on the artist.
    """
    a, t = _fold(artist), _fold(title)
    if not a or not t:
        return None
    return f"{a}::{t}"


def key_for(decision):
    """song_key from a Decision's genre verdict."""
    genre = getattr(decision, "genre", None)
    return song_key(getattr(genre, "artist", None), getattr(genre, "title", None))


def group(state):
    """{song_key: [score_id, ...]} for every score whose song is known."""
    groups = {}
    for score_id, record in (state or {}).items():
        key = (record.get("metrics") or {}).get("song_key")
        if key:
            groups.setdefault(key, []).append(score_id)
    return groups


def duplicates(state):
    """Only the song keys held by more than one score."""
    return {k: v for k, v in group(state).items() if len(v) > 1}


def known_keys(state):
    """Song keys already represented by an accepted score."""
    return {
        (record.get("metrics") or {}).get("song_key")
        for record in (state or {}).values()
        if record.get("outcome") == "accept" and (record.get("metrics") or {}).get("song_key")
    } - {None}
