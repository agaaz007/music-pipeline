# Multi-track electronic MIDI dataset — delivery specification

## Summary

| | |
|---|---|
| Files | **110** |
| Total playing time | **10.06 hours** |
| Format | Standard MIDI File, type 1, one instrument part per track |
| Content | Electronic / EDM arrangements with separate drum, bass and synth stems |
| Naming | `track_000001.mid` … `track_000110.mid` |
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
| sample2 | pass | 7.0 min | 12 | 1 / 1 / 10 | 100% |
| sample3 | pass | 13.1 min | 13 | 1 / 1 / 6 | 55% |
| sample4 | pass | 3.3 min | 18 | 3 / 2 / 9 | 87% |
| sample5 | pass | 4.2 min | 17 | 1 / 1 / 8 | 80% |

## Dataset profile

| measure | distribution |
|---|---|
| Length (seconds) | min 150 · median 297 · max 825 (quartiles 213–422) |
| Tracks per file | min 5 · median 13 · max 48 (quartiles 10–18) |
| Drum stems per file | min 1 · median 2 · max 11 (quartiles 1–3) |
| Bass stems per file | min 1 · median 1 · max 6 (quartiles 1–2) |
| Synth stems per file | min 1 · median 4 · max 16 (quartiles 3–6) |
| Share of pitched notes on synth programs | min 0% · median 36% · max 100% (quartiles 16%–56%) |
| Files that also contain acoustic parts | 33 of 110 |

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

- `midi/` — the 110 MIDI files.
- `manifest.csv` — per file: length in seconds, track count, drum / bass / synth /
  acoustic / other stem counts, synth-note share, SHA-256.
- `SHA256SUMS` — checksums in `sha256sum` format.
- `SPEC.md` — this document.

Verify integrity from inside this folder with `sha256sum -c SHA256SUMS`, or on
Windows compare `Get-FileHash -Algorithm SHA256` against `manifest.csv`.
