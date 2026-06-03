"""Default Ebbinghaus retention curve.

R = last_accuracy * exp(-t / S)

  t = days since last_seen
  S = stability in days; grows with session_count (spaced repetition)

The multiplicative form is intentional. The traditional Ebbinghaus
exp(-t/S) describes "fraction retained of what was learned". On its own
it returns ~1.0 right after a session even if the student got every
question wrong, which is mathematically correct but not what we want
to display: a student who just answered 0% should see RED, not GREEN.
Multiplying by last_accuracy gives a single number that means "how
well does the student know this NOW", combining both how much they
ever learned and how much has decayed since.
"""

from __future__ import annotations

import math
import os
from datetime import datetime

# Seconds per "day" in the decay formula. Defaults to a real day; set to
# 60 in demo/recording mode so a minute of wall-clock decays like a day.
_TIME_UNIT_SECONDS = float(os.getenv("FORGETTING_TIME_UNIT_SECONDS", "86400"))


class EbbinghausV1:
    name = "ebbinghaus_v1"

    def __init__(
        self,
        *,
        base_stability_days: float = 7.0,
        repetition_boost: float = 7.0,
    ) -> None:
        # Defaults give a one-week half-life for first-time learners and
        # double that per additional session (spaced repetition). Tune
        # these without changing the formula shape.
        self.S0 = base_stability_days
        self.beta = repetition_boost

    def retention(
        self,
        *,
        last_seen: datetime,
        last_accuracy: float | None,
        session_count: int,
        now: datetime,
    ) -> float:
        """Return retention probability in [0, 1].

        last_accuracy is multiplied in directly: 0% accuracy → 0 retention
        regardless of recency. None means "never tested" → 0.

        session_count is the total completed study sessions on this item:
        1 = first study (baseline stability), 2+ = each additional session
        adds beta to stability.

        A future last_seen (clock skew) is treated as t=0.
        """
        if last_accuracy is None:
            return 0.0
        days = max((now - last_seen).total_seconds() / _TIME_UNIT_SECONDS, 0.0)
        stability = self.S0 + self.beta * max(session_count - 1, 0)
        if stability <= 0:
            return 0.0
        r = float(last_accuracy) * math.exp(-days / stability)
        return max(0.0, min(1.0, r))
