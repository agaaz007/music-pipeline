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


# Paths come from musescore_midi.config, which reads the environment at import,
# so the same code runs on macOS and Windows.
load_env()
from musescore_midi.config import CLOUD_SCORES as CLOUD  # noqa: E402
from musescore_midi.config import OUTPUT_DIR as OUT  # noqa: E402


def held():
    return {p.stem for p in CLOUD.glob("*.mscz")}


def qualifying_hours():
    """Hours meeting the approved-sample bar: separate drum, bass and synth stems.

    Counted across every folder, because the old genre gates sent some
    qualifying files to review/ and rejected/.
    """
    import hashlib

    from musescore_midi.roles import qualifies
    total, seen = 0.0, set()
    for d in (OUT, OUT / "review", OUT / "rejected"):
        for f in d.glob("*.mid"):
            try:
                ok, p = qualifies(f)
            except Exception:
                continue
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            if ok and digest not in seen:
                seen.add(digest)
                total += p["seconds"]
    return total / 3600.0


def next_batch(size):
    have = held()
    try:
        pool = json.loads(POOL.read_text())
    except Exception:
        return []
    from team.scout import roles
    # Older pool entries predate the three-role filter; score them the same way
    # so a download is never spent on a listing without drums, bass and synth.
    rows = [c for c in pool if c["id"] not in have
            and (c.get("roles") or roles(c.get("text") or "")) >= 3]
    rows.sort(key=lambda c: -(c.get("seconds") or 0))
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
        tail = (LOGS / "fetcher.out").read_text(errors="replace").splitlines()[-60:]
        blocked = sum(1 for line in tail if "never became visible" in line)
        timeouts = sum(1 for line in tail if "handoff timed out" in line)
        dry = 0 if gained else dry + 1
        log("fetcher", f"  landed {gained}" + (f" ({blocked} button-missing)" if blocked else "")
            + (f" ({timeouts} handoff timeouts)" if timeouts else ""))
        # Every click went through but nothing reached Studio: the browser's
        # "Open MuseScore Studio?" prompt or a login mismatch. Retrying on
        # this account only burns time.
        # A hidden "Edit on desktop" button on most of a batch is the account's
        # server-side cap; rotating now beats spending another batch proving it.
        if blocked >= max(3, len(picks) // 2) and gained <= 1:
            log("fetcher", "CAP REACHED - the handoff button is gone; rotate logins")
            return
        if not gained and timeouts >= 3:
            log("fetcher", "HANDOFF BLOCKED - clicks land but Studio receives nothing; "
                           "check the protocol prompt and that Studio's login matches")
            return
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
