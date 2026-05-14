"""Integration tests for /memory/snapshot/{user_id}."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings


@pytest.fixture
def client(settings_override: Settings) -> TestClient:
    from app.main import create_app

    os.environ["SM_USE_FAKE_EMBEDDER"] = "1"
    app = create_app(settings=settings_override)
    with TestClient(app) as c:
        yield c


def test_get_snapshot_empty_user(client: TestClient) -> None:
    r = client.get("/memory/snapshot/u_unknown")
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot_md"] == ""
    assert body["char_count"] == 0
    assert body["truncated"] is False


def test_get_snapshot_after_progress_returns_progress_section(
    client: TestClient,
) -> None:
    client.post(
        "/progress",
        json={
            "user_id": "u1",
            "topic": "Pythagorean Theorem",
            "mastery_overall": 0.6,
            "last_session_id": "s1",
            "kp_samples": [{"kp": "formula", "score": 0.5, "source": "quiz"}],
        },
    )
    r = client.get("/memory/snapshot/u1")
    assert r.status_code == 200
    md = r.json()["snapshot_md"]
    assert "Pythagorean Theorem" in md
    assert "60%" in md


def test_get_snapshot_includes_misconceptions(client: TestClient) -> None:
    client.post(
        "/notes",
        json={
            "user_id": "u1",
            "category": "misconception",
            "content": "misapplies the distance formula between two points",
            "source_event": "submit-quiz",
        },
    )
    r = client.get("/memory/snapshot/u1")
    assert "misapplies the distance formula between two points" in r.json()["snapshot_md"]
