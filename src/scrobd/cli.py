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
import signal
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import FrameType, TracebackType
from typing import Self

from . import _log, aliases, index, queue, sessions
from .mpv import MpvSocket, MpvUnavailableError
from .resolution import UNKNOWN, Resolution
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


def _worth_asking_about(session: Session) -> bool:
    """Whether an unidentified *session* belongs in the review queue. Its row is kept either way.

    Not a URL. The queue keys on a library folder, so a stream's question would
    be keyed on a fragment of its address, and an answer taught for a host --
    `www.youtube.com` -- would name every URL on it as that one title.

    Not a session mpv reported neither a duration nor a position for: nothing
    was played. Lazy directory mode reports `Season 02` as a path of its own
    before its episodes, and queued, that directory asks about a show whose
    episodes are identified on either side of it.
    """
    if "://" in session.path:
        return False
    return session.duration is not None or session.max_pos is not None


class _Recorder:
    """Records each session a watch takes, and knows which one is not yet safe.

    A session is *in hand* from the moment it is taken from the accumulator
    until its row is in the session log or on stderr. That is the window in
    which an unforeseen error could lose it without a word, so `salvage` knows
    what to print.

    A write that fails is reported on stderr with the row in full, and the watch
    carries on. The watcher runs detached, its stderr kept in a file: stopping
    would lose every later file in that mpv, while carrying on loses none of
    them -- each row still lands in that file. `failed` makes the exit code say
    that something was not recorded.
    """

    def __init__(self) -> None:
        """Start with nothing in hand and nothing failed."""
        self.in_hand: Session | None = None
        self.failed = False

    def record(self, session: Session | None) -> None:
        """Resolve and record *session*, queueing it if it cannot be identified."""
        if session is None:
            return
        self.in_hand = session
        now = time.time()
        r = resolve_one(session.path)
        row = sessions.row(session, r, now)
        try:
            written = sessions.record(session, r, now)
        except OSError as exc:
            self._report(row, f"cannot write the session log: {exc}",
                         "  this session was not recorded; its row follows")
            return
        self.in_hand = None
        # `written` is False for a replay deduplicated inside the window, which the
        # post-mortem log should still show was seen. `row`'s own `ts` overrides the
        # one `_log.event` stamps, on purpose: both mark this record, milliseconds
        # apart, and keeping the row's makes the log line the written row exactly.
        _log.event("session", **row, written=written)
        # Same rule as `_cmd_resolve`: `is_actionable`, not `confidence == "none"`.
        if r.is_actionable or not _worth_asking_about(session):
            return
        try:
            # The queue's temporary file has a fixed name, so two watchers
            # writing at once can collide on it; that costs the entry, not the watch.
            entry = queue.add(session.path, r.title or session.path)
        except OSError as exc:
            self._report(row, f"cannot add to the review queue: {exc}",
                         "  this session was recorded but not queued; its row follows")
            return
        _log.event("queued", path=session.path, folder=entry["folder"], seen=entry["seen"])

    def salvage(self, acc: Accumulator) -> None:
        """Print every session not yet safe: the one in hand, then the one still open.

        For an error nothing else caught, on its way out. The rows are left
        unresolved, identity unknown, because the error may have come from the
        resolver itself; the path is in the row, and `scrobd resolve` will
        answer it again.
        """
        now = time.time()
        acc.finish(now)
        unsafe = [s for s in (self.in_hand, acc.take()) if s is not None]
        if not unsafe:
            return
        print("scrobd watch is stopping on an error; these sessions were not recorded, "
              "and their rows follow, unresolved", file=sys.stderr)
        for session in unsafe:
            print(json.dumps(sessions.row(session, UNKNOWN, now)), file=sys.stderr)
        self.in_hand = None

    def _report(self, row: dict, *why: str) -> None:
        """Put a failed write's reason and its row on stderr, where the session survives it."""
        for line in why:
            print(line, file=sys.stderr)
        print(json.dumps(row), file=sys.stderr)
        self.in_hand = None
        self.failed = True


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


