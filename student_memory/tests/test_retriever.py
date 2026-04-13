"""Phase 6: end-to-end retriever tests.

Uses the SQLite repo + hnsw index + fake embedder + reranker stitched
together. ``now_fn`` is injected so recency assertions are deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.sqlite_repo import SqliteRepo
from app.index.hnsw_index import HnswIndex
from app.index.label_store import SqliteLabelStore
from app.memory.formatter import EMPTY_CONTEXT
from app.memory.ingestor import Ingestor
from app.memory.reranker import RerankConfig
from app.memory.retriever import Retriever
from app.embedding.fake_embedder import FakeEmbedder

NOW = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)


def _cfg() -> RerankConfig:
    return RerankConfig(
        w_sim=0.75,
        w_time=0.20,
        w_match=0.05,
        half_life_days=30.0,
        topic_bonus=0.03,
        subtopic_bonus=0.02,
    )


@pytest.fixture
def stack(tmp_path: Path):
    repo = SqliteRepo(tmp_path / "memory.db")
    embedder = FakeEmbedder(dimension=16, model_name="fake-test")
    index = HnswIndex(
        dim=16,
        index_path=tmp_path / "hnsw.bin",
        label_store=SqliteLabelStore(repo),
        max_elements=64,
    )
    index.load_or_init()
    ingestor = Ingestor(repo, embedder, index)
    retriever = Retriever(
        repo,
        embedder,
        index,
        _cfg(),
        recall_k=20,
        top_k_default=5,
        now_fn=lambda: NOW,
    )
    yield ingestor, retriever, repo, embedder, index
    repo.close()


def _payload(
    *,
    user_id: str,
    session_id: str,
    days_ago: float,
    topic: str,
    subtopic: str,
    summary: str,
) -> dict:
    ts = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "user_id": user_id,
        "session_id": session_id,
        "timestamp": ts,
        "goal_text": f"I want to study {topic.lower()}",
        "topic": topic,
        "subtopics": [
            {
                "name": subtopic,
                "summary": summary,
                "questions": [
                    {"question_id": "q1", "is_correct": True, "error_type": None},
                ],
            }
        ],
    }


def test_retrieve_empty_index_returns_stable_empty_response(stack) -> None:
    _, retriever, _, _, _ = stack
    resp = retriever.retrieve("u_001", "anything", top_k=5)
    assert resp.results == []
    assert resp.context_text == EMPTY_CONTEXT


def test_retrieve_returns_results_after_ingest(stack) -> None:
    ingestor, retriever, _, _, _ = stack
    ingestor.ingest(
        _payload(
            user_id="u_001",
            session_id="s_quad",
            days_ago=2,
            topic="Quadratic Equations",
            subtopic="Discriminant",
            summary="confused root cases",
        )
    )
    resp = retriever.retrieve("u_001", "I want to study quadratic equations", top_k=3)
    assert len(resp.results) == 1
    assert resp.results[0].subtopic == "Discriminant"
    assert "Quadratic Equations" in resp.context_text
    assert resp.context_text.startswith("Relevant past learning records:")


def test_retrieve_filters_by_user_id(stack) -> None:
    ingestor, retriever, _, _, _ = stack
    ingestor.ingest(
        _payload(
            user_id="u_alice",
            session_id="s_alice_1",
            days_ago=5,
            topic="Quadratic Equations",
            subtopic="Discriminant",
            summary="alice study",
        )
    )
    ingestor.ingest(
        _payload(
            user_id="u_bob",
            session_id="s_bob_1",
            days_ago=1,
            topic="Quadratic Equations",
            subtopic="Discriminant",
            summary="bob study",
        )
    )
    resp = retriever.retrieve("u_alice", "quadratic equations", top_k=5)
    assert len(resp.results) == 1
    # The summary text from alice's payload, not bob's.
    assert "alice study" in resp.results[0].retrieval_text


def test_retrieve_top_k_limits_results(stack) -> None:
    ingestor, retriever, _, _, _ = stack
    for i in range(4):
        ingestor.ingest(
            _payload(
                user_id="u_001",
                session_id=f"s_{i}",
                days_ago=i,
                topic="Quadratic Equations",
                subtopic=f"Sub{i}",
                summary=f"session {i}",
            )
        )
    resp = retriever.retrieve("u_001", "quadratic equations", top_k=2)
    assert len(resp.results) == 2


def test_retrieve_recent_beats_old_when_similarity_close(stack) -> None:
    ingestor, retriever, _, _, _ = stack
    ingestor.ingest(
        _payload(
            user_id="u_001",
            session_id="s_old",
            days_ago=120,
            topic="Quadratic Equations",
            subtopic="Discriminant",
            summary="old session",
        )
    )
    ingestor.ingest(
        _payload(
            user_id="u_001",
            session_id="s_recent",
            days_ago=2,
            topic="Quadratic Equations",
            subtopic="Discriminant",
            summary="recent session",
        )
    )
    resp = retriever.retrieve("u_001", "quadratic equations review", top_k=2)
    assert len(resp.results) == 2
    # Recent session ranks first because both are equally similar but
    # the recent one decays slower.
    assert resp.results[0].retrieval_text.find("recent session") != -1
    assert resp.results[1].retrieval_text.find("old session") != -1


def test_retrieve_unrelated_query_does_not_crash(stack) -> None:
    ingestor, retriever, _, _, _ = stack
    ingestor.ingest(
        _payload(
            user_id="u_001",
            session_id="s_q",
            days_ago=1,
            topic="Quadratic Equations",
            subtopic="Discriminant",
            summary="study",
        )
    )
    resp = retriever.retrieve("u_001", "完全不相关的查询", top_k=3)
    # The fake embedder hashes unknown text into orthogonal slots, so
    # similarity is 0 — but we still return the candidate (the v1
    # contract is "always return up to top_k", not "filter by min sim").
    assert isinstance(resp.results, list)


def test_retrieve_rejects_empty_query(stack) -> None:
    _, retriever, _, _, _ = stack
    with pytest.raises(ValueError):
        retriever.retrieve("u_001", "", top_k=5)


def test_retrieve_rejects_invalid_top_k(stack) -> None:
    _, retriever, _, _, _ = stack
    with pytest.raises(ValueError):
        retriever.retrieve("u_001", "x", top_k=0)
