"""HTTP client for the student_memory retrieval service.

The student_memory service runs as an independent process (default port 8090).
This module provides two helpers that the main openail app calls at
well-defined points in the learning pipeline:

- retrieve_memory: before chat_node builds its LLM prompt
- ingest_session: after diagnosis completes
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MEMORY_SERVICE_URL = os.getenv("MEMORY_SERVICE_URL", "http://localhost:8090")
DEFAULT_USER_ID = "default_user"

_client: httpx.Client | None = None
_lock = threading.Lock()


def _get_client() -> httpx.Client:
    """Lazy-init a reusable HTTP client (thread-safe)."""
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                base_url=MEMORY_SERVICE_URL,
                timeout=httpx.Timeout(10.0, connect=2.0),
            )
    return _client


# ------------------------------------------------------------------
# Retrieve
# ------------------------------------------------------------------

def retrieve_memory(user_id: str, query: str, top_k: int = 5) -> str:
    """Call POST /retrieve and return prompt-ready context_text.

    Returns empty string on any failure so the caller can proceed
    without memory context (graceful degradation).
    """
    try:
        resp = _get_client().post(
            "/retrieve",
            json={"user_id": user_id, "query": query, "top_k": top_k},
        )
        resp.raise_for_status()
        return resp.json().get("context_text", "")
    except httpx.HTTPError:
        logger.warning("Memory service /retrieve HTTP error, skipping", exc_info=True)
        return ""
    except Exception:
        logger.error("Unexpected error in retrieve_memory", exc_info=True)
        return ""


# ------------------------------------------------------------------
# Ingest
# ------------------------------------------------------------------

def _build_ingest_payload(
    user_id: str,
    session_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Convert openail session state into a /ingest request payload.

    Maps diagnosis results + quiz questions into the subtopic-level
    structure expected by the memory service.
    """
    topic = state.get("topic") or ""
    diagnosis = state.get("diagnosis_result")
    questions = state.get("questions", [])
    answers = state.get("answers", [])

    # Group questions by knowledge_point (= subtopic)
    subtopic_map: dict[str, list[dict[str, Any]]] = {}
    for i, q in enumerate(questions):
        kp = q.knowledge_point if hasattr(q, "knowledge_point") else q.get("knowledge_point", "")
        if kp not in subtopic_map:
            subtopic_map[kp] = []

        correct_idx = q.answer if hasattr(q, "answer") else q.get("answer", -1)
        student_ans = answers[i] if i < len(answers) else -1
        is_correct = student_ans == correct_idx

        subtopic_map[kp].append({
            "question_id": q.id if hasattr(q, "id") else q.get("id", f"q{i}"),
            "question_text": q.question if hasattr(q, "question") else q.get("question", ""),
            "student_answer": str(student_ans),
            "is_correct": is_correct,
            "error_type": None if is_correct else "wrong_answer",
        })

    # Build subtopics list with summary from diagnosis breakdown
    breakdown_map: dict[str, str] = {}
    if diagnosis:
        bd = diagnosis.breakdown if hasattr(diagnosis, "breakdown") else diagnosis.get("breakdown", [])
        for b in bd:
            name = b.name if hasattr(b, "name") else b.get("name", "")
            detail = b.detail if hasattr(b, "detail") else b.get("detail", "")
            status = b.status if hasattr(b, "status") else b.get("status", "")
            breakdown_map[name] = f"{status}: {detail}"

    subtopics = []
    for kp, qs in subtopic_map.items():
        subtopics.append({
            "name": kp,
            "summary": breakdown_map.get(kp, ""),
            "questions": qs,
        })

    return {
        "user_id": user_id,
        "session_id": session_id,
        "goal_text": f"学习 {topic}" if topic else None,
        "topic": topic or None,
        "subtopics": subtopics,
    }


def ingest_session(
    user_id: str,
    session_id: str,
    state: dict[str, Any],
) -> None:
    """Call POST /ingest to persist this learning session as memory.

    Fails silently — memory is enhancement, not critical path.
    """
    payload = _build_ingest_payload(user_id, session_id, state)

    if not payload["subtopics"]:
        logger.debug("No subtopics to ingest, skipping")
        return

    try:
        resp = _get_client().post("/ingest", json=payload)
        if resp.status_code == 409:
            logger.debug("Session %s already ingested (duplicate)", session_id)
            return
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "Ingested session %s: %d memory units",
            session_id,
            data.get("memory_count", 0),
        )
    except httpx.HTTPError:
        logger.warning("Memory service /ingest HTTP error, skipping", exc_info=True)
    except Exception:
        logger.error("Unexpected error in ingest_session", exc_info=True)
