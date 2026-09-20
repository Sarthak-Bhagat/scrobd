"""Read the identity straight out of the path. No I/O, no guessing.

Sonarr and Radarr write the id into both the folder and the filename, so this
works for a file that has been moved out of its folder -- which is every file
mediactl trashes, since its trash flattens the hierarchy.

Tag forms seen in this library, all accepted:
    [tvdbid-466998]   Sonarr, current
    [imdbid-tt16428256]  Radarr, current
    [imdb-tt9646546]     Sonarr, pre-2026-09-20; still on archived copies
An empty tag -- [imdb-] -- is not an id. That was a real fault here: Sonarr's
{ImdbId} token renders empty for a series with no imdb entry, while {TvdbId}
is an int and always renders.
"""

import re
from pathlib import Path

from .resolution import UNKNOWN, Resolution

_ID = re.compile(r"\[(tvdbid|imdbid|tmdbid|imdb|tvdb|tmdb)-([A-Za-z0-9]+)\]")
# Public: index.py and aliases.py reuse these to read the episode numbers off
# a filename whose series was identified some other way.
SE_RE = re.compile(r"\bS(\d{1,3})E(\d{1,4})\b", re.IGNORECASE)
# The absolute number sits between the SxxExx block and the episode title.
ABS_RE = re.compile(r"\bS\d{1,3}E\d{1,4}\b\s*-\s*(\d{2,4})\s*-\s", re.IGNORECASE)

_KEY = {"tvdbid": "tvdb", "tvdb": "tvdb",
        "imdbid": "imdb", "imdb": "imdb",
        "tmdbid": "tmdb", "tmdb": "tmdb"}


def _ids(text: str) -> dict:
    out = {}
    for label, value in _ID.findall(text):
        if not value:                      # [imdb-] -- present but empty
            continue
        key = _KEY[label.lower()]
        out[key] = int(value) if key in ("tvdb", "tmdb") and value.isdigit() else value
    return out


def _title(name: str) -> str:
    """Everything before the first bracket tag. Display only, never matched on."""
    return re.split(r"\s*\[", name, maxsplit=1)[0].strip()


def parse(path: str) -> Resolution:
    """Read season/episode/ids straight out of the filename and folder, no I/O."""
    stem = Path(path).stem

    # The filename wins; the folder is a fallback for files that never had a tag.
    ids = _ids(stem) or _ids(path)
    if not ids:
        return UNKNOWN

    se = SE_RE.search(stem)
    if se:
        if "tvdb" not in ids:
            return UNKNOWN                 # episode numbers with no series id say nothing
        abs_m = ABS_RE.search(stem)
        return Resolution(
            kind="episode", ids=ids,
            season=int(se.group(1)), episode=int(se.group(2)),
            absolute=int(abs_m.group(1)) if abs_m else None,
            title=_title(stem), confidence="exact", source="filename")

    if "imdb" in ids or "tmdb" in ids:
        return Resolution(kind="movie", ids=ids, season=None, episode=None,
                          absolute=None, title=_title(stem),
                          confidence="exact", source="filename")

    return UNKNOWN
