"""A local mirror of what Sonarr and Radarr already know.

Asking Sonarr at play time is a network round trip for a fact it wrote weeks
ago. The index is rebuilt on Sonarr's import webhook instead, so a lookup at
play time is a dict hit.

Keyed on the folder, because every episode of a series shares one and the
filename may have been renamed by hand.
"""

from pathlib import Path

from ._store import read_table, write_atomic
from .paths import data_dir
from .resolution import UNKNOWN, Resolution
from .tier0 import SE_RE, absolute_from, ancestors


def build(series: list, movies: list) -> dict:
    """Build the index dict from already-parsed Sonarr/Radarr JSON."""
    out = {"series": {}, "movies": {}}
    for s in series:
        if not s.get("path") or not s.get("tvdbId"):
            continue
        ids = {"tvdb": s["tvdbId"]}
        if s.get("imdbId"):
            ids["imdb"] = s["imdbId"]
        out["series"][Path(s["path"]).name] = {"ids": ids, "title": s.get("title", "")}
    for m in movies:
        if not m.get("path"):
            continue
        ids = {}
        if m.get("imdbId"):
            ids["imdb"] = m["imdbId"]
        if m.get("tmdbId"):
            ids["tmdb"] = m["tmdbId"]
        if not ids:
            continue
        out["movies"][Path(m["path"]).name] = {"ids": ids, "title": m.get("title", "")}
    return out


def _file() -> Path:
    return data_dir() / "index.json"


def save(idx: dict) -> Path:
    """Write the index to disk atomically and return the path written."""
    p = _file()
    write_atomic(p, idx)    # atomic: the daemon may be reading
    return p


def load() -> dict:
    """Read the index from disk, degrading to empty on missing or corrupt data."""
    return read_table(_file(), {"series": {}, "movies": {}})


def _row(idx: dict, section: str, part: str) -> dict | None:
    """One well-formed row of *section*, or None.

    The file on disk is whatever was last written there, so neither the section
    nor the row is assumed to be the shape `build` emits. `ids` is checked
    strictly because it is the answer; `title` is display-only and defaulted.
    A bad row is skipped, not raised on -- one corrupt series must not make
    every other series in the index unresolvable.
    """
    rows = idx.get(section)
    entry = rows.get(part) if isinstance(rows, dict) else None
    if not (isinstance(entry, dict) and isinstance(entry.get("ids"), dict) and entry["ids"]):
        return None
    return entry


def _title(entry: dict) -> str:
    title = entry.get("title")
    return title if isinstance(title, str) else ""


def lookup(idx: dict, path: str) -> Resolution:
    """Identify a file by walking its ancestor directories against the index."""
    stem = Path(path).stem
    for part in ancestors(path):
        entry = _row(idx, "series", part)
        if entry is not None:
            se = SE_RE.search(stem)
            if not se:
                return UNKNOWN      # the index names the series, not the episode
            return Resolution(kind="episode", ids=dict(entry["ids"]),
                              season=int(se.group(1)), episode=int(se.group(2)),
                              absolute=absolute_from(stem),
                              title=_title(entry), confidence="exact", source="index")
        entry = _row(idx, "movies", part)
        if entry is not None:
            return Resolution(kind="movie", ids=dict(entry["ids"]), season=None,
                              episode=None, absolute=None, title=_title(entry),
                              confidence="exact", source="index")
    return UNKNOWN
