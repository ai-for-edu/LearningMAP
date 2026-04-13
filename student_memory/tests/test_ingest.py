"""Phase 5: end-to-end ingestion through ``Ingestor`` with the fake embedder."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.sqlite_repo import SqliteRepo
from app.index.hnsw_index import HnswIndex
from app.index.label_store import SqliteLabelStore
from app.memory.ingestor import DuplicateSessionError, Ingestor
from app.embedding.fake_embedder import FakeEmbedder


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
    yield Ingestor(repo, embedder, index), repo, embedder, index
    repo.close()


def test_ingest_happy_path(stack, sample_session_payload: dict) -> None:
    ingestor, repo, _, index = stack
    result = ingestor.ingest(sample_session_payload)
    assert result.session_id == "s_001"
    assert result.memory_count == 2

    rows = repo.list_by_user("u_001")
    assert len(rows) == 2
    assert {r.subtopic for r in rows} == {"Discriminant", "Factoring"}
    assert index.count == 2

    # Label mapping is consistent across SQL and hnsw.
    for r in rows:
        assert repo.get_label(r.memory_id) is not None


def test_ingest_duplicate_session_raises(stack, sample_session_payload: dict) -> None:
    ingestor, _, _, _ = stack
    ingestor.ingest(sample_session_payload)
    with pytest.raises(DuplicateSessionError):
        ingestor.ingest(sample_session_payload)


def test_ingest_records_embedding_model(stack, sample_session_payload: dict) -> None:
    ingestor, repo, _, _ = stack
    ingestor.ingest(sample_session_payload)
    assert repo.get_meta("embedding_model") == "fake-test"


def test_ingest_search_returns_inserted_records(stack, sample_session_payload: dict) -> None:
    ingestor, _, embedder, index = stack
    ingestor.ingest(sample_session_payload)
    query = embedder.encode(["I want to study quadratic equations"])
    out = index.search(query, k=2)
    assert len(out) == 2
    sims = [s for _, s in out]
    assert sims[0] >= sims[1]


def test_ingest_persists_index_to_disk(stack, sample_session_payload: dict, tmp_path: Path) -> None:
    ingestor, repo, _, _ = stack
    ingestor.ingest(sample_session_payload)
    # Re-open the index against the same SQLite-backed label store.
    new_index = HnswIndex(
        dim=16,
        index_path=tmp_path / "hnsw.bin",
        label_store=SqliteLabelStore(repo),
        max_elements=64,
    )
    new_index.load_or_init()
    assert new_index.count == 2
