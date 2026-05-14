"""Simple SQLite session store for persisting chat state across restarts."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from src.llm.schemas import DiagnosisResult, KnowledgeGraph, QuizQuestion

logger = logging.getLogger(__name__)

_DB_PATH = Path(
    os.environ.get(
        "SESSIONS_DB_PATH",
        str(Path(__file__).resolve().parent.parent.parent / "sessions.db"),
    )
)


def _serialize_message_list(msgs: list) -> list[dict]:
    """Serialize a list of langchain messages to JSON-safe dicts.

    Preserves type, content, and additional_kwargs round-trip.
    """
    return [
        {
            "type": getattr(m, "type", "human"),
            "content": m.content,
            "additional_kwargs": getattr(m, "additional_kwargs", {}) or {},
        }
        if hasattr(m, "content")
        else {"type": "human", "content": str(m), "additional_kwargs": {}}
        for m in msgs
    ]


def _serialize_state(state: dict[str, Any]) -> str:
    """Convert session state to JSON string."""
    out: dict[str, Any] = {}
    for k, v in state.items():
        if k in ("messages", "chat_messages"):
            out[k] = _serialize_message_list(v)
        elif hasattr(v, "model_dump"):
            out[k] = v.model_dump()
        elif isinstance(v, list) and v and hasattr(v[0], "model_dump"):
            out[k] = [item.model_dump() for item in v]
        else:
            out[k] = v
    return json.dumps(out, ensure_ascii=False, default=str)


def _deserialize_message_list(raw_msgs: list) -> list:
    """Reconstruct langchain message objects from JSON dicts."""
    out = []
    for m in raw_msgs:
        if isinstance(m, dict):
            kwargs = m.get("additional_kwargs", {}) or {}
            if m.get("type") == "ai":
                out.append(AIMessage(content=m["content"], additional_kwargs=kwargs))
            else:
                out.append(HumanMessage(content=m["content"], additional_kwargs=kwargs))
        else:
            out.append(HumanMessage(content=str(m)))
    return out


def _deserialize_state(data: str) -> dict[str, Any]:
    """Convert JSON string back to session state."""
    raw = json.loads(data)

    # Restore messages
    for key in ("messages", "chat_messages"):
        if key in raw and isinstance(raw[key], list):
            raw[key] = _deserialize_message_list(raw[key])

    # Restore Pydantic models
    if raw.get("knowledge_graph") and isinstance(raw["knowledge_graph"], dict):
        try:
            raw["knowledge_graph"] = KnowledgeGraph(**raw["knowledge_graph"])
        except Exception:
            logger.warning("Failed to deserialize knowledge_graph", exc_info=True)

    if raw.get("questions") and isinstance(raw["questions"], list):
        try:
            raw["questions"] = [QuizQuestion(**q) if isinstance(q, dict) else q for q in raw["questions"]]
        except Exception:
            logger.warning("Failed to deserialize questions", exc_info=True)

    if raw.get("review_questions") and isinstance(raw["review_questions"], list):
        try:
            raw["review_questions"] = [
                QuizQuestion(**q) if isinstance(q, dict) else q
                for q in raw["review_questions"]
            ]
        except Exception:
            logger.warning("Failed to deserialize review_questions", exc_info=True)

    if raw.get("diagnosis_result") and isinstance(raw["diagnosis_result"], dict):
        try:
            raw["diagnosis_result"] = DiagnosisResult(**raw["diagnosis_result"])
        except Exception:
            logger.warning("Failed to deserialize diagnosis_result", exc_info=True)

    return raw


def save_session(session_id: str, state: dict[str, Any]) -> None:
    """Persist session state to SQLite."""
    try:
        data = _serialize_state(state)
        with sqlite3.connect(str(_DB_PATH)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)"
            )
            db.execute(
                "INSERT OR REPLACE INTO sessions (id, data) VALUES (?, ?)",
                (session_id, data),
            )
    except Exception:
        logger.exception("Failed to save session %s", session_id)


def load_session(session_id: str) -> dict[str, Any] | None:
    """Load session state from SQLite."""
    try:
        with sqlite3.connect(str(_DB_PATH)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)"
            )
            row = db.execute(
                "SELECT data FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row:
            return _deserialize_state(row[0])
    except Exception:
        logger.exception("Failed to load session %s", session_id)
    return None
