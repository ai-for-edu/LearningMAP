"""End-to-end smoke test for PR-1: simulates the full hybrid memory flow
without main app integration. Verifies all new endpoints wire up.
"""

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


def test_full_flow_quiz_then_snapshot_then_review_decision(client: TestClient) -> None:
    user_id = "xiaoming"

    # 1. submit-quiz → progress + 2 misconceptions written
    r1 = client.post(
        "/progress",
        json={
            "user_id": user_id,
            "topic": "Pythagorean Theorem",
            "mastery_overall": 0.6,
            "last_session_id": "s_001",
            "kp_samples": [
                {"kp": "formula", "score": 0.5, "source": "quiz"},
                {"kp": "coordinate distance", "score": 0.0, "source": "quiz"},
            ],
        },
    )
    assert r1.status_code == 200
    assert r1.json()["new_status"] == "in_progress"

    note_a = client.post(
        "/notes",
        json={
            "user_id": user_id,
            "category": "misconception",
            "content": "confuses 'distance between two points' with 'difference of coordinates'",
            "source_session_id": "s_001",
            "source_event": "submit-quiz",
        },
    )
    note_b = client.post(
        "/notes",
        json={
            "user_id": user_id,
            "category": "misconception",
            "content": "Pythagorean theorem only applies to right triangles",
            "source_session_id": "s_001",
            "source_event": "submit-quiz",
        },
    )
    assert note_a.status_code == note_b.status_code == 200
    note_a_id = note_a.json()["note_id"]

    # 2. Second session start: pull snapshot
    snap = client.get(f"/memory/snapshot/{user_id}")
    md = snap.json()["snapshot_md"]
    assert "Pythagorean Theorem" in md
    assert "distance between two points" in md
    assert "60%" in md

    # 3. Reviewer second run (simulated): hash-dedup hits an existing entry
    notes = client.get(f"/notes/{user_id}", params={"category": "misconception"})
    hashes = {n["content_hash"] for n in notes.json()["notes"]}
    candidate_text = "confuses 'distance between two points' with 'difference of coordinates'"
    import hashlib

    cand_hash = hashlib.sha256(candidate_text.lower().strip().encode()).hexdigest()[:16]
    assert cand_hash in hashes

    # 4. Reviewer decides to DELETE the old entry (soft delete) — via by-id endpoint
    del_resp = client.delete(f"/notes/by-id/{note_a_id}")
    assert del_resp.status_code == 200

    # 5. Write a history row recording the reviewer's DELETE decision — via HTTP endpoint
    hist_resp = client.post(
        "/history",
        json={
            "note_id": note_a_id,
            "event": "DELETE",
            "old_content": candidate_text,
            "new_content": None,
            "reason": "[corr=test-corr-1] student corrected this misconception",
            "source_session_id": "s_002",
            "source_event": "submit-review",
        },
    )
    assert hist_resp.status_code == 204
    # Verify it's actually persisted via the read endpoint
    rows = client.get("/history", params={"event": "DELETE"}).json()["history"]
    assert any(r["note_id"] == note_a_id and "test-corr-1" in r["reason"] for r in rows)

    # 6. Error-log endpoint always returns 200
    err = client.post(
        "/errors/log",
        json={
            "source": "main_app",
            "user_id": user_id,
            "session_id": "s_002",
            "correlation_id": "test-corr-1",
            "op": "fetch_snapshot",
            "status": "http_5xx",
            "error_class": "HTTPError",
            "error_msg": "x",
            "payload_excerpt": "",
        },
    )
    assert err.status_code == 200

    # 7. Single-query correlation call chain
    chain = client.get("/errors/correlation/test-corr-1")
    assert chain.status_code == 200
    assert len(chain.json()["errors"]) >= 1
