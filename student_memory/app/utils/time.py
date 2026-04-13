"""Timestamp parsing and recency helpers.

All times in this service are timezone-aware and stored as ISO-8601 UTC
strings in SQLite. ``parse_timestamp`` returns ``None`` for unparseable
input so the retriever can drop those records via the pre-filter (see
``ARCHITECTURE.md`` §14).
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into a tz-aware UTC ``datetime``.

    Accepts the trailing ``Z`` suffix as a synonym for ``+00:00``. Naive
    timestamps are interpreted as UTC. Returns ``None`` for empty or
    unparseable input — callers should treat that as a recoverable
    pre-filter drop, not a crash.
    """
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_timestamp(dt: datetime) -> str:
    """Format a tz-aware ``datetime`` as ISO-8601 UTC with a trailing ``Z``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_days(ts: datetime, *, now: datetime) -> float:
    """Compute age in days, clamped to ``>= 0``.

    Clock skew or future-dated records must not blow up the recency
    decay formula in the reranker. ``CLAUDE.md`` §11 mandates this clamp.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now - ts).total_seconds() / 86400.0
    return max(delta, 0.0)
