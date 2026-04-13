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
