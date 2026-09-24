# What we are collecting — handoff for every scraper and device

Read this before collecting anything. It is the single source of truth for
what counts. `SETUP.md` covers installing this pipeline; this file covers
**what to look for**, whatever tool or machine you use.

Last updated 2026-09-25.

## 1. The goal

**10 hours of qualifying MIDI**, deduplicated, for one buyer training a model on
"how one instrument influences the other" in electronic music.

| | files | hours |
|---|---|---|
| Qualifying (unique, `qualifies()`) | 118 | **10.34** |
| Delivered — every §2 rule, incl. 2.5–15 min | 110 | **10.06** |
| Still needed | 0 | **0** |

**Goal met on 2026-09-25.** The delivery is in `delivery/` (see `delivery/SPEC.md`).
Only qualifying hours count; total hours collected is a misleading number.

Known risk, accepted for this delivery: `qualifies()` counts a synth stem by
program *or* by track name, so some files have synth-named tracks on non-synth
programs. 21 of the 110 files (2.1 h) have under 10% of notes on synth
programs; the approved samples run 55–100%. Screen on
`synth_note_share` in `delivery/manifest.csv` if the buyer objects.

## 2. The rule — what qualifies

The buyer's words: *"need multi track, separate stems for bass, drums, synths
etc"* and *"EDM would have electronic music instrument labels"*.

A file qualifies when **all** of these hold:

1. **Standard MIDI File, type 1**, one instrument part per track.
2. **A drum stem** — a track on channel 10 (index 9), or named drum / kick /
   snare / hat / clap / perc / kit / cymbal.
3. **A bass stem** — GM program 32–39, or a track named "bass" (not bass
   clarinet, bass drum, bass trombone).
4. **At least one synth stem** — GM program in
   `80–103, 4, 5, 38, 39, 50, 51, 54, 62, 63, 118, 119`, or a track named
   synth / saw / square / sine / pad / lead / pluck / arp.
5. **Not predominantly orchestral** — acoustic stems (clarinet, oboe, flute,
   brass, strings, harp, timpani…) must not outnumber synth + bass stems.
6. A stem only counts if it has **≥ 20 notes**.
7. **Full arrangement, not a fragment.** Target 2.5–15 minutes. No medleys,
   mashups, megamixes or full-album transcriptions.
8. **Not a duplicate** of anything already held (SHA-256 of the bytes, and
   by score ID — see §5).

Extra acoustic parts are fine when 2–5 hold. Approved `sample3` has piano,
violin and harp next to its drum, bass and synth stems.

The executable rule is `musescore_midi/roles.py → qualifies()`. When this
document and the code disagree, **the code wins**. Check files with:

```bash
uv run python scripts/check_midi.py path/to/folder/
```

## 3. The reference: approved samples

`approved_samples/` holds the 4 files the buyer **approved on 2026-09-24**.
Match them. See `approved_samples/APPROVED.txt`.

| file | length | tracks | drum / bass / synth stems | synth notes |
|---|---|---|---|---|
| sample2 | 7:00 | 12 | 1 / 1 / 10 | 100% |
| sample3 | 13:05 | 13 | 1 / 1 / 6 | 55% |
| sample4 | 3:15 | 18 | 3 / 2 / 9 | 87% |
| sample5 | 4:09 | 17 | 1 / 1 / 8 | 80% |

Their track names read like *Effekt Synthesizer, Atmosphere Synthesizer,
Drums, FX, Melodies, Bass*.

## 4. What NOT to collect — the buyer rejected these

The first sample set was concert-band arrangements of real EDM songs: 35–36
tracks of piccolo, flute, oboe, clarinet, bassoon, 0% synth. The buyer said
the arrangements were good but **unusable**, because the instrument labels are
orchestral. A genuine EDM song arranged for wind band **fails**.

Also fails:

- Piano reductions, piano-only or single-timbre scores
- Lead sheets, melody-only transcriptions, loops, excerpts under ~2.5 min
- Files where "drums" is a single snare line with no bass or synth part

**Genre tags are not enough.** Many scores tagged *Electronic* on
musescore.com are orchestral arrangements. Judge by instruments, never by tag.

## 5. Finding candidates cheaply (before spending a download)

MuseScore allows **20 downloads per account per day**, so filter before
downloading. Listing and search cards print each score's instrument list for
free. Keep a candidate only when its listed instruments include **all three**:

- a drum part — *Drum group*, *Drumset*, *Percussion*, *Drum machine*
- a bass part — *Bass guitar*, *Electric bass*, *Synth bass*
- a synth part — *Synthesizer*, *Sampler*, *Electric piano*

and it has ≥ 4 parts, is 150–900 s long, and has no medley/mashup wording in
the title. This is `team/scout.py → roles()`.

Search terms that have produced results: future bass, progressive house,
electro house, dubstep, trance, synthwave, big room, melodic dubstep,
hardstyle, drum and bass, edm, techno, deep house, chiptune, eurodance.
Game-soundtrack arrangements (Sonic, Plants vs Zombies, Animusic) often list
full synth / bass / drum-group instrumentation and pass.

**Skip anything already held.** `handoff/held_score_ids.txt` lists all 232
musescore.com score IDs already downloaded (the number at the end of
`/scores/<id>`). `cloud_scores/<id>.mscz` holds the source files themselves.

## 6. What we already have

`handoff/inventory.csv` — every unique MIDI collected so far, one row each:
folder, whether it qualifies, seconds, stem counts per role, synth-note share,
SHA-256. 220 unique files, 76 qualify.

Folders on the collecting machine (`~/Documents/MuseScore4/Scores/`):
`./` accepted, `review/`, `rejected/`. These came from older genre gates, so
**the folder a file sits in does not tell you whether it qualifies.** Only
`qualifies()` does.

## 7. Output format for anything you collect

- One `.mid` per score, SMF type 1, exported from the full score (for
  MuseScore: `mscore -o out.mid in.mscz`), parts kept as separate tracks.
- Do not quantize, normalise velocity or tempo, merge tracks or rename
  instruments.
- Delivered files are renamed `track_NNNNNN.mid` and scrubbed of source
  metadata (`musescore_midi/scrub.py`) at packaging time, not at collection.
  Keep original names while collecting so duplicates stay traceable.

## 8. Coordinating several scrapers

- Before starting, pull, and take new IDs from `handoff/held_score_ids.txt`.
- After a session, append the IDs you downloaded and push, so no other
  device spends a download on the same score.
- Regenerate `handoff/inventory.csv` from the collecting machine and report
  **qualifying unique hours** only.
- On one machine: one browser, one MuseScore Studio. Scout and fetcher take
  turns through `team/browser.lease`. Never run two fetchers.

## 9. Account facts that cost us time

- Three accounts rotate: agaazsinghal, agaaz (tranzmitai), new001 (trazmit).
  20 downloads each per day.
- When an account's daily cap is spent, MuseScore **hides the "Edit on
  desktop" button** instead of showing an error.
- MuseScore Studio's login must match the browser's, or every score fails
  with `403: not owner`, which shows up as a handoff timeout.
- Long official transcriptions of commercial tracks are usually gated for
  every account. Openly editable community uploads are where the yield is.
