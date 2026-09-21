"""musescore-midi: fetch electronic/EDM scores from musescore.com and export MIDI.

  uv run python -m musescore_midi.pipeline status
  uv run --env-file .env python -m musescore_midi.pipeline fetch --tracks tracks.txt
"""

import argparse
import sys
from pathlib import Path

from . import budget
from . import convert as conv
from . import duration as dur
from . import genre as genre_mod
from .config import CLOUD_SCORES, MSCORE, OUTPUT_DIR, STATE_FILE
from .decide import ACCEPT, REJECT, REVIEW
from .songs import duplicates
from .structure import classify


def read_tracks(path):
    tracks = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                tracks.append(line)
    return tracks


def _print_routed(routed):
    for outcome, label in ((ACCEPT, "accept"), (REVIEW, "review"), (REJECT, "reject")):
        for mscz, dest, decision in routed.get(outcome, []):
            print(f"  {label:<7} {dest.name[:44]:<45} {decision.genre.status}")
            for reason in decision.reasons:
                print(f"            - {reason}")
    for mscz, _dest, why in routed.get("unconvertible", []):
        print(f"  FAILED  {mscz.name:<44} {why}")
    counts = {k: len(v) for k, v in routed.items()}
    print(f"\n  {counts.get(ACCEPT, 0)} accepted -> {OUTPUT_DIR}")
    print(f"  {counts.get(REVIEW, 0)} to review -> {OUTPUT_DIR / 'review'}")
    print(f"  {counts.get(REJECT, 0)} rejected -> {OUTPUT_DIR / 'rejected'}")
    if counts.get("unconvertible"):
        print(f"  {counts['unconvertible']} could not be converted by MuseScore "
              f"(downloaded, but no MIDI)")


def cmd_status(args):
    print(f"mscore        {MSCORE}  {'ok' if MSCORE.exists() else 'MISSING'}")
    print(f"cloud_scores  {CLOUD_SCORES}  ({len(list(CLOUD_SCORES.glob('*.mscz')))} cached)")
    print(f"output        {OUTPUT_DIR}  ({len(list(OUTPUT_DIR.glob('*.mid')))} midi)")
    print(f"state         {STATE_FILE}  {'present' if STATE_FILE.exists() else 'empty'}")
    print(f"downloads     {budget.describe()}")
    overrides = genre_mod.load_overrides()
    print(f"overrides     {len(overrides)} explicit genre label(s), "
          f"{len(genre_mod.load_tags())} score(s) with page tags")
    pending = conv.pending_scores()
    print(f"pending       {len(pending)} score(s) awaiting conversion")


def cmd_budget(args):
    if args.reset:
        had = budget.reset(reason=args.reset)
        print(f"cleared {had} recorded download(s); {budget.describe()}")
        return
    if args.limit:
        print(budget.describe(args.limit))
    else:
        print(budget.describe())
    data = budget._load(budget.BUDGET_FILE).get(budget._today(), [])
    for entry in data:
        print(f"  {entry['at']}  {entry['score_id']:<12} {entry.get('note') or ''}")


def cmd_genre(args):
    client = genre_mod._Client()
    if args.set:
        score_id, status = args.set
        entry = genre_mod.set_override(score_id, status, note=args.note)
        print(f"{score_id}: {entry['status']}" + (f"  ({args.note})" if args.note else ""))
        return
    if args.tags:
        score_id, *tag_values = args.tags
        tags = genre_mod.save_tags(score_id, tag_values)
        print(f"{score_id}: {', '.join(tags)} -> {genre_mod.classify_tags(tags).status}")
        return
    if args.query:
        print(genre_mod.classify_query(args.query, client=client))
        return
    overrides, tags = genre_mod.load_overrides(), genre_mod.load_tags()
    for mscz in sorted(CLOUD_SCORES.glob("*.mscz")):
        verdict = genre_mod.resolve(mscz, client=client, overrides=overrides, tags=tags)
        print(f"{mscz.stem:<11} {verdict}")


