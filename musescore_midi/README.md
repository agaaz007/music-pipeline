# musescore-midi

Fetch electronic/EDM scores from musescore.com and export them as multitrack MIDI.

## Pipeline

```
 screen (free)          fetch (costs 1 download)        convert + route (free)
 ─────────────          ────────────────────────        ──────────────────────
 genre of the track  →  musescore.com search URL     →  mscore -j  →  .mid
 from the search        → click the score               → genre + structure
 phrase, over music     → click "Edit"                  → accept / review / reject
 metadata APIs          → musescore:// opens Studio
                        → Studio writes cloud_scores/<id>.mscz
```

**The browser agent never touches the MuseScore desktop app.** Clicking "Edit" makes
MuseScore Studio download the score into
`~/Library/Application Support/MuseScore/MuseScore4/cloud_scores/<score_id>.mscz`.
From there `mscore -j` exports MIDI headlessly, ~0.7 s per score. Verified on this
machine: of 17 scores also exported by hand through the Studio GUI, 16 came out
**byte-identical** and the 17th differed only in embedded metadata.

Stage 1 navigates straight to `musescore.com/sheetmusic?text=...`, so the agent only
ever needs `CLICK` — no `TYPE_TEXT`, and no `TEXT_MODEL_API_KEY` for the normal flow.

## The download budget

MuseScore allows a limited number of downloads per day per subscription profile —
**20** by default here. Every "Edit" click spends one, and a spent download cannot be
recovered. So:

- the quota is checked **before** the click, and recorded only once a score lands;
- the genre screen runs **before** any download, over music metadata APIs, so a track
  that is positively not electronic never costs anything;
- `fetch` stops cleanly when the quota runs out and says what it did not attempt;
- converting and re-converting cached scores is **free** and never touches the quota.

```bash
uv run python -m musescore_midi.pipeline budget          # what's left today
MUSESCORE_DAILY_LIMIT=10 uv run ... pipeline fetch ...   # or --limit 10
```

## Genre and structure are separate, on purpose

