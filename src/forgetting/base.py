"""Forgetting model contract.

A ForgettingModel takes a (timestamp, accuracy, repetition count) tuple
and returns a retention score in [0, 1]. Implementations are pure functions
on their input — no I/O, no caching — so they're safe to call per-node.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ForgettingModel(Protocol):
    name: str

    def retention(
        self,
        *,
        last_seen: datetime,
        last_accuracy: float | None,
        session_count: int,
        now: datetime,
    ) -> float:
        """Return retention probability in [0, 1].

        last_accuracy is None means the subtopic was never tested,
        which the implementation should treat as zero retention.
        """
        ...
