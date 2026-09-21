"""Score a MIDI file against the buyer's stated requirement.

The requirement given was "separate stems for bass, drums, synths etc" with
instrument labels that name those roles. That is a question about which parts
exist, not about how many notes are synthesised: a file with a synth lead, a
synth bass and a drum kit qualifies even if a piano pad out-notes all three.
"""
import collections
import re

SYNTH_PROGRAMS = frozenset(range(80, 104)) | {4, 5, 38, 39, 50, 51, 54, 62, 63, 118, 119}
BASS_PROGRAMS = frozenset(range(32, 40))
# "Bass clarinet" and "bass drum" name other instruments, not a bass stem.
BASS_NAME = re.compile(r"\bbass\b(?!\s*(?:clarinet|drum|trombone))", re.I)
DRUM_NAME = re.compile(r"\b(drum|kick|snare|hat|clap|perc|kit|cymbal)\b", re.I)
SYNTH_NAME = re.compile(r"\b(synth\w*|saw|square|sine|pad|lead|pluck|arp)\b", re.I)
# Orchestral scoring, which names the wrong roles for electronic production.
ACOUSTIC_NAME = re.compile(
    r"\b(clarinet|oboe|bassoon|flute|piccolo|trombone|trumpet|saxophone|horn|"
    r"tuba|euphonium|cornet|violin|viola|cello|contrabass|harp|timpani)\b", re.I)
MIN_NOTES = 20


def profile(path):
    """Roles present in one file, plus the counts behind the verdict."""
    import mido
    m = mido.MidiFile(path)
    roles = collections.Counter()
    synth_notes = pitched_notes = 0
    for track in m.tracks:
        name = next((x.name for x in track if x.type == "track_name"), "") or ""
        program = next((x.program for x in track if x.type == "program_change"), None)
        channel = next((x.channel for x in track
                        if x.type in ("note_on", "program_change")), None)
        notes = sum(1 for x in track if x.type == "note_on" and x.velocity > 0)
        if notes < MIN_NOTES:
            continue
        if channel == 9 or DRUM_NAME.search(name):
            roles["drums"] += 1
            continue
        pitched_notes += notes
        if program in SYNTH_PROGRAMS:
            synth_notes += notes
        if program in BASS_PROGRAMS or BASS_NAME.search(name):
            roles["bass"] += 1
        elif program in SYNTH_PROGRAMS or SYNTH_NAME.search(name):
            roles["synth"] += 1
        elif ACOUSTIC_NAME.search(name):
            roles["acoustic"] += 1
        else:
            roles["other"] += 1
    return {
        "roles": dict(roles),
        "seconds": m.length,
        "synth_share": (synth_notes / pitched_notes) if pitched_notes else 0.0,
    }


def qualifies(path, *, require_all=True):
    """True when the file carries the stems the buyer asked for.

    `require_all` demands drums, bass and at least one synth part. Relaxed, two
    of the three suffice, which admits e.g. a synth-and-drums track with no
    separate bass stem.
    """
    p = profile(path)
    r = p["roles"]
    present = sum(1 for k in ("drums", "bass", "synth") if r.get(k))
    # An overwhelmingly orchestral file fails whatever else it contains.
    if r.get("acoustic", 0) > r.get("synth", 0) + r.get("bass", 0):
        return False, p
    return (present == 3 if require_all else present >= 2), p
