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
EXPECTED_KG_BASELINE = 'You are an educational knowledge graph builder. Given a learning topic, generate a knowledge graph of related concepts.\n\n**Language: Respond in English only.** All node `name`, `description`, and edge labels must be written in English. Exception: if a prior-mastery hint below lists an existing subtopic and you choose to reuse it, copy that subtopic name verbatim (even if it is not in English) so memory keys stay linked — for newly introduced concepts, always use English names.\n\n\n\n\n\nTopic: Pythagorean theorem\nMaximum nodes: 8\n\nGenerate a knowledge graph with:\n1. The core topic and its prerequisite concepts\n2. Each node has: id, name, description, level (prerequisite/core/extension)\n3. Edges show prerequisite relationships (source → target means source is prerequisite of target)\n4. Include 4-8 nodes covering prerequisites and core concepts\n\nReturn a structured knowledge graph.\n'


def test_knowledge_graph_baseline_env_off():
    out = render_prompt(
        "knowledge_graph",
        topic="Pythagorean theorem",
        max_nodes=8,
        skill_instructions="",
        focus_directive="",
        review_weak_kps=[],
        review_strong_kps=[],
        existing_mastery=[],
        memory_snapshot_block="",
    )
    assert out == EXPECTED_KG_BASELINE


EXPECTED_QUIZ_BASELINE = 'You are a quiz generator for adaptive learning. Generate multiple-choice questions to diagnose student mastery.\n\n**Language: Respond in English only.** All question stems, option texts, and explanations must be written in English. If any input field (knowledge point name, description, or teaching context) is not in English, internally translate it before writing the question, but emit only English in the output.\n\n\n\nKnowledge points to test:\n\n- KP1: desc1\n\n\nGenerate 2 questions per knowledge point. Each question should:\n1. Test understanding of the specific concept\n2. Have exactly 4 options (A/B/C/D)\n3. Have exactly one correct answer\n4. Be appropriate for middle school level\n\nReturn a list of questions.\n'


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


EXPECTED_LESSON_PLAN_BASELINE = "You are a personalized lesson plan generator. Create a lesson that addresses the student's weak knowledge areas.\n\n\n\nTopic: X\n\nWeak areas that need teaching:\n\n- WP1: d1\n\n\nCreate a comprehensive lesson covering each weak area with:\n1. Clear explanation of the concept\n2. Key formulas or rules (if applicable)\n3. Worked examples with step-by-step solutions\n4. Common mistakes to avoid\n\nUse clear, concise language appropriate for a middle school student. Respond in English.\n"
EXPECTED_LESSON_POINT_BASELINE = 'You are a personalized lesson generator. Generate a teaching segment for the following knowledge point.\n\n## Pedagogical Requirements\n\n\n\n\n\n\n## Knowledge Point Information\nTopic: X\nKnowledge point (1 of 1): KP1\nDescription: d\n\n## Output Requirements\n\nGenerate the teaching content for this knowledge point, including:\n1. Concept explanation (following the pedagogical approach)\n2. Key formulas or rules (if applicable)\n3. A complete worked example with step-by-step solution\n4. Common mistakes and corrections\n\nWrite in clear, accessible English suitable for a secondary-school student.\nOutput in Markdown format, using `## KP1` as the section heading.\n'
EXPECTED_LESSON_REVIEW_BASELINE = 'You are a personalized lesson plan generator. The student has mastered all knowledge points — create a review and consolidation lesson.\n\n\nTopic: X\n\nKnowledge points the student has mastered:\n\n- P1: d\n\n\nCreate a consolidation lesson with:\n1. Key concepts summary for each knowledge point\n2. Connections between the concepts\n3. Challenge problems that combine multiple knowledge points\n4. Tips for long-term retention\n\nUse clear, concise language appropriate for a middle school student. Write in English.\n'


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


EXPECTED_REVIEW_BASELINE = 'You are generating review quiz questions for a student. The student has previously studied the knowledge points below, and you now need to check whether each KP is still retained.\n\n**Language: Respond in English only.** All question stems and option texts must be written in English. The `knowledge_point` field is the only exception — it must be copied verbatim from the candidate\'s `subtopic` so memory keys stay linked across sessions (the rest of the JSON must still be English regardless of what language the `subtopic` is in).\n\nCandidate knowledge points (with last scores):\n\n- KP1 (last accuracy: 50%, 3 days ago)\n\n\nRequirements:\n1. Generate **1** 4-option single-choice question per knowledge point — total = 1\n2. Calibrate difficulty to the last score: accuracy < 50% → basic recall question; >= 70% → application question\n3. Questions must be independent — no "combined" or cross-KP questions\n4. Output strict JSON; array order must match the candidates input order\n\nOutput schema:\n[\n  {\n    "id": "rq_<index>",\n    "knowledge_point": "<subtopic field value of the candidate KP, verbatim>",\n    "question": "<question stem, in English>",\n    "options": ["<option text in English>", "<option text in English>", "<option text in English>", "<option text in English>"],\n    "answer": <correct option index 0-3>\n  },\n  ...\n]\n'


def test_review_generate_system_baseline_env_off():
    """env=off: misconceptions=[] → byte-identical to pre-PR-4 (modulo {% if %} early return)."""
    out = render_prompt(
        "review_generate_system",
        candidates=[
            {"subtopic": "KP1", "last_accuracy": 0.5, "days_ago": 3,
             "topic": "T"}
        ],
        misconceptions=[],
    )
    assert out == EXPECTED_REVIEW_BASELINE
