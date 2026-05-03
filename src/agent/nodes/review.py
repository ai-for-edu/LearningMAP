"""Review-flow agent helpers (PR-2).

Pure ranking + template assembly + LLM-bearing question generation +
deterministic grading. Exposed via the API layer (src.api.chat),
not via the LangGraph state machine — review is a session-side-channel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _parse_iso_z(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _staleness(last_seen_at: str, *, now: datetime | None = None) -> float:
    """0 (just seen) → 1 (>= 14 days old). Clamped both ends."""
    now = now or datetime.now(timezone.utc)
    age_days = (now - _parse_iso_z(last_seen_at)).total_seconds() / 86400.0
    return max(0.0, min(age_days / 14.0, 1.0))


def _uncertainty(last_accuracy: float | None) -> float:
    """1 at acc=0.5; 0 at acc=0 or 1."""
    if last_accuracy is None:
        return 0.5
    return 1.0 - abs(last_accuracy - 0.5) * 2


def select_candidate_kps(
    items: list[dict],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Pick 3-5 KPs to review based on staleness + uncertainty.

    Excludes items seen within 1 day (just-learned, no value to retest).
    Sorts by priority = 0.6 * staleness + 0.4 * uncertainty descending.
    Cap: <=5 items → all; 6-10 → 4; >10 → 5.
    Tie-break is stable by input order, so output is deterministic.
    """
    now = now or datetime.now(timezone.utc)
    one_day = 86400.0

    eligible = []
    for item in items:
        try:
            seen = _parse_iso_z(item["last_seen_at"])
        except (KeyError, ValueError):
            continue
        if (now - seen).total_seconds() < one_day:
            continue
        eligible.append(item)

    scored = [
        (
            0.6 * _staleness(item["last_seen_at"], now=now)
            + 0.4 * _uncertainty(item.get("last_accuracy")),
            idx,
            item,
        )
        for idx, item in enumerate(eligible)
    ]
    # Sort by (-priority, idx) so equal priorities preserve input order.
    scored.sort(key=lambda t: (-t[0], t[1]))

    n = len(scored)
    if n <= 5:
        cap = n
    elif n <= 10:
        cap = 4
    else:
        cap = 5
    return [item for _, _, item in scored[:cap]]
