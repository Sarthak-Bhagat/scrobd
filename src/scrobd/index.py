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
from .tier0 import ABS_RE, SE_RE


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


def lookup(idx: dict, path: str) -> Resolution:
    """Identify a file by walking its ancestor directories against the index."""
    stem = Path(path).stem
    for part in reversed(Path(path).parent.parts):
        if part in idx.get("series", {}):
            se = SE_RE.search(stem)
            if not se:
                return UNKNOWN      # the index names the series, not the episode
            entry = idx["series"][part]
            abs_m = ABS_RE.search(stem)
            return Resolution(kind="episode", ids=dict(entry["ids"]),
                              season=int(se.group(1)), episode=int(se.group(2)),
                              absolute=int(abs_m.group(1)) if abs_m else None,
                              title=entry["title"], confidence="exact", source="index")
        if part in idx.get("movies", {}):
            entry = idx["movies"][part]
            return Resolution(kind="movie", ids=dict(entry["ids"]), season=None,
                              episode=None, absolute=None, title=entry["title"],
                              confidence="exact", source="index")
    return UNKNOWN
