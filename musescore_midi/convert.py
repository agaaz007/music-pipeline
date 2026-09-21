"""Stage 2: turn cached .mscz scores into .mid files with the MuseScore CLI.

No GUI automation. `mscore -j job.json` converts a whole batch in one process,
so this stays fast and deterministic regardless of how the scores arrived.
"""

import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .config import CLOUD_SCORES, MSCORE, OUTPUT_DIR, STATE_FILE
from .decide import ACCEPT, REJECT, REVIEW, decide
from .genre import PLACEHOLDERS, _Client, load_overrides, load_tags
from .songs import key_for


def _is_compilation(tags, score_id):
    entry = (tags or {}).get(score_id)
    return bool(isinstance(entry, dict) and entry.get("compilation"))

_PLACEHOLDER_TITLES = {
    "", "untitled score", "untitled", "title", "temp",
    "score", "new score", "subtitle", "movement title",
} | PLACEHOLDERS  # shares the localised defaults with the genre stage

# MuseScore sometimes names the inner .mscx after an upload hash rather than the work.
_HASHY = re.compile(r"[0-9a-f]{16,}|^general \d+|^temp\b|^untitled\b", re.I)


def _usable(name):
    name = (name or "").strip()
    return bool(name) and name.lower() not in _PLACEHOLDER_TITLES and not _HASHY.search(name)


def _sanitize(name):
    name = re.sub(r"[^\w\s.-]", "", name).strip()
    name = re.sub(r"[\s_]+", " ", name)
    return name[:80] or "score"


def score_title(mscz):
    """Best available human name for a score.

    workTitle is the right answer when it is set, but cloud scores often carry an
    empty or placeholder one; the .mscx member name inside the zip survives that.
    """
    try:
        with zipfile.ZipFile(mscz) as z:
            members = [n for n in z.namelist() if n.endswith(".mscx")]
            if members:
                xml = z.read(members[0]).decode("utf-8", "replace")
                for tag in ("workTitle", "movementTitle", "subtitle"):
                    m = re.search(rf'<metaTag name="{tag}">(.*?)</metaTag>', xml)
                    if m and _usable(m.group(1)):
                        return _sanitize(m.group(1))
                stem = Path(members[0]).stem.replace("_", " ")
                if _usable(stem):
                    return _sanitize(stem)
    except (zipfile.BadZipFile, KeyError, OSError):
        pass
    return f"score-{Path(mscz).stem}"


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _next_output(title, taken):
    """Match the existing naming convention: '<Title>-NN.mid', NN auto-incrementing."""
    n = 1
    while True:
        candidate = OUTPUT_DIR / f"{title}-{n:02d}.mid"
        if candidate.name not in taken and not candidate.exists():
            taken.add(candidate.name)
            return candidate
        n += 1


def pending_scores(force=False):
    """Cached scores with no current .mid, newest first."""
    state = load_state()
    out = []
    for mscz in sorted(CLOUD_SCORES.glob("*.mscz"), key=lambda p: p.stat().st_mtime, reverse=True):
        record = state.get(mscz.stem)
        fresh = record and record.get("mtime") == mscz.stat().st_mtime
        if fresh and not force and Path(record.get("output", "")).exists():
            continue
        out.append(mscz)
    return out


