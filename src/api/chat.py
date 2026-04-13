from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import HumanMessage, AIMessage
from src.agent.graph import build_graph
from src.api.store import load_session, save_session
from src.memory_client import ingest_session, DEFAULT_USER_ID
from src.skills.loader import get_skill, list_skills

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
    "slide_outlines": None,
    "slide_contents": None,
    "lesson_format": None,
    "active_skill": None,
    "active_skill_content": None,
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
    message: str = Field(..., min_length=1, max_length=5000)


class SkillInfo(BaseModel):
    name: str
    display_name: str
    body: str
    current_section: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    mode: str
    message: str
    data: dict | None = None
    active_skill: SkillInfo | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Main chat endpoint — all student messages go through here."""
    session_id = req.session_id or str(uuid.uuid4())

    # Get or init session state
    state = _get_session(session_id) or dict(_DEFAULT_STATE)

    # Add user message
    state["messages"] = list(state.get("messages", []))
    state["messages"].append(HumanMessage(content=req.message))

    # Run graph (blocking call → run in thread to avoid blocking event loop)
    graph = _get_graph()
    result = await asyncio.to_thread(graph.invoke, state)

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

    # Build skill metadata for frontend
    skill_info = None
    skill_name = result.get("active_skill")
    if skill_name:
        skill_meta = get_skill(skill_name)
        if skill_meta:
            # Map mode → section name for highlighting
            mode_to_section = {"chat": "chat", "graph": "knowledge_graph", "quiz": "quiz", "lesson": "lesson"}
            skill_info = SkillInfo(
                name=skill_meta.name,
                display_name=skill_meta.display_name,
                body=skill_meta.raw_body,
                current_section=mode_to_section.get(mode),
            )

    return ChatResponse(
        session_id=session_id,
        mode=mode,
        message=last_ai_msg,
        data=data,
        active_skill=skill_info,
    )


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE endpoint — streams chat tokens in real time."""
    from src.agent.nodes.chat import chat_node_stream
    from src.agent.nodes.intent import intent_node

    session_id = req.session_id or str(uuid.uuid4())
    state = _get_session(session_id) or dict(_DEFAULT_STATE)

    state["messages"] = list(state.get("messages", []))
    state["messages"].append(HumanMessage(content=req.message))

    # Run intent classification (non-streaming, fast)
    intent_result = await asyncio.to_thread(intent_node, state)
    state.update(intent_result)

    mode = state.get("mode", "chat")

    # Only stream for chat mode; others fall back to normal response
    if mode != "chat":
        _save(session_id, state)
        # Run the full graph for non-chat modes
        graph = _get_graph()
        result = await asyncio.to_thread(graph.invoke, state)
        _save(session_id, result)

        messages = result.get("messages", [])
        last_msg = ""
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai":
                last_msg = msg.content
                break

        async def non_stream_sse():
            # Send skill event if active (so frontend shows badge)
            active_skill_name = result.get("active_skill") or state.get("active_skill")
            if active_skill_name:
                skill_meta = get_skill(active_skill_name)
                if skill_meta:
                    mode_to_section = {"chat": "chat", "graph": "knowledge_graph", "quiz": "quiz", "lesson": "lesson"}
                    yield _sse_event({
                        "type": "skill",
                        "name": skill_meta.name,
                        "display_name": skill_meta.display_name,
                        "body": skill_meta.raw_body,
                        "current_section": mode_to_section.get(result.get("mode", mode)),
                    })

            m = result.get("mode", mode)
            data: dict | None = None
            if m == "graph" and result.get("knowledge_graph"):
                kg = result["knowledge_graph"]
                data = {"graph": kg.model_dump() if hasattr(kg, "model_dump") else kg}
            yield _sse_event({"type": "complete", "mode": m, "message": last_msg, "data": data, "session_id": session_id})

        return StreamingResponse(non_stream_sse(), media_type="text/event-stream")

    # Stream chat tokens
    async def stream_sse():
        # Send skill info first
        skill_name = state.get("active_skill")
        if skill_name:
            skill_meta = get_skill(skill_name)
            if skill_meta:
                yield _sse_event({"type": "skill", "name": skill_meta.name, "display_name": skill_meta.display_name, "body": skill_meta.raw_body, "current_section": "chat"})

        yield _sse_event({"type": "start", "session_id": session_id})

        full_text = ""
        try:
            for token in chat_node_stream(state):
                full_text += token
                yield _sse_event({"type": "token", "content": token})
        except Exception:
            logger.exception("Stream error in chat")
            yield _sse_event({"type": "error", "message": "AI 服务暂时不可用"})
            return

        # Save complete message to state
        state["messages"].append(AIMessage(content=full_text))
        state["mode"] = "chat"
        _save(session_id, state)

        yield _sse_event({"type": "done", "session_id": session_id})

    return StreamingResponse(stream_sse(), media_type="text/event-stream")


