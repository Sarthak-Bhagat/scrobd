"""`scrobd watch` end to end, against a fake mpv."""

import json
import signal
import socket
import threading

import pytest

from scrobd import cli, queue, sessions
from scrobd.mpv import MpvSocket, MpvUnavailableError
from scrobd.paths import state_dir


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("SCROBD_LOG", "0")
    # Never the real one: a test that forgets --socket must not reach a running mpv.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))


def serve(path, lines, ready):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    with conn:
        conn.recv(4096)
        for line in lines:
            conn.sendall((json.dumps(line) + "\n").encode())
    srv.close()


def start_mpv(sock, lines):
    ready = threading.Event()
    threading.Thread(target=serve, args=(sock, lines, ready), daemon=True).start()
    ready.wait(2)


def run_watch(tmp_path, lines):
    sock = tmp_path / "mpvsocket"
    start_mpv(sock, lines)
    return cli.main(["watch", "--socket", str(sock)])


def test_a_played_file_becomes_a_session_row(tmp_path):
    rc = run_watch(tmp_path, [
        {"event": "property-change", "name": "path",
         "data": "/m/Show (2020) [tvdbid-99]/Season 01/x - S01E03 - t.mkv"},
        {"event": "property-change", "name": "duration", "data": 1400.0},
        {"event": "property-change", "name": "time-pos", "data": 1300.0},
    ])
    assert rc == 0
    rows = sessions.read()
    assert len(rows) == 1
    assert rows[0]["ids"] == {"tvdb": 99}
    assert rows[0]["episode"] == 3
    assert rows[0]["max_pos"] == 1300.0


def test_an_unidentifiable_file_is_recorded_and_queued(tmp_path):
    rc = run_watch(tmp_path, [
        {"event": "property-change", "name": "path", "data": "/m/Mystery/Season 01/x.mkv"},
        {"event": "property-change", "name": "time-pos", "data": 60.0},
    ])
    assert rc == 0
    assert len(sessions.read()) == 1          # recorded regardless
    assert queue.count() == 1                 # and queued for an answer


def test_a_dead_socket_exits_nonzero_with_a_reason(tmp_path, capsys):
    dead = tmp_path / "mpvsocket"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(dead))
    s.close()
    rc = cli.main(["watch", "--socket", str(dead)])
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "nothing listening" in err


def test_a_missing_socket_exits_nonzero_with_a_reason(tmp_path, capsys):
    rc = cli.main(["watch", "--socket", str(tmp_path / "absent")])
    assert rc == 1
    assert "no socket" in capsys.readouterr().err.lower()


SHOW = "/m/Show (2020) [tvdbid-99]/Season 01/x - S01E{:02d} - t.mkv"


def play(path, pos):
    return [{"event": "property-change", "name": "path", "data": path},
            {"event": "property-change", "name": "time-pos", "data": pos}]


def test_every_file_in_one_run_is_its_own_session(tmp_path):
    """The accumulator holds one finished session; an untaken one is overwritten."""
    rc = run_watch(tmp_path, play(SHOW.format(1), 1300.0) + play(SHOW.format(2), 420.0))
    assert rc == 0
    assert [(r["episode"], r["max_pos"]) for r in sessions.read()] == [(1, 1300.0), (2, 420.0)]


def test_a_file_mpv_reported_nothing_about_is_recorded_with_gaps(tmp_path):
    """No duration and no position are `null`, never a number standing in for one."""
    rc = run_watch(tmp_path, play(SHOW.format(3), None)[:1])
    assert rc == 0
    row = sessions.read()[0]
    assert row["duration"] is None
    assert row["max_pos"] is None
    assert row["samples"] == 0


def test_ctrl_c_records_the_session_being_watched(tmp_path, monkeypatch):
    """Losing the file you were watching because you pressed Ctrl-C is the loss to prevent."""
    stream = MpvSocket.events

    def then_ctrl_c(self):
        yield from stream(self)
        raise KeyboardInterrupt

    monkeypatch.setattr(MpvSocket, "events", then_ctrl_c)
    try:
        rc = run_watch(tmp_path, play(SHOW.format(4), 700.0))
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C escaped `watch` instead of ending it")
    assert rc == 0
    assert [r["max_pos"] for r in sessions.read()] == [700.0]


def test_every_session_is_logged_with_its_row_and_whether_it_was_written(tmp_path, monkeypatch):
    """A replay that adds no progress is deduplicated: logged, but not written."""
    monkeypatch.setenv("SCROBD_LOG", "1")
    rc = run_watch(tmp_path, play(SHOW.format(1), 1300.0) + play(SHOW.format(2), 420.0)
                   + play(SHOW.format(1), 600.0))
    assert rc == 0
    logged = [json.loads(line) for line in (state_dir() / "scrobd.jsonl").read_text().splitlines()]
    seen = [e for e in logged if e["event"] == "session"]
    assert [e["written"] for e in seen] == [True, True, False]
    rows = sessions.read()
    assert len(rows) == 2
    assert {k: v for k, v in seen[0].items() if k not in {"event", "written"}} == rows[0]


def test_a_session_log_that_cannot_be_written_stops_the_watch_and_keeps_the_row(
        tmp_path, capsys):
    """The row goes to stderr, so the session survives the failed write."""
    (state_dir() / "sessions.jsonl").mkdir()          # any write to it raises OSError
    rc = run_watch(tmp_path, play(SHOW.format(5), 1300.0))
    assert rc == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert "sessions.jsonl" in err[0]
    row = json.loads(err[-1])
    assert row["path"] == SHOW.format(5)
    assert row["max_pos"] == 1300.0
    assert row["ids"] == {"tvdb": 99}


