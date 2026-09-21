"""Strip provenance from exported MIDI so the dataset carries only music.

The MuseScore CLI does not embed source URLs, but three things still identify
where a file came from: meta events an editor may add (copyright, free text,
sequencer-specific blobs), catalog or library codes typed into the score, and
filenames built from the numeric score id. Instrument track names are kept --
they are what makes the file multi-track rather than a provenance leak.
"""
import re
import subprocess
from pathlib import Path

# Meta event types that carry no musical information and routinely hold
# application, publisher or source strings.
DROP_TYPES = frozenset({
    "copyright", "text", "cue_marker", "device_name", "sequencer_specific",
})

# Strings that identify a source rather than describe the music.
SOURCE = re.compile(
    r"musescore|muse\s*group|https?:|www\.|\.com\b|\.org\b|user/\d+|scores?/\d{4,}"
    r"|sheet\s*music|downloaded|transcrib\w*\s+by|arranged\s+by\s+\S+\s*\d",
    re.I,
)
# Library or catalog codes: letters glued to digits with a serial number.
CATALOG = re.compile(r"\b[A-Z]{2,}[\d.]{2,}\s*[-–]\s*\d{4,}\b")

# Filenames the converter derived from a numeric score id.
SCORE_ID_NAME = re.compile(r"^score-(\d{4,})(-\d+)?$", re.I)


def _identifying(value):
    return bool(value) and bool(SOURCE.search(value) or CATALOG.search(value))


def clean_messages(track):
    """Yield the track's messages without provenance-bearing metadata."""
    for msg in track:
        if not msg.is_meta:
            yield msg
            continue
        if msg.type in DROP_TYPES:
            continue
        value = getattr(msg, "text", None) or getattr(msg, "name", None)
        if _identifying(value):
            continue
        yield msg


def neutral_name(stem):
    """A filename that does not encode the numeric score id."""
    m = SCORE_ID_NAME.match(stem)
    return f"untitled-{m.group(1)[-4:]}" if m else stem


def strip_xattrs(path):
    """Remove macOS extended attributes, which can record download origin."""
    subprocess.run(["xattr", "-c", str(path)], capture_output=True)


def scrub_file(src, dst):
    """Write a provenance-free copy of one MIDI file. Returns events removed."""
    import mido

    midi = mido.MidiFile(src)
    out = mido.MidiFile(ticks_per_beat=midi.ticks_per_beat, type=midi.type)
    removed = 0
    for track in midi.tracks:
        kept = mido.MidiTrack()
        pending = 0
        for msg in track:
            if msg.is_meta and (msg.type in DROP_TYPES
                                or _identifying(getattr(msg, "text", None)
                                                or getattr(msg, "name", None))):
                pending += msg.time          # keep the timeline intact
                removed += 1
                continue
            kept.append(msg.copy(time=msg.time + pending))
            pending = 0
        out.tracks.append(kept)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst)
    strip_xattrs(dst)
    return removed


def scrub_dir(src_dir, dst_dir):
    """Scrub every .mid in src_dir into dst_dir under neutral filenames."""
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    rows = []
    for path in sorted(src_dir.glob("*.mid")):
        target = dst_dir / f"{neutral_name(path.stem)}.mid"
        try:
            removed = scrub_file(path, target)
        except Exception as exc:
            rows.append((path.name, None, f"{type(exc).__name__}: {exc}"))
            continue
        rows.append((path.name, target.name, removed))
    return rows
