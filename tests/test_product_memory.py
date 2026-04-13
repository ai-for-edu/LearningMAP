"""Product-level memory system audit tests.

Tests every memory capability a production education AI should have.
Tests are grouped by category. EXPECTED FAILURES are marked with
comments — they represent missing features, not bugs.

Uses a subprocess to start student_memory service (avoids venv conflicts).
Run: uv run python -m pytest tests/test_product_memory.py -v
"""

from __future__ import annotations

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
            "PATH": str(venv_python.parent),
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

def _make_diagnosis_state(topic: str = "勾股定理", session_id: str = "s1") -> dict:
    return {
        "topic": topic,
        "knowledge_graph": KnowledgeGraph(
            topic=topic,
            nodes=[
                KnowledgeNode(id="n1", name="直角三角形", description="直角三角形的定义", level="prerequisite"),
                KnowledgeNode(id="n2", name="勾股定理公式", description="a²+b²=c²", level="core"),
                KnowledgeNode(id="n3", name="逆定理", description="勾股定理的逆定理", level="extension"),
            ],
            edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
        ),
        "questions": [
            QuizQuestion(id="q1", knowledge_point="直角三角形", question="哪个是直角三角形?", options=["A","B","C","D"], answer=0),
            QuizQuestion(id="q2", knowledge_point="勾股定理公式", question="3²+4²=?", options=["20","25","30","35"], answer=1),
            QuizQuestion(id="q3", knowledge_point="勾股定理公式", question="求c", options=["13","14","15","16"], answer=0),
            QuizQuestion(id="q4", knowledge_point="逆定理", question="能构成直角三角形吗?", options=["能","不能","不确定","缺"], answer=0),
        ],
        "answers": [0, 1, 2, 0],  # q1✓ q2✓ q3✗ q4✓
        "diagnosis_result": DiagnosisResult(
            score=75, total=4, correct=3, incorrect=1,
            breakdown=[
                DiagnosisBreakdown(name="直角三角形", status="mastered", detail="1/1 已掌握"),
                DiagnosisBreakdown(name="勾股定理公式", status="weak", detail="1/2 需复习"),
                DiagnosisBreakdown(name="逆定理", status="mastered", detail="1/1 已掌握"),
            ],
        ),
    }


def _make_graph_only_state(topic: str = "二次方程") -> dict:
    return {
        "topic": topic,
        "knowledge_graph": KnowledgeGraph(
            topic=topic,
            nodes=[
                KnowledgeNode(id="n1", name="一元二次方程", description="ax²+bx+c=0", level="core"),
                KnowledgeNode(id="n2", name="判别式", description="Δ=b²-4ac", level="core"),
            ],
            edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
        ),
        "questions": [],
        "answers": [],
        "diagnosis_result": None,
    }


def _make_lesson_state(topic: str = "勾股定理") -> dict:
    state = _make_diagnosis_state(topic=topic)
    state["lesson_content"] = "# 勾股定理复习\n\n## 重点：勾股定理公式\na²+b²=c²"
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
# 1. MEMORY WRITE COVERAGE — 哪些事件应该产生记忆？
# ===================================================================

class TestWriteCoverage:

    def test_W1_write_after_diagnosis(self, memory_service: httpx.Client) -> None:
        """[已有] 诊断完成后应写入记忆。"""
        with _patch_to_service(memory_service):
            ingest_session("w1", "w1_diag", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "w1", "query": "勾股定理", "top_k": 10})
        assert len(resp.json()["results"]) > 0, "诊断后应有记忆"

    def test_W2_write_after_knowledge_graph_only(self, memory_service: httpx.Client) -> None:
        """[缺失] 只生成知识图谱（不做诊断）也应写入记忆。"""
        payload = _build_ingest_payload("w2", "w2_graph", _make_graph_only_state("集合论"))
        assert len(payload["subtopics"]) > 0, \
            "❌ 缺失：知识图谱 nodes 未转化为 subtopics，无诊断时记忆为空"

    def test_W3_write_after_lesson_generation(self, memory_service: httpx.Client) -> None:
        """[缺失] 课程生成后 payload 应包含课程信息。"""
        payload = _build_ingest_payload("w3", "w3_lesson", _make_lesson_state())
        payload_str = str(payload).lower()
        assert "lesson" in payload_str or "课程" in payload_str or "复习" in payload_str, \
            "❌ 缺失：ingest payload 不含任何课程信息"

    def test_W4_write_chat_only(self, memory_service: httpx.Client) -> None:
        """[缺失] 纯聊天也应产生轻量记忆。"""
        payload = _build_ingest_payload("w4", "w4_chat", _make_chat_only_state())
        assert len(payload["subtopics"]) > 0, \
            "❌ 缺失：纯聊天（无诊断/无图谱）时 subtopics 为空，无法产生记忆"

    def test_W5_accuracy_recorded(self, memory_service: httpx.Client) -> None:
        """[已有] 记忆应包含正确率信息。"""
        with _patch_to_service(memory_service):
            ingest_session("w5", "w5_acc", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "w5", "query": "勾股定理", "top_k": 10})
        ctx = resp.json()["context_text"]
        has_accuracy = any(kw in ctx for kw in ["50", "100", "correct", "accuracy"])
        assert has_accuracy, "记忆应包含正确率"

    def test_W6_weak_points_in_memory(self, memory_service: httpx.Client) -> None:
        """[已有] 记忆应包含薄弱知识点名称。"""
        with _patch_to_service(memory_service):
            ingest_session("w6", "w6_weak", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "w6", "query": "勾股定理", "top_k": 10})
        ctx = resp.json()["context_text"]
        assert "勾股定理公式" in ctx, "记忆应包含知识点名称"


