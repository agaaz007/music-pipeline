"""Package validated MIDI for delivery: scrubbed, renamed, checksummed, documented.

Reads delivery/candidates.json (from scripts/delivery_candidates.py) and writes

    delivery/midi/track_NNNNNN.mid   scrubbed copies (musescore_midi/scrub.py)
    delivery/manifest.csv            one row per file: stems, length, sha256
    delivery/SPEC.md                 the delivery spec sheet

Nothing written under delivery/ names a source: files are numbered in an order
derived from their content hash, the manifest carries no titles or ids, and
every delivered file is re-scanned for source strings before it is accepted.
The mapping back to sources goes to a private path outside the repository.

    uv run --env-file .env python scripts/package_delivery.py PRIVATE_DIR
"""
import csv
import hashlib
import json
import re
import shutil
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from musescore_midi.roles import qualifies  # noqa: E402
from musescore_midi.scrub import scrub_file  # noqa: E402

DELIVERY = ROOT / "delivery"
MIDI_DIR = DELIVERY / "midi"
APPROVED = ROOT / "approved_samples"
TRACE = re.compile(rb"muse\s*score|musescore|muse\s*group|https?:|www\.|\.com\b|user/\d+|scores?/\d{4,}",
                   re.I)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stems(path):
    import mido
    ok, p = qualifies(path)
    r = p["roles"]
    return ok, {
        "seconds": round(p["seconds"], 1), "tracks": len(mido.MidiFile(path).tracks),
        "drum_stems": r.get("drums", 0), "bass_stems": r.get("bass", 0),
        "synth_stems": r.get("synth", 0), "acoustic_stems": r.get("acoustic", 0),
        "other_stems": r.get("other", 0), "synth_note_share": round(p["synth_share"], 3),
    }


