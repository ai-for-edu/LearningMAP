"""Phase 5: parser / normalizer / retrieval-text builder unit tests."""

from __future__ import annotations

import json

import pytest

from app.memory.normalizer import normalize
from app.memory.parser import IngestValidationError, parse_session
from app.memory.retrieval_text import build_retrieval_text


def test_parse_session_happy_path(sample_session_payload: dict) -> None:
    raw = parse_session(sample_session_payload)
    assert raw.user_id == "u_001"
    assert raw.session_id == "s_001"
    assert raw.timestamp == "2026-03-28T10:30:00Z"
    assert raw.topic == "Quadratic Equations"
    assert len(raw.subtopics) == 2
    assert raw.subtopics[0].name == "Discriminant"
    assert len(raw.subtopics[0].questions) == 2


def test_parse_session_generates_session_id_when_missing() -> None:
    raw = parse_session(
        {
            "user_id": "u_002",
            "timestamp": "2026-04-01T00:00:00Z",
            "subtopics": [{"name": "x", "questions": []}],
        }
    )
    assert raw.session_id.startswith("s_")
    assert len(raw.session_id) > 2


def test_parse_session_uses_now_when_timestamp_missing() -> None:
    raw = parse_session(
        {
            "user_id": "u_002",
            "subtopics": [{"name": "x", "questions": []}],
        }
    )
    assert raw.timestamp.endswith("Z")


def test_parse_session_rejects_missing_user_id() -> None:
    with pytest.raises(IngestValidationError):
        parse_session({"subtopics": [{"name": "x"}]})


def test_parse_session_rejects_empty_subtopics() -> None:
    with pytest.raises(IngestValidationError):
        parse_session({"user_id": "u", "subtopics": []})


def test_parse_session_rejects_unparseable_timestamp() -> None:
    with pytest.raises(IngestValidationError):
        parse_session(
            {
                "user_id": "u",
                "timestamp": "not-a-date",
                "subtopics": [{"name": "x"}],
            }
        )


def test_normalize_produces_one_unit_per_subtopic(
    sample_session_payload: dict,
) -> None:
    raw = parse_session(sample_session_payload)
    units = normalize(raw)
    assert len(units) == 2
    assert {u.subtopic for u in units} == {"Discriminant", "Factoring"}
    for u in units:
        assert u.user_id == "u_001"
        assert u.session_id == "s_001"
        assert u.memory_type == "subtopic"
        assert u.memory_id.startswith("m_")


def test_normalize_computes_stats(sample_session_payload: dict) -> None:
    raw = parse_session(sample_session_payload)
    units = {u.subtopic: u for u in normalize(raw)}

    discr = units["Discriminant"]
    assert discr.question_count == 2
    assert discr.correct_count == 1
    assert discr.accuracy == pytest.approx(0.5)
    assert discr.error_types == ["discriminant confusion"]

    factoring = units["Factoring"]
    assert factoring.question_count == 1
    assert factoring.correct_count == 1
    assert factoring.accuracy == pytest.approx(1.0)
    assert factoring.error_types == []


def test_normalize_handles_missing_optional_fields() -> None:
    raw = parse_session(
        {
            "user_id": "u_001",
            "timestamp": "2026-04-01T00:00:00Z",
            "subtopics": [{"name": "Bare", "questions": []}],
        }
    )
    units = normalize(raw)
    assert len(units) == 1
    u = units[0]
    assert u.summary is None
    assert u.question_count == 0
    assert u.correct_count == 0
    assert u.accuracy is None
    assert u.error_types == []


def test_normalize_raw_json_is_per_subtopic_slice(
    sample_session_payload: dict,
) -> None:
    raw = parse_session(sample_session_payload)
    units = normalize(raw)
    for u in units:
        slice_dict = json.loads(u.raw_json)
        assert slice_dict["session_id"] == "s_001"
        assert slice_dict["subtopic"]["name"] == u.subtopic


def test_build_retrieval_text_exact_format(
    sample_session_payload: dict,
) -> None:
    raw = parse_session(sample_session_payload)
    units = {u.subtopic: u for u in normalize(raw)}
    text = build_retrieval_text(units["Discriminant"])
    assert text == (
        "Learning goal: I want to study quadratic equations\n"
        "Topic: Quadratic Equations\n"
        "Subtopic: Discriminant\n"
        "Summary: Student repeatedly confused root cases.\n"
        "Stats: 2 questions, 1 correct, accuracy 50%\n"
        "Error types: discriminant confusion"
    )


def test_build_retrieval_text_handles_missing_fields() -> None:
    raw = parse_session(
        {
            "user_id": "u_001",
            "timestamp": "2026-04-01T00:00:00Z",
            "subtopics": [{"name": "Bare", "questions": []}],
        }
    )
    text = build_retrieval_text(normalize(raw)[0])
    assert "Learning goal: (unspecified)" in text
    assert "Summary: (no diagnostic summary)" in text
    assert "Stats: 0 questions, 0 correct, accuracy n/a" in text
    assert "Error types: none" in text
