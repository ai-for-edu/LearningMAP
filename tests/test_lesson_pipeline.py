"""Tests for the two-step lesson generation pipeline.

Tests cover:
- knowledge_skill.j2 and lesson_point.j2 template rendering
- retrieve_skills() with pedagogy + knowledge skills
- generate_lesson_per_point() concatenation
- lesson_node() end-to-end with mock LLM
- use_knowledge_skills toggle behavior
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from src.llm.schemas import (
    DiagnosisBreakdown,
    DiagnosisResult,
    KnowledgeGraph,
    KnowledgeNode,
)
from src.prompts.loader import render_prompt


# ---------------------------------------------------------------------------
# Prompt template tests
# ---------------------------------------------------------------------------


def test_knowledge_skill_prompt_renders():
    result = render_prompt(
        "knowledge_skill",
        knowledge_point_name="勾股定理",
        knowledge_point_description="直角三角形三边关系 a²+b²=c²",
        pedagogy_method="费曼学习法",
        pedagogy_instructions="用简单语言解释概念，多用类比",
        topic="初中数学-勾股定理",
    )
    assert "勾股定理" in result
    assert "费曼学习法" in result
    assert "a²+b²=c²" in result


def test_lesson_point_prompt_renders():
    result = render_prompt(
        "lesson_point",
        topic="初中数学-勾股定理",
        knowledge_point_name="勾股定理",
        knowledge_point_description="直角三角形三边关系",
        pedagogy_instructions="用简单语言解释",
        knowledge_skill="教学切入点：从测量距离出发...",
        point_index=1,
        total_points=3,
    )
    assert "勾股定理" in result
    assert "教学切入点" in result


def test_lesson_point_prompt_without_knowledge_skill():
    result = render_prompt(
        "lesson_point",
        topic="初中数学",
        knowledge_point_name="基础",
        knowledge_point_description="desc",
        pedagogy_instructions="用简单语言解释",
        knowledge_skill="无额外教学技能",
        point_index=1,
        total_points=1,
    )
    assert "知识点教学技能" not in result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(responses: list[str]):
    client = MagicMock()
    side_effects = []
    for text in responses:
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = text
        side_effects.append(resp)
    client.chat.completions.create.side_effect = side_effects
    return client


def _make_graph_and_diagnosis(
    weak_names: list[str], mastered_names: list[str] | None = None
):
    nodes = []
    breakdown = []
    for i, name in enumerate(weak_names):
        nodes.append(
            KnowledgeNode(
                id=f"w{i}", name=name, description=f"{name} desc", level="core"
            )
        )
        breakdown.append(DiagnosisBreakdown(name=name, status="weak", detail="0/2"))
    for i, name in enumerate(mastered_names or []):
        nodes.append(
            KnowledgeNode(
                id=f"m{i}", name=name, description=f"{name} desc", level="core"
            )
        )
        breakdown.append(DiagnosisBreakdown(name=name, status="mastered", detail="2/2"))

    graph = KnowledgeGraph(topic="测试主题", nodes=nodes, edges=[])
    diagnosis = DiagnosisResult(
        score=0,
        total=len(nodes) * 2,
        correct=len(mastered_names or []) * 2,
        incorrect=len(weak_names) * 2,
        breakdown=breakdown,
    )
    return graph, diagnosis


# ---------------------------------------------------------------------------
# retrieve_skills tests
# ---------------------------------------------------------------------------


def test_retrieve_skills_returns_both_skill_types():
    from src.agent.nodes.lesson import retrieve_skills

    graph, diagnosis = _make_graph_and_diagnosis(["基础"], ["逆定理"])
    state = {
        "topic": "勾股定理",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "用简单语言解释概念"},
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(["教学切入点：..."])
    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        pedagogy, knowledge_skills, weak_points = retrieve_skills(state)

    assert pedagogy == "用简单语言解释概念"
    assert len(knowledge_skills) == 1
    assert "基础" in knowledge_skills
    assert len(weak_points) == 1


def test_retrieve_skills_filters_blank_names():
    """Breakdown entries with empty/whitespace names must not produce skills.

    Regression: an empty-named weak point led to knowledge_skills[""] being
    filled with an LLM fallback card (defaulting to the topic), rendering as a
    ghost ▶ row in the lesson sidebar.
    """
    from src.agent.nodes.lesson import retrieve_skills

    graph, diagnosis = _make_graph_and_diagnosis(["基础", "", "  "])
    state = {
        "topic": "勾股定理",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "用简单语言解释"},
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(["技能卡片内容"])
    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        pedagogy, knowledge_skills, weak_points = retrieve_skills(state)

    assert list(knowledge_skills.keys()) == ["基础"]
    assert [wp["name"] for wp in weak_points] == ["基础"]
    assert mock_client.chat.completions.create.call_count == 1


def test_retrieve_skills_skips_when_disabled():
    from src.agent.nodes.lesson import retrieve_skills

    graph, diagnosis = _make_graph_and_diagnosis(["基础", "应用"])
    state = {
        "topic": "勾股定理",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "用简单语言"},
        "use_knowledge_skills": False,
    }

    mock_client = _make_mock_client([])
    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        pedagogy, knowledge_skills, weak_points = retrieve_skills(state)

    assert knowledge_skills == {}
    assert len(weak_points) == 2
    mock_client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# generate_lesson_per_point tests
# ---------------------------------------------------------------------------


def test_generate_lesson_per_point_concatenates():
    from src.agent.nodes.lesson import generate_lesson_per_point

    mock_client = _make_mock_client(
        [
            "## 基础\n\n内容A",
            "## 应用\n\n内容B",
        ]
    )
    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        result = generate_lesson_per_point(
            topic="勾股定理",
            weak_points=[
                {"name": "基础", "description": "desc1"},
                {"name": "应用", "description": "desc2"},
            ],
            pedagogy_instructions="用简单语言",
            knowledge_skills={"基础": "切入点A", "应用": "切入点B"},
        )

    assert "## 基础" in result
    assert "## 应用" in result
    assert "---" in result
    assert mock_client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# lesson_node integration tests
# ---------------------------------------------------------------------------


def test_lesson_node_two_step_pipeline():
    """2 weak points → 4 LLM calls: 2 knowledge skills + 2 lesson sections."""
    from src.agent.nodes.lesson import lesson_node

    graph, diagnosis = _make_graph_and_diagnosis(["基础", "应用"], ["逆定理"])
    state = {
        "messages": [],
        "mode": "result",
        "topic": "勾股定理",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "用类比解释"},
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(
        [
            "教学技能：基础切入...",
            "教学技能：应用切入...",
            "## 基础\n\n内容...",
            "## 应用\n\n内容...",
        ]
    )

    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        result = lesson_node(state)

    assert result["mode"] == "lesson"
    assert "基础" in result["lesson_content"]
    assert "应用" in result["lesson_content"]
    assert result["knowledge_skills"] is not None
    assert "基础" in result["knowledge_skills"]
    assert mock_client.chat.completions.create.call_count == 4


def test_lesson_node_knowledge_skills_disabled():
    """use_knowledge_skills=False → only 2 LLM calls (lesson content only)."""
    from src.agent.nodes.lesson import lesson_node

    graph, diagnosis = _make_graph_and_diagnosis(["基础", "应用"])
    state = {
        "messages": [],
        "mode": "result",
        "topic": "勾股定理",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "用类比解释"},
        "use_knowledge_skills": False,
    }

    mock_client = _make_mock_client(
        [
            "## 基础\n\n内容...",
            "## 应用\n\n内容...",
        ]
    )

    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        result = lesson_node(state)

    assert result["mode"] == "lesson"
    assert result["knowledge_skills"] is None
    assert mock_client.chat.completions.create.call_count == 2


def test_lesson_node_all_mastered():
    """All mastered → 1 LLM call (review lesson), no knowledge skills."""
    from src.agent.nodes.lesson import lesson_node

    graph, diagnosis = _make_graph_and_diagnosis([], ["基础", "应用"])
    state = {
        "messages": [],
        "mode": "result",
        "topic": "勾股定理",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": None,
        "active_skill_content": None,
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(["复习内容..."])

    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        result = lesson_node(state)

    assert result["mode"] == "lesson"
    assert result["knowledge_skills"] is None
    assert mock_client.chat.completions.create.call_count == 1


# ---------------------------------------------------------------------------
# Wiring tests: focus_directive flows into live LLM prompt (I-1)
# ---------------------------------------------------------------------------


def test_generate_lesson_per_point_threads_focus_directive_into_prompt():
    """Focus directive must appear in the rendered lesson_point prompt."""
    from src.agent.nodes.lesson import generate_lesson_per_point

    weak_points = [{"name": "分配律", "description": "..."}]
    pedagogy_instructions = ""
    knowledge_skills = {}
    directive = "学生在 分配律 上仍薄弱，本次教案应优先衔接这些点。"

    with patch("src.agent.nodes.lesson.get_raw_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "教案"
        mock_client.return_value.chat.completions.create.return_value = mock_resp

        generate_lesson_per_point(
            "二次方程", weak_points, pedagogy_instructions, knowledge_skills,
            focus_directive=directive,
        )

        # Inspect the prompt that was actually sent to the mock LLM.
        call_kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
        sent_prompt = call_kwargs["messages"][0]["content"]
        assert directive in sent_prompt
        assert "复习反馈指令" in sent_prompt