**Genre is a fact about the song, not about the arrangement.** Most qualifying scores
here are *orchestral covers* of EDM tracks — PARALLAX, Avril and blast are flutes,
oboes and strings. Instrumentation therefore cannot decide genre, and `genre.py` never
looks at MIDI programs. It reads title and artist, normalizes them (stripping
`(Piano Cover)`, `feat. …`, trailing instrument words, MuseScore's `Composer /
arranger` placeholders) and asks MusicBrainz what the track is.

Order of authority:

1. **an explicit override** — `pipeline genre --set <score_id> ELECTRONIC_EDM`
2. **musescore.com page tags** — every score page carries curated tags, and they name
   the genre outright. A brass-band cover of Avicii's "Wake Me Up" is tagged
   `Electronic, Folk, Brass Band (New Orleans), Trombone, Tuba` — acoustic
   instrumentation, electronic song, and the tag says so. Instrument and difficulty
   tags are ignored. `fetch` reads these off the score page before clicking Edit, so
   they cost no download, and stores them in `~/.musescore_score_tags.json`.
3. **your selection** — the search phrase names the track: `"Kygo Firestone piano"`
   → Kygo → `edm, deep house`
4. **score metadata** — `workTitle` / `composer` from the `.mscz`

Splitting a search phrase is done by longest exact artist match, not by guessing.
`"Martin Garrix Animals"` split on the first word gives the artist "Martin", who makes
country pop; free-texting the whole phrase matches a classical cover of "Levels".
Trying prefixes longest-first and keeping the one that names a real artist exactly
gets `Martin Garrix` / `Animals`.

A title with no artist is **not** allowed to decide. `Avril` resolves confidently to
Jérôme Minière's chanson and `end of spring` to a K-pop single — both wrong. Those
candidates are recorded as evidence and the verdict stays `AMBIGUOUS`.

Status is `ELECTRONIC_EDM`, `NON_ELECTRONIC`, or `AMBIGUOUS`. Accepted labels include
EDM, house and all its variants, dubstep, drum and bass, trance, techno, future bass,
hardstyle, and broadly electronic tags.

**Structure** (`structure.py`) asks only whether the score is a full, regular,
multi-part arrangement. Its one subtlety is `meaningful_parts`: MuseScore exports one
MIDI track per *staff*, so a single piano arrives as two tracks sharing a name and a
channel. Parts are grouped by `(track name, channel)`, which merges an instrument's
staves while keeping genuinely separate voices apart — two violin desks on different
channels stay two parts.

| metric | role |
| --- | --- |
| `raw_tracks` | MIDI tracks carrying notes |
| `meaningful_parts` | instrument voices after merging staves |
| `substantive_parts` | parts big enough to be a stem, not a one-off fill |
| programs, notes, minutes, drums, bass | **quality signals, not gates** |

**Only two things auto-reject**, because only two are safe without a human:
single-part material, and a two-part reduction on one keyboard instrument. Everything
else — short, sparse, a lead over percussion, a single timbre — is a *signal* that
sends the score to **review**. A minimal electronic track can genuinely be melody plus
drums, and a legitimate 5–6 part arrangement passes.

The substantive-part floor is a share of the score clamped at both ends. Without the
ceiling, a dense 20k-note track's busiest stems raise the bar until its real 116-note
lead line looks like noise; without the small floor, a five-part loop reads as
single-part material and gets wrongly rejected.

## Canonical songs

Two arrangements of Calvin Harris's "Feels" are both legitimate files and one
composition. Every score carries a `song_key` built from its canonical artist and
title, with renditions folded in — `Feels`, `Feels (feat. Pharrell Williams)`,
`Feels - Piano Cover` and `FEELS (Extended Mix)` all collapse to `calvinharris::feels`.

Both sides are required. A title alone cannot identify a song: several unrelated tracks
are called "Alone", so a score with no known artist simply has no key.

An album transcription is **not** one song. MuseScore links "PARALLAX Full Album
Transcription" to a single track from the album, which would make it collide with that
track; compilations are detected from the page title and get no key at all.

```bash
uv run python -m musescore_midi.pipeline duplicates
```

Nothing is deleted — this reports identity, and a person decides.

## Not paying twice for the same score

Downloads are scarce, so `fetch` declines to spend one when it can tell in advance:

- a score URL already present in `cloud_scores/` is skipped before anything opens;
- for a search, the score page is free, so the score id **and** the canonical song are
  both checked there — before the Edit click;
- a song already accepted into the dataset is skipped.

`--force` overrides all three.

## Final acceptance

| outcome | when |
| --- | --- |
| **accept** | genre is `ELECTRONIC_EDM` **and** structure is a full multi-part arrangement |
| **reject** | an obvious reduction or fragment, **or** a confidently non-electronic track |
| **review** | everything else — unknown genre, or an unusual structure |

Nothing borderline is rejected, and nothing is ever deleted. Files land in
`<output>/`, `<output>/review/`, or `<output>/rejected/`, with metrics and reasons
recorded in the state file.

On the 22 scores cached here: 7 accept, 13 review (almost all because the uploader
left no artist in the `.mscz`), 2 reject — the two genuine piano reductions.
`What's Love Got to Do with It` is instructive: its composer is "Kygo feat. Tina
Turner", so it is genuinely electronic, and it is rejected purely for being a
one-part piano reduction.

## Setup

```bash
uv sync
cp .env.example .env     # add TYPESAFE_API_KEY
```

Chrome needs remote debugging once, and MuseScore Studio must be signed in to the
same account as the website:

1. `chrome://inspect/#remote-debugging` → tick **"Allow remote debugging for this
   browser instance"**.
2. Grant Accessibility to your terminal in **System Settings → Privacy & Security →
   Accessibility**, then `uv run browser-harness mac-approve`. (Without it, click
   **Allow** on Chrome's dialog by hand the first time.)
3. `uv run browser-harness --doctor` — every line should read `ok`.

The first time a score opens, Chrome asks whether to open MuseScore Studio. Tick
**"Always allow musescore.com to open links of this type"** or every later fetch
stalls at that dialog.

## Use

```bash
uv run python -m musescore_midi.pipeline status
uv run python -m musescore_midi.pipeline budget

# genre, without spending a download
uv run python -m musescore_midi.pipeline genre --query "Kygo Firestone"
uv run python -m musescore_midi.pipeline genre --tags 7010373 Electronic Dance
uv run python -m musescore_midi.pipeline genre --set 7010373 ELECTRONIC_EDM --note "Alan Walker - Fade"

# fetch (screens genre first, stops at the daily cap)
uv run --env-file .env python -m musescore_midi.pipeline fetch --tracks tracks.txt
uv run --env-file .env python -m musescore_midi.pipeline fetch \
    --track "Martin Garrix Animals" --limit 5 --allow-ambiguous

# convert and route what is already cached (free)
uv run python -m musescore_midi.pipeline convert          # --dry-run, --force, --no-classify

# audits
uv run python -m musescore_midi.pipeline structure
uv run python -m musescore_midi.pipeline duration
```

## Configuration

| variable | default |
| --- | --- |
| `MUSESCORE_CLOUD_SCORES` | `~/Library/Application Support/MuseScore/MuseScore4/cloud_scores` |
| `MIDI_OUTPUT_DIR` | `~/Documents/MuseScore4/Scores` |
| `MSCORE_BIN` | `/Applications/MuseScore 4.app/Contents/MacOS/mscore` |
| `PIPELINE_STATE` | `~/.musescore_midi_state.json` |
| `MUSESCORE_DAILY_LIMIT` | `20` |
| `DOWNLOAD_BUDGET_FILE` | `~/.musescore_download_budget.json` |
| genre cache / tags / overrides | `~/.musescore_genre_cache.json`, `~/.musescore_score_tags.json`, `~/.musescore_genre_overrides.json` |
| `HANDOFF_TIMEOUT_S` | `90` |

## Limits

- Stage 1 depends on musescore.com's markup; the agent reads live element names, so a
  layout change degrades into "no score page reached" rather than a wrong click.
- Chrome's protocol dialog is native. The agent cannot click it — hence "Always allow".
- `fetch` is sequential: MuseScore Studio handles one `musescore://` open at a time.
- MusicBrainz is rate-limited to ~1 request/second and cached in
  `~/.musescore_genre_cache.json`. Failures are retried and **never cached** — caching
  one 503 would poison that lookup for every future run. Its coverage of individual EDM
  singles is patchy, which is why artist-level labels do most of the work.
- musescore.com is behind Cloudflare and will serve an interstitial under repeated
  automated loads. Its title parses as an ordinary score name, so `read_page` detects
  it and leaves stored data untouched rather than overwriting it with
  "Just a moment...". `enrich` pauses 3 s between pages; if it reports blocked, wait
  and re-run — it is idempotent.
- musescore.com pages are behind Cloudflare and cannot be fetched outside the browser
  session, so page tags are only captured for scores fetched through stage 1. For the
  22 already cached here, record them by hand with `genre --tags <score_id> <tags...>`
  or assert the genre with `genre --set`.
