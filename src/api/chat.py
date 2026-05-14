from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

import json

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import HumanMessage, AIMessage
from src.agent.graph import build_graph
from src.agent.mastery_annotator import annotate_graph_with_mastery
from src.api.store import load_session, save_session
from src.forgetting import get_forgetting_model
from src.llm.schemas import KnowledgeGraph
from src import memory_client
from src.memory_client import (
    DEFAULT_USER_ID,
    fetch_mastery,
    ingest_review_session,
    ingest_session,
)
from src.progress_aggregator import refresh_progress
from src.reviewer import run_memory_review
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


_USE_KC_SKILLS_DEFAULT = os.environ.get("USE_KC_SKILLS", "true").lower() == "true"

_REVIEW_FLOW_ENABLED = os.environ.get("ENABLE_REVIEW_FLOW", "false").lower() == "true"

# Hybrid agent memory write-path gate (PR-3). Same env var also gates PR-2's
# reviewer + progress aggregator (they check internally). Module-level: a
# fresh process picks up env changes; tests use monkeypatch.setattr.
_MEMORY_REVIEW_ENABLED = os.environ.get("ENABLE_MEMORY_REVIEW", "false").lower() == "true"

# Module-level cache for has_history (gate-on path); 60s TTL keyed by user_id.
_has_history_cache: dict[str, tuple[float, bool]] = {}
_HAS_HISTORY_TTL_S = 60.0


async def _has_history(user_id: str) -> bool:
    """True if the user has any prior subtopic in L1.

    Returns False unconditionally when ENABLE_REVIEW_FLOW is off, so the
    front-end "复习" button is hidden in that case.

    Memoized for 60 s to avoid hammering the memory service on /state polls.
    Wraps the blocking httpx call in asyncio.to_thread to keep the event
    loop responsive.
    """
    if not _REVIEW_FLOW_ENABLED:
        return False
    now = time.monotonic()
    cached = _has_history_cache.get(user_id)
    if cached is not None and (now - cached[0]) < _HAS_HISTORY_TTL_S:
        return cached[1]
    from src.memory_client import fetch_mastery

    result = bool(await asyncio.to_thread(fetch_mastery, user_id))
    _has_history_cache[user_id] = (now, result)
    return result


async def _ensure_memory_snapshot(state: dict[str, Any]) -> None:
    """If snapshot field is None and env is on, fetch once and freeze.

    Mutates state in place. Never raises — fetch_snapshot already swallows
    on its own (returns ""). After this returns, state["memory_snapshot"]
    is a str (possibly empty) and stays frozen for the rest of the session.
    """
    if not _MEMORY_REVIEW_ENABLED:
        return
    if state.get("memory_snapshot") is not None:
        return  # already filled (incl. ""); never re-fetch in same session
    snap = await asyncio.to_thread(memory_client.fetch_snapshot, DEFAULT_USER_ID)
    state["memory_snapshot"] = snap or ""
    state["memory_snapshot_loaded_at"] = int(time.time())


# ---------------------------------------------------------------------------
# Background-task dispatch for hybrid memory write path (PR-3).
#
# Three independent BG tasks fan out per trigger event: ingest_session
# (existing L1 vector store), run_memory_review (PR-2 LLM consolidation),
# refresh_progress (PR-2 SQL aggregation). Each has its own top-level
# try/except, so a failure inside one does not affect the other two.
# ---------------------------------------------------------------------------

_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro, *, name: str) -> None:
    """Spawn a task and hold a strong reference until it completes.

    Without this, asyncio.create_task() returns a Task that may be GC'd
    mid-flight and silently cancelled. The set + done_callback pattern
    is the documented Python recommendation.
    """
    t = asyncio.create_task(coro, name=name)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


