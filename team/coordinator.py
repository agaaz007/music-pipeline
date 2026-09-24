"""Coordinator: keeps scout and fetcher alive and reports progress to the goal.

Progress is measured only in material that clears the benchmark the buyer
approved (roles.qualifies: drums, bass and synth parts all present, and not
predominantly orchestral). Total hours collected proved a misleading number:
12.96 h collected contained 3.0 h that qualified.

When the fetcher stops because an account's daily cap is spent, the
coordinator rotates MuseScore Studio and Chrome to the next account in
ACCOUNTS and restarts it. When every account is spent it waits for the local
midnight reset and starts over.
"""
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musescore_midi.team import LOGS, browser_lease, lease_holder, log, read_state

HERE = Path(__file__).resolve().parent
PY_ = sys.executable

def load_accounts():
    """Rotation order from team/accounts.json (untracked; see accounts.example.json).

    Each entry: the MuseScore Studio login saved by ms-account.ps1, the Chrome
    profile directory signed in to the same account, and the musescore.com user
    id both must report before a download is allowed.
    """
    path = HERE / "accounts.json"
    if not path.exists():
        raise SystemExit(f"missing {path}; copy accounts.example.json and fill it in")
    return [(a["login"], a["chrome_profile"], int(a["uid"])) for a in json.loads(path.read_text())]


ACCOUNTS = load_accounts()
SWITCH = Path(os.environ.get("LOCALAPPDATA", "")) / "MuseScore/MuseScore4/accounts/ms-account.ps1"
CHROME = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
SPENT = HERE / "accounts_spent.json"


def spawn(role, *args):
    out = open(LOGS / f"{role}.out", "a")
    return subprocess.Popen([PY_, str(HERE / f"{role}.py"), *map(str, args)],
                            stdout=out, stderr=subprocess.STDOUT,
                            cwd=str(HERE.parent))


def _spent_today():
    try:
        data = json.loads(SPENT.read_text())
    except Exception:
        data = {}
    return set(data.get(datetime.date.today().isoformat(), []))


def _mark_spent(email):
    today = datetime.date.today().isoformat()
    SPENT.write_text(json.dumps({today: sorted(_spent_today() | {email})}, indent=1))


def _current():
    marker = SWITCH.parent / "current.txt"
    return marker.read_text(encoding="utf-8-sig").strip() if marker.exists() else ""


def browser_uid():
    """musescore.com user id of the Chrome profile new tabs open in."""
    from jev_ultrafast.browser import Browser
    b = Browser("https://musescore.com/")
    try:
        time.sleep(4)
        return b.evaluate("(() => { try { return UGAPP.store.user.id || 0 } catch (e) { return 0 } })()")
    finally:
        b.close()


def switch_to(email, profile, uid):
    """Point Studio and Chrome at one account. False if they do not agree."""
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", str(SWITCH), "use", email],
                         capture_output=True, text=True, timeout=120)
    log("coord", f"  studio: {(out.stdout or out.stderr).strip()}")
    # Opening a window in the profile makes it the one new CDP tabs land in.
    with browser_lease("coord"):
        subprocess.Popen([str(CHROME), f"--profile-directory={profile}", "https://musescore.com/"])
        time.sleep(15)
        seen = browser_uid()
    if seen != uid:
        log("coord", f"  chrome {profile} is signed in as uid {seen}, expected {uid}; "
                     f"sign that profile in to musescore.com as {email}")
        return False
    from musescore_midi import budget
    budget.reset(reason=f"rotated to {email}")
    log("coord", f"  now on {email} (Studio + Chrome {profile}, uid {uid})")
    return True


def rotate():
    """Move to the next unspent account; wait for midnight when all are spent."""
    while True:
        spent = _spent_today()
        for email, profile, uid in ACCOUNTS:
            if email in spent:
                continue
            log("coord", f"rotating to {email}")
            if switch_to(email, profile, uid):
                return True
            _mark_spent(email)          # unusable today; do not retry it in a loop
        # MuseScore's quota is enforced server-side and need not reset at local
        # midnight, so do not wait for a date change: clear the marks and
        # re-probe hourly. A spent account costs no downloads to detect -- the
        # "Edit on desktop" button is simply absent -- so re-probing is free.
        log("coord", "all accounts spent; re-checking quotas in 1 h")
        time.sleep(3600)
        SPENT.unlink(missing_ok=True)


def _fetcher_reason():
    try:
        lines = (LOGS / "fetcher.log").read_text().splitlines()
    except OSError:
        return ""
    return lines[-1] if lines else ""


def convert_backlog():
    """Convert every cached score not yet exported, so held material counts.

    Free (no downloads). Runs before the fetcher starts, because the fetcher's
    own per-batch convert would otherwise race it over the same scores.
    """
    log("coord", "converting cached scores that have no MIDI yet (free)")
    subprocess.run([PY_, "-m", "musescore_midi.pipeline", "convert", "--no-classify"],
                   cwd=str(HERE.parent), stdout=open(LOGS / "convert.out", "a"),
                   stderr=subprocess.STDOUT, check=False)


def run(target_hours=10.0, poll=60.0):
    # The scout reads listings while the backlog converts; neither costs a download.
    procs = {"scout": spawn("scout", 99)}
    convert_backlog()
    # Always verify Studio and Chrome agree before the first click.
    rotate()
    procs["fetcher"] = spawn("fetcher", target_hours)
    log("coord", f"team started on {_current()} - target {target_hours} h of qualifying material")
    while True:
        s = read_state()
        holder, age = lease_holder()
        log("coord", f"{s.get('hours', 0):.2f}/{target_hours} h qualifying | "
                     f"pool {s.get('pool', 0)} | account {_current()} | browser: {holder or 'free'}"
                     + (f" ({age:.0f}s)" if age else ""))
        if s.get("hours", 0) >= target_hours:
            log("coord", "TARGET MET - stopping team")
            for p in procs.values():
                p.terminate()
            return
        for role, p in list(procs.items()):
            if p.poll() is None:
                continue
            if role == "scout":
                log("coord", "scout exited - restarting")
                procs["scout"] = spawn("scout", 99)
                continue
            reason = _fetcher_reason()
            if "TARGET MET" in reason:
                continue
            if "CAP REACHED" in reason or "HANDOFF BLOCKED" in reason:
                # A blocked handoff will not clear by itself today; move on.
                _mark_spent(_current())
                rotate()
            else:
                # Nothing landed for a reason other than the cap (pool drained,
                # handoff blocked). Give the scout time before trying again.
                log("coord", f"fetcher stopped without the cap ({reason.strip()}); retrying in 10 min")
                time.sleep(600)
            procs["fetcher"] = spawn("fetcher", target_hours)
        time.sleep(poll)


if __name__ == "__main__":
    run(target_hours=float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)
