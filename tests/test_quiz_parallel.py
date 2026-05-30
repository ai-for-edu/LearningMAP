"""Tests for the parallel quiz fan-out helper."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _resolve_quiz_fanout_limit
# ---------------------------------------------------------------------------

def test_resolve_quiz_fanout_limit_explicit_arg_wins(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "7")
    assert _resolve_quiz_fanout_limit(2) == 2


def test_resolve_quiz_fanout_limit_env_var(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "6")
    assert _resolve_quiz_fanout_limit(None) == 6


def test_resolve_quiz_fanout_limit_default(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.delenv("QUIZ_FANOUT_LIMIT", raising=False)
    assert _resolve_quiz_fanout_limit(None) == 4


def test_resolve_quiz_fanout_limit_bad_env_falls_back(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "not-a-number")
    assert _resolve_quiz_fanout_limit(None) == 4


def test_resolve_quiz_fanout_limit_clamps_zero(monkeypatch):
    """Zero must clamp to 1 — otherwise asyncio.Semaphore(0) would deadlock."""
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.delenv("QUIZ_FANOUT_LIMIT", raising=False)
    assert _resolve_quiz_fanout_limit(0) == 1


def test_resolve_quiz_fanout_limit_clamps_env_zero(monkeypatch):
    """Env QUIZ_FANOUT_LIMIT=0 must also clamp to 1."""
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "0")
    assert _resolve_quiz_fanout_limit(None) == 1


def test_resolve_quiz_fanout_limit_clamps_negative(monkeypatch):
    """Negative values must also clamp to 1."""
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.delenv("QUIZ_FANOUT_LIMIT", raising=False)
    assert _resolve_quiz_fanout_limit(-5) == 1


# ---------------------------------------------------------------------------
# generate_quiz_per_point_async — empty / degenerate inputs
# ---------------------------------------------------------------------------

def test_async_returns_empty_when_no_graph():
    """state has no knowledge_graph at all."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    result = asyncio.run(generate_quiz_per_point_async({}))
    assert result == []


def test_async_returns_empty_when_graph_is_none():
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    result = asyncio.run(generate_quiz_per_point_async({"knowledge_graph": None}))
    assert result == []


def test_async_returns_empty_when_nodes_list_empty():
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    graph = MagicMock()
    graph.nodes = []
    result = asyncio.run(generate_quiz_per_point_async({"knowledge_graph": graph}))
    assert result == []


# ---------------------------------------------------------------------------
# generate_quiz_per_point_async — full behavior
# ---------------------------------------------------------------------------

def _make_kg_state(num_nodes: int) -> dict:
    """Build a state dict whose knowledge_graph has N named KCs.

    NOTE: KnowledgeNode requires id + level (see src/llm/schemas.py:20-25).
    """
    from src.llm.schemas import KnowledgeNode
    nodes = [
        KnowledgeNode(
            id=f"kc-{i}",
            name=f"kc-{i}",
            description=f"desc-{i}",
            level="core",
        )
        for i in range(num_nodes)
    ]
    graph = MagicMock()
    graph.nodes = nodes
    return {"knowledge_graph": graph, "kc_skills": {}}


def _make_mock_question(kc_name: str, text: str):
    """Build a mock QuizQuestion-like object the async core can re-tag.

    NOTE: QuizQuestion has fields {id, knowledge_point, question, options,
    answer: int}. There is no `correct_answer` or `explanation` field on
    the current schema (see src/llm/schemas.py:40-45).
    """
    from src.llm.schemas import QuizQuestion
    return QuizQuestion(
        id=f"q-{text}",
        knowledge_point=kc_name,
        question=text,
        options=["A", "B", "C", "D"],
        answer=0,
    )


