"""Validate new Pydantic models for content length, category, etc."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_note_create_rejects_too_long_content() -> None:
    from app.api.schemas import NoteCreate

    with pytest.raises(ValidationError):
        NoteCreate(
            user_id="u1",
            category="misconception",
            content="x" * 201,
            source_event="submit-quiz",
        )


def test_note_create_rejects_invalid_category() -> None:
    from app.api.schemas import NoteCreate

    with pytest.raises(ValidationError):
        NoteCreate(
            user_id="u1", category="random", content="ok", source_event="submit-quiz"
        )


def test_note_create_rejects_invalid_source_event() -> None:
    from app.api.schemas import NoteCreate

    with pytest.raises(ValidationError):
        NoteCreate(
            user_id="u1", category="goal", content="ok", source_event="something-else"
        )


def test_note_out_carries_content_hash() -> None:
    from app.api.schemas import NoteOut

    n = NoteOut(
        id=1,
        content="x",
        content_hash="h",
        category="goal",
        created_at=1000,
        updated_at=1000,
    )
    assert n.content_hash == "h"


def test_progress_upsert_kp_samples_required_and_typed() -> None:
    from app.api.schemas import MasterySample, ProgressUpsert

    p = ProgressUpsert(
        user_id="u1",
        topic="A",
        mastery_overall=0.7,
        last_session_id="s",
        kp_samples=[MasterySample(kp="k1", score=0.5, source="quiz")],
    )
    assert p.kp_samples[0].source == "quiz"


def test_mastery_sample_rejects_invalid_source() -> None:
    from app.api.schemas import MasterySample

    with pytest.raises(ValidationError):
        MasterySample(kp="k", score=0.5, source="invalid")


def test_snapshot_response_caps_md_at_3500() -> None:
    from app.api.schemas import SnapshotResponse

    with pytest.raises(ValidationError):
        SnapshotResponse(
            snapshot_md="x" * 3501, char_count=3501, truncated=False, sections={}
        )


def test_error_log_accepts_nullable_fields() -> None:
    from app.api.schemas import ErrorLog

    e = ErrorLog(
        source="main_app",
        user_id=None,
        session_id=None,
        correlation_id=None,
        op="fetch_snapshot",
        status="http_5xx",
        error_class="HTTPError",
        error_msg="boom",
        payload_excerpt="",
    )
    assert e.user_id is None
