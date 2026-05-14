"""Unit tests for the skill system.

Tests cover:
- SKILL.md parsing (frontmatter + section splitting)
- Missing/partial sections (missing-section handling)
- Skill loader (directory scanning)
- get_skill_section utility
- Skill injection in prompt templates
- API endpoints (/skills, /set-skill)
- ChatResponse active_skill metadata
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.skills.loader import (
    SkillMeta,
    _parse_frontmatter,
    _parse_sections,
    _load_one,
    load_skills,
    get_skill,
    get_skill_section,
    list_skills,
)
from src.prompts.loader import render_prompt


# ------------------------------------------------------------------
# Frontmatter parsing
# ------------------------------------------------------------------

class TestParseFrontmatter:

    def test_basic_frontmatter(self) -> None:
        text = textwrap.dedent("""\
            ---
            name: feynman
            display_name: Feynman Technique
            description: Understand complex concepts using simple language
            ---

            ## chat
            Chat instructions
        """)
        fm, body = _parse_frontmatter(text)
        assert fm["name"] == "feynman"
        assert fm["display_name"] == "Feynman Technique"
        assert fm["description"] == "Understand complex concepts using simple language"
        assert "## chat" in body

    def test_no_frontmatter(self) -> None:
        text = "# Just markdown\nSome content"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_text(self) -> None:
        fm, body = _parse_frontmatter("")
        assert fm == {}

    def test_list_values_in_frontmatter(self) -> None:
        text = textwrap.dedent("""\
            ---
            name: test
            affects:
              - chat
              - quiz
              - lesson
            ---
            body
        """)
        fm, body = _parse_frontmatter(text)
        assert fm["affects"] == ["chat", "quiz", "lesson"]


# ------------------------------------------------------------------
# Section parsing
# ------------------------------------------------------------------

class TestParseSections:

    def test_multiple_sections(self) -> None:
        body = textwrap.dedent("""\
            Some intro text (ignored)

            ## chat
            Chat instruction line one
            Chat instruction line two

            ## quiz
            Quiz instruction

            ## lesson
            Lesson instruction
        """)
        sections = _parse_sections(body)
        assert set(sections.keys()) == {"chat", "quiz", "lesson"}
        assert "Chat instruction line one" in sections["chat"]
        assert "Chat instruction line two" in sections["chat"]
        assert "Quiz instruction" in sections["quiz"]

    def test_single_section(self) -> None:
        body = "## chat\nOnly chat instructions"
        sections = _parse_sections(body)
        assert sections == {"chat": "Only chat instructions"}

    def test_no_sections(self) -> None:
        body = "Plain text with no ## headings"
        sections = _parse_sections(body)
        assert sections == {}

    def test_section_names_lowercase(self) -> None:
        body = "## Chat\ncontent\n## QUIZ\ncontent"
        sections = _parse_sections(body)
        assert "chat" in sections
        assert "quiz" in sections

    def test_empty_body(self) -> None:
        sections = _parse_sections("")
        assert sections == {}


# ------------------------------------------------------------------
# Skill loading from directory
# ------------------------------------------------------------------

class TestLoadSkills:

    def test_load_from_project_skills_dir(self) -> None:
        """Load real skills from the project's skills/ directory."""
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        skills = load_skills(skills_dir)
        assert len(skills) >= 3
        assert "feynman" in skills
        assert "socratic" in skills
        assert "scaffolding" in skills

    def test_feynman_skill_structure(self) -> None:
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        load_skills(skills_dir)
        feynman = get_skill("feynman")
        assert feynman is not None
        assert feynman.display_name == "Feynman Technique"
        assert "chat" in feynman.sections
        assert "quiz" in feynman.sections
        assert "lesson" in feynman.sections
        assert feynman.affects == ("chat", "quiz", "lesson")

    def test_socratic_has_knowledge_graph_section(self) -> None:
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        load_skills(skills_dir)
        socratic = get_skill("socratic")
        assert socratic is not None
        assert "knowledge_graph" in socratic.sections
        assert "knowledge_graph" in socratic.affects

    def test_load_nonexistent_dir(self) -> None:
        skills = load_skills(Path("/nonexistent/path"))
        assert skills == {}

    def test_list_skills_returns_summaries(self) -> None:
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        load_skills(skills_dir)
        summaries = list_skills()
        assert len(summaries) >= 3
        names = {s["name"] for s in summaries}
        assert "feynman" in names
        for s in summaries:
            assert "name" in s
            assert "display_name" in s
            assert "description" in s
            assert "sections" in s
            assert "body" in s

    def test_load_from_tmp_dir(self, tmp_path: Path) -> None:
        """Test loading a custom skill from a temp directory."""
        skill_dir = tmp_path / "my_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: my_skill
            display_name: My Teaching Method
            description: For testing
            ---

            ## chat
            Custom chat instructions

            ## lesson
            Custom lesson instructions
        """), encoding="utf-8")

        skills = load_skills(tmp_path)
        assert "my_skill" in skills
        s = skills["my_skill"]
        assert s.display_name == "My Teaching Method"
        assert s.affects == ("chat", "lesson")
        assert "Custom chat instructions" in s.sections["chat"]
        assert "quiz" not in s.sections  # not written, so absent


# ------------------------------------------------------------------
# get_skill_section utility
# ------------------------------------------------------------------

class TestGetSkillSection:

    def test_returns_section_content(self) -> None:
        state = {
            "active_skill_content": {
                "chat": "Chat instructions",
                "quiz": "Quiz instructions",
            }
        }
        assert get_skill_section(state, "chat") == "Chat instructions"
        assert get_skill_section(state, "quiz") == "Quiz instructions"

    def test_missing_section_returns_empty(self) -> None:
        state = {"active_skill_content": {"chat": "Chat instructions"}}
        assert get_skill_section(state, "quiz") == ""
        assert get_skill_section(state, "knowledge_graph") == ""

    def test_no_skill_returns_empty(self) -> None:
        assert get_skill_section({}, "chat") == ""
        assert get_skill_section({"active_skill_content": None}, "chat") == ""

    def test_empty_state(self) -> None:
        assert get_skill_section({}, "anything") == ""


# ------------------------------------------------------------------
# Prompt template injection
# ------------------------------------------------------------------

class TestPromptInjection:

    def test_quiz_template_with_skill(self) -> None:
        result = render_prompt(
            "quiz_generate",
            knowledge_points=[{"name": "photosynthesis", "description": "Plants produce food"}],
            questions_per_point=2,
            skill_instructions="Focus on conceptual understanding",
        )
        assert "Teaching Method" in result
        assert "Focus on conceptual understanding" in result
        assert "photosynthesis" in result

    def test_quiz_template_without_skill(self) -> None:
        result = render_prompt(
            "quiz_generate",
            knowledge_points=[{"name": "photosynthesis", "description": "Plants produce food"}],
            questions_per_point=2,
            skill_instructions="",
        )
        assert "Teaching Method" not in result
        assert "photosynthesis" in result

    def test_knowledge_graph_template_with_skill(self) -> None:
        result = render_prompt(
            "knowledge_graph",
            topic="photosynthesis",
            max_nodes=8,
            skill_instructions="Organize nodes in question chains",
        )
        assert "Teaching Method" in result
        assert "Organize nodes in question chains" in result

    def test_knowledge_graph_template_without_skill(self) -> None:
        result = render_prompt(
            "knowledge_graph",
            topic="photosynthesis",
            max_nodes=8,
            skill_instructions="",
        )
        assert "Teaching Method" not in result

    def test_lesson_plan_template_with_skill(self) -> None:
        result = render_prompt(
            "lesson_plan",
            topic="photosynthesis",
            weak_points=[{"name": "dark reactions", "description": "carbon fixation"}],
            skill_instructions="Summarize each concept in one sentence first",
        )
        assert "Teaching Method" in result
        assert "Summarize each concept in one sentence first" in result

    def test_lesson_plan_template_without_skill(self) -> None:
        result = render_prompt(
            "lesson_plan",
            topic="photosynthesis",
            weak_points=[{"name": "dark reactions", "description": "carbon fixation"}],
            skill_instructions="",
        )
        assert "Teaching Method" not in result

    def test_lesson_review_template_with_skill(self) -> None:
        result = render_prompt(
            "lesson_review",
            topic="photosynthesis",
            points=[{"name": "light reactions", "description": "light energy conversion"}],
            skill_instructions="Guide with questions throughout",
        )
        assert "Teaching Method" in result
        assert "Guide with questions throughout" in result

    def test_intent_template_with_skills(self) -> None:
        result = render_prompt(
            "intent",
            messages=[{"role": "user", "content": "Learn photosynthesis using the Feynman Technique"}],
            available_skills=[
                {"name": "feynman", "display_name": "Feynman Technique", "description": "Understand concepts using simple language"},
                {"name": "socratic", "display_name": "Socratic Method", "description": "Guide through questioning"},
            ],
        )
        assert "Available Teaching Methods" in result
        assert "feynman" in result
        assert "socratic" in result
        assert "skill" in result

    def test_intent_template_no_skills(self) -> None:
        result = render_prompt(
            "intent",
            messages=[{"role": "user", "content": "I want to learn math"}],
            available_skills=[],
        )
        assert "Available Teaching Methods" not in result
