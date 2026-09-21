# Running this pipeline on another Mac

Copying files gets you the code and its memory of what has already been
collected. It does not get you the browser sessions, the MuseScore Studio
login, or the macOS permissions — those are bound to the machine and must be
re-established by hand. Sections 3–6 are that part, and they are the ones that
actually take time.

## 1. What is in this bundle

```
repo/     the pipeline: musescore_midi/, team/, tests/, pyproject.toml, .env
state/    what has been downloaded, judged, and spent, from the home directory
```

`repo/.env` contains the Jev API key. Treat this bundle as a secret: move it
over AirDrop, a USB disk or an encrypted archive, not email or Slack.

`cloud_scores/` holds the 232 scores already downloaded. At 20 downloads per
account per day that cache represents roughly twelve days of quota, so it
travels with the repo; the pipeline skips any score already present there.
Restore it before collecting:

```bash
cp -R cloud_scores/* ~/Library/Application\ Support/MuseScore/MuseScore4/cloud_scores/
```

Not included: `~/Documents/MuseScore4/Scores/` (28 MB of exported MIDI). It is
the pipeline's output and is re-derivable for free from the cache above with
`uv run python -m musescore_midi.pipeline convert`.

## 2. Install on the new Mac

```bash
mkdir -p ~/code && cp -R repo ~/code/jev-ultrafast && cd ~/code/jev-ultrafast
uv sync                      # installs deps, including mido
uv run pytest tests/ -q      # expect 85 passed
cp ../state/.musescore_*.json ~/    # restore collection memory
```

Also required:

- **MuseScore 4** installed at `/Applications/MuseScore 4.app`. Override with
  `MSCORE_BIN` if it lives elsewhere.
- **Google Chrome** as the browser the harness drives.

## 3. Sign in — both halves, or nothing works

The handoff is authorised by **MuseScore Studio's own login**, which is separate
from the browser's. When they differ every score fails with `403: not owner`
and the fetcher reports a handoff timeout, with no other symptom. This cost a
full overnight run once.

1. Chrome: sign in to musescore.com with the target account.
2. MuseScore Studio: **Home → account → Sign out → Sign in**, same account.
3. Verify before collecting:

```bash
uv run python -c "
from musescore_midi.account import studio_recent_403
print('ownership errors in Studio log:', studio_recent_403())"
```

## 4. Approve Chrome remote debugging

```bash
uv run browser-harness mac-approve     # click Allow when Chrome asks
uv run browser-harness doctor          # daemon alive, connections >= 1
```

If the daemon reports dead later, the usual cause is tab accumulation. Close
the surplus MuseScore tabs and it recovers:

```bash
uv run browser-harness <<'PY'
tabs = [t for t in list_tabs() if "musescore.com" in (t.get("url") or "")]
for t in tabs[1:]:
    close_tab(t["targetId"])
print("remaining:", len(list_tabs()))
PY
```

## 5. Screen Recording (only for automatic account switching)

Switching MuseScore Studio between accounts is a GUI action. Without this
permission you can still run everything — you just sign Studio in by hand when
an account is spent.

System Settings → Privacy & Security → Screen & System Audio Recording → enable
the client, then **quit and relaunch it**. macOS only applies the grant on
restart; toggling it while running has no effect.

## 6. Run it

```bash
# one batch against a candidate list
uv run --env-file .env python -m musescore_midi.pipeline fetch \
  --tracks electronic_candidates.txt --limit 20 --allow-ambiguous

# or the team: scout finds candidates, fetcher downloads, coordinator reports
uv run --env-file .env python team/coordinator.py 10
```

Progress, measured against the buyer's requirement rather than raw hours:

```bash
uv run python -c "
from pathlib import Path
from musescore_midi.roles import qualifies
root = Path.home()/'Documents/MuseScore4/Scores'
n = h = 0
for f in root.glob('*.mid'):
    ok, p = qualifies(f)
    if ok:
        n += 1; h += p['seconds']
print(f'{n} files, {h/3600:.2f} h with drums + bass + synth stems')"
```

## 7. Things that will bite you

- **One browser, one Studio.** Scout and fetcher share both, so they take turns
  through `team/browser.lease`. Do not run two fetchers.
- **The daily cap hides the button.** When an account is spent, "Edit on
  desktop" disappears from score pages rather than erroring. The fetcher stops
  after two empty batches and says to rotate.
- **`--limit` is the daily cap, not the batch size.** `--limit 3` with 15
  already spent means zero remaining, and the run refuses.
- **Never pipe a run through `tail`.** It buffers, so the log stays empty while
  work happens and per-track failure reasons are lost. Redirect to a file.
- **Convert after every batch.** Large scores can land after the handoff wait
  expires; `pipeline convert` picks up anything left in `cloud_scores`.
