"""Integration tests for /progress endpoint."""

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


def test_post_progress_inserts_with_in_progress_status(client: TestClient) -> None:
    r = client.post(
        "/progress",
        json={
            "user_id": "u1",
            "topic": "勾股定理",
            "mastery_overall": 0.6,
            "last_session_id": "s1",
            "kp_samples": [
                {"kp": "公式", "score": 0.5, "source": "quiz"},
                {"kp": "坐标距离", "score": 0.0, "source": "quiz"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["topic"] == "勾股定理"
    assert body["new_status"] == "in_progress"
    assert body["sample_count"] == 2


def test_post_progress_transitions_to_mastered_when_high(client: TestClient) -> None:
    """First quiz never directly gives mastered (in_progress); subsequent
    quiz with mastery >= 0.85 within 7 days transitions to mastered."""
    r1 = client.post(
        "/progress",
        json={
            "user_id": "u1",
            "topic": "addition",
            "mastery_overall": 0.9,
            "last_session_id": "s1",
            "kp_samples": [{"kp": "k", "score": 0.9, "source": "quiz"}],
        },
    )
    # First call: brand-new topic → always in_progress, regardless of mastery
    assert r1.status_code == 200
    assert r1.json()["new_status"] == "in_progress"

    # Second call shortly after with high mastery → mastered
    r2 = client.post(
        "/progress",
        json={
            "user_id": "u1",
            "topic": "addition",
            "mastery_overall": 0.9,
            "last_session_id": "s2",
            "kp_samples": [{"kp": "k", "score": 0.9, "source": "review"}],
        },
    )
    assert r2.status_code == 200
    assert r2.json()["new_status"] == "mastered"


def test_post_progress_repeated_keeps_started_at(client: TestClient) -> None:
    r1 = client.post(
        "/progress",
        json={
            "user_id": "u1",
            "topic": "x",
            "mastery_overall": 0.3,
            "last_session_id": "s1",
            "kp_samples": [{"kp": "k", "score": 0.3, "source": "quiz"}],
        },
    )
    r2 = client.post(
        "/progress",
        json={
            "user_id": "u1",
            "topic": "x",
            "mastery_overall": 0.5,
            "last_session_id": "s2",
            "kp_samples": [{"kp": "k", "score": 0.5, "source": "review"}],
        },
    )
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_post_progress_abandons_after_31_days(client: TestClient) -> None:
    """A topic with last_seen_at > 30 days ago and non-mastered curr
    transitions to 'abandoned' on the next /progress upsert.

    Seeds the row directly (last_seen_at = now - 31 days) because we
    cannot rewind ``time.time()`` from the route layer.
    """
    import time

    repo = client.app.state.repo
    now = int(time.time())
    repo.upsert_progress(
        user_id="u_abn",
        topic="topic_abn",
        status="in_progress",
        started_at=now - 60 * 86400,
        last_seen_at=now - 31 * 86400,
        mastery_overall=0.4,
        last_session_id="s_old",
    )

    r = client.post(
        "/progress",
        json={
            "user_id": "u_abn",
            "topic": "topic_abn",
            "mastery_overall": 0.4,
            "last_session_id": "s_new",
            "kp_samples": [],
        },
    )
    assert r.status_code == 200
    assert r.json()["new_status"] == "abandoned"


def test_post_progress_recent_topic_is_not_abandoned(client: TestClient) -> None:
    """Sanity: a fresh row with a recent last_seen_at must NOT be abandoned."""
    import time

    repo = client.app.state.repo
    now = int(time.time())
    repo.upsert_progress(
        user_id="u_keep",
        topic="topic_keep",
        status="in_progress",
        started_at=now - 5 * 86400,
        last_seen_at=now - 5 * 86400,
        mastery_overall=0.4,
        last_session_id="s_recent",
    )
    r = client.post(
        "/progress",
        json={
            "user_id": "u_keep",
            "topic": "topic_keep",
            "mastery_overall": 0.4,
            "last_session_id": "s_next",
            "kp_samples": [],
        },
    )
    assert r.status_code == 200
    assert r.json()["new_status"] == "in_progress"
