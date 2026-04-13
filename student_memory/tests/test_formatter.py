"""Phase 6: context formatter unit tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.memory.formatter import EMPTY_CONTEXT, format_context
from app.memory.reranker import Candidate, ScoredCandidate

NOW = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)


def _scored(memory_id: str, text: str) -> ScoredCandidate:
    c = Candidate(
        memory_id=memory_id,
        topic=None,
        subtopic=None,
        timestamp=NOW,
        retrieval_text=text,
        raw_sim_score=0.9,
    )
    return ScoredCandidate(
        candidate=c,
        sim_score=0.9,
        recency_score=1.0,
        match_score=0.0,
        final_score=0.875,
    )


def test_format_context_empty_returns_fallback() -> None:
    assert format_context([]) == EMPTY_CONTEXT


def test_format_context_lists_results_in_order() -> None:
    results = [_scored("m_a", "first record"), _scored("m_b", "second record")]
    out = format_context(results)
    assert out.startswith("Relevant past learning records:")
    assert "1. first record" in out
    assert "2. second record" in out
    assert out.index("1. first record") < out.index("2. second record")


def test_format_context_is_deterministic() -> None:
    results = [_scored("m_a", "x"), _scored("m_b", "y")]
    a = format_context(results)
    b = format_context(results)
    assert a == b
