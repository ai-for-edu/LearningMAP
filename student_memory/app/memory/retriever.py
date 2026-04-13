"""End-to-end retrieval orchestrator.

Glues the embedder, the hnsw index, the SQLite repo, the reranker, and
the context formatter together. The contract is the public surface of
the service:

- always returns BOTH ``results[]`` and ``context_text``
- never raises on empty index, no-match, or empty database — those are
  recoverable conditions per ``ARCHITECTURE.md`` §14
- ``now_fn`` injection makes recency tests deterministic
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.core.logging import get_logger
from app.db.sqlite_repo import MemoryRecord, SqliteRepo
from app.embedding.base import Embedder
from app.index.hnsw_index import HnswIndex
from app.memory.formatter import format_context
from app.memory.reranker import (
    Candidate,
    RerankConfig,
    ScoredCandidate,
    rank_candidates,
)
from app.utils.time import parse_timestamp, utcnow

logger = get_logger(__name__)


@dataclass(frozen=True)
class RetrieveResult:
    memory_id: str
    topic: str | None
    subtopic: str | None
    timestamp: str
    sim_score: float
    recency_score: float
    match_score: float
    final_score: float
    retrieval_text: str


@dataclass(frozen=True)
class RetrievalResponse:
    query: str
    results: list[RetrieveResult]
    context_text: str


class Retriever:
    def __init__(
        self,
        repo: SqliteRepo,
        embedder: Embedder,
        index: HnswIndex,
        cfg: RerankConfig,
        *,
        recall_k: int,
        top_k_default: int,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._index = index
        self._cfg = cfg
        self._recall_k = recall_k
        self._top_k_default = top_k_default
        self._now_fn = now_fn

    # ------------------------------------------------------------------
    def retrieve(
        self,
        user_id: str,
        query: str,
        top_k: int | None = None,
    ) -> RetrievalResponse:
        if not user_id:
            raise ValueError("user_id is required")
        if not query or not query.strip():
            raise ValueError("query is required")
        k = top_k if top_k is not None else self._top_k_default
        if k <= 0:
            raise ValueError(f"top_k must be > 0, got {k}")

        # Empty index / empty DB short-circuit. Returning a stable empty
        # response is the contract — the demo-side prompt splicer must
        # not have to special-case "no results".
        if self._index.count == 0 or self._repo.count_memories() == 0:
            return RetrievalResponse(
                query=query,
                results=[],
                context_text=format_context([]),
            )

        # 1. embed query
        query_vec = self._embedder.encode([query])

        # 2. recall_k vector neighbors
        recall = self._index.search(query_vec, k=self._recall_k)
        if not recall:
            return RetrievalResponse(
                query=query, results=[], context_text=format_context([])
            )

        # 3. fetch metadata in one batch
        memory_ids = [mid for mid, _ in recall]
        records = self._repo.get_memories(memory_ids)
        records_by_id: dict[str, MemoryRecord] = {r.memory_id: r for r in records}

        # 4. hard pre-filters: same user_id, parseable timestamp, non-empty
        #    retrieval_text. Fail-soft: bad rows are dropped silently.
        candidates: list[Candidate] = []
        for mid, sim in recall:
            rec = records_by_id.get(mid)
            if rec is None:
                logger.debug("orphan memory id from hnsw search: %s", mid)
                continue
            if rec.user_id != user_id:
                continue
            if not rec.retrieval_text.strip():
                continue
            ts = parse_timestamp(rec.timestamp)
            if ts is None:
                logger.debug("dropping record with unparseable timestamp: %s", mid)
                continue
            candidates.append(
                Candidate(
                    memory_id=rec.memory_id,
                    topic=rec.topic,
                    subtopic=rec.subtopic,
                    timestamp=ts,
                    retrieval_text=rec.retrieval_text,
                    raw_sim_score=sim,
                )
            )

        if not candidates:
            return RetrievalResponse(
                query=query, results=[], context_text=format_context([])
            )

        # 5. rerank with sim + recency + match
        ranked = rank_candidates(candidates, now=self._now_fn(), cfg=self._cfg)
        top = ranked[:k]

        results = [_to_result(s) for s in top]
        return RetrievalResponse(
            query=query,
            results=results,
            context_text=format_context(top),
        )


def _to_result(s: ScoredCandidate) -> RetrieveResult:
    c = s.candidate
    return RetrieveResult(
        memory_id=c.memory_id,
        topic=c.topic,
        subtopic=c.subtopic,
        timestamp=c.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        sim_score=s.sim_score,
        recency_score=s.recency_score,
        match_score=s.match_score,
        final_score=s.final_score,
        retrieval_text=c.retrieval_text,
    )
