"""Product-level memory system audit tests.

Tests every memory capability a production education AI should have.
Tests are grouped by category. EXPECTED FAILURES are marked with
comments — they represent missing features, not bugs.

Uses a subprocess to start student_memory service (avoids venv conflicts).
Run: uv run python -m pytest tests/test_product_memory.py -v
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.llm.schemas import (
    DiagnosisBreakdown,
    DiagnosisResult,
    KnowledgeGraph,
    KnowledgeNode,
    KnowledgeEdge,
    QuizQuestion,
)
from src.memory_client import (
    DEFAULT_USER_ID,
    _build_ingest_payload,
    ingest_session,
    retrieve_memory,
)

STUDENT_MEMORY_DIR = Path(__file__).resolve().parent.parent / "student_memory"
SERVICE_PORT = 18091
SERVICE_URL = f"http://127.0.0.1:{SERVICE_PORT}"


# ------------------------------------------------------------------
# Service fixture
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def memory_service(tmp_path_factory: pytest.TempPathFactory):
    """Start student_memory as a subprocess with FakeEmbedder."""
    venv_python = STUDENT_MEMORY_DIR / ".venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip("student_memory venv not set up")

    tmp_dir = tmp_path_factory.mktemp("product_test")
    proc = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(SERVICE_PORT)],
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
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    client = httpx.Client(base_url=SERVICE_URL, timeout=5.0)
    for _ in range(30):
        try:
            if client.get("/health").status_code == 200:
                break
        except httpx.ConnectError:
            pass
        time.sleep(0.2)
    else:
        proc.kill()
        out = proc.stdout.read().decode() if proc.stdout else ""
        pytest.fail(f"student_memory failed to start:\n{out}")

    yield client
    client.close()
    proc.terminate()
    proc.wait(timeout=5)


def _patch_to_service(service_client: httpx.Client):
    """Patch memory_client to use the test service."""
    patched = httpx.Client(base_url=SERVICE_URL, timeout=5.0)
    return patch("src.memory_client._get_client", return_value=patched)


# ------------------------------------------------------------------
# State builders
# ------------------------------------------------------------------

def _make_diagnosis_state(topic: str = "Pythagorean theorem", session_id: str = "s1") -> dict:
    return {
        "topic": topic,
        "knowledge_graph": KnowledgeGraph(
            topic=topic,
            nodes=[
                KnowledgeNode(id="n1", name="right triangle", description="definition of a right triangle", level="prerequisite"),
                KnowledgeNode(id="n2", name="Pythagorean formula", description="a²+b²=c²", level="core"),
                KnowledgeNode(id="n3", name="converse theorem", description="converse of the Pythagorean theorem", level="extension"),
            ],
            edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
        ),
        "questions": [
            QuizQuestion(id="q1", knowledge_point="right triangle", question="Which is a right triangle?", options=["A","B","C","D"], answer=0),
            QuizQuestion(id="q2", knowledge_point="Pythagorean formula", question="3²+4²=?", options=["20","25","30","35"], answer=1),
            QuizQuestion(id="q3", knowledge_point="Pythagorean formula", question="Find c", options=["13","14","15","16"], answer=0),
            QuizQuestion(id="q4", knowledge_point="converse theorem", question="Can these form a right triangle?", options=["Yes","No","Uncertain","Missing info"], answer=0),
        ],
        "answers": [0, 1, 2, 0],  # q1✓ q2✓ q3✗ q4✓
        "diagnosis_result": DiagnosisResult(
            score=75, total=4, correct=3, incorrect=1,
            breakdown=[
                DiagnosisBreakdown(name="right triangle", status="mastered", detail="1/1 mastered"),
                DiagnosisBreakdown(name="Pythagorean formula", status="weak", detail="1/2 needs review"),
                DiagnosisBreakdown(name="converse theorem", status="mastered", detail="1/1 mastered"),
            ],
        ),
    }


def _make_graph_only_state(topic: str = "quadratic equations") -> dict:
    return {
        "topic": topic,
        "knowledge_graph": KnowledgeGraph(
            topic=topic,
            nodes=[
                KnowledgeNode(id="n1", name="quadratic equation in one variable", description="ax²+bx+c=0", level="core"),
                KnowledgeNode(id="n2", name="discriminant", description="Δ=b²-4ac", level="core"),
            ],
            edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
        ),
        "questions": [],
        "answers": [],
        "diagnosis_result": None,
    }


def _make_lesson_state(topic: str = "Pythagorean theorem") -> dict:
    state = _make_diagnosis_state(topic=topic)
    state["lesson_content"] = "# Pythagorean theorem review\n\n## Key: Pythagorean formula\na²+b²=c²"
    state["lesson_format"] = "markdown"
    return state


def _make_chat_only_state() -> dict:
    return {
        "topic": None,
        "knowledge_graph": None,
        "questions": [],
        "answers": [],
        "diagnosis_result": None,
    }


# ===================================================================
# 1. MEMORY WRITE COVERAGE — which events should produce memories?
# ===================================================================

class TestWriteCoverage:

    def test_W1_write_after_diagnosis(self, memory_service: httpx.Client) -> None:
        """[existing] memory should be written after diagnosis completion."""
        with _patch_to_service(memory_service):
            ingest_session("w1", "w1_diag", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "w1", "query": "Pythagorean theorem", "top_k": 10})
        assert len(resp.json()["results"]) > 0, "memory should exist after diagnosis"

    def test_W2_write_after_knowledge_graph_only(self, memory_service: httpx.Client) -> None:
        """[missing] knowledge graph only (no diagnosis) should also produce memory."""
        payload = _build_ingest_payload("w2", "w2_graph", _make_graph_only_state("set theory"))
        assert len(payload["subtopics"]) > 0, \
            "❌ missing: knowledge graph nodes not converted to subtopics — memory empty without diagnosis"

    def test_W3_write_after_lesson_generation(self, memory_service: httpx.Client) -> None:
        """[missing] lesson generation payload should include lesson info."""
        payload = _build_ingest_payload("w3", "w3_lesson", _make_lesson_state())
        payload_str = str(payload).lower()
        assert "lesson" in payload_str or "review" in payload_str, \
            "❌ missing: ingest payload contains no lesson information"

    def test_W4_write_chat_only(self, memory_service: httpx.Client) -> None:
        """[missing] chat-only sessions should also produce lightweight memory."""
        payload = _build_ingest_payload("w4", "w4_chat", _make_chat_only_state())
        assert len(payload["subtopics"]) > 0, \
            "❌ missing: chat-only (no diagnosis/graph) yields empty subtopics — no memory produced"

    def test_W5_accuracy_recorded(self, memory_service: httpx.Client) -> None:
        """[existing] memory should include accuracy information."""
        with _patch_to_service(memory_service):
            ingest_session("w5", "w5_acc", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "w5", "query": "Pythagorean theorem", "top_k": 10})
        ctx = resp.json()["context_text"]
        has_accuracy = any(kw in ctx for kw in ["50", "100", "correct", "accuracy"])
        assert has_accuracy, "memory should include accuracy information"

    def test_W6_weak_points_in_memory(self, memory_service: httpx.Client) -> None:
        """[existing] memory should include weak knowledge point names."""
        with _patch_to_service(memory_service):
            ingest_session("w6", "w6_weak", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "w6", "query": "Pythagorean theorem", "top_k": 10})
        ctx = resp.json()["context_text"]
        assert "Pythagorean formula" in ctx, "memory should include knowledge point name"


# ===================================================================
# 2. MEMORY READ COVERAGE — which stages should read memory?
# ===================================================================

class TestReadCoverage:

    def test_R1_chat_node_reads(self) -> None:
        """[existing] chat_node reads memory."""
        import inspect
        from src.agent.nodes.chat import chat_node
        assert "retrieve_memory" in inspect.getsource(chat_node)

    def test_R2_lesson_node_reads(self) -> None:
        """[missing] lesson_node should read history weak points to personalize lesson."""
        import inspect
        from src.agent.nodes.lesson import lesson_node
        assert "retrieve_memory" in inspect.getsource(lesson_node), \
            "❌ missing: lesson_node does not read memory — cannot personalize based on history weak points"

    def test_R3_knowledge_graph_node_reads(self) -> None:
        """[missing] knowledge_graph_node should read history."""
        import inspect
        from src.agent.nodes.knowledge_graph import knowledge_graph_node
        assert "retrieve_memory" in inspect.getsource(knowledge_graph_node), \
            "❌ missing: knowledge_graph_node does not read memory"

    def test_R4_quiz_node_reads(self) -> None:
        """[missing] quiz_node should focus on history weak points when generating questions."""
        import inspect
        from src.agent.nodes.quiz import quiz_node
        assert "retrieve_memory" in inspect.getsource(quiz_node), \
            "❌ missing: quiz_node does not read memory — cannot emphasize history weak points"


# ===================================================================
# 3. API INGEST TRIGGERS — API layer ingest trigger points
# ===================================================================

class TestIngestTriggers:

    def test_T1_submit_quiz_triggers(self) -> None:
        """[existing] submit_quiz triggers ingest."""
        import inspect
        from src.api.chat import submit_quiz
        assert "ingest_session" in inspect.getsource(submit_quiz)

    def test_T2_generate_lesson_triggers(self) -> None:
        """[missing] generate_lesson should trigger ingest."""
        import inspect
        from src.api.chat import generate_lesson
        assert "ingest" in inspect.getsource(generate_lesson), \
            "❌ missing: generate_lesson does not trigger ingest"

    def test_T3_generate_slides_triggers(self) -> None:
        """[missing] generate_slides should trigger ingest."""
        import inspect
        from src.api.chat import generate_slides
        assert "ingest" in inspect.getsource(generate_slides), \
            "❌ missing: generate_slides does not trigger ingest"

    def test_T4_chat_graph_mode_triggers(self) -> None:
        """[missing] /api/chat in mode=graph should trigger ingest."""
        import inspect
        from src.api.chat import chat
        src = inspect.getsource(chat)
        assert "graph" in src and "ingest" in src, \
            "❌ missing: /api/chat does not trigger ingest after generating knowledge graph"


# ===================================================================
# 4. CROSS-SESSION USER JOURNEYS
# ===================================================================

class TestUserJourneys:

    def test_J1_what_did_i_learn(self, memory_service: httpx.Client) -> None:
        """Session A learns Pythagorean theorem → Session B asks 'what did I learn before'."""
        with _patch_to_service(memory_service):
            ingest_session("j1", "j1_s", _make_diagnosis_state())
            ctx = retrieve_memory("j1", "what did I learn before")
        assert ctx != "" and "Pythagorean theorem" in ctx

    def test_J2_ask_weak_points(self, memory_service: httpx.Client) -> None:
        """Session A diagnoses weak points → Session B asks 'where am I weak'."""
        with _patch_to_service(memory_service):
            ingest_session("j2", "j2_s", _make_diagnosis_state())
            ctx = retrieve_memory("j2", "which knowledge points am I weak on")
        has_info = any(kw in ctx for kw in ["weak", "wrong", "needs review", "incorrect"])
        assert has_info, "should be able to retrieve weak point information"

    def test_J3_multiple_sessions_accumulate(self, memory_service: httpx.Client) -> None:
        """Memories from two sessions should accumulate."""
        s1 = _make_diagnosis_state(topic="Pythagorean theorem")
        s2 = _make_diagnosis_state(topic="quadratic functions")
        with _patch_to_service(memory_service):
            ingest_session("j3", "j3_a", s1)
            ingest_session("j3", "j3_b", s2)
        resp = memory_service.post("/retrieve", json={"user_id": "j3", "query": "mathematics", "top_k": 20})
        results = resp.json()["results"]
        topics = {r["topic"] for r in results}
        assert len(topics) >= 2, "should have memories for at least two topics"

    def test_J4_topic_relevance_ranking(self, memory_service: httpx.Client) -> None:
        """When querying Pythagorean theorem, its memory should rank above physics."""
        with _patch_to_service(memory_service):
            ingest_session("j4", "j4_math", _make_diagnosis_state("Pythagorean theorem"))
            ingest_session("j4", "j4_phys", _make_diagnosis_state("Newton's laws of motion"))
        resp = memory_service.post("/retrieve", json={"user_id": "j4", "query": "Pythagorean theorem", "top_k": 5})
        results = resp.json()["results"]
        if results:
            assert results[0]["topic"] == "Pythagorean theorem", f"Top result should be Pythagorean theorem, got {results[0]['topic']}"

    def test_J5_user_isolation(self, memory_service: httpx.Client) -> None:
        """User A's memories are not visible to user B."""
        with _patch_to_service(memory_service):
            ingest_session("j5_a", "j5_s", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "j5_b", "query": "Pythagorean theorem", "top_k": 5})
        assert len(resp.json()["results"]) == 0

    def test_J6_duplicate_ingest_idempotent(self, memory_service: httpx.Client) -> None:
        """Ingesting the same session twice should not produce duplicate memories."""
        with _patch_to_service(memory_service):
            ingest_session("j6", "j6_dup", _make_diagnosis_state())
            ingest_session("j6", "j6_dup", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "j6", "query": "Pythagorean theorem", "top_k": 20})
        # 3 knowledge points, should have exactly 3 memories, not 6
        assert len(resp.json()["results"]) == 3


