"""scrobd — identity-first scrobbling.

scrobd resolve <path>                       what is this file?
scrobd review                               what could I not identify?
scrobd review --answer <folder> --tvdb N    teach it, once, for the whole series
scrobd index --from-json <series> <movies>  rebuild the local index
scrobd watch [--socket PATH]                record a session per file mpv plays
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import _log, aliases, index, queue, sessions
from .mpv import MpvSocket, MpvUnavailableError
from .resolution import Resolution
from .resolver import resolve as resolve_one
from .session import Accumulator, Session


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
    # Logged here rather than in `resolve`, which must stay free of I/O -- see
    # `_log`'s docstring. `source` records which tier actually answered.
    _log.event("resolve", path=path, source=r.source, confidence=r.confidence,
               ids=r.ids, kind=r.kind, season=r.season, episode=r.episode,
               absolute=r.absolute, title=r.title)
    if not r.is_actionable:
        # `is_actionable`, not `confidence == "none"`. Tier 3 returns `low`, and
        # the day it lands a `none` test would print a guess and exit 0.
        entry = queue.add(path, r.title or path)
        _log.event("queued", path=path, folder=entry["folder"], seen=entry["seen"])
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
        cleared = queue.resolve_entry(answer)
        if not cleared:
            print(f"nothing queued under {answer!r} -- folder names are matched "
                  "literally; `scrobd review` lists them", file=sys.stderr)
            return 1
        ids = {"tvdb": tvdb} if tvdb else {"imdb": imdb}
        kind = "episode" if tvdb else "movie"
        aliases.remember(answer, ids, kind, answer)
        # `cleared` is True here by construction -- the guard above returns
        # otherwise. Recorded rather than assumed: that ordering is itself a fix
        # (audit 2026-09-21), and a post-mortem should be able to see which
        # order was in force rather than infer it from the version tag.
        _log.event("alias_taught", folder=answer, ids=ids, kind=kind, cleared=cleared)
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
    _log.event("index_rebuilt", series=len(idx["series"]), movies=len(idx["movies"]))
    print(f"indexed {len(idx['series'])} series, {len(idx['movies'])} movies")
    return 0


def _record(session: Session | None) -> bool:
    """Resolve and record one finished session, queueing it if it cannot be identified.

    Return False if the session log could not be written. The row then goes to
    stderr in full, so the session survives in the terminal or the journal, and
    the caller stops: a log that cannot be written now will almost certainly not
    be writable for the next file either, and a watcher left running looks like
    one that is recording.
    """
    if session is None:
        return True
    now = time.time()
    r = resolve_one(session.path)
    row = sessions.row(session, r, now)
    try:
        written = sessions.record(session, r, now)
    except OSError as exc:
        print(f"cannot write the session log: {exc}", file=sys.stderr)
        print("  this session was not recorded; its row follows", file=sys.stderr)
        print(json.dumps(row), file=sys.stderr)
        return False
    # `written` is False for a replay deduplicated inside the window, which the
    # post-mortem log should still show was seen. `row`'s own `ts` overrides the
    # one `_log.event` stamps, on purpose: both mark this record, milliseconds
    # apart, and keeping the row's makes the log line the written row exactly.
    _log.event("session", **row, written=written)
    if not r.is_actionable:
        # Same rule as `_cmd_resolve`: `is_actionable`, not `confidence == "none"`.
        entry = queue.add(session.path, r.title or session.path)
        _log.event("queued", path=session.path, folder=entry["folder"], seen=entry["seen"])
    return True


def _default_socket() -> str | None:
    """Return `$XDG_RUNTIME_DIR/mpvsocket`, or None if that directory is not set.

    Read when the command runs, not at import, like every path in `scrobd.paths`.
    The runtime directory is the user's own (0700), where mpv.conf's
    `input-ipc-server` points. There is deliberately no fallback to /tmp: a
    shared, world-writable path is one another user can bind first, and guessing
    one would watch the wrong place without saying so.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return str(Path(runtime) / "mpvsocket") if runtime else None


def _cmd_watch(socket_path: str | None) -> int:
    """Record a session for every file mpv plays, until mpv exits or Ctrl-C."""
    socket_path = socket_path or _default_socket()
    if socket_path is None:
        print("XDG_RUNTIME_DIR is unset, so there is no default mpv socket -- "
              "pass --socket PATH", file=sys.stderr)
        return 1
    mpv = MpvSocket(socket_path)
    try:
        mpv.connect()
    except MpvUnavailableError as exc:
        mpv.close()     # the subscription can fail with the socket already open
        print(exc, file=sys.stderr)     # it already names the likely cause
        return 1
    acc = Accumulator()
    try:
        for e in mpv.events():
            # `.get`: a property mpv cannot report yet may arrive with no `data`.
            acc.feed(e["name"], e.get("data"), time.time())
            # Take after every feed: the accumulator holds one finished session,
            # and one left untaken is overwritten by the next without a word.
            if not _record(acc.take()):
                return 1
    except KeyboardInterrupt:
        pass        # Ctrl-C ends the watch exactly as mpv exiting does: below
    finally:
        mpv.close()
    # The file still open when the stream ended. mpv closing the socket and
    # Ctrl-C both land here, and losing the session being watched to either one
    # is the silent loss this tool exists to prevent.
    acc.finish(time.time())
    return 0 if _record(acc.take()) else 1


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

    pw = sub.add_parser("watch")
    pw.add_argument("--socket", metavar="PATH",
                    help="mpv's IPC socket (default: $XDG_RUNTIME_DIR/mpvsocket)")

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
    if a.cmd == "watch":
        return _cmd_watch(a.socket)
    return 1


if __name__ == "__main__":
    sys.exit(main())
