"""Split a parsed session into subtopic-level ``MemoryUnit`` objects.

V1 retrieval granularity is subtopic-level (``ARCHITECTURE.md`` §6.2 and
``todo.md`` §5.2). Stats are computed locally and stored alongside the
unit so the retrieval text builder can include them deterministically.

``memory_id`` format: ``m_{12-hex}`` (UUID4 prefixed with ``m_``).
Deterministic slugs were considered and rejected — see CLAUDE.md §11
("memory ids should be opaque to consumers") and the implementation plan
gotcha R7.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from app.memory.parser import RawSession, RawSubtopic


@dataclass(frozen=True)
class MemoryUnit:
    memory_id: str
    user_id: str
    session_id: str
    memory_type: str
    topic: str | None
    subtopic: str | None
    timestamp: str
    goal_text: str | None
    summary: str | None
    question_count: int
    correct_count: int
    accuracy: float | None
    error_types: list[str]
    raw_json: str


def _new_memory_id() -> str:
    return f"m_{uuid.uuid4().hex[:12]}"


def _stats_for(sub: RawSubtopic) -> tuple[int, int, float | None, list[str]]:
    qs = sub.questions
    n = len(qs)
    correct = sum(1 for q in qs if q.is_correct is True)
    accuracy: float | None = (correct / n) if n > 0 else None
    error_types: list[str] = []
    seen: set[str] = set()
    for q in qs:
        if q.error_type and q.error_type not in seen:
            error_types.append(q.error_type)
            seen.add(q.error_type)
    return n, correct, accuracy, error_types


def normalize(session: RawSession) -> list[MemoryUnit]:
    """Split a session into one ``MemoryUnit`` per subtopic.

    Each unit gets its own UUID-derived ``memory_id``. The ``raw_json``
    field stores the per-subtopic slice of the original payload so the
    retriever can return the original questions on demand without
    needing to re-fetch the parent session.
    """
    units: list[MemoryUnit] = []
    for sub in session.subtopics:
        n, correct, accuracy, error_types = _stats_for(sub)
        slice_payload = {
            "user_id": session.user_id,
            "session_id": session.session_id,
            "timestamp": session.timestamp,
            "goal_text": session.goal_text,
            "topic": session.topic,
            "subtopic": {
                "name": sub.name,
                "summary": sub.summary,
                "questions": [asdict(q) for q in sub.questions],
            },
        }
        units.append(
            MemoryUnit(
                memory_id=_new_memory_id(),
                user_id=session.user_id,
                session_id=session.session_id,
                memory_type="subtopic",
                topic=session.topic,
                subtopic=sub.name,
                timestamp=session.timestamp,
                goal_text=session.goal_text,
                summary=sub.summary,
                question_count=n,
                correct_count=correct,
                accuracy=accuracy,
                error_types=error_types,
                raw_json=json.dumps(slice_payload, ensure_ascii=False),
            )
        )
    return units
