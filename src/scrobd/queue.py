"""Things the resolver could not answer. Never sent, never lost.

Read fresh from disk on every call, for the same reason `aliases` is: the daemon
and the `review` command are separate processes, and a cached table would let one
miss the other's writes.

Per-series, not per-episode: a twelve-episode season you binged asks one
question, not twelve. `seen` counts how many files hit it, which is the honest
measure of how much a single answer is worth.
"""

import re
from pathlib import Path

from ._store import read_table, write_atomic
from .paths import state_dir

# Sonarr's own folder formats: seasonFolderFormat is "Season {season:00}" and
# specialsFolderFormat is "Specials". Match those exactly. A startswith("season")
# prefix swallowed real titles -- "Season of the Witch (2011)" is a film, not a
# season folder -- and missed "Specials", which collided every show's specials.
_SEASON_DIR = re.compile(r"season\s*\d+|specials", re.IGNORECASE)


def _file() -> Path:
    return state_dir() / "review.json"


def _load() -> dict:
    return read_table(_file(), {})


def _save(table: dict) -> None:
    write_atomic(_file(), table)    # atomic: the review command may be reading


def _folder_key(path: str) -> str:
    """Return the folder a queue entry keys on: the series, never one of its seasons."""
    parent = Path(path).parent
    return parent.parent.name if _SEASON_DIR.fullmatch(parent.name) else parent.name


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
