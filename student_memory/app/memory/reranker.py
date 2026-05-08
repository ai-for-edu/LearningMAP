"""Pure-math reranking layer.

Similarity normalization (PINNED — do not change without updating every
test that depends on it):

    sim_score = 1 - cosine_distance

where ``cosine_distance`` is what ``hnswlib.Index(space='cosine').knn_query``
returns. Because the ``Embedder`` ABC mandates L2-normalized output,
``1 - cosine_distance`` equals the cosine similarity in ``[-1, 1]``, and
in practice falls in ``[0, 1]`` for bge-family models. The reranker does
NOT clamp the value — preserving the raw number makes debugging easier
if a weird negative ever leaks through.

Final score formula (``ARCHITECTURE.md`` §10.2 / ``todo.md`` §10):

    final_score = w_sim * sim_score
                + w_time * recency_score
                + w_match * match_score

with ``recency_score = exp(-ln(2) * age_days / half_life_days)``.

Tie-break order: ``final_score`` desc, ``sim_score`` desc, ``timestamp`` desc.

This module is pure math: no SQL, no embeddings, no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.config import Settings
from app.utils.time import age_days


@dataclass(frozen=True)
class RerankConfig:
    w_sim: float
    w_time: float
    w_match: float
    half_life_days: float
    topic_bonus: float
    subtopic_bonus: float

    def __post_init__(self) -> None:
        total = self.w_sim + self.w_time + self.w_match
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"w_sim + w_time + w_match must equal 1.0, got {total:.6f}"
            )
        if self.half_life_days <= 0:
            raise ValueError(f"half_life_days must be > 0, got {self.half_life_days}")

    @classmethod
    def from_settings(cls, s: Settings) -> "RerankConfig":
        return cls(
            w_sim=s.w_sim,
            w_time=s.w_time,
            w_match=s.w_match,
            half_life_days=s.half_life_days,
            topic_bonus=s.topic_bonus,
            subtopic_bonus=s.subtopic_bonus,
        )


@dataclass(frozen=True)
class Candidate:
    memory_id: str
    topic: str | None
    subtopic: str | None
    timestamp: datetime
    retrieval_text: str
    raw_sim_score: float  # already 1 - cosine_distance from HnswIndex
    query_topic: str | None = None
    query_subtopic: str | None = None


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    sim_score: float
    recency_score: float
    match_score: float
    final_score: float


def recency_score(ts: datetime, *, now: datetime, half_life_days: float) -> float:
    """Exponential decay with the configured half-life."""
    age = age_days(ts, now=now)
    return math.exp(-math.log(2.0) * age / half_life_days)


def match_score(c: Candidate, *, cfg: RerankConfig) -> float:
    """Optional structured topic/subtopic alignment bonus.

    v1 leaves ``query_topic`` / ``query_subtopic`` ``None`` unless the
    caller supplies them, in which case the bonus contributes
    ``topic_bonus + subtopic_bonus``.
    """
    bonus = 0.0
    if c.query_topic and c.topic and c.query_topic == c.topic:
        bonus += cfg.topic_bonus
    if c.query_subtopic and c.subtopic and c.query_subtopic == c.subtopic:
        bonus += cfg.subtopic_bonus
    return bonus


def score_candidate(
    c: Candidate, *, now: datetime, cfg: RerankConfig
) -> ScoredCandidate:
    sim = c.raw_sim_score
    rec = recency_score(c.timestamp, now=now, half_life_days=cfg.half_life_days)
    mat = match_score(c, cfg=cfg)
    final = cfg.w_sim * sim + cfg.w_time * rec + cfg.w_match * mat
    return ScoredCandidate(
        candidate=c,
        sim_score=sim,
        recency_score=rec,
        match_score=mat,
        final_score=final,
    )


def rank_candidates(
    candidates: Sequence[Candidate], *, now: datetime, cfg: RerankConfig
) -> list[ScoredCandidate]:
    scored = [score_candidate(c, now=now, cfg=cfg) for c in candidates]
    scored.sort(
        key=lambda s: (
            -s.final_score,
            -s.sim_score,
            -s.candidate.timestamp.timestamp(),
        ),
    )
    return scored
