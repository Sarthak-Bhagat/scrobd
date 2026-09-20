"""XDG base directories for scrobd.

Upstream simkl-mps stored everything in ~/kavin/simkl-mps because its own
`user_subdir` setting was a bootstrap paradox and nothing ever called
initialize_paths. Resolving these from the environment on every call, with no
cached module-level constant, makes that class of bug impossible here.
"""

import os
from pathlib import Path

APP = "scrobd"


def _base(env_var: str, *fallback: str) -> Path:
    root = os.environ.get(env_var)
    base = Path(root) if root else Path(os.environ.get("HOME", "~")).expanduser().joinpath(*fallback)
    d = base / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir() -> Path:
    return _base("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _base("XDG_DATA_HOME", ".local", "share")


def state_dir() -> Path:
    return _base("XDG_STATE_HOME", ".local", "state")