def cmd_structure(args):
    target = Path(args.dir) if args.dir else OUTPUT_DIR
    files = sorted(target.glob("*.mid"))
    if not files:
        print(f"no .mid files in {target}")
        return
    results = sorted((classify(f) for f in files),
                     key=lambda r: ({"accept": 0, "review": 1, "reject": 2}[r.verdict],
                                    -len(r.substantive_parts)))
    width = min(max(len(r.path.name) for r in results), 42)
    for r in results:
        print(f"  {r.path.name[:width]:<{width}}  {r}")
        if r.verdict != ACCEPT:
            print(f"      -> {'; '.join(r.reasons)}")


def cmd_duration(args):
    rows, total = dur.summarize(args.dir or OUTPUT_DIR)
    if not rows:
        print("no .mid files found")
        return
    width = max(len(name) for name, _ in rows)
    for name, seconds in rows:
        print(f"  {name:<{width}}  {dur.human(seconds) if seconds else 'unreadable'}")
    print(f"\n  {len(rows)} files, {dur.human(total)} total ({total / 60:.1f} min)")


def cmd_convert(args):
    scores = conv.pending_scores(force=args.force)
    if not scores:
        print("nothing to convert")
        return
    if args.dry_run:
        for mscz, dest in conv.convert(scores=scores, dry_run=True):
            print(f"  {mscz.name:<18} -> {dest.name}")
        return
    if args.no_classify:
        for mscz, dest in conv.convert(scores=scores, force=args.force, classify=False):
            print(f"  {mscz.name:<18} -> {dest.name}")
        return
    _print_routed(conv.convert(scores=scores, force=args.force))


def cmd_seed(args):
    print(f"marked {conv.seed_state()} cached score(s) as handled; only new fetches convert")


def cmd_duplicates(args):
    state = conv.load_state()
    groups = duplicates(state)
    if not groups:
        known = sum(1 for r in state.values() if (r.get("metrics") or {}).get("song_key"))
        print(f"no duplicate songs among {known} score(s) with a known song "
              f"({len(state) - known} still unidentified — run `enrich`)")
        return
    for key, ids in sorted(groups.items()):
        print(f"{key}")
        for score_id in ids:
            record = state[score_id]
            print(f"   {score_id:<11} {record.get('outcome', '?'):<7} "
                  f"{Path(record.get('output', '')).name}")
    print(f"\n{len(groups)} song(s) held by more than one score")


def cmd_explore(args):
    from .search import ELECTRONIC_ARTISTS, ELECTRONIC_TAGS, crawl_urls, explore

    urls = crawl_urls(tag_pages=args.tag_pages, search_pages=args.search_pages)
    print(f"crawling {len(urls)} browse pages "
          f"({len(ELECTRONIC_TAGS)} tags, {len(ELECTRONIC_ARTISTS)} artists); "
          f"no downloads are spent\n")
    cands = explore(urls, out_path=args.out, min_parts=args.min_parts,
                    limit_urls=args.limit_urls)
    print("\ntop candidates:")
    for c in cands[:15]:
        who = c.get("artist") or "?"
        print(f"  {c['id']:<10} {str(c['parts']):>3}p {str(c['seconds']):>4}s  "
              f"{who[:22]:<23} {c['clean_title'][:34]}")


def cmd_enrich(args):
    from .fetch import enrich

    print("reading score pages; this spends no downloads\n")
    results = enrich(args.score_id or None)
    found = sum(1 for _, page in results if page and (page.get("tags") or page.get("title")))
    print(f"\n{found}/{len(results)} score(s) enriched")


