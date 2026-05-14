"""End-to-end tests for review-flow API (PR-2).

Uses FastAPI TestClient; mocks fetch_mastery + LLM client.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from run.serve import app
from src.api import chat as chat_module


def _make_session_id() -> str:
    """Create a pre-populated session directly in the in-memory store."""
    sid = str(uuid.uuid4())
    chat_module._sessions[sid] = chat_module._new_session_state()
    return sid


@pytest.fixture
def client_gate_off(monkeypatch):
    monkeypatch.setattr(chat_module, "_REVIEW_FLOW_ENABLED", False)
    chat_module._has_history_cache.clear()
    return TestClient(app)


@pytest.fixture
def client_gate_on(monkeypatch):
    monkeypatch.setattr(chat_module, "_REVIEW_FLOW_ENABLED", True)
    chat_module._has_history_cache.clear()
    return TestClient(app)


@pytest.fixture
def session_id_gate_on(client_gate_on):
    """Pre-populate a session (gate-on); returns its id."""
    sid = _make_session_id()
    return sid


# ---------------------------------------------------------------------------
# Gate OFF: endpoints disabled
# ---------------------------------------------------------------------------


def test_gate_off_start_review_returns_404(client_gate_off):
    sid = _make_session_id()
    r = client_gate_off.post(f"/api/chat/{sid}/start-review")
    assert r.status_code == 404
    assert r.json()["detail"] == "Review flow disabled"


def test_gate_off_state_has_history_false(client_gate_off):
    sid = _make_session_id()
    r = client_gate_off.get(f"/api/chat/{sid}/state")
    assert r.status_code == 200
    assert r.json().get("has_history") is False


# ---------------------------------------------------------------------------
# Gate ON: full round-trip
# ---------------------------------------------------------------------------


@patch("src.agent.nodes.review.get_llm_client")
@patch("src.memory_client.fetch_mastery")
def test_full_review_round_trip(
    mock_fetch, mock_llm, client_gate_on, session_id_gate_on
):
    from src.llm.schemas import QuizQuestion

    # Stub: 2 stale subtopics
    mock_fetch.return_value = [
        {
            "subtopic": "A",
            "topic": "T",
            "last_seen_at": "2026-04-01T00:00:00Z",
            "session_count": 1,
            "total_questions": 4,
            "total_correct": 2,
            "last_accuracy": 0.5,
        },
        {
            "subtopic": "B",
            "topic": "T",
            "last_seen_at": "2026-04-01T00:00:00Z",
            "session_count": 1,
            "total_questions": 4,
            "total_correct": 3,
            "last_accuracy": 0.75,
        },
    ]
    # Stub LLM: 2 questions
    mock_llm.return_value.chat.completions.create.return_value = [
        QuizQuestion(
            id="rq_0",
            knowledge_point="A",
            question="qa",
            options=["a", "b", "c", "d"],
            answer=0,
        ),
        QuizQuestion(
            id="rq_1",
            knowledge_point="B",
            question="qb",
            options=["a", "b", "c", "d"],
            answer=1,
        ),
    ]

    # Start review
    r1 = client_gate_on.post(f"/api/chat/{session_id_gate_on}/start-review")
    assert r1.status_code == 200, r1.text
    qs = r1.json()["questions"]
    assert len(qs) == 2

    # Submit answers: A correct (0), B wrong (0 instead of 1)
    r2 = client_gate_on.post(
        f"/api/chat/{session_id_gate_on}/submit-review",
        json={"answers": [0, 0]},
    )
    assert r2.status_code == 200, r2.text
    rr = r2.json()["review_result"]
    assert rr["weak_kps"] == ["B"]
    assert rr["strong_kps"] == ["A"]
    assert "B" in rr["focus_directive"]


# ---------------------------------------------------------------------------
# Cross-topic: each returned question must carry its source topic
# ---------------------------------------------------------------------------


@patch("src.agent.nodes.review.get_llm_client")
@patch("src.memory_client.fetch_mastery")
def test_start_review_attaches_source_topic_to_questions(
    mock_fetch, mock_llm, client_gate_on, session_id_gate_on
):
    """Cross-topic review must label each question with the topic it came from
    so the UI can show 'from <topic>' instead of an orphan KP name."""
    from src.llm.schemas import QuizQuestion

    mock_fetch.return_value = [
        {
            "subtopic": "factoring", "topic": "multiplication theorem",
            "last_seen_at": "2026-04-01T00:00:00Z",
            "session_count": 1, "total_questions": 4,
            "total_correct": 2, "last_accuracy": 0.5,
        },
        {
            "subtopic": "hypotenuse", "topic": "Pythagorean theorem",
            "last_seen_at": "2026-04-01T00:00:00Z",
            "session_count": 1, "total_questions": 4,
            "total_correct": 3, "last_accuracy": 0.75,
        },
    ]
    mock_llm.return_value.chat.completions.create.return_value = [
        QuizQuestion(id="rq_0", knowledge_point="factoring",
                     question="q1", options=["a", "b", "c", "d"], answer=0),
        QuizQuestion(id="rq_1", knowledge_point="hypotenuse",
                     question="q2", options=["a", "b", "c", "d"], answer=1),
    ]

    r = client_gate_on.post(f"/api/chat/{session_id_gate_on}/start-review")
    assert r.status_code == 200, r.text
    qs = r.json()["questions"]
    by_kp = {q["knowledge_point"]: q for q in qs}
    assert by_kp["factoring"]["topic"] == "multiplication theorem"
    assert by_kp["hypotenuse"]["topic"] == "Pythagorean theorem"


# ---------------------------------------------------------------------------
# Gate ON, no history → 404
# ---------------------------------------------------------------------------


@patch("src.memory_client.fetch_mastery")
def test_no_history_returns_404(mock_fetch, client_gate_on, session_id_gate_on):
    mock_fetch.return_value = []
    r = client_gate_on.post(f"/api/chat/{session_id_gate_on}/start-review")
    assert r.status_code == 404
    assert r.json()["detail"] == "No history to review"


# ---------------------------------------------------------------------------
# Input validation: answer index range
# ---------------------------------------------------------------------------


def test_submit_review_rejects_out_of_range_answer(
    client_gate_on, session_id_gate_on
):
    """answer index outside [0,3] returns 422 from Pydantic."""
    # Pre-populate state with valid review_questions to bypass the empty check
    from src.llm.schemas import QuizQuestion
    state = chat_module._sessions[session_id_gate_on]
    state["review_questions"] = [
        QuizQuestion(id="rq_0", knowledge_point="A",
                     question="q", options=["a", "b", "c", "d"], answer=0),
    ]
    chat_module._save(session_id_gate_on, state)

    r = client_gate_on.post(
        f"/api/chat/{session_id_gate_on}/submit-review",
        json={"answers": [99]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Variety: snapshot refresh + recent-question feedback
# ---------------------------------------------------------------------------


@patch("src.agent.nodes.review.get_llm_client")
@patch("src.memory_client.fetch_mastery")
def test_start_review_refetches_mastery_even_when_state_has_snapshot(
    mock_fetch, mock_llm, client_gate_on, session_id_gate_on
):
    """Stale state['mastery_snapshot'] must NOT short-circuit fetch_mastery.

    Reason: after submit-review the server-side mastery rows update, but the
    session-cached snapshot stays put — re-using it pins the candidate KP
    list across repeated reviews, which pins the LLM prompt, which pins the
    questions. The fix is to always re-fetch on start-review entry.
    """
    from src.llm.schemas import QuizQuestion

    # Plant a stale snapshot directly in state.
    state = chat_module._sessions[session_id_gate_on]
    state["mastery_snapshot"] = [{
        "subtopic": "STALE", "topic": "T",
        "last_seen_at": "2026-01-01T00:00:00Z",
        "session_count": 1, "total_questions": 1, "total_correct": 0,
        "last_accuracy": 0.0,
    }]
    chat_module._save(session_id_gate_on, state)

    mock_fetch.return_value = [{
        "subtopic": "FRESH", "topic": "T",
        "last_seen_at": "2026-04-01T00:00:00Z",
        "session_count": 1, "total_questions": 4, "total_correct": 2,
        "last_accuracy": 0.5,
    }]
    mock_llm.return_value.chat.completions.create.return_value = [
        QuizQuestion(id="rq_0", knowledge_point="FRESH",
                     question="q", options=["a", "b", "c", "d"], answer=0),
    ]

    r = client_gate_on.post(f"/api/chat/{session_id_gate_on}/start-review")
    assert r.status_code == 200, r.text
    assert mock_fetch.called, "fetch_mastery must be invoked even with a cached snapshot"
    fresh = chat_module._sessions[session_id_gate_on]["mastery_snapshot"]
    assert any(row["subtopic"] == "FRESH" for row in fresh)
    assert not any(row["subtopic"] == "STALE" for row in fresh)


@patch("src.agent.nodes.review.get_llm_client")
@patch("src.memory_client.fetch_mastery")
def test_repeated_start_review_feeds_prior_question_into_prompt(
    mock_fetch, mock_llm, client_gate_on, session_id_gate_on
):
    """Two start-review calls in a row → second call's prompt MUST mention
    the first call's question stem, so the LLM is asked to change angle
    without us touching `temperature` (which would risk factual hallucination)."""
    from src.llm.schemas import QuizQuestion

    mock_fetch.return_value = [{
        "subtopic": "A", "topic": "T",
        "last_seen_at": "2026-04-01T00:00:00Z",
        "session_count": 1, "total_questions": 4, "total_correct": 2,
        "last_accuracy": 0.5,
    }]
    mock_llm.return_value.chat.completions.create.side_effect = [
        [QuizQuestion(id="rq_0", knowledge_point="A",
                      question="Q-FIRST-uniq", options=["a", "b", "c", "d"], answer=0)],
        [QuizQuestion(id="rq_0", knowledge_point="A",
                      question="Q-SECOND", options=["a", "b", "c", "d"], answer=0)],
    ]

    r1 = client_gate_on.post(f"/api/chat/{session_id_gate_on}/start-review")
    assert r1.status_code == 200, r1.text
    r2 = client_gate_on.post(f"/api/chat/{session_id_gate_on}/start-review")
    assert r2.status_code == 200, r2.text

    second_prompt = (
        mock_llm.return_value.chat.completions.create
        .call_args_list[1].kwargs["messages"][0]["content"]
    )
    assert "Q-FIRST-uniq" in second_prompt, (
        "Second start-review's prompt should reference the first call's "
        "question so the LLM is directed to switch angle"
    )


@patch("src.agent.nodes.review.get_llm_client")
@patch("src.memory_client.fetch_mastery")
def test_review_question_history_capped_per_kp(
    mock_fetch, mock_llm, client_gate_on, session_id_gate_on
):
    """state['review_question_history'][kp] must not grow unboundedly.
    Cap = 3 (keep last 3 stems per KP)."""
    from src.llm.schemas import QuizQuestion

    mock_fetch.return_value = [{
        "subtopic": "A", "topic": "T",
        "last_seen_at": "2026-04-01T00:00:00Z",
        "session_count": 1, "total_questions": 4, "total_correct": 2,
        "last_accuracy": 0.5,
    }]
    mock_llm.return_value.chat.completions.create.side_effect = [
        [QuizQuestion(id="rq_0", knowledge_point="A",
                      question=f"Qv{i}", options=["a", "b", "c", "d"], answer=0)]
        for i in range(5)
    ]

    for _ in range(5):
        r = client_gate_on.post(f"/api/chat/{session_id_gate_on}/start-review")
        assert r.status_code == 200

    hist = chat_module._sessions[session_id_gate_on]["review_question_history"]["A"]
    assert len(hist) == 3
    # Oldest two ("Qv0", "Qv1") evicted; last three retained
    assert hist == ["Qv2", "Qv3", "Qv4"]
