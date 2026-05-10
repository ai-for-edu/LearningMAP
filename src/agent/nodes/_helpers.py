"""Shared helpers for agent nodes (PR-4)."""
from __future__ import annotations

from src.agent.state import LearningState


def memory_snapshot_block(state: LearningState | None) -> str:
    """Return the rendered memory snapshot block, or empty string.

    Empty when state is None / lacks the field / value is None / empty /
    whitespace — covering env=off (PR-3's _ensure_memory_snapshot is a
    no-op), fetch failure (field is ""), old sessions (field absent),
    and helper functions in lesson.py that accept state defaulting to
    None. Non-empty: returns a string with a leading double-newline so
    callers can append directly to a system prompt or drop into a Jinja
    template at the end. Env state is NOT checked here — it's encoded
    by PR-3 in the state field's value.
    """
    if not state:
        return ""
    snap = (state.get("memory_snapshot") or "").strip()
    if not snap:
        return ""
    return f"\n\n{snap}"
