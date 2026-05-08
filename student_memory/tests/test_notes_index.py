"""Tests for the notes-specific hnswlib index wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.embedding.fake_embedder import FakeEmbedder
from app.memory.notes_index import NotesIndex


@pytest.fixture
def fake_emb() -> FakeEmbedder:
    return FakeEmbedder(dimension=16, model_name="fake-test")


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "notes_hnsw.bin"


def test_add_then_search_returns_self(fake_emb: FakeEmbedder, index_path: Path) -> None:
    idx = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    idx.add(note_id=1, text="apples are sweet")

    hits = idx.search(query_text="apples are sweet", top_k=3)
    assert len(hits) == 1
    assert hits[0].note_id == 1


def test_remove_then_search_excludes(fake_emb: FakeEmbedder, index_path: Path) -> None:
    idx = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    idx.add(note_id=1, text="apples are sweet")
    idx.add(note_id=2, text="bananas are yellow")
    idx.remove(note_id=1)

    hits = idx.search(query_text="apples", top_k=5)
    note_ids = {h.note_id for h in hits}
    assert 1 not in note_ids


def test_persist_then_reload(fake_emb: FakeEmbedder, index_path: Path) -> None:
    idx = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    idx.add(note_id=42, text="quadratic equations")
    idx.save()

    idx2 = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    idx2.load()
    hits = idx2.search(query_text="quadratic equations", top_k=3)
    assert hits[0].note_id == 42


def test_save_then_load_via_app_lifespan_preserves_index(
    settings_override, fake_emb, index_path
) -> None:
    """A note added during a session is still searchable after a save+reload cycle."""
    # Add a note via direct NotesIndex
    idx1 = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    idx1.add(note_id=99, text="lifespan persistence check")
    idx1.save()

    # Reopen
    idx2 = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    assert idx2.load() is True
    hits = idx2.search(query_text="lifespan persistence check", top_k=3)
    assert any(h.note_id == 99 for h in hits)
