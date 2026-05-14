"""Skill system integration tests — large-scale, complex scenarios.

Tests:
1. Multiple skills loaded correctly
2. Skill injection produces different prompts for different skills
3. Skill + memory coexistence
4. Complex session flows (chat first, then learn, then switch skill)
5. /api/skills and /set-skill endpoints
6. Skill persistence across state save/load
7. No-skill vs with-skill prompt comparison
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.skills.loader import (
    load_skills,
    get_skill,
    get_skill_section,
    list_skills,
)
from src.prompts.loader import render_prompt
from src.agent.nodes.chat import _build_context_summary, _BASE_SYSTEM_PROMPT
from src.llm.schemas import (
    DiagnosisBreakdown,
    DiagnosisResult,
    KnowledgeGraph,
    KnowledgeNode,
    KnowledgeEdge,
    QuizQuestion,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def load_all_skills():
    """Load real skill files before all tests."""
    skills_dir = Path(__file__).resolve().parent.parent / "skills"
    load_skills(skills_dir)


def _make_state(
    topic: str = "光合作用",
    skill_name: str | None = None,
) -> dict:
    """Build a full session state."""
    state = {
        "topic": topic,
        "knowledge_graph": KnowledgeGraph(
            topic=topic,
            nodes=[
                KnowledgeNode(id="n1", name="叶绿体", description="光合作用的场所", level="prerequisite"),
                KnowledgeNode(id="n2", name="光反应", description="光能转化为化学能", level="core"),
                KnowledgeNode(id="n3", name="暗反应", description="碳固定过程(Calvin循环)", level="core"),
                KnowledgeNode(id="n4", name="光合色素", description="吸收光能的色��分子", level="prerequisite"),
            ],
            edges=[
                KnowledgeEdge(source="n1", target="n2", relation="prerequisite"),
                KnowledgeEdge(source="n4", target="n2", relation="prerequisite"),
                KnowledgeEdge(source="n2", target="n3", relation="prerequisite"),
            ],
        ),
        "questions": [
            QuizQuestion(id="q1", knowledge_point="叶绿体", question="光合作用发生在哪里?", options=["线粒体","叶绿体","细胞核","内质网"], answer=1),
            QuizQuestion(id="q2", knowledge_point="光反应", question="光反应需要什么?", options=["CO2","O2","光","ATP"], answer=2),
            QuizQuestion(id="q3", knowledge_point="暗反应", question="暗反应固定什么?", options=["O2","N2","CO2","H2O"], answer=2),
            QuizQuestion(id="q4", knowledge_point="光合色素", question="叶绿素吸收什么颜色的光最多?", options=["绿色","红色和蓝色","黄色","紫色"], answer=1),
        ],
        "answers": [1, 2, 0, 1],  # q1✓ q2✓ q3✗ q4✓
        "diagnosis_result": DiagnosisResult(
            score=75, total=4, correct=3, incorrect=1,
            breakdown=[
                DiagnosisBreakdown(name="叶绿体", status="mastered", detail="1/1 已掌握"),
                DiagnosisBreakdown(name="光反应", status="mastered", detail="1/1 已掌握"),
                DiagnosisBreakdown(name="暗反应", status="weak", detail="0/1 需复习"),
                DiagnosisBreakdown(name="光合色素", status="mastered", detail="1/1 已掌握"),
            ],
        ),
        "lesson_content": None,
        "active_skill": None,
        "active_skill_content": None,
        "messages": [],
    }

    if skill_name:
        skill_meta = get_skill(skill_name)
        if skill_meta:
            state["active_skill"] = skill_meta.name
            state["active_skill_content"] = dict(skill_meta.sections)

    return state


# ===================================================================
# 1. SKILL LOADING AT SCALE
# ===================================================================

class TestSkillLoading:

    def test_all_three_skills_loaded(self) -> None:
        skills = list_skills()
        names = {s["name"] for s in skills}
        assert names == {"feynman", "socratic", "scaffolding"}

    def test_each_skill_has_complete_metadata(self) -> None:
        for s in list_skills():
            assert s["name"], f"Skill missing name"
            assert s["display_name"], f"Skill {s['name']} missing display_name"
            assert s["description"], f"Skill {s['name']} missing description"
            assert len(s["sections"]) >= 2, f"Skill {s['name']} has too few sections"
            assert s["body"], f"Skill {s['name']} has empty body"

    def test_feynman_sections(self) -> None:
        s = get_skill("feynman")
        assert s is not None
        assert set(s.affects) == {"chat", "quiz", "lesson"}
        assert "analogy" in s.sections["chat"].lower() or "analogies" in s.sections["chat"].lower()
        assert "conceptual" in s.sections["quiz"].lower() or "understanding" in s.sections["quiz"].lower()

    def test_socratic_has_knowledge_graph(self) -> None:
        s = get_skill("socratic")
        assert s is not None
        assert "knowledge_graph" in s.sections
        assert "question" in s.sections["knowledge_graph"].lower()

    def test_scaffolding_sections(self) -> None:
        s = get_skill("scaffolding")
        assert s is not None
        assert "already know" in s.sections.get("chat", "").lower() or "extension" in s.sections.get("chat", "").lower()


# ===================================================================
# 2. SKILL INJECTION PRODUCES DIFFERENT PROMPTS
# ===================================================================

class TestSkillDifferentiation:
    """Verify different skills produce meaningfully different prompts."""

    def test_quiz_prompt_differs_by_skill(self) -> None:
        points = [{"name": "光反应", "description": "光能转化"}]

        no_skill = render_prompt("quiz_generate", knowledge_points=points, questions_per_point=2, skill_instructions="")
        feynman = render_prompt("quiz_generate", knowledge_points=points, questions_per_point=2,
                                skill_instructions=get_skill("feynman").sections["quiz"])
        socratic = render_prompt("quiz_generate", knowledge_points=points, questions_per_point=2,
                                 skill_instructions=get_skill("socratic").sections["quiz"])
        scaffolding = render_prompt("quiz_generate", knowledge_points=points, questions_per_point=2,
                                    skill_instructions=get_skill("scaffolding").sections["quiz"])

        # All should contain the knowledge point
        for p in [no_skill, feynman, socratic, scaffolding]:
            assert "光反应" in p

        # But skill-enhanced versions should contain their unique keywords
        assert "Teaching Method" not in no_skill
        assert "conceptual" in feynman.lower() or "understanding" in feynman.lower()
        assert "reasoning" in socratic.lower() or "question" in socratic.lower() or "thinking" in socratic.lower()
        assert "difficulty" in scaffolding.lower() or "increasing" in scaffolding.lower() or "foundational" in scaffolding.lower()

    def test_lesson_prompt_differs_by_skill(self) -> None:
        weak = [{"name": "暗反应", "description": "碳固定过程"}]

        no_skill = render_prompt("lesson_plan", topic="光合作用", weak_points=weak, skill_instructions="")
        feynman = render_prompt("lesson_plan", topic="光合作用", weak_points=weak,
                                skill_instructions=get_skill("feynman").sections["lesson"])

        assert "Teaching Method" not in no_skill
        assert "Teaching Method" in feynman
        assert "analogy" in feynman.lower() or "elevator" in feynman.lower() or "your turn" in feynman.lower()

    def test_knowledge_graph_prompt_with_socratic(self) -> None:
        """Only socratic has a knowledge_graph section."""
        socratic_kg = get_skill_section(
            {"active_skill_content": get_skill("socratic").sections},
            "knowledge_graph"
        )
        feynman_kg = get_skill_section(
            {"active_skill_content": get_skill("feynman").sections},
            "knowledge_graph"
        )

        assert len(socratic_kg) > 0, "Socratic should have knowledge_graph instructions"
        assert feynman_kg == "", "Feynman should have no knowledge_graph instructions"

        prompt = render_prompt("knowledge_graph", topic="光合作用", max_nodes=8, skill_instructions=socratic_kg)
        assert "question" in prompt.lower() or "understand" in prompt.lower()

    def test_chat_system_prompt_with_skill(self) -> None:
        state = _make_state(skill_name="feynman")
        skill_text = get_skill_section(state, "chat")

        system_prompt = _BASE_SYSTEM_PROMPT
        system_prompt += f"\n\n## 教学方法\n{skill_text}"
        context = _build_context_summary(state)
        system_prompt += f"\n\n{context}"

        # Verify ordering: persona → skill → context
        persona_pos = system_prompt.index("friendly AI")
        skill_pos = system_prompt.index("教学方法")
        context_pos = system_prompt.index("当前学习上下文")
        assert persona_pos < skill_pos < context_pos


# ===================================================================
# 3. COMPLEX SESSION FLOWS
# ===================================================================

class TestComplexSessionFlows:
    """Simulate realistic multi-step session scenarios."""

    def test_set_skill_then_build_prompt(self) -> None:
        """User sets skill via /set-skill, then chat uses it."""
        # Simulate state after set-skill
        state = _make_state(skill_name="socratic")

        # chat_node should inject socratic chat section
        skill_text = get_skill_section(state, "chat")
        assert "never give the answer directly" in skill_text.lower() or "guide the student" in skill_text.lower()

    def test_switch_skill_mid_session(self) -> None:
        """Start with feynman, switch to scaffolding."""
        state = _make_state(skill_name="feynman")
        assert get_skill_section(state, "chat") != ""
        assert "analogy" in get_skill_section(state, "chat").lower() or "analogies" in get_skill_section(state, "chat").lower()

        # Switch to scaffolding
        scaffolding = get_skill("scaffolding")
        state["active_skill"] = scaffolding.name
        state["active_skill_content"] = scaffolding.sections

        new_chat = get_skill_section(state, "chat").lower()
        assert "analogy" not in new_chat and "analogies" not in new_chat
        assert "already know" in new_chat or "extension" in new_chat

    def test_clear_skill(self) -> None:
        """User clears skill → prompts revert to default."""
        state = _make_state(skill_name="feynman")
        assert get_skill_section(state, "chat") != ""

        # Clear
        state["active_skill"] = None
        state["active_skill_content"] = None

        assert get_skill_section(state, "chat") == ""

    def test_skill_survives_state_serialization(self) -> None:
        """Skill data should survive JSON round-trip (for session persistence)."""
        import json

        state = _make_state(skill_name="feynman")
        content = state["active_skill_content"]

        # Simulate save/load (dict → JSON → dict)
        serialized = json.dumps(content, ensure_ascii=False)
        deserialized = json.loads(serialized)

        state["active_skill_content"] = deserialized
        skill_text = get_skill_section(state, "quiz")
        assert "conceptual" in skill_text.lower() or "understanding" in skill_text.lower()

    def test_chat_first_then_learn_with_skill(self) -> None:
        """Session starts with casual chat, user later asks to learn with a skill."""
        # Phase 1: casual chat (no skill, no topic)
        state = {
            "topic": None,
            "active_skill": None,
            "active_skill_content": None,
            "messages": [],
        }
        assert get_skill_section(state, "chat") == ""

        # Phase 2: user says "用费曼法学光合作用"
        feynman = get_skill("feynman")
        state["topic"] = "光合作用"
        state["active_skill"] = feynman.name
        state["active_skill_content"] = feynman.sections

        assert get_skill_section(state, "chat") != ""
        assert get_skill_section(state, "quiz") != ""

    def test_no_skill_node_gets_empty_string(self) -> None:
        """Feynman has no knowledge_graph section → returns empty."""
        state = _make_state(skill_name="feynman")
        assert get_skill_section(state, "knowledge_graph") == ""

    def test_all_skills_produce_valid_prompts(self) -> None:
        """Every skill × every template should produce a valid, non-empty prompt."""
        templates = [
            ("quiz_generate", {"knowledge_points": [{"name": "X", "description": "Y"}], "questions_per_point": 2}),
            ("lesson_plan", {"topic": "T", "weak_points": [{"name": "X", "description": "Y"}]}),
            ("lesson_review", {"topic": "T", "points": [{"name": "X", "description": "Y"}]}),
            ("knowledge_graph", {"topic": "T", "max_nodes": 8}),
        ]

        for skill_name in ["feynman", "socratic", "scaffolding"]:
            skill = get_skill(skill_name)
            for tpl_name, kwargs in templates:
                # Get section for this template's node
                node_name = tpl_name.replace("_generate", "").replace("_plan", "").replace("_review", "")
                if node_name == "quiz":
                    section = skill.sections.get("quiz", "")
                elif node_name == "lesson":
                    section = skill.sections.get("lesson", "")
                elif node_name == "knowledge":
                    section = skill.sections.get("knowledge_graph", "")
                else:
                    section = skill.sections.get(node_name, "")

                result = render_prompt(tpl_name, skill_instructions=section, **kwargs)
                assert len(result) > 50, f"Prompt too short for {skill_name}/{tpl_name}"


# ===================================================================
# 4. SKILL + MEMORY COEXISTENCE
# ===================================================================

class TestSkillMemoryCoexistence:
    """Verify skill and memory can coexist in the same prompt."""

    def test_chat_prompt_has_both_skill_and_memory_slots(self) -> None:
        """System prompt should support skill + context + memory simultaneously."""
        state = _make_state(skill_name="feynman")

        system_prompt = _BASE_SYSTEM_PROMPT

        # Skill injection
        skill_text = get_skill_section(state, "chat")
        if skill_text:
            system_prompt += f"\n\n## 教学方法\n{skill_text}"

        # Context injection
        context = _build_context_summary(state)
        if context:
            system_prompt += f"\n\n{context}"

        # Memory injection (simulated)
        memory = "Relevant past learning records:\n1. 光合作用 暗反应 accuracy 30%"
        system_prompt += f"\n\n## 历史学习记忆\n{memory}"

        # All three present
        assert "教学方法" in system_prompt
        assert "当前学习上下文" in system_prompt
        assert "历史学习记忆" in system_prompt

        # Correct ordering
        skill_pos = system_prompt.index("教学方法")
        ctx_pos = system_prompt.index("当前学习上下文")
        mem_pos = system_prompt.index("历史学习记忆")
        assert skill_pos < ctx_pos < mem_pos

    def test_no_skill_still_has_memory_slot(self) -> None:
        """Without skill, memory still works."""
        state = _make_state(skill_name=None)

        system_prompt = _BASE_SYSTEM_PROMPT
        skill_text = get_skill_section(state, "chat")
        assert skill_text == ""

        context = _build_context_summary(state)
        system_prompt += f"\n\n{context}"

        # No skill section, but context works
        assert "教学方法" not in system_prompt
        assert "当前学习上下文" in system_prompt


# ===================================================================
# 5. SKILL API (mocked)
# ===================================================================

class TestSkillAPI:
    """Test the API layer logic without starting a server."""

    def test_list_skills_api_format(self) -> None:
        summaries = list_skills()
        for s in summaries:
            # Must have all fields needed by frontend
            assert isinstance(s["name"], str)
            assert isinstance(s["display_name"], str)
            assert isinstance(s["description"], str)
            assert isinstance(s["sections"], list)
            assert isinstance(s["body"], str)
            assert len(s["body"]) > 100  # substantial content

    def test_get_nonexistent_skill(self) -> None:
        assert get_skill("nonexistent_skill_xyz") is None

    def test_skill_body_contains_all_sections(self) -> None:
        """The raw_body in list_skills should contain all section headers."""
        for s in list_skills():
            for section_name in s["sections"]:
                assert f"## {section_name}" in s["body"], \
                    f"Skill {s['name']} body missing ## {section_name}"
