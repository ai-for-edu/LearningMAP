from __future__ import annotations

from langgraph.graph import MessagesState
from src.llm.schemas import DiagnosisResult, KnowledgeGraph, QuizQuestion


class LearningState(MessagesState):
    """LangGraph state for the learning agent."""

    mode: str  # "chat" | "graph" | "quiz" | "result" | "lesson"

    # Diagnosis fields
    topic: str | None
    knowledge_graph: KnowledgeGraph | None
    current_question_index: int
    questions: list[QuizQuestion]
    answers: list[int]  # student's selected option indices
    diagnosis_result: DiagnosisResult | None

    # Lesson
    lesson_content: str | None

    # Slides
    slide_outlines: list | None
    slide_contents: list | None
    lesson_format: str | None  # "markdown" | "slides"

    # Skill
    active_skill: str | None  # skill name, e.g. "feynman"
    active_skill_content: dict | None  # {"chat": "...", "quiz": "...", ...}

    # Knowledge point skills (generated per knowledge point, derived from pedagogy skill)
    knowledge_skills: dict | None  # {knowledge_point_name: skill_text}
    use_knowledge_skills: (
        bool  # whether to generate knowledge point skills (user toggle)
    )

    # KC skills (knowledge-component metadata loaded from XES3G5M/metadata/kc_skills.json)
    # Orthogonal to the existing knowledge_skills field (which holds pedagogy-derived text).
    kc_skills: dict | None  # {kp_name: KCSkill.model_dump()}
    use_kc_skills: bool     # whether kc_skill_resolver should run + prompts inject

    # Pending topic waiting for skill selection
    pending_topic: str | None
