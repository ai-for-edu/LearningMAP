from __future__ import annotations

from langgraph.graph import StateGraph, END
from src.agent.state import LearningState
from src.agent.nodes import (
    intent_node,
    chat_node,
    knowledge_graph_node,
    kc_skill_resolver_node,
    quiz_node,
    grading_node,
    diagnosis_node,
    lesson_node,
)


def _route_after_intent(state: LearningState) -> str:
    """Route based on detected intent / current mode."""
    mode = state.get("mode", "chat")
    if mode == "graph":
        return "knowledge_graph"
    elif mode == "quiz":
        # If questions exist and student sent an answer, go to grading
        questions = state.get("questions", [])
        current_idx = state.get("current_question_index", 0)
        if questions and current_idx < len(questions):
            return "grading"
        return "quiz"
    elif mode == "result":
        return "diagnosis"
    elif mode == "lesson":
        return "lesson"
    return "chat"


def _route_after_grading(state: LearningState) -> str:
    """After grading, either next question or diagnosis."""
    mode = state.get("mode", "quiz")
    if mode == "result":
        return "diagnosis"
    return "quiz"


def _route_after_diagnosis(state: LearningState) -> str:
    """After diagnosis, generate lesson."""
    return "lesson"


def build_graph() -> StateGraph:
    """Build and compile the learning agent graph."""
    builder = StateGraph(LearningState)

    # Add nodes
    builder.add_node("intent", intent_node)
    builder.add_node("chat", chat_node)
    builder.add_node("knowledge_graph", knowledge_graph_node)
    builder.add_node("kc_skill_resolver", kc_skill_resolver_node)
    builder.add_node("quiz", quiz_node)
    builder.add_node("grading", grading_node)
    builder.add_node("diagnosis", diagnosis_node)
    builder.add_node("lesson", lesson_node)

    # Entry point
    builder.set_entry_point("intent")

    # Edges from intent
    builder.add_conditional_edges("intent", _route_after_intent, {
        "chat": "chat",
        "knowledge_graph": "knowledge_graph",
        "quiz": "quiz",
        "grading": "grading",
        "diagnosis": "diagnosis",
        "lesson": "lesson",
    })

    # Terminal edges
    builder.add_edge("chat", END)
    builder.add_edge("knowledge_graph", "kc_skill_resolver")
    builder.add_edge("kc_skill_resolver", END)
    builder.add_edge("quiz", END)

    # Grading → quiz or diagnosis
    builder.add_conditional_edges("grading", _route_after_grading, {
        "quiz": "quiz",
        "diagnosis": "diagnosis",
    })

    # Diagnosis → lesson
    builder.add_edge("diagnosis", "lesson")

    # Lesson → END (back to chat on next message)
    builder.add_edge("lesson", END)

    return builder.compile()
