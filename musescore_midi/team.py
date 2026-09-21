"""Shared state and a browser lease for the collection team.

Chrome, the browser-harness daemon and MuseScore Studio are each single
instances. Two processes driving them at once produce tab churn, IPC timeouts
and cross-talk between a crawl and a fetch, so browser work is serialised
through one lease while CPU-only work (conversion, QA, packaging) runs freely.
"""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "team"
ROOT.mkdir(exist_ok=True)
LEASE = ROOT / "browser.lease"
STATE = ROOT / "state.json"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

STALE_AFTER = 900.0          # a holder that dies should not block the team


def _now():
    return time.time()


def read_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"pool": 0, "downloaded": 0, "hours": 0.0, "notes": []}


def write_state(**changes):
    s = read_state()
    s.update(changes)
    s["updated"] = time.strftime("%H:%M:%S")
    STATE.write_text(json.dumps(s, indent=1))
    return s


def lease_holder():
    """(holder, age_seconds) or (None, None) when free."""
    try:
        raw = json.loads(LEASE.read_text())
    except Exception:
        return None, None
    age = _now() - raw.get("at", 0)
    if age > STALE_AFTER:
        LEASE.unlink(missing_ok=True)
        return None, None
    return raw.get("who"), age


@contextmanager
def browser_lease(who, wait=True, poll=5.0, timeout=3600.0):
    """Exclusive use of the browser. Blocks until free, or raises on timeout."""
    deadline = _now() + timeout
    while True:
        holder, _ = lease_holder()
        if holder is None:
            try:
                fd = os.open(LEASE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                pass                      # lost the race; look again
            else:
                with os.fdopen(fd, "w") as fh:
                    json.dump({"who": who, "at": _now()}, fh)
                break
        if not wait:
            raise RuntimeError(f"browser held by {holder!r}")
        if _now() > deadline:
            raise TimeoutError(f"waited {timeout:g}s for the browser; held by {holder!r}")
        time.sleep(poll)
    try:
        yield
    finally:
        LEASE.unlink(missing_ok=True)


def log(role, message):
    line = f"{time.strftime('%H:%M:%S')}  {message}"
    print(f"[{role}] {line}", flush=True)
    with open(LOGS / f"{role}.log", "a") as fh:
        fh.write(line + "\n")
