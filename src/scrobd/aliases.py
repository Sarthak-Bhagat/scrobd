"""Answers you have already given, keyed on the folder.

No in-process cache. `scrobd review` writes an alias in one process while the
daemon runs in another; a cached table would leave the daemon blind to an answer
you just gave until it restarted. Reading a few KB of JSON per played file costs
nothing. It also means no `global` statement, which ruff rejects (PLW0603) and
which this project does not silence.

The review queue is per-series, not per-episode: one answer writes an alias and
every episode of that show -- future seasons included -- resolves from it. A new
unknown show asks exactly once.

Confidence is `high`, not `exact`: a human said so rather than *arr, which is
worth distinguishing when auditing later.
"""

from pathlib import Path

from ._store import read_table, write_atomic
from .paths import data_dir
from .resolution import UNKNOWN, Resolution
from .tier0 import SE_RE, absolute_from, ancestors


def _file() -> Path:
    return data_dir() / "aliases.json"


def load() -> dict:
    """Every alias taught so far, keyed by folder. Read fresh every call."""
    return read_table(_file(), {})


def remember(folder: str, ids: dict, kind: str, title: str) -> None:
    """Teach the table one folder's answer, atomically."""
    t = load()
    t[folder] = {"ids": ids, "kind": kind, "title": title}
    write_atomic(_file(), t)    # atomic: the daemon may be reading


def _row(table: dict, part: str) -> dict | None:
    """One well-formed alias, or None.

    The table is whatever is on disk, so nothing is assumed about a row's shape.
    `ids` and `kind` are checked strictly -- they decide the answer, and an
    unrecognised `kind` used to fall through to the episode branch silently.
    `title` is display-only and defaulted. A bad row is skipped rather than
    raised on, so one corrupt answer does not cost every other answer given.
    """
    entry = table.get(part)
    if not (isinstance(entry, dict) and isinstance(entry.get("ids"), dict) and entry["ids"]):
        return None
    return entry if entry.get("kind") in ("episode", "movie") else None


def lookup(path: str) -> Resolution:
    """Identify a file by walking its ancestor directories against the alias table."""
    t = load()
    stem = Path(path).stem
    for part in ancestors(path):
        entry = _row(t, part)
        if entry is None:
            continue
        title = entry.get("title")
        title = title if isinstance(title, str) else ""
        if entry["kind"] == "movie":
            return Resolution(kind="movie", ids=dict(entry["ids"]), season=None,
                              episode=None, absolute=None, title=title,
                              confidence="high", source="alias")
        se = SE_RE.search(stem)
        if not se:
            return UNKNOWN
        return Resolution(kind="episode", ids=dict(entry["ids"]),
                          season=int(se.group(1)), episode=int(se.group(2)),
                          absolute=absolute_from(stem),
                          title=title, confidence="high", source="alias")
    return UNKNOWN
