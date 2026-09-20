"""The review queue: files the resolver could not identify, one entry per series."""

import pytest

from scrobd import queue
from scrobd.paths import state_dir


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


def test_one_entry_per_series_not_per_episode():
    for ep in range(1, 13):
        queue.add(f"/m/Unknown Show/Season 01/x - S01E{ep:02d} - t.mkv", "Unknown Show")
    assert queue.count() == 1


def test_entry_records_the_folder_and_an_example_file():
    queue.add("/m/Unknown Show/Season 01/x - S01E01 - t.mkv", "Unknown Show")
    e = queue.pending()[0]
    assert e["folder"] == "Unknown Show"
    assert e["example"].endswith("S01E01 - t.mkv")
    assert e["seen"] == 1


def test_seen_count_rises_with_repeats():
    for _ in range(3):
        queue.add("/m/Unknown Show/Season 01/x - S01E01 - t.mkv", "Unknown Show")
    assert queue.pending()[0]["seen"] == 3


def test_resolving_removes_it():
    queue.add("/m/Unknown Show/Season 01/x - S01E01 - t.mkv", "Unknown Show")
    queue.resolve_entry("Unknown Show")
    assert queue.count() == 0


def test_resolving_something_absent_is_not_an_error():
    queue.resolve_entry("Never Queued")
    assert queue.count() == 0


def test_survives_a_corrupt_file():
    (state_dir() / "review.json").write_text("{broken")
    queue.add("/m/X/Season 01/x - S01E01 - t.mkv", "X")
    assert queue.count() == 1
