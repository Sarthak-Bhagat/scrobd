"""The mpv IPC client, exercised against a real unix socket."""

import json
import socket
import threading
from pathlib import Path

import pytest

from scrobd.mpv import MpvSocket, MpvUnavailableError


def fake_mpv(path: Path, script: list[dict | str], ready: threading.Event):
    """Speak just enough of mpv's protocol to test against.

    A dict in *script* is sent as one complete JSON line. A str is sent
    verbatim, which is how a line mpv truncated by exiting mid-write is staged.
    """
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    with conn:
        conn.recv(4096)                      # the observe_property commands
        for line in script:
            raw = line if isinstance(line, str) else json.dumps(line) + "\n"
            conn.sendall(raw.encode())
    srv.close()


@pytest.fixture
def mpv_at(tmp_path):
    threads = []

    def start(script):
        path = tmp_path / "mpvsocket"
        ready = threading.Event()
        t = threading.Thread(target=fake_mpv, args=(path, script, ready), daemon=True)
        t.start()
        threads.append(t)
        ready.wait(2)
        return path

    yield start
    for t in threads:
        t.join(2)


def test_yields_property_changes_in_order(mpv_at):
    path = mpv_at([
        {"event": "property-change", "name": "path", "data": "/m/a.mkv"},
        {"event": "property-change", "name": "duration", "data": 1400.0},
        {"event": "property-change", "name": "time-pos", "data": 12.5},
    ])
    with MpvSocket(str(path)) as m:
        got = [(e["name"], e["data"]) for e in m.events()]
    assert got == [("path", "/m/a.mkv"), ("duration", 1400.0), ("time-pos", 12.5)]


def test_ignores_non_property_events(mpv_at):
    path = mpv_at([
        {"event": "seek"},
        {"request_id": 0, "error": "success"},
        {"event": "property-change", "name": "pause", "data": True},
    ])
    with MpvSocket(str(path)) as m:
        got = [e["name"] for e in m.events()]
    assert got == ["pause"]


def test_a_socket_with_nothing_listening_raises_loudly(tmp_path):
    """mpv-discord steals --input-ipc-server and leaves the path on disk.

    mediactl lost a whole session's watch data to exactly this, silently.
    """
    dead = tmp_path / "mpvsocket"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(dead))
    s.close()                                # file exists, nobody is listening
    with pytest.raises(MpvUnavailableError, match="nothing listening"):
        MpvSocket(str(dead)).connect()


def test_a_missing_socket_raises_loudly(tmp_path):
    with pytest.raises(MpvUnavailableError, match="no socket"):
        MpvSocket(str(tmp_path / "absent")).connect()


def test_a_truncated_line_does_not_crash_the_stream(mpv_at):
    """A final partial line is dropped, not parsed -- mpv can close mid-write."""
    path = mpv_at([
        {"event": "property-change", "name": "path", "data": "/m/a.mkv"},
        '{"event": "property-change", "name": "durat',     # no newline: mpv exited here
    ])
    with MpvSocket(str(path)) as m:
        got = list(m.events())
    assert [e["name"] for e in got] == ["path"]


def test_one_malformed_line_does_not_end_the_stream(mpv_at):
    """A bad line costs that line, never the rest of the session."""
    path = mpv_at([
        {"event": "property-change", "name": "path", "data": "/m/a.mkv"},
        "{not json\n",
        "\n",
        {"event": "property-change", "name": "time-pos", "data": 12.5},
    ])
    with MpvSocket(str(path)) as m:
        got = [e["name"] for e in m.events()]
    assert got == ["path", "time-pos"]
