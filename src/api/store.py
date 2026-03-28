"""Simple SQLite session store for persisting chat state across restarts."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from src.llm.schemas import DiagnosisResult, KnowledgeGraph, QuizQuestion

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "sessions.db"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)"
    )
    return conn


def _serialize_state(state: dict[str, Any]) -> str:
    """Convert session state to JSON string."""
    out: dict[str, Any] = {}
    for k, v in state.items():
        if k == "messages":
            out[k] = [
                {"type": getattr(m, "type", "human"), "content": m.content}
                if hasattr(m, "content")
                else {"type": "human", "content": str(m)}
                for m in v
            ]
        elif hasattr(v, "model_dump"):
            out[k] = v.model_dump()
        elif isinstance(v, list) and v and hasattr(v[0], "model_dump"):
            out[k] = [item.model_dump() for item in v]
        else:
            out[k] = v
    return json.dumps(out, ensure_ascii=False, default=str)


def _deserialize_state(data: str) -> dict[str, Any]:
    """Convert JSON string back to session state."""
    raw = json.loads(data)

    # Restore messages
    if "messages" in raw and isinstance(raw["messages"], list):
        msgs = []
        for m in raw["messages"]:
            if isinstance(m, dict):
                if m.get("type") == "ai":
                    msgs.append(AIMessage(content=m["content"]))
                else:
                    msgs.append(HumanMessage(content=m["content"]))
            else:
                msgs.append(HumanMessage(content=str(m)))
        raw["messages"] = msgs

    # Restore Pydantic models
    if raw.get("knowledge_graph") and isinstance(raw["knowledge_graph"], dict):
        try:
            raw["knowledge_graph"] = KnowledgeGraph(**raw["knowledge_graph"])
        except Exception:
            pass

    if raw.get("questions") and isinstance(raw["questions"], list):
        try:
            raw["questions"] = [QuizQuestion(**q) if isinstance(q, dict) else q for q in raw["questions"]]
        except Exception:
            pass

    if raw.get("diagnosis_result") and isinstance(raw["diagnosis_result"], dict):
        try:
            raw["diagnosis_result"] = DiagnosisResult(**raw["diagnosis_result"])
        except Exception:
            pass

    return raw


def save_session(session_id: str, state: dict[str, Any]) -> None:
    """Persist session state to SQLite."""
    try:
        data = _serialize_state(state)
        db = _get_db()
        db.execute(
            "INSERT OR REPLACE INTO sessions (id, data) VALUES (?, ?)",
            (session_id, data),
        )
        db.commit()
        db.close()
    except Exception:
        logger.exception("Failed to save session %s", session_id)


def load_session(session_id: str) -> dict[str, Any] | None:
    """Load session state from SQLite."""
    try:
        db = _get_db()
        row = db.execute(
            "SELECT data FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        db.close()
        if row:
            return _deserialize_state(row[0])
    except Exception:
        logger.exception("Failed to load session %s", session_id)
    return None
