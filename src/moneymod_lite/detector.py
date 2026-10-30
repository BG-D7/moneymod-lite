"""Working out from successive pings whether the server is restarting."""

from __future__ import annotations

from enum import Enum


class Phase(Enum):
    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"


class Event(Enum):
    WENT_DOWN = "went_down"
    RESTART_FINISHED = "restart_finished"


class RestartDetector:
    """Tracks whether the server is up, ignoring one-off failed pings.

    The server is only considered down after `failures_to_down` failed pings
    in a row, so a single network blip doesn't look like a restart. Once it
    is down, the first successful ping means the restart has finished.
    """

    def __init__(self, failures_to_down: int = 2) -> None:
        if failures_to_down < 1:
            raise ValueError("failures_to_down must be at least 1")
        self.failures_to_down = failures_to_down
        self.phase = Phase.UNKNOWN
        self._failures = 0

    def update(self, reachable: bool) -> Event | None:
        """Record the result of a ping, returning an event if the phase changed."""
        if reachable:
            self._failures = 0
            was_down = self.phase is Phase.DOWN
            self.phase = Phase.UP
            return Event.RESTART_FINISHED if was_down else None

        self._failures += 1
        if self._failures >= self.failures_to_down and self.phase is not Phase.DOWN:
            self.phase = Phase.DOWN
            return Event.WENT_DOWN
        return None
