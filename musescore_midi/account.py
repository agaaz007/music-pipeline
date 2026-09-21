"""Verify MuseScore Studio and the browser are signed into the same account.

The `musescore://` handoff is authorised against Studio's own login, not the
browser's. When they differ the server answers 403 "not owner", Studio writes
nothing, and the fetcher reports a handoff timeout -- a silent failure that
consumed an entire overnight run. Checking both before a batch turns that into
an upfront error.
"""
import re

from .config import CLOUD_SCORES

LOG_DIR = CLOUD_SCORES.parent / "logs"
_NOT_OWNER = re.compile(r"403: not owner")
_ACCOUNT_URL = re.compile(r"musescore\.com/user/(\d+)")


def studio_recent_403(limit=3):
    """How many '403: not owner' errors the newest Studio log carries."""
    logs = sorted(LOG_DIR.glob("MuseScore_*.log"), key=lambda p: p.stat().st_mtime)
    if not logs:
        return 0
    text = logs[-1].read_text(errors="replace")
    return len(_NOT_OWNER.findall(text))


def browser_account(agent_factory):
    """Profile handle musescore.com reports for the browser session."""
    from .fetch import wait_for_page_ready
    js = ("(() => {const b=[...document.querySelectorAll('button')]"
          ".find(e=>/profile menu/i.test(e.getAttribute('aria-label')||''));"
          "return b?b.getAttribute('aria-label'):null;})()")
    with agent_factory("https://musescore.com/") as agent:
        wait_for_page_ready(agent)
        label = agent.browser.evaluate(js) or ""
    m = re.search(r"profile menu for (\S+)", label, re.I)
    return m.group(1) if m else None


def preflight(agent_factory, *, tolerate=0):
    """(ok, message). False when Studio is rejecting scores as 'not owner'."""
    browser = browser_account(agent_factory)
    failures = studio_recent_403()
    if failures > tolerate:
        return False, (
            f"Studio is refusing scores ({failures} x '403: not owner' in its latest log). "
            f"The browser is signed in as {browser!r}; sign MuseScore Studio into the same "
            "account (Home -> Accounts -> Sign out, then Sign in) before fetching.")
    return True, f"browser={browser!r}, Studio reports no ownership errors"
