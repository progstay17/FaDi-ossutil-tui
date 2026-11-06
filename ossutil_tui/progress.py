"""Speed and ETA computed by the TUI itself.

ossutil reports average speed since start, which is too slow to react to
stalls, so instantaneous speed is derived from byte deltas and smoothed.
"""
from __future__ import annotations


class SpeedTracker:
    def __init__(self, total_bytes: int, alpha: float = 0.3) -> None:
        if total_bytes <= 0:
            raise ValueError("total_bytes must be positive")
        self.total = total_bytes
        self._alpha = alpha
        self._done = 0
        self._last_time: float | None = None
        self._speed: float | None = None

    def update(self, percent: float, now: float) -> None:
        done = min(self.total, int(self.total * percent / 100))
        if self._last_time is not None and now > self._last_time:
            instant = (done - self._done) / (now - self._last_time)
            if instant >= 0:
                if self._speed is None:
                    self._speed = instant
                else:
                    self._speed = self._alpha * instant + (1 - self._alpha) * self._speed
        self._done = done
        self._last_time = now

    @property
    def done_bytes(self) -> int:
        return self._done

    @property
    def speed(self) -> float | None:
        return self._speed

    def eta_seconds(self) -> float | None:
        if not self._speed or self._speed <= 0:
            return None
        return (self.total - self._done) / self._speed