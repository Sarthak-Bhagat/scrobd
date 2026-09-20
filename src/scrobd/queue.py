"""Things the resolver could not answer. Never sent, never lost.

Read fresh from disk on every call, for the same reason `aliases` is: the daemon
and the `review` command are separate processes, and a cached table would let one
miss the other's writes.

Per-series, not per-episode: a twelve-episode season you binged asks one
question, not twelve. `seen` counts how many files hit it, which is the honest
measure of how much a single answer is worth.
"""

import json
from pathlib import Path

from .paths import state_dir


def _file() -> Path:
    return state_dir() / "review.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(table: dict) -> None:
    p = _file()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(table, indent=1))
    tmp.replace(p)          # atomic: the review command may be reading


def _folder_key(path: str) -> str:
    """Return the series folder for path, walking up past a Season NN directory."""
    parent = Path(path).parent
    if parent.name.lower().startswith("season"):
        parent = parent.parent
    return parent.name


def add(path: str, title: str) -> None:
    """Record one file the resolver could not identify, keyed by series folder."""
    table = _load()
    folder = _folder_key(path)
    entry = table.setdefault(
        folder,
        {"folder": folder, "title": title, "example": path, "seen": 0},
    )
    entry["seen"] += 1
    _save(table)


def pending() -> list[dict]:
    """Return queued entries, most-repeated question first."""
    return sorted(_load().values(), key=lambda e: -e["seen"])


def count() -> int:
    """Return how many series are currently queued."""
    return len(_load())


def resolve_entry(folder: str) -> None:
    """Remove folder from the queue. A no-op if it was never queued."""
    table = _load()
    if table.pop(folder, None) is not None:
        _save(table)
