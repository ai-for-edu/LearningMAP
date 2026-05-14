"""Unit tests for src.memory_client.

Tests cover:
- _build_ingest_payload: data mapping from openail state to ingest format
- retrieve_memory: happy path + HTTP error + service down
- ingest_session: happy path + 409 duplicate + HTTP error + empty subtopics
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.llm.schemas import (
    DiagnosisBreakdown,
    DiagnosisResult,
    QuizQuestion,
)
from src.memory_client import (
    _build_ingest_payload,
    ingest_session,
    retrieve_memory,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_questions() -> list[QuizQuestion]:
    """Two questions on different knowledge points."""
    return [
        QuizQuestion(
            id="q1",
            knowledge_point="discriminant",
            question="What is the role of the discriminant b²-4ac?",
            options=["determine the number of roots", "find roots", "factor", "complete the square"],
            answer=0,
        ),
        QuizQuestion(
            id="q2",
            knowledge_point="quadratic formula",
            question="Solve x²-4x+4=0.",
            options=["x=1", "x=2", "x=3", "x=4"],
            answer=1,
        ),
    ]


def _make_diagnosis() -> DiagnosisResult:
    return DiagnosisResult(
        score=50,
        total=2,
        correct=1,
        incorrect=1,
        breakdown=[
            DiagnosisBreakdown(name="discriminant", status="weak", detail="0/1 needs review"),
            DiagnosisBreakdown(name="quadratic formula", status="mastered", detail="1/1 mastered"),
        ],
    )


def _make_state(
    with_diagnosis: bool = True,
    answers: list[int] | None = None,
) -> dict:
    """Build a realistic openail session state."""
    state = {
        "topic": "Pythagorean theorem",
        "questions": _make_questions(),
        "answers": answers if answers is not None else [1, 1],  # q1 wrong, q2 correct
    }
    if with_diagnosis:
        state["diagnosis_result"] = _make_diagnosis()
    return state


# ------------------------------------------------------------------
# _build_ingest_payload
# ------------------------------------------------------------------

class TestBuildIngestPayload:
    """Tests for the state-to-payload data mapping."""

    def test_basic_structure(self) -> None:
        payload = _build_ingest_payload("u1", "s1", _make_state())

        assert payload["user_id"] == "u1"
        assert payload["session_id"] == "s1"
        assert payload["topic"] == "Pythagorean theorem"
        assert payload["goal_text"] == "Learn Pythagorean theorem"
        assert len(payload["subtopics"]) == 2

    def test_subtopics_grouped_by_knowledge_point(self) -> None:
        payload = _build_ingest_payload("u1", "s1", _make_state())
        names = {s["name"] for s in payload["subtopics"]}
        assert names == {"discriminant", "quadratic formula"}

    def test_question_correctness_mapping(self) -> None:
        """answers=[1, 1] → q1(answer=0) wrong, q2(answer=1) correct."""
        payload = _build_ingest_payload("u1", "s1", _make_state(answers=[1, 1]))

        subtopic_map = {s["name"]: s for s in payload["subtopics"]}

        # discriminant: student answered 1, correct is 0 → wrong
        q1 = subtopic_map["discriminant"]["questions"][0]
        assert q1["is_correct"] is False
        assert q1["error_type"] == "wrong_answer"
        assert q1["student_answer"] == "1"

        # quadratic formula: student answered 1, correct is 1 → right
        q2 = subtopic_map["quadratic formula"]["questions"][0]
        assert q2["is_correct"] is True
        assert q2["error_type"] is None

    def test_diagnosis_summary_in_subtopics(self) -> None:
        payload = _build_ingest_payload("u1", "s1", _make_state())
        subtopic_map = {s["name"]: s for s in payload["subtopics"]}

        assert "weak" in subtopic_map["discriminant"]["summary"]
        assert "mastered" in subtopic_map["quadratic formula"]["summary"]

    def test_no_diagnosis_still_works(self) -> None:
        payload = _build_ingest_payload("u1", "s1", _make_state(with_diagnosis=False))
        assert len(payload["subtopics"]) == 2
        # summaries are empty without diagnosis
        for st in payload["subtopics"]:
            assert st["summary"] == ""

    def test_empty_questions_returns_empty_subtopics(self) -> None:
        state = {"topic": "Pythagorean theorem", "questions": [], "answers": []}
        payload = _build_ingest_payload("u1", "s1", state)
        assert payload["subtopics"] == []

    def test_no_topic_returns_none(self) -> None:
        state = {"topic": None, "questions": [], "answers": []}
        payload = _build_ingest_payload("u1", "s1", state)
        assert payload["topic"] is None
        assert payload["goal_text"] is None

    def test_dict_format_questions(self) -> None:
        """State may contain dict-format questions (deserialized from JSON)."""
        state = {
            "topic": "algebra",
            "questions": [
                {
                    "id": "q1",
                    "knowledge_point": "equations",
                    "question": "1+1=?",
                    "options": ["1", "2", "3", "4"],
                    "answer": 1,
                }
            ],
            "answers": [1],
            "diagnosis_result": None,
        }
        payload = _build_ingest_payload("u1", "s1", state)
        assert len(payload["subtopics"]) == 1
        assert payload["subtopics"][0]["name"] == "equations"
        assert payload["subtopics"][0]["questions"][0]["is_correct"] is True

    def test_fewer_answers_than_questions(self) -> None:
        """Student didn't answer all questions."""
        state = _make_state(answers=[0])  # only answered q1
        payload = _build_ingest_payload("u1", "s1", state)

        subtopic_map = {s["name"]: s for s in payload["subtopics"]}
        # q1 answered (0 == correct 0) → correct
        assert subtopic_map["discriminant"]["questions"][0]["is_correct"] is True
        # q2 not answered (index -1 != 1) → wrong
        assert subtopic_map["quadratic formula"]["questions"][0]["is_correct"] is False
        assert subtopic_map["quadratic formula"]["questions"][0]["student_answer"] == "-1"


