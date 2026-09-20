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

import json
from pathlib import Path

from .paths import data_dir
from .resolution import UNKNOWN, Resolution
from .tier0 import ABS_RE, SE_RE


def _file() -> Path:
    return data_dir() / "aliases.json"


def table() -> dict:
    """Every alias taught so far, keyed by folder. Read fresh every call."""
    try:
        return json.loads(_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def remember(folder: str, ids: dict, kind: str, title: str) -> None:
    """Teach the table one folder's answer, atomically."""
    t = table()
    t[folder] = {"ids": ids, "kind": kind, "title": title}
    p = _file()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, indent=1))
    tmp.replace(p)          # atomic: the daemon may be reading


def lookup(path: str) -> Resolution:
    """Identify a file by walking its ancestor directories against the alias table."""
    t = table()
    stem = Path(path).stem
    for part in reversed(Path(path).parent.parts):
        entry = t.get(part)
        if not entry:
            continue
        if entry["kind"] == "movie":
            return Resolution(kind="movie", ids=dict(entry["ids"]), season=None,
                              episode=None, absolute=None, title=entry["title"],
                              confidence="high", source="alias")
        se = SE_RE.search(stem)
        if not se:
            return UNKNOWN
        abs_m = ABS_RE.search(stem)
        return Resolution(kind="episode", ids=dict(entry["ids"]),
                          season=int(se.group(1)), episode=int(se.group(2)),
                          absolute=int(abs_m.group(1)) if abs_m else None,
                          title=entry["title"], confidence="high", source="alias")
    return UNKNOWN
