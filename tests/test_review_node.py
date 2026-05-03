"""Unit tests for src.agent.nodes.review."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch  # noqa: F401  # used by Tasks 4/5 mock-LLM tests


def _make_item(
    subtopic: str,
    *,
    last_seen_days_ago: float,
    last_accuracy: float,
    session_count: int = 1,
) -> dict:
    """Build a fake mastery item with a relative last_seen_at timestamp."""
    from datetime import datetime, timezone
    seen_at = (datetime.now(timezone.utc) - timedelta(days=last_seen_days_ago))
    return {
        "subtopic": subtopic,
        "topic": "T",
        "last_seen_at": seen_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_count": session_count,
        "total_questions": 4,
        "total_correct": 2,
        "last_accuracy": last_accuracy,
    }


class TestSelectCandidateKps:

    def test_empty_input_returns_empty(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        assert select_candidate_kps([]) == []

    def test_excludes_recently_seen_within_1_day(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item("FreshlyDone", last_seen_days_ago=0.5, last_accuracy=0.6),
            _make_item("OldButGood", last_seen_days_ago=10, last_accuracy=0.6),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert "FreshlyDone" not in names
        assert "OldButGood" in names

    def test_takes_all_when_few_items(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item(f"K{i}", last_seen_days_ago=10, last_accuracy=0.5)
            for i in range(3)
        ]
        assert len(select_candidate_kps(items)) == 3

    def test_caps_at_5_when_many_items(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item(f"K{i}", last_seen_days_ago=10, last_accuracy=0.5)
            for i in range(20)
        ]
        assert len(select_candidate_kps(items)) == 5

    def test_priority_prefers_stale_and_uncertain(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            # Recent + certain (low priority)
            _make_item("Recent", last_seen_days_ago=2, last_accuracy=1.0),
            # Stale + uncertain (highest)
            _make_item("StaleUncertain", last_seen_days_ago=14, last_accuracy=0.5),
            # Stale + certain
            _make_item("StaleCertain", last_seen_days_ago=14, last_accuracy=1.0),
            # Recent + uncertain
            _make_item("RecentUncertain", last_seen_days_ago=2, last_accuracy=0.5),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert names[0] == "StaleUncertain"

    def test_deterministic_for_same_input(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item("A", last_seen_days_ago=10, last_accuracy=0.4),
            _make_item("B", last_seen_days_ago=10, last_accuracy=0.6),
        ]
        first = select_candidate_kps(items)
        second = select_candidate_kps(items)
        assert first == second

    def test_equal_priority_preserves_input_order(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        # Two items with identical staleness and uncertainty → equal priority.
        # Output order must match input order (stable tie-break).
        items = [
            _make_item("First", last_seen_days_ago=10, last_accuracy=0.5),
            _make_item("Second", last_seen_days_ago=10, last_accuracy=0.5),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert names == ["First", "Second"]