def test_the_default_socket_is_mpvsocket_in_the_runtime_dir(tmp_path, monkeypatch):
    """Read when the command runs: the env is set here, long after `cli` was imported."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    start_mpv(tmp_path / "mpvsocket", play(SHOW.format(6), 900.0))
    assert cli.main(["watch"]) == 0
    assert [r["max_pos"] for r in sessions.read()] == [900.0]


@pytest.mark.parametrize("runtime_dir", [None, ""], ids=["unset", "empty"])
def test_no_runtime_dir_and_no_socket_refuses_rather_than_guess(runtime_dir, monkeypatch,
                                                                capsys):
    """No fallback to /tmp: a shared path another user can bind is the wrong place to watch."""
    if runtime_dir is None:
        monkeypatch.delenv("XDG_RUNTIME_DIR")
    else:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime_dir)

    def connect(self):
        pytest.fail(f"watch guessed a socket and connected to {self.path}")

    monkeypatch.setattr(MpvSocket, "connect", connect)
    assert cli.main(["watch"]) == 1
    err = capsys.readouterr().err
    assert "XDG_RUNTIME_DIR is unset" in err
    assert "--socket" in err


def test_the_help_names_the_default_socket(capsys):
    with pytest.raises(SystemExit):
        cli.main(["watch", "--help"])
    assert "$XDG_RUNTIME_DIR/mpvsocket" in capsys.readouterr().out


STOP_SIGNALS = [signal.SIGINT]
STOP_IDS = ["SIGINT"]


def watch_through(tmp_path, lines):
    """Run `run_watch`; fail the test, rather than abort pytest, if an interrupt escapes."""
    try:
        return run_watch(tmp_path, lines)
    except KeyboardInterrupt:
        pytest.fail("an interrupt escaped `watch` instead of ending it")


def signal_while_resolving(monkeypatch, sig, path):
    """Send *sig* while *path* is being recorded: taken and resolved, not yet written."""
    real = cli.resolve_one

    def resolve_then_signal(p):
        r = real(p)
        if p == path:
            signal.raise_signal(sig)
        return r

    monkeypatch.setattr(cli, "resolve_one", resolve_then_signal)


@pytest.mark.parametrize("sig", STOP_SIGNALS, ids=STOP_IDS)
def test_a_signal_while_a_session_is_being_recorded_waits_for_the_write(
        tmp_path, monkeypatch, sig):
    """File 1 is in hand -- taken when file 2 opened -- so it is written before the watch stops."""
    signal_while_resolving(monkeypatch, sig, SHOW.format(1))
    rc = watch_through(tmp_path, play(SHOW.format(1), 1300.0) + play(SHOW.format(2), 420.0))
    assert rc == 0
    # The watch then stops at once, before file 2's position is read: file 2 is
    # recorded as far as it got. Reading on to 420 would mean the signal was ignored.
    assert [(r["episode"], r["max_pos"]) for r in sessions.read()] == [(1, 1300.0), (2, None)]


def test_a_signal_while_the_final_session_is_being_recorded_waits_for_the_write(
        tmp_path, monkeypatch):
    """A second Ctrl-C, landing on the last record of the watch, must not cost that record."""
    signal_while_resolving(monkeypatch, signal.SIGINT, SHOW.format(9))
    assert watch_through(tmp_path, play(SHOW.format(9), 500.0)) == 0
    assert [r["max_pos"] for r in sessions.read()] == [500.0]


@pytest.mark.parametrize("sig", STOP_SIGNALS, ids=STOP_IDS)
def test_a_signal_while_waiting_for_mpv_records_the_file_being_watched(
        tmp_path, monkeypatch, sig):
    """A paused film sends nothing, so the signal is the only thing that can end this wait."""
    stream = MpvSocket.events

    def then_signal(self):
        events = stream(self)
        yield next(events)          # the file
        yield next(events)          # its position; then nothing, as when mpv is paused
        signal.raise_signal(sig)
        pytest.fail(f"{sig.name} did not interrupt the wait for mpv")

    monkeypatch.setattr(MpvSocket, "events", then_signal)
    rc = watch_through(tmp_path, play(SHOW.format(7), 900.0))
    assert rc == 0
    assert [r["max_pos"] for r in sessions.read()] == [900.0]


def test_a_watch_puts_the_previous_signal_handlers_back(tmp_path):
    """The handlers are the watch's only while it runs; any other command keeps its own."""
    before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
    assert run_watch(tmp_path, play(SHOW.format(8), 60.0)) == 0
    assert {sig: signal.getsignal(sig) for sig in STOP_SIGNALS} == before


def test_a_subscription_that_fails_closes_the_socket(tmp_path, monkeypatch, capsys):
    """`connect` can fail after the socket is open: mpv exiting mid-subscribe."""
    opened = []

    def mpv_exits_while_subscribing(self, _payloads):
        opened.append(self._sock)
        msg = f"{self.path} closed while subscribing -- did mpv just exit?"
        raise MpvUnavailableError(msg)

    monkeypatch.setattr(MpvSocket, "_send", mpv_exits_while_subscribing)
    assert run_watch(tmp_path, []) == 1
    assert "closed while subscribing" in capsys.readouterr().err
    assert opened[0].fileno() == -1         # closed, not left for the garbage collector
