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
        knowledge_point_name="Pythagorean theorem",
        knowledge_point_description="right triangle side relationship a²+b²=c²",
        pedagogy_method="Feynman Technique",
        pedagogy_instructions="Explain concepts in simple language, use analogies",
        topic="middle school math - Pythagorean theorem",
    )
    assert "Pythagorean theorem" in result
    assert "Feynman Technique" in result
    assert "a²+b²=c²" in result


def test_lesson_point_prompt_renders():
    result = render_prompt(
        "lesson_point",
        topic="middle school math - Pythagorean theorem",
        knowledge_point_name="Pythagorean theorem",
        knowledge_point_description="right triangle side relationship",
        pedagogy_instructions="Explain in simple language",
        knowledge_skill="Teaching entry point: starting from measuring distance...",
        point_index=1,
        total_points=3,
    )
    assert "Pythagorean theorem" in result
    assert "Teaching entry point" in result


def test_lesson_point_prompt_without_knowledge_skill():
    result = render_prompt(
        "lesson_point",
        topic="middle school math",
        knowledge_point_name="fundamentals",
        knowledge_point_description="desc",
        pedagogy_instructions="Explain in simple language",
        knowledge_skill="no additional teaching strategy",
        point_index=1,
        total_points=1,
    )
    assert "Knowledge-Point Teaching Strategy" not in result


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

    graph = KnowledgeGraph(topic="test topic", nodes=nodes, edges=[])
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

    graph, diagnosis = _make_graph_and_diagnosis(["fundamentals"], ["converse theorem"])
    state = {
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "Explain concepts in simple language"},
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(["Teaching entry point: ..."])
    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        pedagogy, knowledge_skills, weak_points = retrieve_skills(state)

    assert pedagogy == "Explain concepts in simple language"
    assert len(knowledge_skills) == 1
    assert "fundamentals" in knowledge_skills
    assert len(weak_points) == 1


def test_retrieve_skills_filters_blank_names():
    """Breakdown entries with empty/whitespace names must not produce skills.

    Regression: an empty-named weak point led to knowledge_skills[""] being
    filled with an LLM fallback card (defaulting to the topic), rendering as a
    ghost ▶ row in the lesson sidebar.
    """
    from src.agent.nodes.lesson import retrieve_skills

    graph, diagnosis = _make_graph_and_diagnosis(["fundamentals", "", "  "])
    state = {
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "Explain in simple language"},
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(["Skill card content"])
    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        pedagogy, knowledge_skills, weak_points = retrieve_skills(state)

    assert list(knowledge_skills.keys()) == ["fundamentals"]
    assert [wp["name"] for wp in weak_points] == ["fundamentals"]
    assert mock_client.chat.completions.create.call_count == 1


def test_retrieve_skills_drops_review_strong_kps():
    """KPs marked strong by the latest review must not reach lesson generation.

    Without this filter, focus_directive is only a soft prompt hint and the
    lesson still produces a section per diagnosis-weak point — defeating the
    point of the review.
    """
    from src.agent.nodes.lesson import retrieve_skills

    graph, diagnosis = _make_graph_and_diagnosis(["fundamentals", "applications", "extensions"])
    state = {
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "use_knowledge_skills": False,
        "review_result": {
            "weak_kps": ["applications"],
            "strong_kps": ["fundamentals", "extensions"],
            "score": 0.33,
            "focus_directive": "...",
        },
    }
    _, _, weak_points = retrieve_skills(state)
    assert [wp["name"] for wp in weak_points] == ["applications"]


def test_retrieve_skills_no_review_keeps_all_weak():
    """Sanity: missing review_result leaves the diagnosis-weak list untouched."""
    from src.agent.nodes.lesson import retrieve_skills

    graph, diagnosis = _make_graph_and_diagnosis(["fundamentals", "applications"])
    state = {
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "use_knowledge_skills": False,
        "review_result": None,
    }
    _, _, weak_points = retrieve_skills(state)
    assert {wp["name"] for wp in weak_points} == {"fundamentals", "applications"}


def test_retrieve_skills_skips_when_disabled():
    from src.agent.nodes.lesson import retrieve_skills

    graph, diagnosis = _make_graph_and_diagnosis(["fundamentals", "applications"])
    state = {
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "Simple language"},
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
            "## fundamentals\n\nContent A",
            "## applications\n\nContent B",
        ]
    )
    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        result = generate_lesson_per_point(
            topic="Pythagorean theorem",
            weak_points=[
                {"name": "fundamentals", "description": "desc1"},
                {"name": "applications", "description": "desc2"},
            ],
            pedagogy_instructions="Simple language",
            knowledge_skills={"fundamentals": "Entry point A", "applications": "Entry point B"},
        )

    assert "## fundamentals" in result
    assert "## applications" in result
    assert "---" in result
    assert mock_client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# lesson_node integration tests
# ---------------------------------------------------------------------------


def test_lesson_node_two_step_pipeline():
    """2 weak points → 4 LLM calls: 2 knowledge skills + 2 lesson sections."""
    from src.agent.nodes.lesson import lesson_node

    graph, diagnosis = _make_graph_and_diagnosis(["fundamentals", "applications"], ["converse theorem"])
    state = {
        "messages": [],
        "mode": "result",
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "Explain with analogies"},
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(
        [
            "Teaching skill: fundamentals entry...",
            "Teaching skill: applications entry...",
            "## fundamentals\n\nContent...",
            "## applications\n\nContent...",
        ]
    )

    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        result = lesson_node(state)

    assert result["mode"] == "lesson"
    assert "fundamentals" in result["lesson_content"]
    assert "applications" in result["lesson_content"]
    assert result["knowledge_skills"] is not None
    assert "fundamentals" in result["knowledge_skills"]
    assert mock_client.chat.completions.create.call_count == 4


def test_lesson_node_knowledge_skills_disabled():
    """use_knowledge_skills=False → only 2 LLM calls (lesson content only)."""
    from src.agent.nodes.lesson import lesson_node

    graph, diagnosis = _make_graph_and_diagnosis(["fundamentals", "applications"])
    state = {
        "messages": [],
        "mode": "result",
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "Explain with analogies"},
        "use_knowledge_skills": False,
    }

    mock_client = _make_mock_client(
        [
            "## fundamentals\n\nContent...",
            "## applications\n\nContent...",
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

    graph, diagnosis = _make_graph_and_diagnosis([], ["fundamentals", "applications"])
    state = {
        "messages": [],
        "mode": "result",
        "topic": "Pythagorean theorem",
        "knowledge_graph": graph,
        "diagnosis_result": diagnosis,
        "active_skill": None,
        "active_skill_content": None,
        "use_knowledge_skills": True,
    }

    mock_client = _make_mock_client(["Review content..."])

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

    weak_points = [{"name": "distributive law", "description": "..."}]
    pedagogy_instructions = ""
    knowledge_skills = {}
    directive = "The student is still weak on distributive law; prioritize bridging this point in the lesson plan."

    with patch("src.agent.nodes.lesson.get_raw_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Lesson plan"
        mock_client.return_value.chat.completions.create.return_value = mock_resp

        generate_lesson_per_point(
            "quadratic equations", weak_points, pedagogy_instructions, knowledge_skills,
            focus_directive=directive,
        )

        # Inspect the prompt that was actually sent to the mock LLM.
        call_kwargs = mock_client.return_value.chat.completions.create.call_args.kwargs
        sent_prompt = call_kwargs["messages"][0]["content"]
        assert directive in sent_prompt
        assert "Review Feedback Directive" in sent_prompt
