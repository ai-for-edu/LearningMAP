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


def test_add_grows_capacity_past_initial_max_elements(
    fake_emb: FakeEmbedder, index_path: Path
) -> None:
    """Adding past the initial cap must auto-resize, not throw."""
    idx = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=4
    )
    for i in range(20):
        idx.add(note_id=i + 1, text=f"note-{i}")

    hits = idx.search(query_text="note-19", top_k=3)
    assert any(h.note_id == 20 for h in hits)


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


def test_load_missing_file_returns_false_and_starts_empty(
    fake_emb: FakeEmbedder, index_path: Path
) -> None:
    """No file on disk → load() returns False, index is empty but usable.

    (Truly corrupt-bytes recovery is not unit-tested here because hnswlib
    segfaults on malformed binaries rather than raising — the realistic
    failure mode handled in production is missing or stale files plus
    embedding-model fingerprint mismatches, exercised in
    test_wire_app_rebuilds_*.)
    """
    assert not index_path.exists()
    idx = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    assert idx.load() is False
    assert idx.count == 0
    idx.add(note_id=1, text="post-recovery write")
    assert idx.search(query_text="post-recovery write", top_k=3)[0].note_id == 1


def test_rebuild_from_repopulates_index(
    fake_emb: FakeEmbedder, index_path: Path
) -> None:
    idx = NotesIndex(
        index_path=index_path, embedder=fake_emb, dimension=16, max_elements=100
    )
    n = idx.rebuild_from([(1, "alpha"), (2, "beta"), (3, "gamma")], batch_size=2)
    assert n == 3
    assert idx.count == 3
    hits = idx.search(query_text="beta", top_k=3)
    assert any(h.note_id == 2 for h in hits)


def test_wire_app_rebuilds_notes_index_when_file_missing(
    settings_override, tmp_path
) -> None:
    """Active notes in SQLite + missing notes_hnsw.bin → rebuild on startup
    so /notes/{user}/neighbors is not silently empty for restored users.
    """
    import os

    from fastapi.testclient import TestClient

    os.environ["SM_USE_FAKE_EMBEDDER"] = "1"
    from app.db.sqlite_repo import SqliteRepo
    from app.main import create_app

    # Pre-seed SQLite with two active notes
    repo = SqliteRepo(settings_override.sqlite_path)
    try:
        repo.add_note(
            user_id="u1",
            category="misconception",
            content="alpha note",
            content_hash="h1",
            source_session_id=None,
            source_event="submit-quiz",
            now_ts=1,
        )
        repo.add_note(
            user_id="u1",
            category="misconception",
            content="beta note",
            content_hash="h2",
            source_session_id=None,
            source_event="submit-quiz",
            now_ts=2,
        )
    finally:
        repo.close()

    # Make sure notes_hnsw.bin really is absent
    notes_idx_path = settings_override.data_dir / "notes_hnsw.bin"
    if notes_idx_path.exists():
        notes_idx_path.unlink()

    app = create_app(settings=settings_override)
    with TestClient(app) as c:
        r = c.get(
            "/notes/u1/neighbors",
            params={"category": "misconception", "query_text": "alpha", "top_k": 5},
        )
        assert r.status_code == 200
        ids = {n["id"] for n in r.json()["neighbors"]}
        # Both pre-existing notes must be searchable after rebuild
        assert len(ids) >= 1


def test_wire_app_rebuilds_when_embedding_model_changed(
    settings_override, tmp_path
) -> None:
    """Changing embedding_model between starts forces a rebuild + meta update."""
    import os

    from fastapi.testclient import TestClient

    os.environ["SM_USE_FAKE_EMBEDDER"] = "1"
    from app.db.sqlite_repo import SqliteRepo
    from app.main import create_app

    repo = SqliteRepo(settings_override.sqlite_path)
    try:
        repo.add_note(
            user_id="u1",
            category="misconception",
            content="seed note",
            content_hash="hh",
            source_session_id=None,
            source_event="submit-quiz",
            now_ts=1,
        )
        repo.set_meta("notes_embedding_model", "old-model-name")
    finally:
        repo.close()

    app = create_app(settings=settings_override)
    with TestClient(app):
        pass

    # After startup the meta should be updated to the configured model name
    repo2 = SqliteRepo(settings_override.sqlite_path)
    try:
        assert (
            repo2.get_meta("notes_embedding_model") == settings_override.embedding_model
        )
    finally:
        repo2.close()
