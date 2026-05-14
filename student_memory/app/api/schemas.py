"""Pydantic request/response schemas for the public API.

The shapes here are the only stable coupling surface with the demo
branch. Do not break them without bumping an API version.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _reject_blank_content(value: str) -> str:
    if not value.strip():
        raise ValueError("content must not be blank")
    return value


class HealthResponse(BaseModel):
    status: str = "ok"


# ----------------------------------------------------------------------
# /ingest
# ----------------------------------------------------------------------
class QuestionPayload(BaseModel):
    question_id: str | None = None
    question_text: str | None = None
    student_answer: str | None = None
    is_correct: bool | None = None
    error_type: str | None = None


class SubtopicPayload(BaseModel):
    name: str
    summary: str | None = None
    questions: list[QuestionPayload] = Field(default_factory=list)


class IngestRequest(BaseModel):
    user_id: str
    session_id: str | None = None
    timestamp: str | None = None
    goal_text: str | None = None
    topic: str | None = None
    subtopics: list[SubtopicPayload]


class IngestResponse(BaseModel):
    status: str = "ok"
    session_id: str
    memory_count: int


# ----------------------------------------------------------------------
# /retrieve
# ----------------------------------------------------------------------
class RetrieveRequest(BaseModel):
    user_id: str
    query: str
    top_k: int | None = Field(default=None, gt=0, le=100)


class RetrievedMemory(BaseModel):
    memory_id: str
    topic: str | None
    subtopic: str | None
    timestamp: str
    sim_score: float
    recency_score: float
    match_score: float
    final_score: float
    retrieval_text: str


class RetrieveResponse(BaseModel):
    query: str
    results: list[RetrievedMemory]
    context_text: str


# ----------------------------------------------------------------------
# /mastery
# ----------------------------------------------------------------------
class MasteryItem(BaseModel):
    subtopic: str
    topic: str | None = None
    last_seen_at: str
    session_count: int
    total_questions: int
    total_correct: int
    last_accuracy: float | None = None
    # Time-decay-weighted average accuracy across all sessions for this
    # subtopic. Each session weighted by question_count * 2^(-age/half_life).
    # None if no session had a non-null accuracy.
    weighted_accuracy: float | None = None


class MasteryResponse(BaseModel):
    user_id: str
    items: list[MasteryItem]


# ----------------------------------------------------------------------
# error envelope
# ----------------------------------------------------------------------
class ErrorResponse(BaseModel):
    error: str
    detail: Any | None = None


# ----------------------------------------------------------------------
# /notes (L2 subjective notes)
# ----------------------------------------------------------------------
NoteCategory = Literal["misconception", "preference", "style", "goal"]
SourceEvent = Literal["submit-quiz", "submit-review", "generate-lesson"]


class NoteCreate(BaseModel):
    user_id: str
    category: NoteCategory
    content: str = Field(..., min_length=1, max_length=200)
    source_session_id: str | None = None
    source_event: SourceEvent

    _reject_blank = field_validator("content")(_reject_blank_content)


class NoteOut(BaseModel):
    id: int
    category: NoteCategory
    content: str
    content_hash: str
    created_at: int
    updated_at: int


class NoteDetailOut(NoteOut):
    """`GET /notes/by-id/{note_id}` shape per spec §5.7.

    Reviewer needs ``user_id`` (audit) and ``archived`` (so DELETE/UPDATE
    callers can reason about soft-deleted notes), plus the source fields
    that are present in the table but not in the list view.
    """

    user_id: str
    archived: bool
    source_session_id: str | None = None
    source_event: str


class NotesListResponse(BaseModel):
    notes: list[NoteOut]


class NotePatch(BaseModel):
    content: str = Field(..., min_length=1, max_length=200)

    _reject_blank = field_validator("content")(_reject_blank_content)


class NoteWriteResult(BaseModel):
    note_id: int
    active_count_in_category: int


class NeighborOut(BaseModel):
    id: int
    content: str
    sim_score: float
    age_days: float


class NeighborsResponse(BaseModel):
    neighbors: list[NeighborOut]


class HistoryOut(BaseModel):
    id: int
    note_id: int | None
    event: Literal["ADD", "UPDATE", "DELETE", "NOOP", "REJECT"]
    old_content: str | None
    new_content: str | None
    reason: str
    source_session_id: str | None
    source_event: str | None
    ts: int


class HistoryListResponse(BaseModel):
    history: list[HistoryOut]


class HistoryCreate(BaseModel):
    """Reviewer writes history rows over HTTP for ADD/UPDATE/DELETE/NOOP/REJECT.

    ``note_id`` is null for REJECT/NOOP events that don't correspond to a
    persisted note (the candidate was rejected before any row was created).
    """

    note_id: int | None = None
    event: Literal["ADD", "UPDATE", "DELETE", "NOOP", "REJECT"]
    old_content: str | None = None
    new_content: str | None = None
    reason: str = Field(..., min_length=1, max_length=200)
    source_session_id: str | None = None
    source_event: SourceEvent | None = None


# ----------------------------------------------------------------------
# /progress + mastery
# ----------------------------------------------------------------------
class MasterySample(BaseModel):
    kp: str
    score: float = Field(..., ge=0.0, le=1.0)
    source: Literal["quiz", "review", "lesson_completion"]


class ProgressUpsert(BaseModel):
    user_id: str
    topic: str
    mastery_overall: float = Field(..., ge=0.0, le=1.0)
    last_session_id: str
    kp_samples: list[MasterySample] = Field(default_factory=list)


class ProgressUpsertResponse(BaseModel):
    topic: str
    new_status: Literal["in_progress", "mastered", "abandoned"]
    sample_count: int


# ----------------------------------------------------------------------
# /memory/snapshot
# ----------------------------------------------------------------------
class SnapshotResponse(BaseModel):
    snapshot_md: str = Field(..., max_length=3500)
    char_count: int
    truncated: bool
    sections: dict[str, int]


# ----------------------------------------------------------------------
# /errors
# ----------------------------------------------------------------------
class ErrorLog(BaseModel):
    source: Literal["main_app", "memory_service"]
    user_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    op: str
    status: str
    error_class: str | None = None
    error_msg: str | None = None
    payload_excerpt: str | None = None


class ErrorLogResponse(BaseModel):
    logged: bool


class ErrorOut(BaseModel):
    id: int
    ts: int
    source: str
    user_id: str | None
    session_id: str | None
    correlation_id: str | None
    op: str
    status: str
    error_class: str | None
    error_msg: str | None
    payload_excerpt: str | None


class ErrorsListResponse(BaseModel):
    errors: list[ErrorOut]
