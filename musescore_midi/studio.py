"""Keep MuseScore Studio from piling up windows during a batch.

Every musescore:// handoff opens the score in Studio and leaves it open. A
twenty-score run therefore ends with twenty windows and a slow laptop, so each
score is closed once its .mscz is safely on disk.

Closing is a keystroke through System Events, which needs Accessibility
permission for the terminal -- the same grant `browser-harness mac-approve`
required. Without it this degrades to a no-op rather than failing the fetch:
leaving a window open is untidy, not harmful.
"""

import os
import signal
import subprocess
import time

# The menu-bar name has changed across releases ("MuseScore 4", "MuseScore
# Studio"), and the binary is "mscore", so the process is found at runtime
# rather than hardcoded.
_FIND_PROCESS = """
tell application "System Events"
    set names to name of every application process whose background only is false
    repeat with n in names
        if n contains "MuseScore" or n contains "mscore" then return n as text
    end repeat
end tell
return ""
"""

_COUNT_WINDOWS = 'tell application "System Events" to tell process "{p}" to count windows'

# Cmd+W closes the score. A freshly opened cloud score is unmodified, so this
# does not raise a save prompt; the dialog check below verifies that per run.
_CLOSE_ONE = """
tell application "System Events"
    tell process "{p}"
        set frontmost to true
        keystroke "w" using command down
    end tell
end tell
"""


def _osa(script, timeout=15):
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


def process_name():
    """Whatever MuseScore calls itself right now, or None if it is not running."""
    return (_osa(_FIND_PROCESS) or "").strip() or None


def window_count(process=None):
    process = process or process_name()
    if not process:
        return 0
    out = _osa(_COUNT_WINDOWS.format(p=process))
    try:
        return int(out)
    except (TypeError, ValueError):
        return 0


def close_score_window(process=None, keep=1):
    """Close score windows down to `keep` (the Home tab). Returns how many closed.

    Best effort: no Accessibility permission means no close, and the caller
    carries on. It never quits Studio, because the next handoff needs it running.
    """
    process = process or process_name()
    if not process:
        return 0
    closed = 0
    for _ in range(12):  # bounded, so a stuck window cannot spin here
        before = window_count(process)
        if before <= keep:
            break
        _osa(_CLOSE_ONE.format(p=process))
        after = window_count(process)
        if after >= before:  # nothing moved: no permission, or a modal is up
            break
        closed += 1
    return closed


# --- instances -------------------------------------------------------------

# Each musescore:// handoff launches a *separate* MuseScore process rather than
# reusing a running one, so a twenty-score batch ends with twenty app instances
# in the Dock. Closing a window only ever addresses one of them; the instances
# themselves have to go.
MSCORE_BIN_NAME = "MuseScore 4.app/Contents/MacOS/mscore"


def instances():
    """PIDs of running MuseScore instances, oldest first."""
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid,lstart,command"], capture_output=True, text=True, timeout=15
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    pids = []
    for line in out.splitlines():
        if MSCORE_BIN_NAME in line and "crashpad" not in line:
            try:
                pids.append(int(line.split(None, 1)[0]))
            except (ValueError, IndexError):
                continue
    return pids  # ps lists in pid order, which is launch order here


def quit_extras(keep=1, grace=3.0):
    """Ask surplus MuseScore instances to quit. Returns how many were asked.

    SIGTERM, never SIGKILL: a scored opened straight from the website has no
    unsaved edits, and a graceful quit still lets MuseScore save its own state.
    The oldest instance is the one kept, both because it is the one a person
    may be looking at and because keeping one warm avoids a cold app launch on
    the next handoff.
    """
    pids = instances()
    if len(pids) <= keep:
        return 0
    doomed = pids[keep:] if keep else pids
    for pid in doomed:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(grace)
    return len(doomed)
