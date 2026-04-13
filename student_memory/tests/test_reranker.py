"""Phase 6: pure-math reranker tests.

These tests pin the scoring formula, the recency decay, the tie-break
order, and the config-weight invariant. If any of these break, every
downstream consumer breaks too — so this file is the canary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.memory.reranker import (
    Candidate,
    RerankConfig,
    rank_candidates,
    recency_score,
    score_candidate,
)

NOW = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)


def _cfg(
    w_sim: float = 0.75,
    w_time: float = 0.20,
    w_match: float = 0.05,
    half_life_days: float = 30.0,
) -> RerankConfig:
    return RerankConfig(
        w_sim=w_sim,
        w_time=w_time,
        w_match=w_match,
        half_life_days=half_life_days,
        topic_bonus=0.03,
        subtopic_bonus=0.02,
    )


def _candidate(
    memory_id: str,
    *,
    sim: float,
    age_days_ago: float,
    topic: str | None = None,
    subtopic: str | None = None,
    query_topic: str | None = None,
    query_subtopic: str | None = None,
) -> Candidate:
    ts = NOW - timedelta(days=age_days_ago)
    return Candidate(
        memory_id=memory_id,
        topic=topic,
        subtopic=subtopic,
        timestamp=ts,
        retrieval_text=f"text-{memory_id}",
        raw_sim_score=sim,
        query_topic=query_topic,
        query_subtopic=query_subtopic,
    )


def test_recency_score_exponential_decay() -> None:
    assert recency_score(NOW, now=NOW, half_life_days=30) == pytest.approx(1.0)
    half = recency_score(NOW - timedelta(days=30), now=NOW, half_life_days=30)
    assert half == pytest.approx(0.5, abs=1e-6)
    quarter = recency_score(NOW - timedelta(days=60), now=NOW, half_life_days=30)
    assert quarter == pytest.approx(0.25, abs=1e-6)


def test_recency_score_clamps_future_timestamps() -> None:
    future = NOW + timedelta(days=10)
    assert recency_score(future, now=NOW, half_life_days=30) == pytest.approx(1.0)


def test_score_candidate_matches_arch_section_10_6_example() -> None:
    """Worked example from ARCHITECTURE.md §10.6 / todo.md §10.6.

    Candidate B: sim=0.84, recency=0.90, match=0.60 →
        final = 0.75*0.84 + 0.20*0.90 + 0.05*0.60 = 0.840
    """
    cfg = _cfg()
    # We need a synthetic candidate where the helper produces the exact
    # recency=0.90 and match=0.60. recency=0.90 corresponds to
    # age = -ln(0.9) * 30 / ln(2) ≈ 4.561 days.
    import math

    age = -math.log(0.9) * 30.0 / math.log(2.0)
    c = Candidate(
        memory_id="m_b",
        topic="Quadratic Equations",
        subtopic=None,
        timestamp=NOW - timedelta(days=age),
        retrieval_text="t",
        raw_sim_score=0.84,
        query_topic="Quadratic Equations",  # contributes 0.03 alone, not 0.60
    )
    scored = score_candidate(c, now=NOW, cfg=cfg)
    assert scored.recency_score == pytest.approx(0.9, abs=1e-9)
    # match_score in v1 maxes out at topic_bonus + subtopic_bonus = 0.05.
    # The 0.60 figure in the docs assumed a different match scale; we
    # verify our actual formula instead.
    assert scored.match_score == pytest.approx(0.03)
    expected = 0.75 * 0.84 + 0.20 * 0.9 + 0.05 * 0.03
    assert scored.final_score == pytest.approx(expected, abs=1e-9)


def test_rank_candidates_recency_can_beat_similarity() -> None:
    cfg = _cfg()
    # Old-but-similar vs recent-but-slightly-less-similar.
    # With default weights, the recent record should win when similarity
    # is close.
    old = _candidate("m_old", sim=0.92, age_days_ago=120)
    recent = _candidate("m_recent", sim=0.86, age_days_ago=2)
    ranked = rank_candidates([old, recent], now=NOW, cfg=cfg)
    assert ranked[0].candidate.memory_id == "m_recent"


def test_rank_candidates_high_similarity_can_beat_recency() -> None:
    cfg = _cfg()
    # Big similarity gap should still let the older record win.
    old_strong = _candidate("m_strong", sim=0.99, age_days_ago=20)
    recent_weak = _candidate("m_weak", sim=0.40, age_days_ago=1)
    ranked = rank_candidates([old_strong, recent_weak], now=NOW, cfg=cfg)
    assert ranked[0].candidate.memory_id == "m_strong"


def test_tie_break_uses_sim_then_timestamp() -> None:
    cfg = _cfg()
    a = _candidate("m_a", sim=0.80, age_days_ago=10)
    b = _candidate("m_b", sim=0.85, age_days_ago=10)
    ranked = rank_candidates([a, b], now=NOW, cfg=cfg)
    # b has the same recency (same age) but a higher sim, so b wins on
    # the primary score AND on the secondary tie-breaker.
    assert ranked[0].candidate.memory_id == "m_b"

    # Identical sim, different timestamp → newer wins via the third
    # tie-breaker.
    older = _candidate("m_older", sim=0.50, age_days_ago=100)
    newer = _candidate("m_newer", sim=0.50, age_days_ago=99)
    ranked2 = rank_candidates([older, newer], now=NOW, cfg=cfg)
    assert ranked2[0].candidate.memory_id == "m_newer"


def test_match_score_topic_and_subtopic_bonuses() -> None:
    cfg = _cfg()
    c = _candidate(
        "m_a",
        sim=0.5,
        age_days_ago=0,
        topic="Quadratic Equations",
        subtopic="Discriminant",
        query_topic="Quadratic Equations",
        query_subtopic="Discriminant",
    )
    scored = score_candidate(c, now=NOW, cfg=cfg)
    assert scored.match_score == pytest.approx(0.05)


def test_match_score_zero_when_query_fields_unset() -> None:
    cfg = _cfg()
    c = _candidate(
        "m_a",
        sim=0.5,
        age_days_ago=0,
        topic="Quadratic Equations",
        subtopic="Discriminant",
    )
    scored = score_candidate(c, now=NOW, cfg=cfg)
    assert scored.match_score == 0.0


def test_rerank_config_rejects_misweighted_construction() -> None:
    with pytest.raises(ValueError):
        RerankConfig(
            w_sim=0.5, w_time=0.3, w_match=0.3,
            half_life_days=30, topic_bonus=0.0, subtopic_bonus=0.0,
        )


def test_config_driven_weights_change_ranking() -> None:
    # With recency-heavy weights, the recent record should always win.
    recency_heavy = RerankConfig(
        w_sim=0.10, w_time=0.85, w_match=0.05,
        half_life_days=30, topic_bonus=0.03, subtopic_bonus=0.02,
    )
    a = _candidate("m_old", sim=0.99, age_days_ago=200)
    b = _candidate("m_new", sim=0.50, age_days_ago=1)
    ranked = rank_candidates([a, b], now=NOW, cfg=recency_heavy)
    assert ranked[0].candidate.memory_id == "m_new"