# ===================================================================
# 2. MEMORY READ COVERAGE — 哪些环节应该读取记忆？
# ===================================================================

class TestReadCoverage:

    def test_R1_chat_node_reads(self) -> None:
        """[已有] chat_node 读取记忆。"""
        import inspect
        from src.agent.nodes.chat import chat_node
        assert "retrieve_memory" in inspect.getsource(chat_node)

    def test_R2_lesson_node_reads(self) -> None:
        """[缺失] lesson_node 应读取历史薄弱点来个性化课程。"""
        import inspect
        from src.agent.nodes.lesson import lesson_node
        assert "retrieve_memory" in inspect.getsource(lesson_node), \
            "❌ 缺失：lesson_node 未读取记忆，无法基于历史薄弱点生成个性化课程"

    def test_R3_knowledge_graph_node_reads(self) -> None:
        """[缺失] knowledge_graph_node 应读取历史。"""
        import inspect
        from src.agent.nodes.knowledge_graph import knowledge_graph_node
        assert "retrieve_memory" in inspect.getsource(knowledge_graph_node), \
            "❌ 缺失：knowledge_graph_node 未读取记忆"

    def test_R4_quiz_node_reads(self) -> None:
        """[缺失] quiz_node 应基于历史薄弱点侧重出题。"""
        import inspect
        from src.agent.nodes.quiz import quiz_node
        assert "retrieve_memory" in inspect.getsource(quiz_node), \
            "❌ 缺失：quiz_node 未读取记忆，不能侧重历史薄弱点出题"


# ===================================================================
# 3. API INGEST TRIGGERS — API 层 ingest 触发点
# ===================================================================

class TestIngestTriggers:

    def test_T1_submit_quiz_triggers(self) -> None:
        """[已有] submit_quiz 触发 ingest。"""
        import inspect
        from src.api.chat import submit_quiz
        assert "ingest_session" in inspect.getsource(submit_quiz)

    def test_T2_generate_lesson_triggers(self) -> None:
        """[缺失] generate_lesson 应触发 ingest。"""
        import inspect
        from src.api.chat import generate_lesson
        assert "ingest" in inspect.getsource(generate_lesson), \
            "❌ 缺失：generate_lesson 未触发 ingest"

    def test_T3_generate_slides_triggers(self) -> None:
        """[缺失] generate_slides 应触发 ingest。"""
        import inspect
        from src.api.chat import generate_slides
        assert "ingest" in inspect.getsource(generate_slides), \
            "❌ 缺失：generate_slides 未触发 ingest"

    def test_T4_chat_graph_mode_triggers(self) -> None:
        """[缺失] /api/chat 在 mode=graph 时应触发 ingest。"""
        import inspect
        from src.api.chat import chat
        src = inspect.getsource(chat)
        assert "graph" in src and "ingest" in src, \
            "❌ 缺失：/api/chat 在生成知识图谱后未触发 ingest"


# ===================================================================
# 4. CROSS-SESSION USER JOURNEYS
# ===================================================================

