"""Final acceptance: genre AND structure, kept deliberately separate.

    accept   the underlying track is electronic/EDM *and* the score is a full,
             regular, multi-part arrangement
    reject   an obvious reduction or fragment, or a track that is positively
             not electronic
    review   everything else — unknown genre, or a structurally unusual score

Nothing borderline is rejected. A score only lands in reject/ when one of the
two stages is confident, never because both were merely unsure.
"""

from dataclasses import dataclass

from . import genre as genre_mod
from . import structure as structure_mod

ACCEPT, REVIEW, REJECT = "accept", "review", "reject"

# General MIDI programs that denote synthesised timbres.
SYNTH_PROGRAMS = frozenset(range(80, 104)) | {4, 5, 38, 39, 50, 51, 54, 62, 63, 118, 119}
# Above this share of notes on synth programs, the file is electronically
# instrumented as a matter of fact, whatever an artist lookup concludes.
SYNTH_DOMINANT = 0.5


def synth_share(midi_path):
    """Share of notes played on synth programs, percussion channel excluded."""
    import collections

    import mido
    try:
        m = mido.MidiFile(midi_path)
    except Exception:
        return 0.0
    program, notes = {}, collections.Counter()
    for track in m.tracks:
        for msg in track:
            if msg.type == "program_change":
                program[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                notes[msg.channel] += 1
    synth = total = 0
    for channel, count in notes.items():
        if channel == 9:
            continue
        p = program.get(channel)
        if p is None:
            continue
        total += count
        if p in SYNTH_PROGRAMS:
            synth += count
    return (synth / total) if total else 0.0


@dataclass
class Decision:
    outcome: str
    genre: object
    structure: object

    @property
    def reasons(self):
        out = []
        if self.genre.status != genre_mod.ELECTRONIC_EDM:
            out.append(f"genre {self.genre.status}: {self.genre.reason}")
        out.extend(self.structure.reasons)
        return out

    @property
    def metrics(self):
        return {
            "genre_status": self.genre.status,
            "genre_labels": self.genre.electronic or self.genre.other,
            "artist": self.genre.artist,
            "title": self.genre.title,
            "structure": self.structure.verdict,
            **self.structure.signals,
        }

    def __str__(self):
        return (
            f"{self.outcome.upper():<7} genre={self.genre.status:<15} "
            f"structure={self.structure.verdict:<7} {self.structure}"
        )


def decide(midi_path, mscz_path, *, query=None, client=None, overrides=None, tags=None):
    """Combine the two stages into one outcome for a converted score."""
    g = genre_mod.resolve(mscz_path, query=query, client=client, overrides=overrides, tags=tags)
    s = structure_mod.classify(midi_path)

    # The exported MIDI is direct evidence of instrumentation; an artist lookup
    # is indirect and fails entirely on original or obscure tracks. When the file
    # is synth-dominant, that outranks a NON_ELECTRONIC verdict about the artist.
    electronic_by_instrumentation = synth_share(midi_path) >= SYNTH_DOMINANT

    if s.verdict == structure_mod.REJECT:
        outcome = REJECT                      # obvious reduction or fragment
    elif electronic_by_instrumentation and s.verdict == structure_mod.ACCEPT:
        outcome = ACCEPT
    elif g.status == genre_mod.NON_ELECTRONIC:
        outcome = REJECT                      # confidently the wrong kind of music
    elif g.status == genre_mod.ELECTRONIC_EDM and s.verdict == structure_mod.ACCEPT:
        outcome = ACCEPT
    else:
        outcome = REVIEW                      # unknown genre, or unusual structure
    return Decision(outcome=outcome, genre=g, structure=s)
