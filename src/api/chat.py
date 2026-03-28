from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from src.agent.graph import build_graph
from src.api.store import load_session, save_session

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory cache backed by SQLite
_sessions: dict[str, dict[str, Any]] = {}
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


_DEFAULT_STATE = {
    "messages": [],
    "mode": "chat",
    "topic": None,
    "knowledge_graph": None,
    "current_question_index": 0,
    "questions": [],
    "answers": [],
    "diagnosis_result": None,
    "lesson_content": None,
}


def _get_session(session_id: str) -> dict[str, Any] | None:
    """Get session from memory cache, falling back to SQLite."""
    if session_id in _sessions:
        return _sessions[session_id]
    stored = load_session(session_id)
    if stored:
        _sessions[session_id] = stored
        return stored
    return None


def _save(session_id: str, state: dict[str, Any]) -> None:
    """Save session to both memory cache and SQLite."""
    _sessions[session_id] = state
    save_session(session_id, state)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    mode: str
    message: str
    data: dict | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Main chat endpoint — all student messages go through here."""
    session_id = req.session_id or str(uuid.uuid4())

    # Get or init session state
    state = _get_session(session_id) or dict(_DEFAULT_STATE)

    # Add user message
    state["messages"] = list(state.get("messages", []))
    state["messages"].append(HumanMessage(content=req.message))

    # Run graph
    graph = _get_graph()
    result = graph.invoke(state)

    # Save state
    _save(session_id, result)

    # Extract response
    messages = result.get("messages", [])
    last_ai_msg = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai":
            last_ai_msg = msg.content
            break

    mode = result.get("mode", "chat")

    # Build response data based on mode
    data: dict | None = None
    if mode == "graph" and result.get("knowledge_graph"):
        kg = result["knowledge_graph"]
        data = {"graph": kg.model_dump() if hasattr(kg, "model_dump") else kg}
    elif mode == "quiz" and result.get("questions"):
        questions = result["questions"]
        data = {
            "questions": [q.model_dump() if hasattr(q, "model_dump") else q for q in questions],
        }
    elif mode == "result" and result.get("diagnosis_result"):
        dr = result["diagnosis_result"]
        data = {"result": dr.model_dump() if hasattr(dr, "model_dump") else dr}
    elif mode == "lesson" and result.get("lesson_content"):
        data = {"lesson": result["lesson_content"]}

    return ChatResponse(
        session_id=session_id,
        mode=mode,
        message=last_ai_msg,
        data=data,
    )


class SubmitQuizRequest(BaseModel):
    answers: list[int]  # list of selected option indices [0-3]


@router.post("/chat/{session_id}/submit-quiz")
async def submit_quiz(session_id: str, req: SubmitQuizRequest) -> dict:
    """Submit all quiz answers, run diagnosis only (no lesson)."""
    state = _get_session(session_id)
    if not state:
        return {"error": "session not found"}

    from src.agent.nodes.diagnosis import diagnosis_node

    state["answers"] = req.answers
    state["current_question_index"] = len(req.answers)

    diag_result = diagnosis_node(state)
    state.update(diag_result)
    _save(session_id, state)

    dr = state.get("diagnosis_result")
    return {
        "session_id": session_id,
        "diagnosis_result": dr.model_dump() if hasattr(dr, "model_dump") else dr,
    }


@router.post("/chat/{session_id}/generate-lesson")
async def generate_lesson(session_id: str) -> dict:
    """Generate lesson for weak points (called from lesson page)."""
    state = _get_session(session_id)
    if not state:
        return {"error": "session not found"}

    from src.agent.nodes.lesson import lesson_node

    lesson_result = lesson_node(state)
    state.update(lesson_result)
    _save(session_id, state)

    return {
        "session_id": session_id,
        "lesson_content": state.get("lesson_content", ""),
    }


@router.get("/chat/{session_id}/state")
async def get_state(session_id: str) -> dict:
    """Get current session state for frontend UI switching."""
    state = _get_session(session_id)
    if not state:
        return {"mode": "chat", "session_id": session_id}

    result = {
        "session_id": session_id,
        "mode": state.get("mode", "chat"),
        "topic": state.get("topic"),
        "has_graph": state.get("knowledge_graph") is not None,
        "question_progress": f"{state.get('current_question_index', 0)}/{len(state.get('questions', []))}",
        "has_result": state.get("diagnosis_result") is not None,
        "has_lesson": state.get("lesson_content") is not None,
    }

    # Include full data when available
    kg = state.get("knowledge_graph")
    if kg:
        result["graph"] = kg.model_dump() if hasattr(kg, "model_dump") else kg

    dr = state.get("diagnosis_result")
    if dr:
        result["diagnosis_result"] = dr.model_dump() if hasattr(dr, "model_dump") else dr

    lc = state.get("lesson_content")
    if lc:
        result["lesson_content"] = lc

    return result
