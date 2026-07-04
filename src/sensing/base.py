from abc import ABC, abstractmethod


class Sensor(ABC):
    def __init__(self):
        self.healthy = True
        self.last_error = None

    @abstractmethod
    def read(self) -> dict:
        """Return a dict of normalised readings, each value clamped to a
        usable/acceptable range (e.g. 0..1 for levels). Keys must be unique
        across all sensors in use, since main.py merges every sensor's dict
        together with `readings.update(...)`.

        If a live hardware read fails after construction already succeeded
        (e.g. a camera disconnecting mid-run), catch it here and fall back
        to this sensor's own mock reading — call self._mark_failed(exc)
        first so the failure is recorded for a future health/error display,
        then self._mark_ok() on the next successful real read."""

    def _mark_failed(self, exc: Exception) -> None:
        """Record that this sensor's real hardware read just failed. Only
        prints on the transition into failure, not every tick, so a sensor
        stuck failing doesn't spam the log at 20Hz."""
        if self.healthy:
            print(f"[{type(self).__name__}] read failed, falling back to its mock: {exc}")
        self.healthy = False
        self.last_error = str(exc)

    def _mark_ok(self) -> None:
        """Record that this sensor's real hardware read just succeeded."""
        if not self.healthy:
            print(f"[{type(self).__name__}] recovered")
        self.healthy = True
        self.last_error = None