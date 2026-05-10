"""PR-4 baseline byte-identity tests.

Each test renders a j2 template with empty memory_snapshot_block and
asserts the output exactly equals the captured pre-PR-4 baseline string.
The whitespace control `{{- memory_snapshot_block }}` in templates ensures
empty var produces byte-identical output to pre-PR-4.

Regenerate baselines with:
  uv run python -c "from src.prompts.loader import render_prompt; \\
                    print(repr(render_prompt('<name>', **kwargs)))"
"""
from __future__ import annotations

from src.prompts.loader import render_prompt


# Captured from pre-PR-4 knowledge_graph.j2 with empty var; regenerate if
# the template content (other than {{- memory_snapshot_block }}) ever changes.
EXPECTED_KG_BASELINE = 'You are an educational knowledge graph builder. Given a learning topic, generate a knowledge graph of related concepts.\n\n\n\n\n\nTopic: 勾股定理\nMaximum nodes: 8\n\nGenerate a knowledge graph with:\n1. The core topic and its prerequisite concepts\n2. Each node has: id, name, description, level (prerequisite/core/extension)\n3. Edges show prerequisite relationships (source → target means source is prerequisite of target)\n4. Include 4-8 nodes covering prerequisites and core concepts\n\nReturn a structured knowledge graph.\n'


def test_knowledge_graph_baseline_env_off():
    out = render_prompt(
        "knowledge_graph",
        topic="勾股定理",
        max_nodes=8,
        skill_instructions="",
        focus_directive="",
        review_weak_kps=[],
        review_strong_kps=[],
        existing_mastery=[],
        memory_snapshot_block="",
    )
    assert out == EXPECTED_KG_BASELINE


EXPECTED_QUIZ_BASELINE = 'You are a quiz generator for adaptive learning. Generate multiple-choice questions to diagnose student mastery.\n\n\n\nKnowledge points to test:\n\n- KP1: desc1\n\n\nGenerate 2 questions per knowledge point. Each question should:\n1. Test understanding of the specific concept\n2. Have exactly 4 options (A/B/C/D)\n3. Have exactly one correct answer\n4. Be appropriate for middle school level\n\nReturn a list of questions.\n'


def test_quiz_generate_baseline_env_off():
    out = render_prompt(
        "quiz_generate",
        skill_instructions="",
        kc_skill_block="",
        knowledge_points=[{"name": "KP1", "description": "desc1"}],
        questions_per_point=2,
        memory_snapshot_block="",
    )
    assert out == EXPECTED_QUIZ_BASELINE


EXPECTED_LESSON_PLAN_BASELINE = "You are a personalized lesson plan generator. Create a lesson that addresses the student's weak knowledge areas.\n\n\n\nTopic: X\n\nWeak areas that need teaching:\n\n- WP1: d1\n\n\nCreate a comprehensive lesson covering each weak area with:\n1. Clear explanation of the concept\n2. Key formulas or rules (if applicable)\n3. Worked examples with step-by-step solutions\n4. Common mistakes to avoid\n\nUse clear, concise language appropriate for a middle school student. Write in Chinese.\n"
EXPECTED_LESSON_POINT_BASELINE = '你是一个个性化教案生成器。请为以下知识点生成一段教学内容。\n\n## 教学法要求\n\n\n\n\n\n\n## 知识点信息\n主题：X\n知识点（第 1/1 个）：KP1\n描述：d\n\n## 输出要求\n\n请生成该知识点的教学内容，包含：\n1. 概念讲解（遵循教学法中的策略）\n2. 关键公式或规则（如适用）\n3. 一个完整的例题及分步解答\n4. 常见错误及纠正\n\n用中文撰写，语言通俗易懂，适合中学生阅读。\n以 Markdown 格式输出，用 `## KP1` 作为章节标题。\n'
EXPECTED_LESSON_REVIEW_BASELINE = 'You are a personalized lesson plan generator. The student has mastered all knowledge points — create a review and consolidation lesson.\n\n\nTopic: X\n\nKnowledge points the student has mastered:\n\n- P1: d\n\n\nCreate a consolidation lesson with:\n1. Key concepts summary for each knowledge point\n2. Connections between the concepts\n3. Challenge problems that combine multiple knowledge points\n4. Tips for long-term retention\n\nUse clear, concise language appropriate for a middle school student. Write in Chinese.\n'


def test_lesson_plan_baseline_env_off():
    out = render_prompt(
        "lesson_plan",
        skill_instructions="",
        focus_directive="",
        topic="X",
        weak_points=[{"name": "WP1", "description": "d1"}],
        memory_snapshot_block="",
    )
    assert out == EXPECTED_LESSON_PLAN_BASELINE


def test_lesson_point_baseline_env_off():
    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="KP1",
        knowledge_point_description="d",
        pedagogy_instructions="",
        knowledge_skill="",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="",
        memory_snapshot_block="",
    )
    assert out == EXPECTED_LESSON_POINT_BASELINE


def test_lesson_review_baseline_env_off():
    out = render_prompt(
        "lesson_review",
        topic="X",
        points=[{"name": "P1", "description": "d"}],
        skill_instructions="",
        memory_snapshot_block="",
    )
    assert out == EXPECTED_LESSON_REVIEW_BASELINE