@router.post("/chat/{session_id}/generate-lesson/stream")
async def generate_lesson_stream(session_id: str):
    """SSE endpoint — streams lesson content tokens."""
    from src.agent.nodes.lesson import lesson_node_stream

    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    async def stream_sse():
        skill_name = state.get("active_skill")
        if skill_name:
            skill_meta = get_skill(skill_name)
            if skill_meta:
                yield _sse_event({"type": "skill", "name": skill_meta.name, "display_name": skill_meta.display_name, "body": skill_meta.raw_body, "current_section": "lesson"})

        yield _sse_event({"type": "start"})

        full_text = ""
        try:
            for token in lesson_node_stream(state):
                full_text += token
                yield _sse_event({"type": "token", "content": token})
        except Exception:
            logger.exception("Stream error in lesson")
            yield _sse_event({"type": "error", "message": "课程生成失败"})
            return

        state["lesson_content"] = full_text
        state["mode"] = "lesson"
        state["lesson_format"] = "markdown"
        _save(session_id, state)

        yield _sse_event({"type": "done", "session_id": session_id})

    return StreamingResponse(stream_sse(), media_type="text/event-stream")


@router.post("/chat/{session_id}/generate-quiz/stream")
async def generate_quiz_stream(session_id: str):
    """SSE endpoint — generates quiz questions progressively per knowledge point."""
    from src.agent.nodes.quiz import quiz_generate_progressive

    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    graph = state.get("knowledge_graph")
    if not graph:
        raise HTTPException(status_code=400, detail="No knowledge graph — learn a topic first")

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    total_points = len(nodes)

    async def stream_sse():
        yield _sse_event({"type": "start", "total_points": total_points})

        all_questions = []
        point_idx = 0

        for batch in quiz_generate_progressive(state):
            questions_data = [q.model_dump() for q in batch]
            all_questions.extend(batch)
            point_idx += 1
            yield _sse_event({
                "type": "questions",
                "batch": questions_data,
                "point_index": point_idx,
                "total_points": total_points,
            })

        # Save all questions to state
        state["questions"] = all_questions
        state["current_question_index"] = 0
        state["answers"] = []
        state["mode"] = "quiz"
        _save(session_id, state)

        yield _sse_event({"type": "done", "total_questions": len(all_questions)})

    return StreamingResponse(stream_sse(), media_type="text/event-stream")


class SubmitQuizRequest(BaseModel):
    answers: list[int] = Field(..., min_length=1, max_length=50)

    @field_validator("answers")
    @classmethod
    def validate_answer_range(cls, v: list[int]) -> list[int]:
        for ans in v:
            if not 0 <= ans <= 3:
                raise ValueError(f"Answer index must be 0-3, got {ans}")
        return v


@router.post("/chat/{session_id}/submit-quiz")
async def submit_quiz(session_id: str, req: SubmitQuizRequest) -> dict:
    """Submit all quiz answers, run diagnosis only (no lesson)."""
    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.agent.nodes.diagnosis import diagnosis_node

    state["answers"] = req.answers
    state["current_question_index"] = len(req.answers)

    diag_result = await asyncio.to_thread(diagnosis_node, state)
    state.update(diag_result)
    _save(session_id, state)

    # Persist to student_memory service (background, non-blocking)
    asyncio.get_event_loop().run_in_executor(
        None, ingest_session, DEFAULT_USER_ID, session_id, state
    )

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
        raise HTTPException(status_code=404, detail="Session not found")

    from src.agent.nodes.lesson import lesson_node

    lesson_result = await asyncio.to_thread(lesson_node, state)
    state.update(lesson_result)
    _save(session_id, state)

    return {
        "session_id": session_id,
        "lesson_content": state.get("lesson_content", ""),
    }


@router.post("/chat/{session_id}/generate-slides")
async def generate_slides(session_id: str) -> dict:
    """Generate slide outlines + all slide contents."""
    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.agent.nodes.slides import generate_slide_content, generate_slide_outlines

    # Stage 1: Generate outlines
    outlines = await asyncio.to_thread(generate_slide_outlines, state)
    if not outlines:
        raise HTTPException(status_code=502, detail="Failed to generate slide outlines")

    # Stage 2: Generate content for each slide
    slides: list[dict | None] = []
    for outline in outlines:
        slide = await asyncio.to_thread(generate_slide_content, outline)
        if slide:
            slides.append(slide.model_dump())
        else:
            slides.append(None)

    state["slide_outlines"] = [o.model_dump() for o in outlines]
    state["slide_contents"] = slides
    state["lesson_format"] = "slides"
    _save(session_id, state)

    return {
        "session_id": session_id,
        "outlines": [o.model_dump() for o in outlines],
        "slides": slides,
    }


