"""Structural classifier: is this MIDI a full, regular, multi-part arrangement?

Genre is deliberately absent from this module. An orchestral cover of an EDM
track is structurally identical to an orchestral cover of a rock track, so
instrumentation cannot and must not decide genre. See `genre.py`.

The one measurement that needs care is how many parts a score really has.
MuseScore exports one MIDI track per *staff*, so a single piano arrives as two
tracks — treble and bass — that share a channel and a track name. Counting raw
tracks would call that a two-stem arrangement. Parts are therefore grouped by
(track name, channel), which merges the staves of one instrument while keeping
genuinely separate voices apart: two violin desks on different channels stay two
parts, and five independent piano voices on five channels stay five.

Only obvious reductions and fragments are rejected outright. Everything that is
merely unusual goes to review, never to reject.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .duration import _tracks, _vlq, duration_seconds

DRUM_CHANNEL = 9
BASS_MEDIAN_PITCH = 52  # at or below roughly E3 reads as a bass register

# A part carrying almost nothing is a fill or a one-off effect, not a stem.
# The share test scales with the score, but a part with this many notes is real
# whatever else is going on: without the ceiling a dense track's own busiest
# stems raise the bar until its genuine lead lines look like noise.
# The floor is a share of the score, clamped at both ends: without the ceiling a
# dense track's busiest stems raise the bar until its genuine lead looks like
# noise; without the small floor a tiny loop's every part would be discounted
# until a five-part loop read as single-part material.
SUBSTANTIVE_MIN_NOTES = 20
SUBSTANTIVE_MIN_SHARE = 0.02
SUBSTANTIVE_ALWAYS = 100

# The only automatic rejection is a score that cannot be a multi-part
# arrangement whatever the genre: one substantive part, or two parts of a single
# keyboard instrument. Everything else is a judgement call and goes to review.
REDUCTION_MAX_PARTS = 2
KEYBOARD_PROGRAMS = frozenset(range(0, 8)) | {16, 17, 18, 19, 20}  # pianos and organs

# Thin, but not disqualifying: a minimal electronic track can be a complete
# arrangement with little more than a lead and percussion.
SHORT_SECONDS = 45.0
SPARSE_NOTES = 250

# Comfortably a full arrangement. Below this it is reviewed, not rejected.
FULL_MIN_PARTS = 4
FULL_MIN_SECONDS = 90.0
FULL_MIN_NOTES = 700

ACCEPT, REVIEW, REJECT = "accept", "review", "reject"


@dataclass
class Part:
    name: str
    channel: int
    notes: int
    median_pitch: int
    programs: tuple = ()

    @property
    def is_drums(self):
        return self.channel == DRUM_CHANNEL

    @property
    def is_bass(self):
        return not self.is_drums and self.median_pitch <= BASS_MEDIAN_PITCH


@dataclass
class Structure:
    path: Path
    raw_tracks: int = 0
    parts: list = field(default_factory=list)
    programs: set = field(default_factory=set)
    notes: int = 0
    seconds: float = 0.0
    verdict: str = REVIEW
    reasons: list = field(default_factory=list)

    @property
    def meaningful_parts(self):
        return len(self.parts)

    @property
    def substantive_parts(self):
        floor = min(
            SUBSTANTIVE_ALWAYS,
            max(SUBSTANTIVE_MIN_NOTES, self.notes * SUBSTANTIVE_MIN_SHARE),
        )
        return [p for p in self.parts if p.notes >= floor]

    @property
    def has_drums(self):
        return any(p.is_drums for p in self.parts)

    @property
    def has_bass(self):
        return any(p.is_bass for p in self.parts)

    @property
    def signals(self):
        """Quality signals, reported but never decisive on their own."""
        return {
            "raw_tracks": self.raw_tracks,
            "meaningful_parts": self.meaningful_parts,
            "substantive_parts": len(self.substantive_parts),
            "programs": len(self.programs),
            "notes": self.notes,
            "minutes": round(self.seconds / 60, 1),
            "drums": self.has_drums,
            "bass": self.has_bass,
        }

    def __str__(self):
        s = self.signals
        return (
            f"{self.verdict.upper():<6} {s['substantive_parts']:>2}/{s['meaningful_parts']:>2} parts "
            f"({s['raw_tracks']:>2} trk)  {s['programs']:>2} prog  {s['notes']:>6} notes  "
            f"{s['minutes']:>5.1f}m  {'drums' if s['drums'] else '     '} "
            f"{'bass' if s['bass'] else '    '}"
        )


def read_parts(path):
    """Group a MIDI file's tracks into instrument parts."""
    data = Path(path).read_bytes()
    _division, chunks = _tracks(data)
    groups, order = {}, []
    raw = 0
    for chunk in chunks:
        i, running = 0, None
        name, notes, pitches = None, 0, []
        programs, channels = set(), set()
        while i < len(chunk):
            _delta, i = _vlq(chunk, i)
            if i >= len(chunk):
                break
            status = chunk[i]
            if status == 0xFF:
                kind = chunk[i + 1]
                length, i = _vlq(chunk, i + 2)
                payload = chunk[i : i + length]
                i += length
                if kind == 0x03 and name is None:
                    name = payload.decode("utf-8", "replace").strip()
            elif status in (0xF0, 0xF7):
                length, i = _vlq(chunk, i + 1)
                i += length
            else:
                if status & 0x80:
                    running = status
                    i += 1
                elif running is None:
                    break
                else:
                    status = running
                kind = status & 0xF0
                if kind == 0x90 and chunk[i + 1] > 0:
                    notes += 1
                    channels.add(status & 0x0F)
                    pitches.append(chunk[i])
                elif kind == 0xC0:
                    programs.add(chunk[i])
                i += 1 if kind in (0xC0, 0xD0) else 2
        if not notes:
            continue
        raw += 1
        channel = min(channels) if channels else 0
        # Staves of one instrument share both a name and a channel.
        key = (name or f"channel {channel}", channel)
        if key not in groups:
            groups[key] = {"notes": 0, "pitches": [], "programs": set()}
            order.append(key)
        groups[key]["notes"] += notes
        groups[key]["pitches"].extend(pitches)
        groups[key]["programs"] |= programs

    parts = []
    for key in order:
        g = groups[key]
        pitches = sorted(g["pitches"])
        parts.append(
            Part(
                name=key[0],
                channel=key[1],
                notes=g["notes"],
                median_pitch=pitches[len(pitches) // 2],
                programs=tuple(sorted(g["programs"])),
            )
        )
    return raw, parts


def classify(path):
    """accept / review / reject, with the metrics and the reasons behind it."""
    path = Path(path)
    raw, parts = read_parts(path)
    result = Structure(
        path=path,
        raw_tracks=raw,
        parts=parts,
        programs={p for part in parts for p in part.programs},
        notes=sum(p.notes for p in parts),
        seconds=duration_seconds(path),
    )

    substantive = len(result.substantive_parts)
    pitched = [p for p in result.substantive_parts if not p.is_drums]

    # --- the only automatic rejections ---
    if substantive <= 1:
        result.reasons.append(f"single-part material ({substantive} substantive part)")
    elif (substantive <= REDUCTION_MAX_PARTS
          and len(result.programs) <= 1
          and result.programs <= KEYBOARD_PROGRAMS):
        result.reasons.append(f"{substantive}-part piano reduction")
    if result.reasons:
        result.verdict = REJECT
        return result

    # --- comfortably a full multi-part arrangement ---
    thin = []
    if substantive < FULL_MIN_PARTS:
        thin.append(f"only {substantive} substantive parts")
    if result.seconds < FULL_MIN_SECONDS:
        thin.append(f"short: {result.seconds / 60:.1f} min")
    if result.notes < FULL_MIN_NOTES:
        thin.append(f"sparse: {result.notes} notes")
    if not (result.has_drums or result.has_bass):
        thin.append("no percussion or bass register")
    if len(result.programs) <= 1:
        thin.append("single timbre across all parts")
    if len(pitched) <= 1:
        # A lead over percussion can be a complete minimal electronic track, so
        # this is a flag for a person, not a disqualification.
        thin.append("one pitched part over percussion")
    if result.seconds < SHORT_SECONDS:
        thin.append(f"very short: {result.seconds / 60:.1f} min")
    if result.notes < SPARSE_NOTES:
        thin.append(f"very sparse: {result.notes} notes")

    if thin:
        result.verdict = REVIEW
        result.reasons = thin
    else:
        result.verdict = ACCEPT
        result.reasons = [f"{substantive} substantive parts over {len(result.programs)} instruments"]
    return result
