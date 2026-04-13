"""Build the retrieval text used for embedding and prompt context.

Template is fixed (``todo.md`` §7 / ``ARCHITECTURE.md`` §9). Tests assert
on the exact bytes, so do NOT introduce locale-dependent formatting,
randomness, or wall-clock timestamps inside this builder.
"""

from __future__ import annotations

from app.memory.normalizer import MemoryUnit


def _format_accuracy(unit: MemoryUnit) -> str:
    if unit.accuracy is None:
        return "n/a"
    return f"{int(round(unit.accuracy * 100))}%"


def build_retrieval_text(unit: MemoryUnit) -> str:
    """Return a deterministic, compact retrieval-text block for ``unit``."""
    goal = unit.goal_text or "(unspecified)"
    topic = unit.topic or "(unspecified)"
    subtopic = unit.subtopic or "(unspecified)"
    summary = unit.summary or "(no diagnostic summary)"
    error_types = ", ".join(unit.error_types) if unit.error_types else "none"
    accuracy = _format_accuracy(unit)
    return (
        f"Learning goal: {goal}\n"
        f"Topic: {topic}\n"
        f"Subtopic: {subtopic}\n"
        f"Summary: {summary}\n"
        f"Stats: {unit.question_count} questions, "
        f"{unit.correct_count} correct, accuracy {accuracy}\n"
        f"Error types: {error_types}"
    )