def _dispatch_memory_writes(
    user_id: str,
    session_id: str,
    state: dict[str, Any],
    source_event: str,
    *,
    ingest_fn=None,
    extra_ingest_session_id: str | None = None,
) -> None:
    """Fire 3 background tasks in parallel: ingest + reviewer + progress.

    Contract: never raises. reviewer + progress have their own top-level
    try/except (PR-2 §6.1 / §7), and the ingest functions silently swallow.
    The dispatch helper itself only catches RuntimeError from create_task in
    pathological event-loop states.

    extra_ingest_session_id: tasks.py run_lesson_generation passes
    f"{sid}_lesson" to namespace-separate the ingest row; reviewer +
    progress always use the real session_id for audit accuracy.

    ingest_fn: defaults to ingest_session; submit-review passes
    ingest_review_session. Resolved at call time (not at definition time) so
    that test patches on the module attribute are honoured.
    """
    try:
        state_snapshot = dict(state)        # shallow copy → BG sees stable view
        ingest_sid = extra_ingest_session_id or session_id
        _ingest_fn = ingest_fn if ingest_fn is not None else ingest_session

        _spawn(
            asyncio.to_thread(_ingest_fn, user_id, ingest_sid, state_snapshot),
            name=f"ingest:{session_id}:{source_event}",
        )
        _spawn(
            asyncio.to_thread(
                run_memory_review, user_id, session_id, state_snapshot, source_event,
            ),
            name=f"memory_review:{session_id}:{source_event}",
        )
        _spawn(
            asyncio.to_thread(
                refresh_progress, user_id, session_id, state_snapshot,
            ),
            name=f"refresh_progress:{session_id}:{source_event}",
        )
        logger.info(
            "memory_dispatch session=%s event=%s ingest_sid=%s "
            "tasks=ingest+review+progress",
            session_id, source_event, ingest_sid,
        )
    except Exception:
        logger.exception("memory_dispatch failed (events lost)")


_DEFAULT_STATE = {
    "messages": [],
    "chat_messages": [],
    "mode": "chat",
    "session_mode": "chat",
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
    "active_skill": "feynman",
    "active_skill_content": None,
    "knowledge_skills": None,
    "use_knowledge_skills": True,
    "kc_skills": None,
    "use_kc_skills": _USE_KC_SKILLS_DEFAULT,
    "pending_topic": None,
    "quiz_task_status": None,
    "quiz_task_progress": None,
    "lesson_task_status": None,
    "slides_task_status": None,
    "slides_task_progress": None,
    "mastery_snapshot": None,
    "review_questions": [],
    "review_answers": [],
    "review_result": None,
    "review_question_history": {},
    "memory_snapshot": None,
    "memory_snapshot_loaded_at": None,
}


def _ensure_skill_content(state: dict[str, Any]) -> None:
    """Lazily materialize active_skill_content from the skill registry.

    _DEFAULT_STATE only stores the skill name (not its sections) because the
    skill registry may not be loaded at module import time. This helper fills
    in the content on first read.
    """
    name = state.get("active_skill")
    if name and not state.get("active_skill_content"):
        meta = get_skill(name)
        if meta:
            state["active_skill_content"] = dict(meta.sections)


def _new_session_state() -> dict[str, Any]:
    """Build a fresh session state with default skill content materialized."""
    state = dict(_DEFAULT_STATE)
    _ensure_skill_content(state)
    return state


def _apply_initial_mode(state: dict[str, Any], req_mode: str | None) -> None:
    """Honor request.mode only when the session has no messages yet.

    Prevents reconnecting clients from accidentally overwriting an
    established session's mode via a stale `mode` field on subsequent posts.
    """
    if (
        req_mode in ("chat", "learning")
        and not state.get("messages")
        and not state.get("chat_messages")
    ):
        state["session_mode"] = req_mode


def _get_session(session_id: str) -> dict[str, Any] | None:
    """Get session from memory cache, falling back to SQLite."""
    if session_id in _sessions:
        state = _sessions[session_id]
    else:
        stored = load_session(session_id)
        if not stored:
            return None
        _sessions[session_id] = stored
        state = stored
    state.setdefault("chat_messages", [])
    _ensure_skill_content(state)
    return state


