"""Unit tests for the skill system.

Tests cover:
- SKILL.md parsing (frontmatter + section splitting)
- Missing/partial sections (缺省处理)
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
            display_name: 费曼学习法
            description: 用简单语言理解复杂概念
            ---

            ## chat
            对话指令
        """)
        fm, body = _parse_frontmatter(text)
        assert fm["name"] == "feynman"
        assert fm["display_name"] == "费曼学习法"
        assert fm["description"] == "用简单语言理解复杂概念"
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
            对话指令第一行
            对话指令第二行

            ## quiz
            出题指令

            ## lesson
            课程指令
        """)
        sections = _parse_sections(body)
        assert set(sections.keys()) == {"chat", "quiz", "lesson"}
        assert "对话指令第一行" in sections["chat"]
        assert "对话指令第二行" in sections["chat"]
        assert "出题指令" in sections["quiz"]

    def test_single_section(self) -> None:
        body = "## chat\n只有对话指令"
        sections = _parse_sections(body)
        assert sections == {"chat": "只有对话指令"}

    def test_no_sections(self) -> None:
        body = "纯文本���没有任何 ## 标题"
        sections = _parse_sections(body)
        assert sections == {}

    def test_section_names_lowercase(self) -> None:
        body = "## Chat\n内容\n## QUIZ\n内容"
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
        assert feynman.display_name == "费曼学习法"
        assert "chat" in feynman.sections
        assert "quiz" in feynman.sections
        assert "lesson" in feynman.sections
        assert feynman.affects == ["chat", "quiz", "lesson"]

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
            display_name: 我的教学法
            description: 测试用
            ---

            ## chat
            自定义对话指令

            ## lesson
            自定义课程指令
        """), encoding="utf-8")

        skills = load_skills(tmp_path)
        assert "my_skill" in skills
        s = skills["my_skill"]
        assert s.display_name == "我的教学法"
        assert s.affects == ["chat", "lesson"]
        assert "自定义对话指令" in s.sections["chat"]
        assert "quiz" not in s.sections  # 缺省：没写就没有


# ------------------------------------------------------------------
# get_skill_section utility
# ------------------------------------------------------------------

class TestGetSkillSection:

    def test_returns_section_content(self) -> None:
        state = {
            "active_skill_content": {
                "chat": "对话指令",
                "quiz": "出题指令",
            }
        }
        assert get_skill_section(state, "chat") == "对话指令"
        assert get_skill_section(state, "quiz") == "出题指令"

    def test_missing_section_returns_empty(self) -> None:
        state = {"active_skill_content": {"chat": "对话指令"}}
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
            knowledge_points=[{"name": "光合作用", "description": "植物制造食物"}],
            questions_per_point=2,
            skill_instructions="题目侧重概念理解",
        )
        assert "Teaching Method" in result
        assert "题目侧重概念理解" in result
        assert "光合作用" in result

    def test_quiz_template_without_skill(self) -> None:
        result = render_prompt(
            "quiz_generate",
            knowledge_points=[{"name": "光合作用", "description": "植物制造食物"}],
            questions_per_point=2,
            skill_instructions="",
        )
        assert "Teaching Method" not in result
        assert "光合作用" in result

    def test_knowledge_graph_template_with_skill(self) -> None:
        result = render_prompt(
            "knowledge_graph",
            topic="光合作用",
            max_nodes=8,
            skill_instructions="按问题链组织节点",
        )
        assert "Teaching Method" in result
        assert "按问题链组织节点" in result

    def test_knowledge_graph_template_without_skill(self) -> None:
        result = render_prompt(
            "knowledge_graph",
            topic="光合作用",
            max_nodes=8,
            skill_instructions="",
        )
        assert "Teaching Method" not in result

    def test_lesson_plan_template_with_skill(self) -> None:
        result = render_prompt(
            "lesson_plan",
            topic="光合作用",
            weak_points=[{"name": "暗反应", "description": "碳固定"}],
            skill_instructions="每个概念先一句话总结",
        )
        assert "Teaching Method" in result
        assert "每个概念先一句话总结" in result

    def test_lesson_plan_template_without_skill(self) -> None:
        result = render_prompt(
            "lesson_plan",
            topic="光合作用",
            weak_points=[{"name": "暗反应", "description": "碳固定"}],
            skill_instructions="",
        )
        assert "Teaching Method" not in result

    def test_lesson_review_template_with_skill(self) -> None:
        result = render_prompt(
            "lesson_review",
            topic="光合作用",
            points=[{"name": "光反应", "description": "光能转化"}],
            skill_instructions="全程用问题引导",
        )
        assert "Teaching Method" in result
        assert "全程用问题引导" in result

    def test_intent_template_with_skills(self) -> None:
        result = render_prompt(
            "intent",
            messages=[{"role": "user", "content": "用费曼法学光合作用"}],
            available_skills=[
                {"name": "feynman", "display_name": "费曼学习法", "description": "用简单语言理解概念"},
                {"name": "socratic", "display_name": "苏格拉底提问法", "description": "通过提问引导"},
            ],
        )
        assert "Available Teaching Methods" in result
        assert "feynman" in result
        assert "socratic" in result
        assert "skill" in result

    def test_intent_template_no_skills(self) -> None:
        result = render_prompt(
            "intent",
            messages=[{"role": "user", "content": "我想学数学"}],
            available_skills=[],
        )
        assert "Available Teaching Methods" not in result
