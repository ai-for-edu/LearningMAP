"""Phase 7: HTTP-level tests via TestClient.

These run a real FastAPI app against the fake embedder by setting
``SM_USE_FAKE_EMBEDDER=1`` via the test ``Settings`` fixture, so the
``BgeEmbedder`` (and torch) are never even touched.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client(settings: Settings) -> TestClient:
    app = create_app(settings=settings)
    return TestClient(app)


def test_health_endpoint(settings_override: Settings) -> None:
    with _client(settings_override) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ingest_then_retrieve_round_trip(
    settings_override: Settings, sample_session_payload: dict
) -> None:
    with _client(settings_override) as client:
        ingest_resp = client.post("/ingest", json=sample_session_payload)
        assert ingest_resp.status_code == 200, ingest_resp.text
        body = ingest_resp.json()
        assert body["status"] == "ok"
        assert body["session_id"] == "s_001"
        assert body["memory_count"] == 2

        retrieve_resp = client.post(
            "/retrieve",
            json={
                "user_id": "u_001",
                "query": "I want to study quadratic equations",
                "top_k": 3,
            },
        )
    assert retrieve_resp.status_code == 200
    rbody = retrieve_resp.json()
    assert rbody["query"] == "I want to study quadratic equations"
    assert isinstance(rbody["results"], list)
    assert len(rbody["results"]) >= 1
    assert isinstance(rbody["context_text"], str)
    assert rbody["context_text"].startswith("Relevant past learning records:")


def test_ingest_duplicate_session_returns_409(
    settings_override: Settings, sample_session_payload: dict
) -> None:
    with _client(settings_override) as client:
        first = client.post("/ingest", json=sample_session_payload)
        assert first.status_code == 200
        second = client.post("/ingest", json=sample_session_payload)
    assert second.status_code == 409


def test_ingest_missing_user_id_returns_422(settings_override: Settings) -> None:
    with _client(settings_override) as client:
        resp = client.post(
            "/ingest",
            json={
                "timestamp": "2026-04-01T00:00:00Z",
                "subtopics": [{"name": "x", "questions": []}],
            },
        )
    assert resp.status_code == 422


def test_ingest_unparseable_timestamp_returns_422(
    settings_override: Settings,
) -> None:
    with _client(settings_override) as client:
        resp = client.post(
            "/ingest",
            json={
                "user_id": "u_001",
                "timestamp": "not-a-date",
                "subtopics": [{"name": "x", "questions": []}],
            },
        )
    assert resp.status_code == 422


def test_retrieve_invalid_top_k_returns_422(settings_override: Settings) -> None:
    with _client(settings_override) as client:
        resp = client.post(
            "/retrieve",
            json={"user_id": "u_001", "query": "x", "top_k": 0},
        )
    assert resp.status_code == 422


def test_retrieve_empty_index_returns_stable_empty_payload(
    settings_override: Settings,
) -> None:
    with _client(settings_override) as client:
        resp = client.post(
            "/retrieve",
            json={"user_id": "u_001", "query": "anything", "top_k": 3},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["context_text"] == "(no relevant past learning records)"


def test_health_via_router_module() -> None:
    """Confirm the router-mounted /health (Phase 7) is reachable as well
    as the eager one mounted in create_app (Phase 1)."""
    from fastapi.testclient import TestClient

    from app.api.routes import router

    # Sanity: the router should expose /health.
    paths = {r.path for r in router.routes}
    assert "/health" in paths
    assert "/ingest" in paths
    assert "/retrieve" in paths