def _save(session_id: str, state: dict[str, Any]) -> None:
    """Save session to both memory cache and SQLite."""
    _sessions[session_id] = state
    save_session(session_id, state)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(..., min_length=1, max_length=5000)
    mode: str | None = None  # only honored for newly-created sessions


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
    state = _get_session(session_id) or _new_session_state()
    # Only honor req.mode if this is a brand-new session.
    _apply_initial_mode(state, req.mode)
    await _ensure_memory_snapshot(state)

    # Add user message to the active track
    track_key = "chat_messages" if state.get("session_mode") == "chat" else "messages"
    state[track_key] = list(state.get(track_key, []))
    state[track_key].append(HumanMessage(content=req.message))

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
            "questions": [
                q.model_dump() if hasattr(q, "model_dump") else q for q in questions
            ],
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
            mode_to_section = {
                "chat": "chat",
                "graph": "knowledge_graph",
                "quiz": "quiz",
                "lesson": "lesson",
            }
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
    state = _get_session(session_id) or _new_session_state()
    # Only honor req.mode if this is a brand-new session.
    _apply_initial_mode(state, req.mode)
    await _ensure_memory_snapshot(state)

    # Add user message to the active track
    track_key = "chat_messages" if state.get("session_mode") == "chat" else "messages"
    state[track_key] = list(state.get(track_key, []))
    state[track_key].append(HumanMessage(content=req.message))

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
                    mode_to_section = {
                        "chat": "chat",
                        "graph": "knowledge_graph",
                        "quiz": "quiz",
                        "lesson": "lesson",
                    }
                    yield _sse_event(
                        {
                            "type": "skill",
                            "name": skill_meta.name,
                            "display_name": skill_meta.display_name,
                            "body": skill_meta.raw_body,
                            "current_section": mode_to_section.get(
                                result.get("mode", mode)
                            ),
                        }
                    )

            m = result.get("mode", mode)
            data: dict | None = None
            if m == "graph" and result.get("knowledge_graph"):
                kg = result["knowledge_graph"]
                data = {"graph": kg.model_dump() if hasattr(kg, "model_dump") else kg}
            yield _sse_event(
                {
                    "type": "complete",
                    "mode": m,
                    "message": last_msg,
                    "data": data,
                    "session_id": session_id,
                }
            )

        return StreamingResponse(non_stream_sse(), media_type="text/event-stream")

    # Stream chat tokens
    async def stream_sse():
        from src.agent.nodes.chat import _cta_kwargs

        # Send skill info first
        skill_name = state.get("active_skill")
        if skill_name:
            skill_meta = get_skill(skill_name)
            if skill_meta:
                yield _sse_event(
                    {
                        "type": "skill",
                        "name": skill_meta.name,
                        "display_name": skill_meta.display_name,
                        "body": skill_meta.raw_body,
                        "current_section": "chat",
                    }
                )

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

        # Attach CTA metadata if intent_node stashed a topic, then clear it.
        cta_topic = state.get("pending_cta_topic")
        extra_kwargs = _cta_kwargs(cta_topic)
        if cta_topic:
            yield _sse_event({"type": "cta", "topic": cta_topic})
        track_key = "chat_messages" if state.get("session_mode") == "chat" else "messages"
        state[track_key] = list(state.get(track_key, []))
        state[track_key].append(AIMessage(content=full_text, **extra_kwargs))
        state["pending_cta_topic"] = None
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
                yield _sse_event(
                    {
                        "type": "skill",
                        "name": skill_meta.name,
                        "display_name": skill_meta.display_name,
                        "body": skill_meta.raw_body,
                        "current_section": "lesson",
                    }
                )

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

        # Fan out 3 BG tasks (PR-3). Gated on env=on: pre-PR-3 this HTTP
        # entry never called ingest, so env=off must preserve that to satisfy
        # spec §6.1 "no new rows under env=off". Lesson namespace per §6.6.
        if _MEMORY_REVIEW_ENABLED:
            _dispatch_memory_writes(
                DEFAULT_USER_ID, session_id, state,
                source_event="generate-lesson",
                extra_ingest_session_id=f"{session_id}_lesson",
            )

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
        raise HTTPException(
            status_code=400, detail="No knowledge graph — learn a topic first"
        )

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
            yield _sse_event(
                {
                    "type": "questions",
                    "batch": questions_data,
                    "point_index": point_idx,
                    "total_points": total_points,
                }
            )

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