def cmd_fetch(args):
    from .fetch import FETCHED, fetch_all

    tracks = read_tracks(args.tracks) if args.tracks else args.track
    if not tracks:
        print("no tracks given; use --tracks FILE or --track NAME", file=sys.stderr)
        return 2

    print(f"{budget.describe(args.limit)}\n")
    results = fetch_all(
        tracks, timeout=args.timeout, allow_ambiguous=args.allow_ambiguous,
        limit=args.limit, screen_genre=not args.no_screen, force=args.force,
        close_windows=not args.keep_windows,
    )
    print()
    for result in results:
        print(f"  {result}")

    fetched = [r for r in results if r.status == FETCHED]
    print(f"\n  {len(fetched)} downloaded, "
          f"{sum(1 for r in results if r.status != FETCHED)} skipped without cost")
    if fetched and not args.no_convert:
        queries = {r.path.stem: r.query for r in fetched}
        print(f"\nconverting {len(fetched)} score(s)...")
        _print_routed(conv.convert(scores=[r.path for r in fetched], queries=queries))
    print(f"\n{budget.describe(args.limit)}")
    return 0 if fetched or not tracks else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="musescore-midi", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show pipeline state").set_defaults(func=cmd_status)
    sub.add_parser("seed", help="treat the current cache as converted").set_defaults(func=cmd_seed)

    b = sub.add_parser("budget", help="daily download quota")
    b.add_argument("--limit", type=int, help=f"default {budget.DAILY_LIMIT}")
    b.add_argument("--reset", metavar="REASON",
                   help="clear today's count (e.g. after switching account)")
    b.set_defaults(func=cmd_budget)

    g = sub.add_parser("genre", help="genre of cached scores, or of a query")
    g.add_argument("--query", help="classify a search phrase without downloading")
    g.add_argument("--set", nargs=2, metavar=("SCORE_ID", "STATUS"),
                   help="assert a genre: ELECTRONIC_EDM | NON_ELECTRONIC | AMBIGUOUS")
    g.add_argument("--note", help="why, recorded with --set")
    g.add_argument("--tags", nargs="+", metavar=("SCORE_ID", "TAG"),
                   help="record the tags from a score's musescore.com page")
    g.set_defaults(func=cmd_genre)

    st = sub.add_parser("structure", help="structural audit of exported MIDI")
    st.add_argument("--dir", help=f"defaults to {OUTPUT_DIR}")
    st.set_defaults(func=cmd_structure)

    d = sub.add_parser("duration", help="total playing time of exported MIDI")
    d.add_argument("--dir", help=f"defaults to {OUTPUT_DIR}")
    d.set_defaults(func=cmd_duration)

    c = sub.add_parser("convert", help="convert cached scores and route them")
    c.add_argument("--force", action="store_true")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--no-classify", action="store_true", help="keep everything, no routing")
    c.set_defaults(func=cmd_convert)

    sub.add_parser("duplicates", help="scores that are the same song").set_defaults(
        func=cmd_duplicates)

    x = sub.add_parser("explore", help="crawl musescore for candidates (no downloads)")
    x.add_argument("--out", default="candidates.json")
    x.add_argument("--tag-pages", type=int, default=3)
    x.add_argument("--search-pages", type=int, default=2)
    x.add_argument("--min-parts", type=int, default=4)
    x.add_argument("--limit-urls", type=int, help="stop after N browse pages")
    x.set_defaults(func=cmd_explore)

    e = sub.add_parser("enrich", help="read cached scores' pages for tags (no downloads)")
    e.add_argument("score_id", nargs="*", help="defaults to every cached score")
    e.set_defaults(func=cmd_enrich)

    f = sub.add_parser("fetch", help="screen, fetch, convert, and route tracks")
    f.add_argument("--tracks", help="file with one search phrase or score URL per line")
    f.add_argument("--track", action="append", default=[], help="repeatable")
    f.add_argument("--timeout", type=float, default=90.0, help="handoff wait, seconds")
    f.add_argument("--limit", type=int, default=budget.DAILY_LIMIT, help="daily download cap")
    f.add_argument("--allow-ambiguous", action="store_true",
                   help="also download tracks whose genre could not be confirmed")
    f.add_argument("--no-screen", action="store_true", help="skip the genre pre-screen")
    f.add_argument("--no-convert", action="store_true", help="fetch only")
    f.add_argument("--keep-windows", action="store_true",
                   help="leave each score open in MuseScore Studio")
    f.add_argument("--force", action="store_true",
                   help="download even if the score or song is already held")
    f.set_defaults(func=cmd_fetch)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
