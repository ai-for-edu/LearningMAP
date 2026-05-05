"""Cross-session memory integration test.

Starts a real student_memory service process with FakeEmbedder, then
tests the full round-trip: openail diagnosis → ingest → new session retrieve.

Requires: student_memory/uv.lock to be synced (uv sync --extra dev).
Skips gracefully if student_memory is not set up.

This test proves that:
1. _build_ingest_payload produces valid /ingest requests
2. Ingested data is retrievable from a different session
3. Multiple sessions accumulate memories
4. User isolation works
5. Duplicate ingestion is idempotent
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from src.llm.schemas import (
    DiagnosisBreakdown,
    DiagnosisResult,
    QuizQuestion,
)
from src.memory_client import _build_ingest_payload

STUDENT_MEMORY_DIR = Path(__file__).resolve().parent.parent / "student_memory"
SERVICE_PORT = 18090  # Use non-standard port to avoid conflicts
SERVICE_URL = f"http://127.0.0.1:{SERVICE_PORT}"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def memory_service(tmp_path_factory: pytest.TempPathFactory):
    """Start a real student_memory process with FakeEmbedder for the test module."""
    tmp_dir = tmp_path_factory.mktemp("memory_data")

    # Check student_memory venv exists
    venv_python = STUDENT_MEMORY_DIR / ".venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip("student_memory venv not set up (run: cd student_memory && uv sync --extra dev)")

    proc = subprocess.Popen(
        [
            str(venv_python), "-m", "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(SERVICE_PORT),
        ],
        cwd=str(STUDENT_MEMORY_DIR),
        env={
            **os.environ,
            "SM_USE_FAKE_EMBEDDER": "1",
            "SM_DATA_DIR": str(tmp_dir),
            "SM_SQLITE_PATH": str(tmp_dir / "memory.db"),
            "SM_INDEX_PATH": str(tmp_dir / "hnsw.bin"),
            "SM_EMBEDDING_DIM": "16",
            "SM_EMBEDDING_MODEL": "fake-test",
            "SM_SERVICE_PORT": str(SERVICE_PORT),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Wait for service to be ready
    client = httpx.Client(base_url=SERVICE_URL, timeout=2.0)
    for _ in range(30):
        try:
            resp = client.get("/health")
            if resp.status_code == 200:
                break
        except httpx.ConnectError:
            pass
        time.sleep(0.2)
    else:
        proc.kill()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        pytest.fail(f"student_memory failed to start:\n{stdout}")

    yield client

    client.close()
    proc.terminate()
    proc.wait(timeout=5)


def _make_session_a_state() -> dict:
    """Session A: 学勾股定理, 判别式答错, 求根公式答对."""
    return {
        "topic": "勾股定理",
        "questions": [
            QuizQuestion(
                id="q1", knowledge_point="判别式",
                question="判别式 b²-4ac 的作用是？",
                options=["判断根的个数", "求根", "因式分解", "配方"], answer=0,
            ),
            QuizQuestion(
                id="q2", knowledge_point="求根公式",
                question="x²-4x+4=0 的解是？",
                options=["x=1", "x=2", "x=3", "x=4"], answer=1,
            ),
        ],
        "answers": [2, 1],  # q1 wrong, q2 correct
        "diagnosis_result": DiagnosisResult(
            score=50, total=2, correct=1, incorrect=1,
            breakdown=[
                DiagnosisBreakdown(name="判别式", status="weak", detail="0/1 需复习"),
                DiagnosisBreakdown(name="求根公式", status="mastered", detail="1/1 已掌握"),
            ],
        ),
    }


def _make_session_b_state() -> dict:
    """Session B: 学二次函数, 顶点式答错."""
    return {
        "topic": "二次函数",
        "questions": [
            QuizQuestion(
                id="q3", knowledge_point="顶点式",
                question="y=2(x-1)²+3 的顶点是？",
                options=["(1,3)", "(2,3)", "(-1,3)", "(1,-3)"], answer=0,
            ),
            QuizQuestion(
                id="q4", knowledge_point="开口方向",
                question="y=-x²+1 的开口方向？",
                options=["向上", "向下", "向左", "向右"], answer=1,
            ),
        ],
        "answers": [2, 1],  # q3 wrong, q4 correct
        "diagnosis_result": DiagnosisResult(
            score=50, total=2, correct=1, incorrect=1,
            breakdown=[
                DiagnosisBreakdown(name="顶点式", status="weak", detail="0/1 需复习"),
                DiagnosisBreakdown(name="开口方向", status="mastered", detail="1/1 已掌握"),
            ],
        ),
    }


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestPayloadValidity:
    """Verify _build_ingest_payload produces valid /ingest requests."""

    def test_session_a_payload_accepted(self, memory_service: httpx.Client) -> None:
        payload = _build_ingest_payload("user1", "valid_a", _make_session_a_state())
        resp = memory_service.post("/ingest", json=payload)
        assert resp.status_code == 200, f"Rejected: {resp.text}"
        data = resp.json()
        assert data["status"] == "ok"
        assert data["memory_count"] == 2  # 判别式 + 求根公式

    def test_session_b_payload_accepted(self, memory_service: httpx.Client) -> None:
        payload = _build_ingest_payload("user1", "valid_b", _make_session_b_state())
        resp = memory_service.post("/ingest", json=payload)
        assert resp.status_code == 200, f"Rejected: {resp.text}"
        assert resp.json()["memory_count"] == 2  # 顶点式 + 开口方向


class TestCrossSessionRetrieval:
    """Core test: ingest from session A, retrieve from session B."""

    def test_ingest_then_retrieve_finds_memory(
        self, memory_service: httpx.Client
    ) -> None:
        # Session A: ingest
        payload_a = _build_ingest_payload("cross_user", "cross_a", _make_session_a_state())
        resp = memory_service.post("/ingest", json=payload_a)
        assert resp.status_code == 200

        # Session B: retrieve (different session, same user)
        resp = memory_service.post("/retrieve", json={
            "user_id": "cross_user",
            "query": "勾股定理 判别式",
            "top_k": 5,
        })
        assert resp.status_code == 200

        data = resp.json()
        assert len(data["results"]) > 0
        assert data["context_text"] != ""

        # Should contain session A's topic
        all_text = data["context_text"]
        assert "勾股定理" in all_text

    def test_multiple_sessions_accumulate(
        self, memory_service: httpx.Client
    ) -> None:
        # Ingest two sessions for the same user
        for sid, state in [("accum_a", _make_session_a_state()), ("accum_b", _make_session_b_state())]:
            payload = _build_ingest_payload("accum_user", sid, state)
            resp = memory_service.post("/ingest", json=payload)
            assert resp.status_code == 200

        # Retrieve broadly
        resp = memory_service.post("/retrieve", json={
            "user_id": "accum_user",
            "query": "数学学习",
            "top_k": 10,
        })
        assert resp.status_code == 200

        results = resp.json()["results"]
        # 2 subtopics from session A + 2 from session B = 4
        assert len(results) == 4

        topics = {r["topic"] for r in results}
        assert "勾股定理" in topics
        assert "二次函数" in topics

    def test_retrieve_shows_diagnosis_detail(
        self, memory_service: httpx.Client
    ) -> None:
        """Retrieval text should include accuracy and error info."""
        payload = _build_ingest_payload("detail_user", "detail_a", _make_session_a_state())
        memory_service.post("/ingest", json=payload)

        resp = memory_service.post("/retrieve", json={
            "user_id": "detail_user",
            "query": "勾股定理",
            "top_k": 5,
        })

        context = resp.json()["context_text"]
        # Should contain stats from the ingested session
        assert "wrong_answer" in context or "weak" in context or "判别式" in context


class TestUserIsolation:
    """Different users cannot see each other's memories."""

    def test_user2_cannot_see_user1(self, memory_service: httpx.Client) -> None:
        payload = _build_ingest_payload("iso_user1", "iso_a", _make_session_a_state())
        memory_service.post("/ingest", json=payload)

        resp = memory_service.post("/retrieve", json={
            "user_id": "iso_user2",
            "query": "勾股定理",
            "top_k": 5,
        })
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 0


class TestDuplicateProtection:
    """Same session_id cannot be ingested twice."""

    def test_duplicate_returns_409_and_no_double_data(
        self, memory_service: httpx.Client
    ) -> None:
        payload = _build_ingest_payload("dup_user", "dup_session", _make_session_a_state())

        # First ingest: OK
        resp1 = memory_service.post("/ingest", json=payload)
        assert resp1.status_code == 200

        # Second ingest: 409 Conflict
        resp2 = memory_service.post("/ingest", json=payload)
        assert resp2.status_code == 409

        # Retrieve should still return exactly 2 (not 4)
        resp = memory_service.post("/retrieve", json={
            "user_id": "dup_user",
            "query": "勾股定理",
            "top_k": 10,
        })
        assert len(resp.json()["results"]) == 2


class TestEmptyState:
    """Edge cases with empty or minimal state."""

    def test_retrieve_before_any_ingest(self, memory_service: httpx.Client) -> None:
        resp = memory_service.post("/retrieve", json={
            "user_id": "empty_user",
            "query": "什么都没学过",
            "top_k": 5,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 0
        assert data["context_text"] != ""  # Should have fallback text
