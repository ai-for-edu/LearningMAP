"""Pydantic request/response schemas for the public API.

The shapes here are the only stable coupling surface with the demo
branch. Do not break them without bumping an API version.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class MasteryResponse(BaseModel):
    user_id: str
    items: list[MasteryItem]


# ----------------------------------------------------------------------
# error envelope
# ----------------------------------------------------------------------
class ErrorResponse(BaseModel):
    error: str
    detail: Any | None = None