class SubmitReviewRequest(BaseModel):
    answers: list[int] = Field(..., min_length=1, max_length=5)

    @field_validator("answers")
    @classmethod
    def validate_answer_range(cls, v: list[int]) -> list[int]:
        if any(a < 0 or a > 3 for a in v):
            raise ValueError("answer index must be in [0, 3]")
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
    _has_history_cache.pop(DEFAULT_USER_ID, None)

    # Fan out 3 BG tasks: ingest_session + reviewer + progress (PR-3).
    _dispatch_memory_writes(
        DEFAULT_USER_ID, session_id, state, source_event="submit-quiz",
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

    # Fan out 3 BG tasks (PR-3). Gated on env=on: pre-PR-3 this HTTP
    # entry never called ingest, so env=off must preserve that to satisfy
    # spec §6.1 "no new rows under env=off". Lesson namespace per §6.6.
    if _MEMORY_REVIEW_ENABLED:
        _dispatch_memory_writes(
            DEFAULT_USER_ID, session_id, state,
            source_event="generate-lesson",
            extra_ingest_session_id=f"{session_id}_lesson",
        )

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
        yield _sse_event(
            {"type": "outlines", "outlines": outlines_data, "total": len(outlines)}
        )

        # Stage 2: Generate each slide
        slides: list[dict | None] = []
        for i, outline in enumerate(outlines):
            slide = await asyncio.to_thread(generate_slide_content, outline)
            slide_data = slide.model_dump() if slide else None
            slides.append(slide_data)
            yield _sse_event(
                {
                    "type": "slide",
                    "index": i,
                    "total": len(outlines),
                    "slide": slide_data,
                }
            )

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
        return {"mode": "chat", "session_mode": "chat", "session_id": session_id}

    result = {
        "session_id": session_id,
        "mode": state.get("mode", "chat"),
        "session_mode": state.get("session_mode", "chat"),
        "topic": state.get("topic"),
        "has_graph": state.get("knowledge_graph") is not None,
        "question_progress": f"{state.get('current_question_index', 0)}/{len(state.get('questions', []))}",
        "has_result": state.get("diagnosis_result") is not None,
        "has_lesson": state.get("lesson_content") is not None,
    }

    # Include full data when available. Re-annotate the KG with the freshest
    # mastery rows so the frontend sees node colors update in the same session
    # as soon as new memory is ingested (post-quiz, post-lesson). The on-disk
    # state retains the generation-time snapshot — this refresh is per-response.
    kg = state.get("knowledge_graph")
    if kg:
        kg_obj = (
            kg
            if hasattr(kg, "model_dump")
            else KnowledgeGraph.model_validate(kg)
        )
        topic = state.get("topic") or ""
        if topic:
            try:
                mastery_rows = await asyncio.to_thread(
                    fetch_mastery, DEFAULT_USER_ID, topic
                )
            except Exception:
                logger.warning(
                    "fetch_mastery raised in get_state; degrading to no-mastery",
                    exc_info=True,
                )
                mastery_rows = []
        else:
            mastery_rows = []
        kg_obj = annotate_graph_with_mastery(
            kg_obj,
            mastery_rows=mastery_rows,
            model=get_forgetting_model(),
            now=datetime.now(timezone.utc),
        )
        result["graph"] = kg_obj.model_dump()

    dr = state.get("diagnosis_result")
    if dr:
        result["diagnosis_result"] = (
            dr.model_dump() if hasattr(dr, "model_dump") else dr
        )

    lc = state.get("lesson_content")
    if lc:
        result["lesson_content"] = lc

    # Questions and answers for export
    questions = state.get("questions", [])
    if questions:
        result["questions"] = [
            q.model_dump() if hasattr(q, "model_dump") else q for q in questions
        ]
        result["answers"] = state.get("answers", [])

    # Messages for export
    messages = state.get("messages", [])
    if messages:
        result["messages"] = [
            {
                "role": "ai" if getattr(m, "type", "human") == "ai" else "user",
                "content": m.content,
                "additional_kwargs": getattr(m, "additional_kwargs", {}) or {},
            }
            if hasattr(m, "content")
            else {"role": "user", "content": str(m), "additional_kwargs": {}}
            for m in messages
        ]

    chat_msgs = state.get("chat_messages", [])
    if chat_msgs:
        result["chat_messages"] = [
            {
                "role": "ai" if getattr(m, "type", "human") == "ai" else "user",
                "content": m.content,
                "additional_kwargs": getattr(m, "additional_kwargs", {}) or {},
            }
            if hasattr(m, "content")
            else {"role": "user", "content": str(m), "additional_kwargs": {}}
            for m in chat_msgs
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
        skill_meta = get_skill(state["active_skill"])
        if skill_meta:
            result["active_skill_display_name"] = skill_meta.display_name
            result["active_skill_lesson_section"] = dict(skill_meta.sections).get("lesson", "")
    if state.get("knowledge_skills"):
        result["knowledge_skills"] = state["knowledge_skills"]
    result["use_knowledge_skills"] = state.get("use_knowledge_skills", True)
    if state.get("kc_skills"):
        result["kc_skills"] = state["kc_skills"]
    result["use_kc_skills"] = state.get("use_kc_skills", _USE_KC_SKILLS_DEFAULT)
    if state.get("pending_topic"):
        result["pending_topic"] = state["pending_topic"]

    # Task status
    for t in ("quiz", "lesson", "slides"):
        s = state.get(f"{t}_task_status")
        if s:
            # Recovery: if state says running but no live task, reset
            if s == "running" and not task_manager.is_running(session_id, t):
                s = None
                state[f"{t}_task_status"] = None
                _save(session_id, state)
            result[f"{t}_task_status"] = s
        p = state.get(f"{t}_task_progress")
        if p:
            result[f"{t}_task_progress"] = p

    result["has_history"] = await _has_history(DEFAULT_USER_ID)

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
        state = _new_session_state()

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

    # Auto-resume: if a topic was waiting for skill selection, start learning
    pending = state.get("pending_topic")
    if pending:
        state["pending_topic"] = None
        state["topic"] = pending
        state["mode"] = "graph"
        _save(session_id, state)
        return {
            "session_id": session_id,
            "active_skill": {
                "name": skill_meta.name,
                "display_name": skill_meta.display_name,
                "sections": skill_meta.affects,
            },
            "auto_resume": True,
            "topic": pending,
        }

    _save(session_id, state)

    return {
        "session_id": session_id,
        "active_skill": {
            "name": skill_meta.name,
            "display_name": skill_meta.display_name,
            "sections": skill_meta.affects,
        },
    }


class SetModeRequest(BaseModel):
    mode: str  # "chat" | "learning"


@router.post("/chat/{session_id}/set-mode")
async def set_mode(session_id: str, req: SetModeRequest) -> dict:
    """Switch a session's user-facing mode (chat vs learning).

    Requires an existing session; returns 404 if not found. Unlike
    set-skill, this endpoint does NOT auto-create — pre-setting mode on
    a non-existent session serves no real use case.
    """
    if req.mode not in ("chat", "learning"):
        raise HTTPException(status_code=400, detail=f"Invalid mode '{req.mode}'")
    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    state["session_mode"] = req.mode
    _save(session_id, state)
    return {"session_id": session_id, "session_mode": req.mode}


class ToggleKnowledgeSkillsRequest(BaseModel):
    enabled: bool


@router.post("/chat/{session_id}/toggle-knowledge-skills")
async def toggle_knowledge_skills(
    session_id: str, req: ToggleKnowledgeSkillsRequest
) -> dict:
    """Toggle knowledge point skill generation on/off for a session."""
    state = _get_session(session_id)
    if not state:
        state = _new_session_state()
    state["use_knowledge_skills"] = req.enabled
    _save(session_id, state)
    return {"session_id": session_id, "use_knowledge_skills": req.enabled}


class ToggleKCSkillsRequest(BaseModel):
    enabled: bool


@router.post("/chat/{session_id}/toggle-kc-skills")
async def toggle_kc_skills(
    session_id: str, req: ToggleKCSkillsRequest
) -> dict:
    """Toggle KC skill resolver + prompt injection on/off for a session."""
    state = _get_session(session_id)
    if not state:
        state = _new_session_state()
    state["use_kc_skills"] = req.enabled
    _save(session_id, state)
    return {"session_id": session_id, "use_kc_skills": req.enabled}


# ------------------------------------------------------------------
# Background generation endpoints
# ------------------------------------------------------------------

from src.api.tasks import (
    task_manager,
    run_quiz_generation,
    run_lesson_generation,
    run_slides_generation,
)


@router.post("/chat/{session_id}/generate/{task_type}")
async def start_generation(session_id: str, task_type: str) -> dict:
    """Start background generation. Returns immediately.

    task_type: "quiz" | "lesson" | "slides"
    """
    if task_type not in ("quiz", "lesson", "slides"):
        raise HTTPException(status_code=400, detail=f"Invalid task type: {task_type}")

    state = _get_session(session_id)
    if not state:
        state = _new_session_state()
        _save(session_id, state)

    # Snapshot state for the background task
    snapshot = dict(state)

    if task_type == "quiz":
        if not state.get("knowledge_graph"):
            raise HTTPException(
                status_code=400, detail="No knowledge graph — learn a topic first"
            )
        task_id = task_manager.start(
            session_id,
            task_type,
            lambda: run_quiz_generation(
                session_id,
                snapshot,
                _get_session,
                _save,
            ),
        )
    elif task_type == "lesson":
        if not state.get("diagnosis_result"):
            raise HTTPException(
                status_code=400, detail="No diagnosis — complete quiz first"
            )
        task_id = task_manager.start(
            session_id,
            task_type,
            lambda: run_lesson_generation(
                session_id,
                snapshot,
                _get_session,
                _save,
            ),
        )
    elif task_type == "slides":
        if not state.get("diagnosis_result"):
            raise HTTPException(
                status_code=400, detail="No diagnosis — complete quiz first"
            )
        task_id = task_manager.start(
            session_id,
            task_type,
            lambda: run_slides_generation(
                session_id,
                snapshot,
                _get_session,
                _save,
            ),
        )

    return {"session_id": session_id, "task_type": task_type, "status": "running"}


@router.get("/chat/{session_id}/generate/{task_type}/status")
async def generation_status(session_id: str, task_type: str) -> dict:
    """Poll generation status."""
    if task_type not in ("quiz", "lesson", "slides"):
        raise HTTPException(status_code=400, detail=f"Invalid task type: {task_type}")

    state = _get_session(session_id)
    if not state:
        return {"status": None, "progress": None, "error": None}

    status_key = f"{task_type}_task_status"
    progress_key = f"{task_type}_task_progress"

    status = state.get(status_key)

    # Recovery: if state says "running" but no live task exists, reset
    if status == "running" and not task_manager.is_running(session_id, task_type):
        status = None
        state[status_key] = None
        _save(session_id, state)

    return {
        "status": status,
        "progress": state.get(progress_key),
        "error": state.get(f"{task_type}_task_error"),
    }


@router.post("/chat/{session_id}/generate/{task_type}/cancel")
async def cancel_generation(session_id: str, task_type: str) -> dict:
    """Cancel a running generation task."""
    if task_type not in ("quiz", "lesson", "slides"):
        raise HTTPException(status_code=400, detail=f"Invalid task type: {task_type}")

    cancelled = task_manager.cancel(session_id, task_type)

    # Reset status in state
    state = _get_session(session_id)
    if state:
        state[f"{task_type}_task_status"] = None
        state[f"{task_type}_task_progress"] = None
        _save(session_id, state)

    return {"cancelled": cancelled}


@router.post("/chat/{session_id}/start-review")
async def start_review(session_id: str) -> dict:
    if not _REVIEW_FLOW_ENABLED:
        raise HTTPException(status_code=404, detail="Review flow disabled")

    state = _get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.memory_client import fetch_mastery
    from src.agent.nodes.review import (
        select_candidate_kps, generate_review_questions,
    )

    # Always re-fetch: the in-state snapshot is stale by definition after
    # submit-review runs progress aggregation. Pinning it would pin the
    # candidate KP list → pin the LLM prompt → pin the generated questions.
    snapshot = await asyncio.to_thread(fetch_mastery, DEFAULT_USER_ID)
    state["mastery_snapshot"] = snapshot

    candidates = select_candidate_kps(snapshot)
    if not candidates:
        raise HTTPException(status_code=404, detail="No history to review")

    # Surface only the candidate KPs' prior questions to keep the prompt tight.
    history: dict[str, list[str]] = state.get("review_question_history") or {}
    candidate_kp_set = {c["subtopic"] for c in candidates}
    recent_for_prompt = {
        kp: list(stems) for kp, stems in history.items()
        if kp in candidate_kp_set and stems
    }

    questions = await asyncio.to_thread(
        generate_review_questions, candidates,
        recent_questions_by_kp=recent_for_prompt or None,
    )
    if not questions:
        raise HTTPException(status_code=502, detail="Failed to generate review questions")

    # Accumulate stems per KP, cap to last 3 — bigger windows blow the prompt
    # without adding diversity (LLM already pattern-broke after a couple).
    updated_history = dict(history)
    for q in questions:
        kp = q.knowledge_point
        stem = q.question
        if not kp or not stem:
            continue
        prev = list(updated_history.get(kp) or [])
        prev.append(stem)
        updated_history[kp] = prev[-3:]
    state["review_question_history"] = updated_history

    state["review_questions"] = questions
    state["review_answers"] = []
    state["review_result"] = None
    _save(session_id, state)

    topic_by_kp = {c["subtopic"]: c.get("topic", "") for c in candidates}
    return {
        "questions": [
            {**q.model_dump(), "topic": topic_by_kp.get(q.knowledge_point, "")}
            for q in questions
        ]
    }


@router.post("/chat/{session_id}/submit-review")
async def submit_review(session_id: str, req: SubmitReviewRequest) -> dict:
    if not _REVIEW_FLOW_ENABLED:
        raise HTTPException(status_code=404, detail="Review flow disabled")

    state = _get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")

    questions = state.get("review_questions") or []
    if not questions:
        raise HTTPException(status_code=400, detail="No review in progress")

    if len(req.answers) != len(questions):
        raise HTTPException(
            status_code=400,
            detail=f"Expected {len(questions)} answers, got {len(req.answers)}",
        )

    from src.agent.nodes.review import grade_review

    result = grade_review(questions, req.answers)

    state["review_answers"] = req.answers
    state["review_result"] = result.model_dump()
    _save(session_id, state)
    _has_history_cache.pop(DEFAULT_USER_ID, None)

    # Fan out 3 BG tasks: ingest_review_session + reviewer + progress (PR-3).
    _dispatch_memory_writes(
        DEFAULT_USER_ID, session_id, state,
        source_event="submit-review",
        ingest_fn=ingest_review_session,
    )

    return {"review_result": result.model_dump()}
