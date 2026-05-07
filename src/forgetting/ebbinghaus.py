"""Default Ebbinghaus retention curve.

R = exp(-t / S)   where t is days since last_seen and S is "stability"
in days. Stability grows with last_accuracy (you remember mastered
material longer) and with session_count (spaced repetition compounds).
"""
from __future__ import annotations

import math
from datetime import datetime


class EbbinghausV1:
    name = "ebbinghaus_v1"

    def __init__(
        self,
        *,
        base_stability_days: float = 1.0,
        accuracy_boost: float = 4.0,
        repetition_boost: float = 1.0,
    ) -> None:
        self.S0 = base_stability_days
        self.alpha = accuracy_boost
        self.beta = repetition_boost

    def retention(
        self,
        *,
        last_seen: datetime,
        last_accuracy: float | None,
        session_count: int,
        now: datetime,
    ) -> float:
        if last_accuracy is None:
            return 0.0
        days = max((now - last_seen).total_seconds() / 86400.0, 0.0)
        stability = (
            self.S0
            + self.alpha * last_accuracy
            + self.beta * max(session_count - 1, 0)
        )
        if stability <= 0:
            return 0.0
        r = math.exp(-days / stability)
        return max(0.0, min(1.0, r))
