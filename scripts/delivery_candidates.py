"""List every exported MIDI that meets TARGET.md, mapped back to its score id.

Checks each rule in TARGET.md section 2 separately so a failure says which
rule, deduplicates by SHA-256 and by musescore.com score id, and writes the
survivors to delivery/candidates.json for the source scrape and packaging.

    uv run --env-file .env python scripts/delivery_candidates.py
"""
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musescore_midi.config import OUTPUT_DIR, STATE_FILE  # noqa: E402
from musescore_midi.roles import profile, qualifies  # noqa: E402

COMP = re.compile(r"medley|mashup|full album|megamix|compilation", re.I)
MIN_S, MAX_S = 150, 900
OUT = Path(__file__).resolve().parent.parent / "delivery" / "candidates.json"


def main():
    import mido

    state = json.loads(Path(STATE_FILE).read_text())
    by_output = {Path(r["output"]).resolve(): sid for sid, r in state.items() if r.get("output")}

    files = [f for d in (OUTPUT_DIR, OUTPUT_DIR / "review", OUTPUT_DIR / "rejected")
             for f in sorted(d.glob("*.mid"))]
    fails, kept, seen_sha, seen_id = Counter(), [], {}, {}
    for f in files:
        sid = by_output.get(f.resolve())
        try:
            smf = mido.MidiFile(f)
            ok, p = qualifies(f)
        except Exception as exc:
            fails[f"unreadable ({type(exc).__name__})"] += 1
            continue
        reasons = []
        if smf.type != 1:
            reasons.append(f"SMF type {smf.type}, not 1")
        if not ok:
            reasons.append("fails qualifies() (drum/bass/synth stems, orchestral balance)")
        if not MIN_S <= p["seconds"] <= MAX_S:
            reasons.append("length outside 2.5-15 min")
        if COMP.search(f.stem):
            reasons.append("medley/mashup/compilation title")
        if sid is None:
            reasons.append("no score id in pipeline state")
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if not reasons and sha in seen_sha:
            reasons.append(f"duplicate bytes of {seen_sha[sha]}")
        if not reasons and sid in seen_id:
            reasons.append(f"duplicate score id of {seen_id[sid]}")
        if reasons:
            for r in reasons:
                fails[r] += 1
            continue
        seen_sha[sha], seen_id[sid] = f.name, f.name
        r = p["roles"]
        kept.append({
            "score_id": sid, "file": str(f), "folder": f.parent.name if f.parent != OUTPUT_DIR else ".",
            "sha256": sha, "seconds": round(p["seconds"], 3), "tracks": len(smf.tracks),
            "drum_stems": r.get("drums", 0), "bass_stems": r.get("bass", 0),
            "synth_stems": r.get("synth", 0), "acoustic_stems": r.get("acoustic", 0),
            "other_stems": r.get("other", 0), "synth_note_share": round(p["synth_share"], 3),
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(kept, indent=1))
    print(f"scanned {len(files)} MIDI files")
    print(f"deliverable: {len(kept)} files, {sum(k['seconds'] for k in kept) / 3600:.2f} h -> {OUT}")
    print("rejected (a file can fail several rules):")
    for reason, n in fails.most_common():
        print(f"  {n:4d}  {reason}")


if __name__ == "__main__":
    main()