class _Interrupts:
    """SIGINT and SIGTERM for the life of one watch, acted on only while waiting for mpv.

    SIGTERM is the one that arrives in use: the mpv script starts the watcher
    detached, with no terminal to press Ctrl-C in, so logout or shutdown is what
    ends it. Both end the watch the same way, so there is one path to the final
    record, not two.

    Only the wait for mpv's next event is cut short. That is where the watcher
    can block indefinitely -- a paused film sends nothing -- and the one place
    no session is in hand. Anywhere else a session may already be taken from
    the accumulator and milliseconds from written, and cutting it short there
    loses it without a word; so there the signal is noted and acted on at the
    next wait, and one during the final record is noted and let go.

    Held off in the handler rather than by `signal.pthread_sigmask`, because a
    mask is per thread: a signal sent to the process goes to any thread not
    masking it, and CPython then runs the handler in the main thread at its next
    bytecode -- inside the very section the mask was protecting. A handler that
    checks where the watcher is holds however many threads there are.
    """

    def __init__(self) -> None:
        """Change nothing yet; installing is `__enter__`'s job."""
        self._requested = False
        self._waiting = False
        self._previous: dict[signal.Signals,
                             Callable[[int, FrameType | None], object] | int | None] = {}

    def __enter__(self) -> Self:
        """Take SIGINT and SIGTERM over, keeping whatever handled them before."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._previous[sig] = signal.signal(sig, self._on_signal)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        """Hand both signals back, so no other command inherits the watch's handling."""
        for sig, previous in self._previous.items():
            signal.signal(sig, previous)

    def _on_signal(self, _signum: int, _frame: FrameType | None) -> None:
        """Cut the wait for mpv short; anywhere else, only note that the watch should end."""
        self._requested = True
        if self._waiting:
            self._waiting = False
            raise KeyboardInterrupt

    def next_event(self, events: Iterator[dict]) -> dict | None:
        """Return mpv's next event, or None once mpv closes the socket or a signal arrives."""
        try:
            # `_waiting` is set before `_requested` is read, and the handler
            # does the reverse, so a signal is either seen here or raised from
            # inside `next`. Neither order can leave the watch blocked on a
            # paused film with its signal already spent.
            self._waiting = True
            event = None if self._requested else next(events, None)
            self._waiting = False
        except KeyboardInterrupt:
            self._waiting = False   # already so if the handler raised; not if it came another way
            return None
        return event


def _cmd_watch(socket_path: str | None) -> int:
    """Record a session for every file mpv plays, until mpv exits, Ctrl-C or SIGTERM."""
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
    recorder = _Recorder()
    with _Interrupts() as interrupts:
        try:
            _follow(mpv, acc, recorder, interrupts)
        except BaseException:
            # Anything nothing else caught. It still ends the watch, with its
            # traceback, but not before every session in hand is on stderr.
            recorder.salvage(acc)
            raise
    return 1 if recorder.failed else 0


def _follow(mpv: MpvSocket, acc: Accumulator, recorder: _Recorder,
            interrupts: _Interrupts) -> None:
    """Record a session for every file mpv plays, then the one open when the watch ends."""
    events = mpv.events()
    try:
        while (e := interrupts.next_event(events)) is not None:
            # `.get`: a property mpv cannot report yet may arrive with no `data`.
            acc.feed(e["name"], e.get("data"), time.time())
            # Take after every feed: the accumulator holds one finished session,
            # and one left untaken is overwritten by the next without a word.
            recorder.record(acc.take())
    finally:
        mpv.close()
    # The file still open when the watch ended. mpv closing the socket,
    # Ctrl-C and SIGTERM all land here, and losing the session being watched
    # to any of them is the silent loss this tool exists to prevent.
    acc.finish(time.time())
    recorder.record(acc.take())


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
