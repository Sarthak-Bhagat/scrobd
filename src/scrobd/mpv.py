"""A minimal client for mpv's JSON IPC.

Newline-delimited JSON over a unix socket. We only ever read: observe a few
properties, then consume the property-change events mpv pushes. No playback
control, so there is nothing here that can disturb what is playing.

`/tmp/mpvsocket` comes from `input-ipc-server` in mpv.conf, which is a single
global option -- any user script setting it takes the channel over. mpv-discord
does this, leaving the path on disk with nothing behind it. mediactl lost a
session's watch data to that failure presenting as silence, so it raises here.
"""

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Self

WATCHED_PROPERTIES = ("path", "duration", "time-pos", "pause", "eof-reached")


class MpvUnavailableError(Exception):
    """mpv could not be reached. Always carries why."""


class MpvSocket:
    """Read-only view of a running mpv instance."""

    def __init__(self, path: str, *, timeout: float = 5.0) -> None:
        """Point at the IPC socket at *path* without opening it.

        *timeout* bounds `connect` only. Reading is left blocking on purpose: a
        paused film reports nothing for as long as it stays paused, and a read
        timeout would end the event stream -- and with it the session -- while
        the file is still open. `events` ends when mpv closes the socket; the
        caller ends it with Ctrl-C.
        """
        self.path = path
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        """Open the socket and subscribe. Raise MpvUnavailableError naming the cause."""
        if not Path(self.path).exists():
            msg = f"no socket at {self.path} -- is mpv running with --input-ipc-server?"
            raise MpvUnavailableError(msg)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
        except OSError as exc:
            # ConnectionRefusedError -- a socket file with nothing behind it --
            # is the case this module exists for, and is itself an OSError, so
            # one clause covers it. The message carries `exc` rather than
            # guessing which errno arrived.
            sock.close()
            msg = (f"{self.path} exists but nothing listening ({exc}) -- a user script "
                   f"may have taken --input-ipc-server; mpv-discord does this")
            raise MpvUnavailableError(msg) from exc
        sock.settimeout(None)       # reads block; see __init__ for why
        self._sock = sock
        # One write, not one per property. A subscription half-sent because mpv
        # exited mid-loop would leave us observing some properties and not
        # others, and a session missing its positions is the silent
        # under-reporting this whole module is here to prevent.
        self._send([{"command": ["observe_property", i, prop]}
                    for i, prop in enumerate(WATCHED_PROPERTIES, start=1)])

    def _send(self, payloads: list[dict]) -> None:
        """Write *payloads* to mpv as newline-delimited JSON, in one call."""
        if self._sock is None:
            msg = "not connected"
            raise MpvUnavailableError(msg)
        blob = "".join(json.dumps(p) + "\n" for p in payloads).encode()
        try:
            self._sock.sendall(blob)
        except OSError as exc:
            # mpv can go away between `connect` returning and the subscription
            # landing. That must reach the caller as this exception like every
            # other unreachable-mpv case, not as a raw BrokenPipeError.
            msg = f"{self.path} closed while subscribing ({exc}) -- did mpv just exit?"
            raise MpvUnavailableError(msg) from exc

    def events(self) -> Iterator[dict]:
        """Yield property-change events until mpv closes the socket."""
        if self._sock is None:
            self.connect()
        sock = self._sock
        buf = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                return                                # closed under us; keep what we have
            if not chunk:
                return                                # mpv exited; drop any partial line
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue                          # never let one bad line end the stream
                if msg.get("event") == "property-change" and "name" in msg:
                    yield msg

    def close(self) -> None:
        """Close the socket. Safe on one never opened, and safe to call twice."""
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> Self:
        """Connect on entry, so an unreachable mpv is raised at the `with`, not later."""
        self.connect()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        """Close the socket, whatever happened inside the block."""
        self.close()