def _patch_quiz_client(side_effect):
    """Install a mock instructor client at src.agent.nodes.quiz.get_llm_client.

    side_effect is the function called for each .chat.completions.create(...).
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = side_effect
    return patch("src.agent.nodes.quiz.get_llm_client", return_value=mock_client), mock_client


def test_async_preserves_kc_order_when_completions_finish_out_of_order():
    """KC-0 sleeps longest, KC-2 finishes first; final list is still KC-0, KC-1, KC-2."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    import time

    delays = [0.05, 0.02, 0.0]
    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        # quiz core re-pins knowledge_point to the KG node name, so the
        # name we put here is irrelevant — text disambiguates per call.
        return [_make_mock_question("paraphrased", f"Q-from-call-{i}")]

    state = _make_kg_state(3)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        result = asyncio.run(generate_quiz_per_point_async(state))

    # Order of LLM calls matches enumeration order over nodes (call 0 → KC-0,
    # call 1 → KC-1, call 2 → KC-2) because each coroutine builds its prompt
    # before awaiting. Completion order is the reverse of declaration. Final
    # list must still be KC-0 question, KC-1 question, KC-2 question.
    assert [q.knowledge_point for q in result] == ["kc-0", "kc-1", "kc-2"]
    assert [q.question for q in result] == [
        "Q-from-call-0",
        "Q-from-call-1",
        "Q-from-call-2",
    ]


def test_async_caps_concurrency_at_limit():
    """limit=2 and 6 KCs → peak concurrent in-flight calls must be <= 2."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    import threading
    import time

    in_flight = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def _create(**kwargs):
        with lock:
            in_flight["now"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        time.sleep(0.02)
        with lock:
            in_flight["now"] -= 1
        return [_make_mock_question("x", "q")]

    state = _make_kg_state(6)
    ctx, mock_client = _patch_quiz_client(_create)
    with ctx:
        asyncio.run(generate_quiz_per_point_async(state, limit=2))

    assert in_flight["peak"] <= 2, f"peak {in_flight['peak']} exceeded limit 2"
    assert mock_client.chat.completions.create.call_count == 6


def test_async_on_point_done_monotonic_done_count():
    """on_point_done is called N times with done_count = 1..N (any kc_idx order)."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    import time

    N = 5
    delays = [0.01 * (N - i) for i in range(N)]  # KC-0 slowest
    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        return [_make_mock_question("x", f"q-{i}")]

    callbacks = []

    def _on_done(done_count, kc_index, batch):
        callbacks.append((done_count, kc_index, len(batch)))

    state = _make_kg_state(N)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        asyncio.run(
            generate_quiz_per_point_async(state, on_point_done=_on_done)
        )

    done_counts = [c[0] for c in callbacks]
    kc_indices = [c[1] for c in callbacks]
    batch_sizes = [c[2] for c in callbacks]
    assert done_counts == [1, 2, 3, 4, 5]
    assert sorted(kc_indices) == [0, 1, 2, 3, 4]
    assert all(s == 1 for s in batch_sizes)


def test_async_best_effort_on_single_kc_failure():
    """KC-1 raises; KC-0, KC-2, KC-3 succeed; function returns N-1 questions, no raise."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async

    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        if i == 1:
            raise RuntimeError("synthetic LLM failure")
        return [_make_mock_question("x", f"q-{i}")]

    callbacks = []

    def _on_done(done_count, kc_index, batch):
        callbacks.append((done_count, kc_index, len(batch)))

    state = _make_kg_state(4)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        result = asyncio.run(
            generate_quiz_per_point_async(state, on_point_done=_on_done)
        )

    # Function does NOT raise. Returns questions for KC-0, KC-2, KC-3.
    assert [q.knowledge_point for q in result] == ["kc-0", "kc-2", "kc-3"]
    # Callback fires N times including the failed KC (with batch=[]).
    assert len(callbacks) == 4
    done_counts = [c[0] for c in callbacks]
    assert done_counts == [1, 2, 3, 4]
    # Exactly one callback has an empty batch — the failed KC.
    failed = [c for c in callbacks if c[2] == 0]
    assert len(failed) == 1
    assert failed[0][1] == 1  # kc_index of the failure
