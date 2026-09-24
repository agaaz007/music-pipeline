"""Coordinator: keeps scout and fetcher alive and reports progress to the goal.

Progress is measured only in material that meets the buyer's bar -- files whose
separate drum, bass and synth stems (the approved-sample bar) -- because total hours collected proved a
misleading number: 12.96 h collected contained 3.0 h that qualified.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musescore_midi.team import LOGS, lease_holder, log, read_state

HERE = Path(__file__).resolve().parent
PY_ = sys.executable


def spawn(role, *args):
    out = open(LOGS / f"{role}.out", "a")
    return subprocess.Popen([PY_, str(HERE / f"{role}.py"), *map(str, args)],
                            stdout=out, stderr=subprocess.STDOUT,
                            cwd=str(HERE.parent))


def run(target_hours=10.0, poll=60.0):
    procs = {"scout": spawn("scout", 99), "fetcher": spawn("fetcher", target_hours)}
    log("coord", f"team started - target {target_hours} h of qualifying material")
    while True:
        s = read_state()
        holder, age = lease_holder()
        log("coord", f"{s.get('hours', 0):.2f}/{target_hours} h qualifying | "
                     f"pool {s.get('pool', 0)} | browser: {holder or 'free'}"
                     + (f" ({age:.0f}s)" if age else ""))
        if s.get("hours", 0) >= target_hours:
            log("coord", "TARGET MET - stopping team")
            for p in procs.values():
                p.terminate()
            return
        for role, p in list(procs.items()):
            if p.poll() is not None:
                if role == "fetcher":
                    log("coord", "fetcher exited (cap reached or target met); "
                                 "rotate the MuseScore login to continue")
                    procs["fetcher"] = None
                else:
                    log("coord", f"{role} exited - restarting")
                    procs[role] = spawn(role, 99)
        procs = {k: v for k, v in procs.items() if v is not None}
        if not procs:
            log("coord", "no workers left - stopping")
            return
        time.sleep(poll)


if __name__ == "__main__":
    run(target_hours=float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)
