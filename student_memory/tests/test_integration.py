"""Cross-phase integration test.

Ingests three learning sessions with different ages, runs a retrieval,
asserts the recent + relevant record ranks first, and validates the
``context_text`` is non-empty and prompt-ready. Then restarts the app
against the same SQLite + index files to validate persistence.

This is the single test that trips if any phase silently broke its
contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

NOW = datetime(2026, 4, 9, 12, 0, 0, tzinfo=timezone.utc)


def _payload(
    *,
    session_id: str,
    days_ago: float,
    topic: str,
    subtopic: str,
    summary: str,
    user_id: str = "u_001",
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


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        sqlite_path=tmp_path / "memory.db",
        index_path=tmp_path / "hnsw.bin",
        data_dir=tmp_path,
        embedding_dim=16,
        embedding_model="fake-test",
        use_fake_embedder=True,
        top_k=3,
        recall_k=20,
    )


def test_full_pipeline_ingest_retrieve_persist(settings: Settings) -> None:
    # 1. Spin up the service.
    app = create_app(settings=settings)

    with TestClient(app) as client:
        # 2. Ingest three sessions.
        for body in (
            _payload(
                session_id="s_calc",
                days_ago=40,
                topic="Calculus",
                subtopic="Derivatives",
                summary="early exposure to derivatives",
            ),
            _payload(
                session_id="s_quad_old",
                days_ago=20,
                topic="Quadratic Equations",
                subtopic="Discriminant",
                summary="old quadratic study",
            ),
            _payload(
                session_id="s_quad_new",
                days_ago=2,
                topic="Quadratic Equations",
                subtopic="Discriminant",
                summary="recent quadratic study",
            ),
        ):
            resp = client.post("/ingest", json=body)
            assert resp.status_code == 200, resp.text

        # 3. Retrieve.
        resp = client.post(
            "/retrieve",
            json={
                "user_id": "u_001",
                "query": "I want to study quadratic equations",
                "top_k": 3,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["results"], list)
        assert len(body["results"]) >= 2

        # 4. Recent quadratic record ranks first.
        top = body["results"][0]
        assert "Quadratic Equations" in top["topic"]
        assert "recent quadratic study" in top["retrieval_text"]

        # 5. Old quadratic record ranks second.
        second = body["results"][1]
        assert "Quadratic Equations" in second["topic"]
        assert "old quadratic study" in second["retrieval_text"]

        # 6. context_text is non-empty and contains the top retrieval text.
        assert body["context_text"].startswith("Relevant past learning records:")
        assert "recent quadratic study" in body["context_text"]

    # 7. Restart against the same SQLite + index files. Persistence check.
    app2 = create_app(settings=settings)
    with TestClient(app2) as client2:
        resp2 = client2.post(
            "/retrieve",
            json={
                "user_id": "u_001",
                "query": "I want to study quadratic equations",
                "top_k": 3,
            },
        )
        assert resp2.status_code == 200
        assert "recent quadratic study" in resp2.json()["context_text"]


def test_full_pipeline_recovers_when_index_file_missing(
    settings: Settings, tmp_path: Path
) -> None:
    """Delete the hnsw index file between runs and verify the recovery
    path from ARCHITECTURE.md §16 rebuilds from SQLite."""
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.post(
            "/ingest",
            json=_payload(
                session_id="s_quad",
                days_ago=1,
                topic="Quadratic Equations",
                subtopic="Discriminant",
                summary="initial",
            ),
        )

    # Delete the persisted index file. SQLite stays.
    index_path = settings.index_path
    assert index_path.exists()
    index_path.unlink()

    app2 = create_app(settings=settings)
    with TestClient(app2) as client2:
        resp = client2.post(
            "/retrieve",
            json={"user_id": "u_001", "query": "quadratic", "top_k": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 1
        assert "initial" in body["context_text"]
