"""Turns a momentary presence signal (e.g. PIR's `presence` reading, which
drops back to 0 a few seconds after motion stops) into a debounced
"activated" state that stays true for a while after the last detection, so
the installation doesn't flicker between idle/activated between individual
PIR triggers."""


class ActivationTracker:
    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout
        self._last_active_at = None

    def update(self, presence: bool, now: float) -> bool:
        """Call once per tick with the latest raw presence reading and the
        current time. Returns True if presence was seen within `timeout`
        seconds, False once quiet for longer than that."""
        if presence:
            self._last_active_at = now
        return self._last_active_at is not None and (now - self._last_active_at) < self.timeout