# ===================================================================
# 5. GRACEFUL DEGRADATION
# ===================================================================

class TestGracefulDegradation:

    def test_G1_retrieve_when_down(self) -> None:
        """retrieve returns empty when memory service is unavailable."""
        with patch("src.memory_client._get_client") as m:
            m.return_value.post.side_effect = httpx.ConnectError("refused")
            assert retrieve_memory("u", "q") == ""

    def test_G2_ingest_when_down(self) -> None:
        """ingest does not crash when memory service is unavailable."""
        with patch("src.memory_client._get_client") as m:
            m.return_value.post.side_effect = httpx.ConnectError("refused")
            ingest_session("u", "s", _make_diagnosis_state())  # should not raise

    def test_G3_empty_retrieve(self, memory_service: httpx.Client) -> None:
        """retrieve returns stable empty response when no memories exist."""
        resp = memory_service.post("/retrieve", json={"user_id": "nobody", "query": "nothing", "top_k": 5})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 0


# ===================================================================
# 6. DATA QUALITY
# ===================================================================

class TestDataQuality:

    def test_D1_all_knowledge_points_present(self) -> None:
        """payload contains all tested knowledge points."""
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state())
        names = {s["name"] for s in payload["subtopics"]}
        assert names == {"right triangle", "Pythagorean formula", "converse theorem"}

    def test_D2_correct_count_matches(self) -> None:
        """is_correct matches actual answers."""
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state())
        qs = [q for st in payload["subtopics"] for q in st["questions"]]
        assert sum(q["is_correct"] for q in qs) == 3

    def test_D3_topic_preserved(self) -> None:
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state("calculus"))
        assert payload["topic"] == "calculus"

    def test_D4_goal_text_has_topic(self) -> None:
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state("probability theory"))
        assert "probability theory" in payload["goal_text"]

    def test_D5_graph_nodes_as_subtopics(self) -> None:
        """[missing] knowledge graph nodes should be convertible to subtopics."""
        payload = _build_ingest_payload("u", "s", _make_graph_only_state("set theory"))
        assert len(payload["subtopics"]) > 0, \
            "❌ missing: knowledge graph nodes not converted to subtopics"

    def test_D6_lesson_info_in_payload(self) -> None:
        """[missing] lesson information should appear in payload."""
        payload = _build_ingest_payload("u", "s", _make_lesson_state())
        s = str(payload).lower()
        assert any(kw in s for kw in ["lesson", "review"]), \
            "❌ missing: payload contains no lesson information"
