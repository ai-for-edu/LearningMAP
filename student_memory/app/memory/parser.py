"""Raw learning-record parser.

Pure functions only — no I/O, no embedder, no SQLite. Validates the
shape of the inbound JSON, fills in defaults, and returns a typed
``RawSession`` dataclass that the normalizer can split into memory units.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.utils.time import format_timestamp, parse_timestamp, utcnow


class IngestValidationError(ValueError):
    """Raised when an ingest payload cannot be parsed.

    Mapped to HTTP 422 by the API layer.
    """


@dataclass(frozen=True)
class RawQuestion:
    question_id: str | None
    question_text: str | None
    student_answer: str | None
    is_correct: bool | None
    error_type: str | None


@dataclass(frozen=True)
class RawSubtopic:
    name: str
    summary: str | None
    questions: list[RawQuestion] = field(default_factory=list)


@dataclass(frozen=True)
class RawSession:
    user_id: str
    session_id: str
    timestamp: str  # ISO-8601 UTC string
    goal_text: str | None
    topic: str | None
    subtopics: list[RawSubtopic]
    raw: dict[str, Any]


def _require_str(payload: dict[str, Any], key: str) -> str:
    val = payload.get(key)
    if not isinstance(val, str) or not val.strip():
        raise IngestValidationError(f"missing or invalid required field: {key}")
    return val.strip()


def _maybe_str(payload: dict[str, Any], key: str) -> str | None:
    val = payload.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise IngestValidationError(f"field {key} must be a string")
    return val.strip() or None


def _parse_question(raw: Any, idx: int) -> RawQuestion:
    if not isinstance(raw, dict):
        raise IngestValidationError(f"question[{idx}] must be an object")
    return RawQuestion(
        question_id=raw.get("question_id"),
        question_text=raw.get("question_text"),
        student_answer=raw.get("student_answer"),
        is_correct=raw.get("is_correct"),
        error_type=raw.get("error_type"),
    )


def _parse_subtopic(raw: Any, idx: int) -> RawSubtopic:
    if not isinstance(raw, dict):
        raise IngestValidationError(f"subtopics[{idx}] must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise IngestValidationError(f"subtopics[{idx}].name is required")
    questions_raw = raw.get("questions") or []
    if not isinstance(questions_raw, list):
        raise IngestValidationError(f"subtopics[{idx}].questions must be a list")
    questions = [_parse_question(q, j) for j, q in enumerate(questions_raw)]
    summary = raw.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise IngestValidationError(f"subtopics[{idx}].summary must be a string")
    return RawSubtopic(
        name=name.strip(),
        summary=summary.strip()
        if isinstance(summary, str) and summary.strip()
        else None,
        questions=questions,
    )


def parse_session(payload: dict[str, Any]) -> RawSession:
    """Validate and convert a POST /ingest payload into ``RawSession``.

    Generates ``session_id`` if absent. Parses and re-formats the
    timestamp; rejects unparseable timestamps with
    ``IngestValidationError`` (mapped to HTTP 422 upstream) so we never
    persist garbage that the retriever would have to silently drop.
    """
    if not isinstance(payload, dict):
        raise IngestValidationError("payload must be a JSON object")

    user_id = _require_str(payload, "user_id")

    session_id = payload.get("session_id")
    if session_id is None or (isinstance(session_id, str) and not session_id.strip()):
        session_id = f"s_{uuid.uuid4().hex[:12]}"
    elif not isinstance(session_id, str):
        raise IngestValidationError("session_id must be a string")
    else:
        session_id = session_id.strip()

    ts_raw = payload.get("timestamp")
    if ts_raw is None or (isinstance(ts_raw, str) and not ts_raw.strip()):
        ts = utcnow()
    elif not isinstance(ts_raw, str):
        raise IngestValidationError("timestamp must be an ISO-8601 string")
    else:
        parsed = parse_timestamp(ts_raw)
        if parsed is None:
            raise IngestValidationError(f"unparseable timestamp: {ts_raw!r}")
        ts = parsed
    timestamp = format_timestamp(ts)

    subtopics_raw = payload.get("subtopics") or []
    if not isinstance(subtopics_raw, list):
        raise IngestValidationError("subtopics must be a list")
    if len(subtopics_raw) == 0:
        raise IngestValidationError("at least one subtopic is required")
    subtopics = [_parse_subtopic(s, i) for i, s in enumerate(subtopics_raw)]

    return RawSession(
        user_id=user_id,
        session_id=session_id,
        timestamp=timestamp,
        goal_text=_maybe_str(payload, "goal_text"),
        topic=_maybe_str(payload, "topic"),
        subtopics=subtopics,
        raw=payload,
    )
