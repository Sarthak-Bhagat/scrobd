"""scrobd — identity-first scrobbling.

scrobd resolve <path>                       what is this file?
scrobd review                               what could I not identify?
scrobd review --answer <folder> --tvdb N    teach it, once, for the whole series
scrobd index --from-json <series> <movies>  rebuild the local index
"""

import argparse
import json
import sys
from pathlib import Path

from . import aliases, index, queue
from .resolution import Resolution
from .resolver import resolve as resolve_one


def _describe(r: Resolution) -> str:
    if r.kind == "episode":
        se = f"S{r.season:02d}E{r.episode:02d}"
        ab = f" ({r.absolute:03d})" if r.absolute else ""
        return f"{r.title}  {se}{ab}  {r.ids}  [{r.confidence} via {r.source}]"
    if r.kind == "movie":
        return f"{r.title}  {r.ids}  [{r.confidence} via {r.source}]"
    return "unknown"


def _cmd_resolve(path: str) -> int:
    """Identify one file; queue it for review if it cannot be identified."""
    r = resolve_one(path)
    if not r.is_actionable:
        # `is_actionable`, not `confidence == "none"`. Tier 3 returns `low`, and
        # the day it lands a `none` test would print a guess and exit 0.
        queue.add(path, r.title or path)
        print(f"could not identify: {path}")
        print("  queued for review -- run `scrobd review`")
        return 1
    print(_describe(r))
    return 0


def _cmd_review(answer: str | None, tvdb: int | None, imdb: str | None) -> int:
    """Show the review queue, or teach an answer for one queued folder."""
    if answer:
        if not (tvdb or imdb):
            print("give an id: --tvdb N or --imdb ttNNNNN", file=sys.stderr)
            return 2
        # Clear the queue first. If the folder was never queued, the alias would
        # go in under a key no lookup can ever produce, and the entry would stay
        # queued forever while the command reported success.
        if not queue.resolve_entry(answer):
            print(f"nothing queued under {answer!r} -- folder names are matched "
                  "literally; `scrobd review` lists them", file=sys.stderr)
            return 1
        ids = {"tvdb": tvdb} if tvdb else {"imdb": imdb}
        kind = "episode" if tvdb else "movie"
        aliases.remember(answer, ids, kind, answer)
        print(f"remembered {answer} -> {ids}")
        return 0

    rows = queue.pending()
    if not rows:
        print("nothing to review")
        return 0
    for e in rows:
        print(f"  {e['seen']:>4} file(s)  {e['folder']}")
        print(f"              e.g. {e['example']}")
    print("\n  answer with: scrobd review --answer '<folder>' --tvdb N")
    return 0


def _cmd_index(series_path: str, movies_path: str) -> int:
    """Rebuild the local index from Sonarr/Radarr JSON exports."""
    try:
        series = json.loads(Path(series_path).read_text())
        movies = json.loads(Path(movies_path).read_text())
    except OSError as exc:
        print(f"cannot read the export: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"export is not valid JSON: {exc}", file=sys.stderr)
        return 2
    idx = index.build(series, movies)
    index.save(idx)
    print(f"indexed {len(idx['series'])} series, {len(idx['movies'])} movies")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scrobd", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("resolve")
    pr.add_argument("path")

    pv = sub.add_parser("review")
    pv.add_argument("--answer", metavar="FOLDER")
    pv.add_argument("--tvdb", type=int)
    pv.add_argument("--imdb")

    pi = sub.add_parser("index")
    pi.add_argument("--from-json", nargs=2, metavar=("SERIES", "MOVIES"), required=True)

    return p


def main(argv: list[str] | None = None) -> int:
    """Run the scrobd CLI; return the process's exit code."""
    a = _build_parser().parse_args(argv)

    if a.cmd == "resolve":
        return _cmd_resolve(a.path)
    if a.cmd == "review":
        return _cmd_review(a.answer, a.tvdb, a.imdb)
    if a.cmd == "index":
        s, m = a.from_json
        return _cmd_index(s, m)
    return 1


if __name__ == "__main__":
    sys.exit(main())
