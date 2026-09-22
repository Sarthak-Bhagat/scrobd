"""Turning a stream of mpv property changes into one record per file."""

from scrobd.session import Accumulator


def feed(acc, events):
    for name, data, now in events:
        acc.feed(name, data, now)


def test_one_file_becomes_one_session():
    acc = Accumulator()
    feed(acc, [("path", "/m/a.mkv", 100.0), ("duration", 1400.0, 100.0),
               ("time-pos", 10.0, 110.0), ("time-pos", 700.0, 800.0)])
    acc.finish(900.0)
    s = acc.take()
    assert s.path == "/m/a.mkv"
    assert s.started_at == 100.0
    assert s.ended_at == 900.0
    assert s.duration == 1400.0
    assert s.max_pos == 700.0


def test_a_new_path_closes_the_previous_session():
    acc = Accumulator()
    feed(acc, [("path", "/m/a.mkv", 100.0), ("time-pos", 50.0, 150.0),
               ("path", "/m/b.mkv", 200.0)])
    first = acc.take()
    assert first.path == "/m/a.mkv"
    assert first.max_pos == 50.0
    assert first.ended_at == 200.0
    feed(acc, [("time-pos", 30.0, 250.0)])
    acc.finish(300.0)
    assert acc.take().path == "/m/b.mkv"


def test_max_pos_survives_seeking_backwards():
    """Watching to 90% then rewinding to rewatch a scene is still a 90% watch."""
    acc = Accumulator()
    feed(acc, [("path", "/m/a.mkv", 0.0), ("duration", 100.0, 0.0),
               ("time-pos", 90.0, 10.0), ("time-pos", 20.0, 20.0)])
    acc.finish(30.0)
    assert acc.take().max_pos == 90.0


def test_pause_does_not_end_a_session():
    acc = Accumulator()
    feed(acc, [("path", "/m/a.mkv", 0.0), ("time-pos", 10.0, 10.0),
               ("pause", True, 20.0), ("pause", False, 600.0),
               ("time-pos", 40.0, 610.0)])
    acc.finish(620.0)
    s = acc.take()
    assert s.max_pos == 40.0
    assert s.ended_at == 620.0


def test_no_position_ever_reported_leaves_max_pos_none():
    """A missing number is a gap. Never invent progress."""
    acc = Accumulator()
    feed(acc, [("path", "/m/a.mkv", 0.0), ("duration", 1400.0, 0.0)])
    acc.finish(10.0)
    s = acc.take()
    assert s.max_pos is None
    assert s.duration == 1400.0


def test_take_returns_none_when_nothing_has_played():
    assert Accumulator().take() is None


def test_take_is_not_repeatable():
    acc = Accumulator()
    feed(acc, [("path", "/m/a.mkv", 0.0)])
    acc.finish(5.0)
    assert acc.take() is not None
    assert acc.take() is None


def test_samples_counts_position_reports():
    acc = Accumulator()
    feed(acc, [("path", "/m/a.mkv", 0.0), ("time-pos", 1.0, 1.0),
               ("time-pos", 2.0, 2.0), ("time-pos", 3.0, 3.0)])
    acc.finish(4.0)
    assert acc.take().samples == 3