# ------------------------------------------------------------------
# retrieve_memory
# ------------------------------------------------------------------

class TestRetrieveMemory:

    @patch("src.memory_client._get_client")
    def test_happy_path(self, mock_get_client: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "query": "Pythagorean theorem",
            "results": [],
            "context_text": "History: discriminant accuracy 50%",
        }
        mock_get_client.return_value.post.return_value = mock_resp

        result = retrieve_memory("u1", "Pythagorean theorem")
        assert result == "History: discriminant accuracy 50%"

        mock_get_client.return_value.post.assert_called_once_with(
            "/retrieve",
            json={"user_id": "u1", "query": "Pythagorean theorem", "top_k": 5},
        )

    @patch("src.memory_client._get_client")
    def test_http_error_returns_empty(self, mock_get_client: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )
        mock_get_client.return_value.post.return_value = mock_resp

        result = retrieve_memory("u1", "Pythagorean theorem")
        assert result == ""

    @patch("src.memory_client._get_client")
    def test_connection_error_returns_empty(self, mock_get_client: MagicMock) -> None:
        mock_get_client.return_value.post.side_effect = httpx.ConnectError("refused")

        result = retrieve_memory("u1", "Pythagorean theorem")
        assert result == ""

    @patch("src.memory_client._get_client")
    def test_missing_context_text_returns_empty(self, mock_get_client: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"query": "x", "results": []}
        mock_get_client.return_value.post.return_value = mock_resp

        result = retrieve_memory("u1", "x")
        assert result == ""

    @patch("src.memory_client._get_client")
    def test_custom_top_k(self, mock_get_client: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"context_text": "ok"}
        mock_get_client.return_value.post.return_value = mock_resp

        retrieve_memory("u1", "query", top_k=10)
        call_args = mock_get_client.return_value.post.call_args
        assert call_args[1]["json"]["top_k"] == 10


