"""Fetcher: turns pooled candidates into accepted MIDI, longest first.

Holds the browser lease only while downloading. Stops after two consecutive
batches that land nothing, which is the signature of the daily cap hiding the
"Edit on desktop" button, so a spent account is not ground against for hours.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musescore_midi.team import LOGS, browser_lease, log, write_state

POOL = Path(__file__).resolve().parent / "pool.json"
BATCH = Path(__file__).resolve().parent / "batch.txt"
CLOUD = Path.home() / "Library/Application Support/MuseScore/MuseScore4/cloud_scores"
OUT = Path.home() / "Documents/MuseScore4/Scores"
SYNTH = set(range(80, 104)) | {4, 5, 38, 39, 50, 51, 54, 62, 63, 118, 119}


def load_env():
    """Read .env so a worker started outside `uv run --env-file` has the keys."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def held():
    return {p.stem for p in CLOUD.glob("*.mscz")}


def qualifying_hours():
    """Hours of material that actually meets the synth-dominant bar."""
    import collections

    import mido
    total = 0.0
    for f in OUT.glob("*.mid"):
        try:
            m = mido.MidiFile(f)
        except Exception:
            continue
        cp, nb = {}, collections.Counter()
        for t in m.tracks:
            for msg in t:
                if msg.type == "program_change":
                    cp[msg.channel] = msg.program
                elif msg.type == "note_on" and msg.velocity > 0:
                    nb[msg.channel] += 1
        sn = tot = 0
        for ch, c in nb.items():
            if ch == 9:
                continue
            p = cp.get(ch)
            if p is None:
                continue
            tot += c
            if p in SYNTH:
                sn += c
        if tot and sn / tot >= 0.5:
            total += m.length
    return total / 3600.0


def next_batch(size):
    have = held()
    try:
        pool = json.loads(POOL.read_text())
    except Exception:
        return []
    rows = [c for c in pool if c["id"] not in have]
    rows.sort(key=lambda c: (-(c.get("ratio") or 0), -(c.get("seconds") or 0)))
    return rows[:size]


def run(target_hours=10.0, batch=8):
    load_env()
    dry = 0
    while True:
        hours = qualifying_hours()
        write_state(hours=round(hours, 2), downloaded=len(list(OUT.glob("*.mid"))))
        if hours >= target_hours:
            log("fetcher", f"TARGET MET: {hours:.2f} h of qualifying material")
            return
        picks = next_batch(batch)
        if not picks:
            log("fetcher", "pool empty - waiting for scout")
            time.sleep(60)
            continue
        BATCH.write_text("\n".join(c["url"] for c in picks) + "\n")
        before = len(held())
        log("fetcher", f"{hours:.2f}/{target_hours} h - fetching {len(picks)}")
        with browser_lease("fetcher"):
            subprocess.run([sys.executable, "-m", "musescore_midi.pipeline", "fetch",
                            "--tracks", str(BATCH), "--limit", "20", "--no-convert",
                            "--allow-ambiguous", "--no-screen"],
                           cwd=str(Path(__file__).resolve().parent.parent), check=False)
        subprocess.run([sys.executable, "-m", "musescore_midi.pipeline", "convert"],
                       cwd=str(Path(__file__).resolve().parent.parent), check=False)
        gained = len(held()) - before
        blocked = sum(1 for line in (LOGS / "fetcher.out").read_text().splitlines()[-60:]
                      if "never became visible" in line)
        dry = 0 if gained else dry + 1
        log("fetcher", f"  landed {gained}" + (f" ({blocked} button-missing)" if blocked else ""))
        if dry >= 2:
            reason = ("CAP REACHED - the handoff button is gone; rotate logins"
                      if blocked else
                      "NO PROGRESS - candidates are being skipped before download, "
                      "not blocked by quota; check the skip reasons")
            log("fetcher", reason)
            return
        time.sleep(5)


if __name__ == "__main__":
    run(target_hours=float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)
