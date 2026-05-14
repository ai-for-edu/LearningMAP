from __future__ import annotations

from typing import Annotated
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
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
    use_kc_skills: bool  # whether kc_skill_resolver should run + prompts inject

    # User-facing session mode (chat vs learning) — gates whether intent_node
    # is allowed to route to the pipeline. "chat" = pure chat + CTA only;
    # "learning" = full routing (graph / quiz / lesson) enabled.
    session_mode: str  # "chat" | "learning"

    # Intra-turn key set by intent_node when chat-mode detects a learnable
    # topic. Read by chat_node to attach a CTA card on the AI reply, then
    # cleared in the same chat_node return (never persisted non-None).
    pending_cta_topic: str | None

    # Chat-track conversation. `messages` (inherited from MessagesState) is
    # the learning track — pipeline content + free-form Q&A within learning.
    # The two tracks never see each other in LLM context; only the global
    # memory snapshot crosses the boundary.
    chat_messages: Annotated[list, add_messages]

    # Pending topic waiting for skill selection
    pending_topic: str | None

    # Review — review-flow PR-2
    mastery_snapshot: list[dict] | None  # cached at first start-review call, expires >24h
    review_questions: list[QuizQuestion] | None
    review_answers: list[int] | None
    review_result: dict | None  # {weak_kps, strong_kps, score, focus_directive}

    # Hybrid agent memory — frozen per-session snapshot (PR-3)
    memory_snapshot: str | None  # session-startup snapshot Markdown; "" = empty/failed; frozen for session
    memory_snapshot_loaded_at: int | None  # epoch seconds; debug/audit only
