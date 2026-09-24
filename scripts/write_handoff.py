"""Refresh handoff/ after a collecting session (TARGET.md section 8).

Appends newly downloaded score ids to handoff/held_score_ids.txt, so no other
device spends a download on them, and rewrites handoff/inventory.csv with one
row per unique exported MIDI.

    uv run --env-file .env python scripts/write_handoff.py
"""
import csv
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from musescore_midi.config import CLOUD_SCORES, OUTPUT_DIR  # noqa: E402
from musescore_midi.roles import qualifies  # noqa: E402

HANDOFF = ROOT / "handoff"


def main():
    ids_path = HANDOFF / "held_score_ids.txt"
    held = [line.strip() for line in ids_path.read_text().splitlines() if line.strip()]
    new = sorted({p.stem for p in CLOUD_SCORES.glob("*.mscz")} - set(held), key=int)
    ids_path.write_text("\n".join(held + new) + "\n")

    rows, seen = [], set()
    folders = {OUTPUT_DIR: "accepted", OUTPUT_DIR / "review": "review", OUTPUT_DIR / "rejected": "rejected"}
    for d, label in folders.items():
        for f in sorted(d.glob("*.mid")):
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            try:
                ok, p = qualifies(f)
            except Exception:
                continue
            r = p["roles"]
            rows.append({"file": f.name, "folder": label, "qualifies": "yes" if ok else "no",
                         "seconds": round(p["seconds"], 1), "drum_stems": r.get("drums", 0),
                         "bass_stems": r.get("bass", 0), "synth_stems": r.get("synth", 0),
                         "acoustic_stems": r.get("acoustic", 0),
                         "synth_note_share": round(p["synth_share"], 3), "sha256": digest})
    with open(HANDOFF / "inventory.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    q = [r for r in rows if r["qualifies"] == "yes"]
    print(f"held ids: {len(held)} + {len(new)} new = {len(held) + len(new)}")
    print(f"inventory: {len(rows)} unique MIDI, {len(q)} qualify, "
          f"{sum(r['seconds'] for r in q) / 3600:.2f} h qualifying")


if __name__ == "__main__":
    main()