# ------------------------------------------------------------------
# ingest_session
# ------------------------------------------------------------------

class TestIngestSession:

    @patch("src.memory_client._get_client")
    def test_happy_path(self, mock_get_client: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "session_id": "s1", "memory_count": 2}
        mock_get_client.return_value.post.return_value = mock_resp

        # Should not raise
        ingest_session("u1", "s1", _make_state())

        mock_get_client.return_value.post.assert_called_once()
        payload = mock_get_client.return_value.post.call_args[1]["json"]
        assert payload["user_id"] == "u1"
        assert payload["session_id"] == "s1"
        assert len(payload["subtopics"]) == 2

    @patch("src.memory_client._get_client")
    def test_duplicate_409_silent(self, mock_get_client: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_get_client.return_value.post.return_value = mock_resp

        # Should not raise
        ingest_session("u1", "s1", _make_state())

    @patch("src.memory_client._get_client")
    def test_http_error_silent(self, mock_get_client: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        mock_get_client.return_value.post.return_value = mock_resp

        # Should not raise
        ingest_session("u1", "s1", _make_state())

    @patch("src.memory_client._get_client")
    def test_connection_error_silent(self, mock_get_client: MagicMock) -> None:
        mock_get_client.return_value.post.side_effect = httpx.ConnectError("refused")

        # Should not raise
        ingest_session("u1", "s1", _make_state())

    @patch("src.memory_client._get_client")
    def test_empty_subtopics_skips_request(self, mock_get_client: MagicMock) -> None:
        state = {"topic": "x", "questions": [], "answers": []}
        ingest_session("u1", "s1", state)

        # Should NOT call the HTTP client at all
        mock_get_client.return_value.post.assert_not_called()


# ------------------------------------------------------------------
# fetch_mastery
# ------------------------------------------------------------------

class TestFetchMastery:

    @patch("src.memory_client._get_client")
    def test_happy_path(self, mock_get_client: MagicMock) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "user_id": "u1",
            "items": [
                {
                    "subtopic": "discriminant", "topic": "quadratic equations",
                    "last_seen_at": "2026-04-15T10:00:00Z",
                    "session_count": 2, "total_questions": 4,
                    "total_correct": 2, "last_accuracy": 0.5,
                }
            ],
        }
        mock_get_client.return_value.get.return_value = mock_resp

        items = fetch_mastery("u1")
        assert len(items) == 1
        assert items[0]["subtopic"] == "discriminant"

        mock_get_client.return_value.get.assert_called_once_with(
            "/mastery/u1", params={}
        )

    @patch("src.memory_client._get_client")
    def test_passes_topic_and_since_days_as_params(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"user_id": "u1", "items": []}
        mock_get_client.return_value.get.return_value = mock_resp

        fetch_mastery("u1", topic="algebra", since_days=14)

        mock_get_client.return_value.get.assert_called_once_with(
            "/mastery/u1", params={"topic": "algebra", "since_days": 14}
        )

    @patch("src.memory_client._get_client")
    def test_http_error_returns_empty(self, mock_get_client: MagicMock) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )
        mock_get_client.return_value.get.return_value = mock_resp

        assert fetch_mastery("u1") == []

    @patch("src.memory_client._get_client")
    def test_connection_error_returns_empty(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import fetch_mastery

        mock_get_client.return_value.get.side_effect = httpx.ConnectError(
            "refused"
        )

        assert fetch_mastery("u1") == []

    @patch("src.memory_client._get_client")
    def test_malformed_response_returns_empty(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"unexpected": "shape"}
        mock_get_client.return_value.get.return_value = mock_resp

        # Missing "items" key — should degrade to []
        assert fetch_mastery("u1") == []


# ------------------------------------------------------------------
# ingest_review_session — cross-topic review write
# ------------------------------------------------------------------


class TestIngestReviewSession:
    """submit-review writes per-topic ingest payloads, dedup-safe via
    session_id suffix `{session_id}_review_{topic_slug}`."""

    def _state_with_review(self, snapshot: list[dict]) -> dict:
        return {
            "review_questions": [
                QuizQuestion(
                    id="rq1",
                    knowledge_point="discriminant",
                    question="Is b²-4ac > 0?",
                    options=["a", "b", "c", "d"],
                    answer=0,
                ),
                QuizQuestion(
                    id="rq2",
                    knowledge_point="addition-multiplication principle",
                    question="2×3=?",
                    options=["5", "6", "8", "9"],
                    answer=1,
                ),
            ],
            "review_answers": [0, 0],  # rq1 right, rq2 wrong
            "review_result": {
                "weak_kps": ["addition-multiplication principle"],
                "strong_kps": ["discriminant"],
                "score": 0.5,
                "focus_directive": "...",
            },
            "mastery_snapshot": snapshot,
        }

    @patch("src.memory_client._get_client")
    def test_splits_by_topic_one_call_per_topic(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import ingest_review_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "memory_count": 1}
        mock_get_client.return_value.post.return_value = mock_resp

        snapshot = [
            {"subtopic": "discriminant", "topic": "quadratic equations"},
            {"subtopic": "addition-multiplication principle", "topic": "multiplication principle"},
        ]
        ingest_review_session("u1", "s1", self._state_with_review(snapshot))

        # 2 different topics → 2 ingest calls
        assert mock_get_client.return_value.post.call_count == 2
        call_topics = [
            c.kwargs["json"]["topic"]
            for c in mock_get_client.return_value.post.call_args_list
        ]
        assert set(call_topics) == {"quadratic equations", "multiplication principle"}

    @patch("src.memory_client._get_client")
    def test_session_id_suffixed_per_topic(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import ingest_review_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "memory_count": 1}
        mock_get_client.return_value.post.return_value = mock_resp

        snapshot = [
            {"subtopic": "discriminant", "topic": "quadratic equations"},
            {"subtopic": "addition-multiplication principle", "topic": "multiplication principle"},
        ]
        ingest_review_session("u1", "sess123", self._state_with_review(snapshot))

        ids = sorted(
            c.kwargs["json"]["session_id"]
            for c in mock_get_client.return_value.post.call_args_list
        )
        # Each is "sess123_review_<slug>" and they differ
        assert all(i.startswith("sess123_review_") for i in ids)
        assert ids[0] != ids[1]

    @patch("src.memory_client._get_client")
    def test_summary_marks_review_strong_vs_weak(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import ingest_review_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "memory_count": 1}
        mock_get_client.return_value.post.return_value = mock_resp

        snapshot = [
            {"subtopic": "discriminant", "topic": "quadratic equations"},
            {"subtopic": "addition-multiplication principle", "topic": "multiplication principle"},
        ]
        ingest_review_session("u1", "s1", self._state_with_review(snapshot))

        # Find the call for "quadratic equations" and check its summary contains "strong"
        for c in mock_get_client.return_value.post.call_args_list:
            payload = c.kwargs["json"]
            sub = payload["subtopics"][0]
            if payload["topic"] == "quadratic equations":
                assert "strong" in sub["summary"]
            elif payload["topic"] == "multiplication principle":
                assert "weak" in sub["summary"]

    @patch("src.memory_client._get_client")
    def test_no_questions_means_no_post(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import ingest_review_session

        ingest_review_session("u1", "s1", {})
        mock_get_client.return_value.post.assert_not_called()

    @patch("src.memory_client._get_client")
    def test_http_error_silent(self, mock_get_client: MagicMock) -> None:
        from src.memory_client import ingest_review_session

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        mock_get_client.return_value.post.return_value = mock_resp

        snapshot = [{"subtopic": "discriminant", "topic": "quadratic equations"}]
        # Should not raise
        ingest_review_session("u1", "s1", self._state_with_review(snapshot))
