"""Tier 2: the learned alias table, keyed on folder, answered once per series."""

import pytest

from scrobd import aliases
from scrobd.paths import data_dir


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def test_answer_once_then_every_episode_inherits_it():
    aliases.remember("Some Sideloaded Show", {"tvdb": 999}, "episode", "Some Sideloaded Show")
    r = aliases.lookup("/x/Some Sideloaded Show/Season 02/whatever - S02E07 - t.mkv")
    assert r.ids == {"tvdb": 999}
    assert (r.season, r.episode) == (2, 7)
    assert r.confidence == "high"
    assert r.source == "alias"


def test_future_seasons_inherit_too():
    """The queue is per-series. A new season must not ask again."""
    aliases.remember("Some Sideloaded Show", {"tvdb": 999}, "episode", "S")
    r = aliases.lookup("/x/Some Sideloaded Show/Season 05/x - S05E01 - t.mkv")
    assert r.ids == {"tvdb": 999}
    assert r.season == 5


def test_unknown_folder_is_unresolved():
    assert aliases.lookup("/x/Never Seen/Season 01/x - S01E01 - t.mkv").confidence == "none"


def test_movie_alias():
    aliases.remember("A Film Folder", {"imdb": "tt1"}, "movie", "A Film")
    r = aliases.lookup("/x/A Film Folder/film.mkv")
    assert r.kind == "movie"
    assert r.ids == {"imdb": "tt1"}


def test_persists_across_processes():
    """The daemon and `scrobd review` are separate processes; disk is the only channel."""
    aliases.remember("Persisted", {"tvdb": 7}, "episode", "P")
    assert aliases.lookup("/x/Persisted/Season 01/x - S01E01 - t.mkv").ids == {"tvdb": 7}


def test_corrupt_file_degrades_to_empty():
    (data_dir() / "aliases.json").write_text("{broken")
    assert aliases.lookup("/x/Anything/Season 01/x - S01E01 - t.mkv").confidence == "none"
