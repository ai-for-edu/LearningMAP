"""Shared pytest fixtures.

These fixtures are the foundation that lets every phase be tested in
isolation against deterministic embeddings and tmp-scoped storage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.embedding.fake_embedder import FakeEmbedder


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder(dimension=16, model_name="fake-test")


@pytest.fixture
def tmp_sqlite_path(tmp_path: Path) -> Path:
    return tmp_path / "memory.db"


@pytest.fixture
def tmp_index_path(tmp_path: Path) -> Path:
    return tmp_path / "hnsw.bin"


@pytest.fixture
def frozen_now() -> datetime:
    """Fixed reference time for deterministic recency assertions."""
    return datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings_override(tmp_path: Path) -> Settings:
    """A tmp-scoped Settings suitable for end-to-end tests.

    Uses the fake embedder dimension (16) and points all storage paths
    inside ``tmp_path``. Tests inject this via FastAPI dependency
    overrides; nothing reads ``get_settings()`` directly.
    """
    return Settings(
        sqlite_path=tmp_path / "memory.db",
        index_path=tmp_path / "hnsw.bin",
        data_dir=tmp_path,
        embedding_dim=16,
        embedding_model="fake-test",
        use_fake_embedder=True,
        top_k=5,
        recall_k=20,
    )


@pytest.fixture
def sample_session_payload() -> dict:
    """Mirrors the example in todo.md §11.1, with two subtopics for split coverage."""
    return {
        "user_id": "u_001",
        "session_id": "s_001",
        "timestamp": "2026-03-28T10:30:00Z",
        "goal_text": "I want to study quadratic equations",
        "topic": "Quadratic Equations",
        "subtopics": [
            {
                "name": "Discriminant",
                "summary": "Student repeatedly confused root cases.",
                "questions": [
                    {
                        "question_id": "q1",
                        "question_text": "x^2 - 4x + 4 = 0",
                        "student_answer": "two distinct real roots",
                        "is_correct": False,
                        "error_type": "discriminant confusion",
                    },
                    {
                        "question_id": "q2",
                        "question_text": "x^2 + 1 = 0",
                        "student_answer": "no real roots",
                        "is_correct": True,
                        "error_type": None,
                    },
                ],
            },
            {
                "name": "Factoring",
                "summary": "Mostly correct, occasional sign error.",
                "questions": [
                    {
                        "question_id": "q3",
                        "question_text": "x^2 - 5x + 6 = 0",
                        "student_answer": "(x-2)(x-3)",
                        "is_correct": True,
                        "error_type": None,
                    },
                ],
            },
        ],
    }


@pytest.fixture
def sample_query() -> str:
    return "I want to study quadratic equations"