class TestUserJourneys:

    def test_J1_what_did_i_learn(self, memory_service: httpx.Client) -> None:
        """Session A 学勾股定理 → Session B 问'我之前学了什么'。"""
        with _patch_to_service(memory_service):
            ingest_session("j1", "j1_s", _make_diagnosis_state())
            ctx = retrieve_memory("j1", "我之前学了什么")
        assert ctx != "" and "勾股定理" in ctx

    def test_J2_ask_weak_points(self, memory_service: httpx.Client) -> None:
        """Session A 诊断薄弱 → Session B 问'哪里薄弱'。"""
        with _patch_to_service(memory_service):
            ingest_session("j2", "j2_s", _make_diagnosis_state())
            ctx = retrieve_memory("j2", "我哪些知识点薄弱")
        has_info = any(kw in ctx for kw in ["weak", "wrong", "需复习", "incorrect"])
        assert has_info, "应能检索到薄弱点信息"

    def test_J3_multiple_sessions_accumulate(self, memory_service: httpx.Client) -> None:
        """两个 session 的记忆应累积。"""
        s1 = _make_diagnosis_state(topic="勾股定理")
        s2 = _make_diagnosis_state(topic="二次函数")
        with _patch_to_service(memory_service):
            ingest_session("j3", "j3_a", s1)
            ingest_session("j3", "j3_b", s2)
        resp = memory_service.post("/retrieve", json={"user_id": "j3", "query": "数学", "top_k": 20})
        results = resp.json()["results"]
        topics = {r["topic"] for r in results}
        assert len(topics) >= 2, "应有两个主题的记忆"

    def test_J4_topic_relevance_ranking(self, memory_service: httpx.Client) -> None:
        """查勾股定理时，勾股定理的记忆应排在物理记忆前面。"""
        with _patch_to_service(memory_service):
            ingest_session("j4", "j4_math", _make_diagnosis_state("勾股定理"))
            ingest_session("j4", "j4_phys", _make_diagnosis_state("牛顿运动定律"))
        resp = memory_service.post("/retrieve", json={"user_id": "j4", "query": "勾股定理", "top_k": 5})
        results = resp.json()["results"]
        if results:
            assert results[0]["topic"] == "勾股定理", f"Top result should be 勾股定理, got {results[0]['topic']}"

    def test_J5_user_isolation(self, memory_service: httpx.Client) -> None:
        """用户 A 的记忆用户 B 看不到。"""
        with _patch_to_service(memory_service):
            ingest_session("j5_a", "j5_s", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "j5_b", "query": "勾股定理", "top_k": 5})
        assert len(resp.json()["results"]) == 0

    def test_J6_duplicate_ingest_idempotent(self, memory_service: httpx.Client) -> None:
        """同一 session 重复 ingest 不会产生重复记忆。"""
        with _patch_to_service(memory_service):
            ingest_session("j6", "j6_dup", _make_diagnosis_state())
            ingest_session("j6", "j6_dup", _make_diagnosis_state())
        resp = memory_service.post("/retrieve", json={"user_id": "j6", "query": "勾股定理", "top_k": 20})
        # 3 knowledge points, should have exactly 3 memories, not 6
        assert len(resp.json()["results"]) == 3


# ===================================================================
# 5. GRACEFUL DEGRADATION
# ===================================================================

class TestGracefulDegradation:

    def test_G1_retrieve_when_down(self) -> None:
        """记忆服务不可用时 retrieve 返回空。"""
        with patch("src.memory_client._get_client") as m:
            m.return_value.post.side_effect = httpx.ConnectError("refused")
            assert retrieve_memory("u", "q") == ""

    def test_G2_ingest_when_down(self) -> None:
        """记忆服务不可用时 ingest 不崩溃。"""
        with patch("src.memory_client._get_client") as m:
            m.return_value.post.side_effect = httpx.ConnectError("refused")
            ingest_session("u", "s", _make_diagnosis_state())  # should not raise

    def test_G3_empty_retrieve(self, memory_service: httpx.Client) -> None:
        """无记忆时 retrieve 返回稳定空响应。"""
        resp = memory_service.post("/retrieve", json={"user_id": "nobody", "query": "什么", "top_k": 5})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 0


# ===================================================================
# 6. DATA QUALITY
# ===================================================================

class TestDataQuality:

    def test_D1_all_knowledge_points_present(self) -> None:
        """payload 包含所有被测试的知识点。"""
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state())
        names = {s["name"] for s in payload["subtopics"]}
        assert names == {"直角三角形", "勾股定理公式", "逆定理"}

    def test_D2_correct_count_matches(self) -> None:
        """is_correct 与实际答案一致。"""
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state())
        qs = [q for st in payload["subtopics"] for q in st["questions"]]
        assert sum(q["is_correct"] for q in qs) == 3

    def test_D3_topic_preserved(self) -> None:
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state("微积分"))
        assert payload["topic"] == "微积分"

    def test_D4_goal_text_has_topic(self) -> None:
        payload = _build_ingest_payload("u", "s", _make_diagnosis_state("概率论"))
        assert "概率论" in payload["goal_text"]

    def test_D5_graph_nodes_as_subtopics(self) -> None:
        """[缺失] 知识图谱 nodes 应能转为 subtopics。"""
        payload = _build_ingest_payload("u", "s", _make_graph_only_state("集合论"))
        assert len(payload["subtopics"]) > 0, \
            "❌ 缺失：知识图谱 nodes 未转化为 subtopics"

    def test_D6_lesson_info_in_payload(self) -> None:
        """[缺失] 课程信息应出现在 payload 中。"""
        payload = _build_ingest_payload("u", "s", _make_lesson_state())
        s = str(payload).lower()
        assert any(kw in s for kw in ["lesson", "课程", "复习"]), \
            "❌ 缺失：payload 中无课程信息"
