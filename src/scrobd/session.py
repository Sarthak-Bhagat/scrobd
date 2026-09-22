"""One record per file played, built from a stream of mpv property changes.

Sessions, not thresholds. The record carries start, end and the furthest
position reached; whether that counts as "watched" is derived at read time and
never stored. A 30% abandon is real data -- no sink can hold it, this log can.

Pure: no socket, no clock. `now` is supplied by the caller, so tests drive time
directly instead of sleeping.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    """One continuous play of one file."""

    path: str
    started_at: float
    ended_at: float
    duration: float | None
    max_pos: float | None
    samples: int


class Accumulator:
    """Folds property-change events into one Session per file."""

    def __init__(self) -> None:
        """Start with nothing playing and nothing waiting to be taken."""
        self._path: str | None = None
        self._started: float = 0.0
        self._last: float = 0.0
        self._duration: float | None = None
        self._max_pos: float | None = None
        self._samples: int = 0
        self._done: Session | None = None

    def feed(self, name: str, data: object, now: float) -> None:
        """Absorb one property change.

        Only `path` can end a session. Everything else -- `pause` above all --
        is absorbed into the open one: pausing for an hour mid-film is the most
        ordinary thing that happens, and must not split one watch into two.
        """
        if name == "path":
            # mpv reports `path` as null when it goes idle between files. That
            # is not a new file, so it does not open one; the session stays
            # open until a real path arrives or the caller calls `finish`.
            if isinstance(data, str) and data != self._path:
                self._close(now)
                self._open(data, now)
            return
        if self._path is None:
            return
        self._last = now
        if name == "duration" and isinstance(data, int | float):
            self._duration = float(data)
        elif name == "time-pos" and isinstance(data, int | float):
            pos = float(data)
            self._samples += 1
            # Monotonic on purpose. Watching to 90% and rewinding to rewatch a
            # scene is still a 90% watch, so a later, lower position never
            # overwrites a higher earlier one.
            if self._max_pos is None or pos > self._max_pos:
                self._max_pos = pos

    def finish(self, now: float) -> None:
        """End the current session, if any."""
        self._close(now)

    def take(self) -> Session | None:
        """Return the most recently finished session, once.

        One slot, not a queue: the caller is expected to take after every
        `feed` and `finish`, which is the only way at most one session can
        close between takes. A caller that batches instead would lose the
        earlier of two.
        """
        done, self._done = self._done, None
        return done

    def _open(self, path: str, now: float) -> None:
        """Begin a session for *path*, discarding any leftover state."""
        self._path, self._started, self._last = path, now, now
        self._duration = self._max_pos = None
        self._samples = 0

    def _close(self, now: float) -> None:
        """Freeze the open session, if there is one, into the take slot."""
        if self._path is None:
            return
        # `max_pos` and `duration` stay None when mpv never reported them.
        # A missing number is a gap the reader can handle; a guessed one is a
        # lie in a watch history. Nothing here derives "watched".
        self._done = Session(path=self._path, started_at=self._started,
                             ended_at=max(now, self._last), duration=self._duration,
                             max_pos=self._max_pos, samples=self._samples)
        self._path = None
