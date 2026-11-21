from __future__ import annotations

import time


class CooldownLimiter:
    def __init__(self, cooldown_seconds: float) -> None:
        self._cooldown = cooldown_seconds
        self._timestamps: dict[int, float] = {}

    def check(self, user_id: int) -> float:
        if self._cooldown <= 0:
            return 0.0
        now = time.monotonic()
        last = self._timestamps.get(user_id)
        if last is not None:
            remaining = self._cooldown - (now - last)
            if remaining > 0:
                return remaining

        self._timestamps[user_id] = now
        if len(self._timestamps) > 2048:
            cutoff = now - self._cooldown * 2
            self._timestamps = {
                uid: ts for uid, ts in self._timestamps.items() if ts >= cutoff
            }
        return 0.0