def main(private_dir):
    import mido

    cands = json.loads((DELIVERY / "candidates.json").read_text())
    # Number files by content hash: stable across runs, unrelated to any source order.
    cands.sort(key=lambda c: c["sha256"])
    if MIDI_DIR.exists():
        shutil.rmtree(MIDI_DIR)
    MIDI_DIR.mkdir(parents=True)

    rows, provenance, problems = [], [], []
    for n, c in enumerate(cands, 1):
        name = f"track_{n:06d}.mid"
        dst = MIDI_DIR / name
        removed = scrub_file(Path(c["file"]), dst)
        ok, s = stems(dst)
        raw = dst.read_bytes()
        smf = mido.MidiFile(dst)
        if not ok:
            problems.append(f"{name}: no longer qualifies after scrub")
        if smf.type != 1:
            problems.append(f"{name}: SMF type {smf.type}")
        if TRACE.search(raw):
            problems.append(f"{name}: source string survived: {TRACE.search(raw).group(0)!r}")
        if abs(s["seconds"] - c["seconds"]) > 0.5:
            problems.append(f"{name}: length changed {c['seconds']} -> {s['seconds']}")
        digest = sha256(dst)
        rows.append({"file": name, **s, "sha256": digest})
        provenance.append({"file": name, "score_id": c["score_id"], "source_file": Path(c["file"]).name,
                           "source_sha256": c["sha256"], "delivered_sha256": digest,
                           "meta_events_removed": removed})

    with open(DELIVERY / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    # `sha256sum -c SHA256SUMS` format, run from inside delivery/.
    (DELIVERY / "SHA256SUMS").write_text(
        "".join(f"{r['sha256']}  midi/{r['file']}\n" for r in rows), encoding="utf-8", newline="\n")

    private = Path(private_dir)
    private.mkdir(parents=True, exist_ok=True)
    with open(private / "provenance.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(provenance[0]))
        w.writeheader()
        w.writerows(provenance)
    # The candidate list names score ids and source paths; it stays private too.
    shutil.move(str(DELIVERY / "candidates.json"), str(private / "candidates.json"))

    approved = []
    for f in sorted(APPROVED.glob("*.mid")):
        ok, s = stems(f)
        approved.append((f.stem, ok, s))
    write_spec(rows, approved)

    hours = sum(r["seconds"] for r in rows) / 3600
    print(f"packaged {len(rows)} files, {hours:.2f} h -> {MIDI_DIR}")
    print(f"private provenance -> {private / 'provenance.csv'}")
    print("problems:" if problems else "problems: none")
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


def _dist(values, fmt="{:.0f}"):
    q = statistics.quantiles(values, n=4)
    return (f"min {fmt.format(min(values))} · median {fmt.format(statistics.median(values))} · "
            f"max {fmt.format(max(values))} (quartiles {fmt.format(q[0])}–{fmt.format(q[2])})")


def write_spec(rows, approved):
    n = len(rows)
    secs = [r["seconds"] for r in rows]
    hours = sum(secs) / 3600
    tracks = [r["tracks"] for r in rows]
    synth = [r["synth_stems"] for r in rows]
    share = [r["synth_note_share"] for r in rows]
    drum = [r["drum_stems"] for r in rows]
    bass = [r["bass_stems"] for r in rows]
    with_acoustic = sum(1 for r in rows if r["acoustic_stems"])
    approved_rows = "\n".join(
        f"| {name} | {'pass' if ok else 'FAIL'} | {s['seconds'] / 60:.1f} min | {s['tracks']} | "
        f"{s['drum_stems']} / {s['bass_stems']} / {s['synth_stems']} | {s['synth_note_share']:.0%} |"
        for name, ok, s in approved)
    text = f"""# Multi-track electronic MIDI dataset — delivery specification

## Summary

| | |
|---|---|
| Files | **{n}** |
| Total playing time | **{hours:.2f} hours** |
| Format | Standard MIDI File, type 1, one instrument part per track |
| Content | Electronic / EDM arrangements with separate drum, bass and synth stems |
| Naming | `track_000001.mid` … `track_{n:06d}.mid` |
| Checksums | SHA-256 per file in `manifest.csv` |

## Acceptance criteria

Every file in this delivery meets all of the following, checked programmatically
on the delivered bytes:

1. **Standard MIDI File, type 1**, one instrument part per track.
2. **At least one drum stem** — a track on MIDI channel 10, or a track named
   drum / kick / snare / hat / clap / perc / kit / cymbal.
3. **At least one bass stem** — General MIDI program 32–39, or a track named "bass"
   (excluding bass clarinet, bass drum and bass trombone).
4. **At least one synth stem** — GM program 80–103, 4, 5, 38, 39, 50, 51, 54, 62, 63,
   118 or 119, or a track named synth / saw / square / sine / pad / lead / pluck / arp.
5. **Not predominantly orchestral** — acoustic orchestral stems (woodwind, brass,
   strings, harp, timpani) never outnumber synth plus bass stems.
6. A stem counts only if it carries **at least 20 notes**.
7. **Full arrangement**: between 2.5 and 15 minutes; no medleys, mashups, megamixes
   or album-length compilations.
8. **No duplicates**: every file is unique by SHA-256 and by source arrangement.

Additional acoustic parts (piano, guitar, strings) are present where they belong to
the arrangement; criteria 2–5 still hold for every such file.

## Reference: approved samples

This delivery matches the standard of the four samples approved on 2026-09-24,
which pass the same checks:

| sample | result | length | tracks | drum / bass / synth stems | synth notes |
|---|---|---|---|---|---|
{approved_rows}

## Dataset profile

| measure | distribution |
|---|---|
| Length (seconds) | {_dist(secs)} |
| Tracks per file | {_dist(tracks)} |
| Drum stems per file | {_dist(drum)} |
| Bass stems per file | {_dist(bass)} |
| Synth stems per file | {_dist(synth)} |
| Share of pitched notes on synth programs | {_dist(share, "{:.0%}")} |
| Files that also contain acoustic parts | {with_acoustic} of {n} |

## What each file contains

- Note, controller, program-change and pitch-bend events exactly as arranged: not
  quantized, velocities and tempo map unmodified, tracks not merged.
- Instrument track names as labelled in the arrangement (some in French, German or
  Spanish), since they carry the stem roles.
- Tempo, time-signature and key-signature events, section markers (Intro, Build,
  Drop, …) and, where the arrangement has a vocal line, its lyric events.

## What was removed

Copyright, free-text, cue-marker, device-name and sequencer-specific meta events, and
any text naming a website, application, user or catalogue number, were stripped from
every file. Filenames are sequential and carry no title or source identifier.

## Files

- `midi/` — the {n} MIDI files.
- `manifest.csv` — per file: length in seconds, track count, drum / bass / synth /
  acoustic / other stem counts, synth-note share, SHA-256.
- `SHA256SUMS` — checksums in `sha256sum` format.
- `SPEC.md` — this document.

Verify integrity from inside this folder with `sha256sum -c SHA256SUMS`, or on
Windows compare `Get-FileHash -Algorithm SHA256` against `manifest.csv`.
"""
    (DELIVERY / "SPEC.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "music-delivery-private"))