def convert(scores=None, force=False, dry_run=False, classify=True, queries=None):
    """Convert scores to MIDI and route them by outcome.

    Returns {"accept": [...], "review": [...], "reject": [...]} of
    (mscz, mid, decision) when classifying, or a flat [(mscz, mid)] when not.
    `queries` maps a score id to the search phrase that fetched it, which is the
    strongest genre signal available.
    """
    if not MSCORE.exists():
        raise FileNotFoundError(f"MuseScore CLI not found at {MSCORE}; set MSCORE_BIN")

    scores = list(scores) if scores is not None else pending_scores(force=force)
    if not scores:
        return {ACCEPT: [], REVIEW: [], REJECT: [], "unconvertible": []} if classify else []

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    taken = set()
    jobs, planned = [], []
    for mscz in scores:
        dest = _next_output(score_title(mscz), taken)
        jobs.append({"in": str(mscz), "out": str(dest)})
        planned.append((mscz, dest))

    if dry_run:
        return planned

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(jobs, fh)
        job_path = fh.name
    try:
        proc = subprocess.run(
            [str(MSCORE), "-j", job_path], capture_output=True, text=True, timeout=600
        )
    finally:
        Path(job_path).unlink(missing_ok=True)

    converted = [(m, d) for m, d in planned if d.exists() and d.stat().st_size > 0]
    # A score mscore cannot render produces no file and no message (exit 40 on
    # one of these). Silently dropping it loses a score that cost a download,
    # so failures are reported alongside the routed results.
    failed = [m for m, d in planned if not (d.exists() and d.stat().st_size > 0)]
    # A non-zero exit here is per-score, not fatal: mscore returns 40 for a
    # score it cannot render and still converts the rest of the batch. Raising
    # would hide which scores actually failed, so the failures are reported.
    why = "mscore produced no output"
    if proc.returncode:
        detail = (proc.stderr or proc.stdout or "").strip()[-200:]
        why += f" (exit {proc.returncode}{': ' + detail if detail else ''})"

    if not classify:
        for mscz in failed:
            state[mscz.stem] = {"mtime": mscz.stat().st_mtime, "output": "",
                                "outcome": "unconvertible", "reasons": [why]}
        for mscz, dest in converted:
            state[mscz.stem] = {"mtime": mscz.stat().st_mtime, "output": str(dest),
                                "title": dest.stem}
        save_state(state)
        return converted

    queries = queries or {}
    client, overrides, tags = _Client(), load_overrides(), load_tags()
    routed = {ACCEPT: [], REVIEW: [], REJECT: [], "unconvertible": []}
    for mscz in failed:
        state[mscz.stem] = {"mtime": mscz.stat().st_mtime, "output": "",
                            "outcome": "unconvertible", "reasons": [why]}
        routed["unconvertible"].append((mscz, None, why))
    # Anything not accepted is filed for a human, never deleted.
    folders = {ACCEPT: OUTPUT_DIR, REVIEW: OUTPUT_DIR / "review", REJECT: OUTPUT_DIR / "rejected"}
    for mscz, dest in converted:
        decision = decide(dest, mscz, query=queries.get(mscz.stem),
                          client=client, overrides=overrides, tags=tags)
        # The genre stage resolves the canonical track, which is often the only
        # place a name exists: an uploader's .mscz frequently carries no title
        # at all, leaving the fallback "score-<id>".
        # Rename when the file carries no real name: either the score-id
        # fallback, or a placeholder the uploader left in ("Subtítulo").
        stem = re.sub(r"-\d+$", "", dest.stem).strip().lower()
        nameless = dest.stem.startswith("score-") or stem in _PLACEHOLDER_TITLES
        if nameless and decision.genre.title:
            better = _sanitize(decision.genre.title)
            if better and not better.startswith("score-"):
                renamed = _next_output(better, taken)
                dest = dest.replace(renamed)
        folder = folders[decision.outcome]
        if folder is not OUTPUT_DIR:
            folder.mkdir(exist_ok=True)
            dest = dest.replace(folder / dest.name)
        state[mscz.stem] = {
            "mtime": mscz.stat().st_mtime,
            "output": str(dest),
            "title": dest.stem,
            "outcome": decision.outcome,
            "metrics": {**decision.metrics,
                        "song_key": None if _is_compilation(tags, mscz.stem)
                                    else key_for(decision)},
            "reasons": decision.reasons,
        }
        routed[decision.outcome].append((mscz, dest, decision))
    save_state(state)
    return routed


def seed_state():
    """Mark every currently cached score as already handled.

    Use this once on an existing setup so the pipeline only converts scores
    fetched from here on, instead of re-exporting a back catalogue.
    """
    state = load_state()
    for mscz in CLOUD_SCORES.glob("*.mscz"):
        state.setdefault(
            mscz.stem,
            {"mtime": mscz.stat().st_mtime, "output": "", "title": score_title(mscz), "seeded": True},
        )
    save_state(state)
    return len(state)
