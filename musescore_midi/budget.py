"""Daily download budget.

MuseScore allows a limited number of score downloads per day per subscription
profile (20 by default here). Every "Edit" click in stage 1 spends one, and a
spent download cannot be recovered, so the budget is checked before the click
and recorded immediately after the score lands.

Converting and re-converting cached .mscz files costs nothing — the budget
covers downloads only.
"""

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import HOME

BUDGET_FILE = Path(os.environ.get("DOWNLOAD_BUDGET_FILE", HOME / ".musescore_download_budget.json"))
DAILY_LIMIT = int(os.environ.get("MUSESCORE_DAILY_LIMIT", "20"))


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _today():
    return date.today().isoformat()


def spent_today(path=BUDGET_FILE):
    """Downloads recorded for the local calendar day."""
    return len(_load(path).get(_today(), []))


def remaining(limit=DAILY_LIMIT, path=BUDGET_FILE):
    return max(0, limit - spent_today(path))


def resets_in(path=BUDGET_FILE):
    """Seconds until local midnight, when the quota rolls over."""
    now = datetime.now()
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
    return (midnight - now).total_seconds()


def record(score_id, note=None, path=BUDGET_FILE):
    """Log one spent download. Call this only after a score actually arrives."""
    data = _load(path)
    data.setdefault(_today(), []).append(
        {"score_id": str(score_id), "at": time.strftime("%H:%M:%S"), "note": note}
    )
    # Two weeks of history is enough to explain a surprising quota state.
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    data = {k: v for k, v in data.items() if k >= cutoff}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))
    return len(data[_today()])


def describe(limit=DAILY_LIMIT, path=BUDGET_FILE):
    left = remaining(limit, path)
    hours = resets_in(path) / 3600
    return f"{left}/{limit} downloads left today (resets in {hours:.1f}h)"


def reset(reason=None, path=BUDGET_FILE):
    """Clear today's count, for a fresh account or quota.

    The quota belongs to the MuseScore subscription, not to this machine, so
    signing in as a different account starts a new allowance that this local
    counter knows nothing about.
    """
    data = _load(path)
    had = len(data.pop(_today(), []))
    data[f"{_today()}-reset"] = [{"at": time.strftime("%H:%M:%S"),
                                  "cleared": had, "reason": reason}]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))
    return had