@router.post("/chat/{session_id}/generate-slides/stream")
async def generate_slides_stream(session_id: str):
    """SSE endpoint — generates slides progressively (outlines then per-slide)."""
    from src.agent.nodes.slides import generate_slide_content, generate_slide_outlines

    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    async def stream_sse():
        # Stage 1: Generate outlines
        outlines = await asyncio.to_thread(generate_slide_outlines, state)
        if not outlines:
            yield _sse_event({"type": "error", "message": "大纲生成失败"})
            return

        outlines_data = [o.model_dump() for o in outlines]
        yield _sse_event({"type": "outlines", "outlines": outlines_data, "total": len(outlines)})

        # Stage 2: Generate each slide
        slides: list[dict | None] = []
        for i, outline in enumerate(outlines):
            slide = await asyncio.to_thread(generate_slide_content, outline)
            slide_data = slide.model_dump() if slide else None
            slides.append(slide_data)
            yield _sse_event({
                "type": "slide",
                "index": i,
                "total": len(outlines),
                "slide": slide_data,
            })

        # Save to state
        state["slide_outlines"] = outlines_data
        state["slide_contents"] = slides
        state["lesson_format"] = "slides"
        _save(session_id, state)

        yield _sse_event({"type": "done", "session_id": session_id})

    return StreamingResponse(stream_sse(), media_type="text/event-stream")


@router.get("/chat/{session_id}/export-pptx")
async def export_pptx(session_id: str):
    """Export slides as downloadable PPTX file."""
    from fastapi.responses import StreamingResponse
    from src.export.pptx_export import export_slides_to_pptx

    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    slides = state.get("slide_contents", [])
    if not slides:
        raise HTTPException(status_code=400, detail="No slides to export")

    buf = await asyncio.to_thread(export_slides_to_pptx, slides)
    topic = state.get("topic", "lesson")
    # URL-encode filename for non-ASCII characters (RFC 5987)
    from urllib.parse import quote
    filename = f"{topic}.pptx"
    encoded = quote(filename)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


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

    # Questions and answers for export
    questions = state.get("questions", [])
    if questions:
        result["questions"] = [q.model_dump() if hasattr(q, "model_dump") else q for q in questions]
        result["answers"] = state.get("answers", [])

    # Messages for export
    messages = state.get("messages", [])
    if messages:
        result["messages"] = [
            {"role": "ai" if getattr(m, "type", "human") == "ai" else "user", "content": m.content}
            if hasattr(m, "content")
            else {"role": "user", "content": str(m)}
            for m in messages
        ]

    # Slide data
    if state.get("slide_contents"):
        result["slide_contents"] = state["slide_contents"]
    if state.get("slide_outlines"):
        result["slide_outlines"] = state["slide_outlines"]
    if state.get("lesson_format"):
        result["lesson_format"] = state["lesson_format"]

    # Skill data
    if state.get("active_skill"):
        result["active_skill"] = state["active_skill"]

    return result


@router.get("/skills")
async def get_available_skills() -> list[dict]:
    """Return all available skills for frontend display."""
    return list_skills()


class SetSkillRequest(BaseModel):
    skill: str | None = None  # skill name, or null to clear


@router.post("/chat/{session_id}/set-skill")
async def set_skill(session_id: str, req: SetSkillRequest) -> dict:
    """Directly set or clear the active skill for a session."""
    state = _get_session(session_id)
    if not state:
        # Auto-create session (user may select skill before first message)
        state = dict(_DEFAULT_STATE)

    if req.skill is None:
        # Clear skill
        state["active_skill"] = None
        state["active_skill_content"] = None
        _save(session_id, state)
        return {"session_id": session_id, "active_skill": None}

    skill_meta = get_skill(req.skill)
    if not skill_meta:
        raise HTTPException(status_code=404, detail=f"Skill '{req.skill}' not found")

    state["active_skill"] = skill_meta.name
    state["active_skill_content"] = dict(skill_meta.sections)
    _save(session_id, state)

    return {
        "session_id": session_id,
        "active_skill": {
            "name": skill_meta.name,
            "display_name": skill_meta.display_name,
            "sections": skill_meta.affects,
        },
    }
