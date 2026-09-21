"""The CLI: resolve, review and index as run from a shell."""

import json

import pytest

from scrobd import cli, queue


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))


def test_resolve_prints_the_identity(capsys):
    rc = cli.main(["resolve", "Spooky in Love (2026) [tvdbid-466998] - S01E02 - Episode 2.mkv"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "tvdb" in out
    assert "466998" in out
    assert "S01E02" in out


def test_resolve_of_an_unknown_file_exits_nonzero_and_queues_it(capsys):
    rc = cli.main(["resolve", "/m/Mystery Show/Season 01/x - S01E01 - t.mkv"])
    assert rc == 1
    assert "could not identify" in capsys.readouterr().out.lower()
    assert queue.count() == 1


def test_review_lists_pending(capsys):
    cli.main(["resolve", "/m/Mystery Show/Season 01/x - S01E01 - t.mkv"])
    cli.main(["review"])
    assert "Mystery Show" in capsys.readouterr().out


def test_review_with_an_answer_writes_an_alias_and_clears_the_queue():
    cli.main(["resolve", "/m/Mystery Show/Season 01/x - S01E01 - t.mkv"])
    rc = cli.main(["review", "--answer", "Mystery Show", "--tvdb", "4242"])
    assert rc == 0
    assert queue.count() == 0
    r = cli.resolve_one("/m/Mystery Show/Season 03/x - S03E05 - t.mkv")
    assert r.ids == {"tvdb": 4242}


def test_review_answer_requires_an_id():
    cli.main(["resolve", "/m/Mystery Show/Season 01/x - S01E01 - t.mkv"])
    rc = cli.main(["review", "--answer", "Mystery Show"])
    assert rc == 2


def test_index_from_json(tmp_path, capsys):
    s = tmp_path / "s.json"
    s.write_text(json.dumps(
        [{"id": 1, "title": "T", "tvdbId": 1, "path": "/m/TV_Shows/T (2020)"}]))
    m = tmp_path / "m.json"
    m.write_text("[]")
    rc = cli.main(["index", "--from-json", str(s), str(m)])
    assert rc == 0
    assert "1 series" in capsys.readouterr().out
