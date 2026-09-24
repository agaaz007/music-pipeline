"""Check MIDI files against the buyer-approved standard.

    uv run python scripts/check_midi.py path/to/file.mid [more.mid ...]
    uv run python scripts/check_midi.py path/to/folder/

Prints PASS/FAIL per file with the stems found, then the total qualifying hours.
Any scraper, on any device, should run this before counting a file as collected.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musescore_midi.roles import qualifies


def main(args):
    files = []
    for a in map(Path, args):
        files += sorted(a.glob("*.mid")) if a.is_dir() else [a]
    n = total = 0
    for f in files:
        try:
            ok, p = qualifies(f)
        except Exception as exc:
            print(f"ERROR  {f.name}  {type(exc).__name__}: {exc}")
            continue
        r = p["roles"]
        print(f"{'PASS' if ok else 'FAIL'}  {f.name:<50} {p['seconds'] / 60:5.1f}m  "
              f"drums {r.get('drums', 0)}  bass {r.get('bass', 0)}  synth {r.get('synth', 0)}  "
              f"acoustic {r.get('acoustic', 0)}")
        if ok:
            n += 1
            total += p["seconds"]
    print(f"\n{n}/{len(files)} pass, {total / 3600:.2f} h qualifying")


if __name__ == "__main__":
    main(sys.argv[1:] or ["."])
