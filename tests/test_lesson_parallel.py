"""Tests for the parallel lesson section fan-out helper."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _resolve_fanout_limit
# ---------------------------------------------------------------------------

def test_resolve_fanout_limit_explicit_arg_wins(monkeypatch):
    from src.agent.nodes.lesson import _resolve_fanout_limit
    monkeypatch.setenv("LESSON_FANOUT_LIMIT", "7")
    assert _resolve_fanout_limit(2) == 2


def test_resolve_fanout_limit_env_var(monkeypatch):
    from src.agent.nodes.lesson import _resolve_fanout_limit
    monkeypatch.setenv("LESSON_FANOUT_LIMIT", "6")
    assert _resolve_fanout_limit(None) == 6


def test_resolve_fanout_limit_default(monkeypatch):
    from src.agent.nodes.lesson import _resolve_fanout_limit
    monkeypatch.delenv("LESSON_FANOUT_LIMIT", raising=False)
    assert _resolve_fanout_limit(None) == 4


def test_resolve_fanout_limit_bad_env_falls_back(monkeypatch):
    from src.agent.nodes.lesson import _resolve_fanout_limit
    monkeypatch.setenv("LESSON_FANOUT_LIMIT", "not-a-number")
    assert _resolve_fanout_limit(None) == 4


# ---------------------------------------------------------------------------
# generate_lesson_per_point_async — ordering
# ---------------------------------------------------------------------------

def _make_async_mock_client(per_call_outputs, per_call_delays=None):
    """Build a mock OpenAI client whose .chat.completions.create returns sequential
    outputs. If per_call_delays is provided, each call sleeps that many seconds
    BEFORE returning (simulates per-section LLM latency)."""
    import time

    outputs = list(per_call_outputs)
    delays = list(per_call_delays) if per_call_delays else [0.0] * len(outputs)
    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        if delays[i] > 0:
            time.sleep(delays[i])  # blocks inside asyncio.to_thread worker; fine
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = outputs[i]
        return resp

    client = MagicMock()
    client.chat.completions.create.side_effect = _create
    return client


def test_async_fanout_preserves_kc_order_when_completions_finish_out_of_order():
    """First KC sleeps longest; final markdown must still be section-0 then section-1
    then section-2 in original weak_points order."""
    from src.agent.nodes.lesson import generate_lesson_per_point_async

    weak_points = [
        {"name": "kc-0", "description": "d0"},
        {"name": "kc-1", "description": "d1"},
        {"name": "kc-2", "description": "d2"},
    ]
    # KC 0 takes longest, KC 2 finishes first.
    mock_client = _make_async_mock_client(
        per_call_outputs=["SECTION-0", "SECTION-1", "SECTION-2"],
        per_call_delays=[0.05, 0.02, 0.0],
    )

    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        result = asyncio.run(
            generate_lesson_per_point_async(
                topic="t",
                weak_points=weak_points,
                pedagogy_instructions="",
                knowledge_skills={"kc-0": "s0", "kc-1": "s1", "kc-2": "s2"},
            )
        )

    # Expect original KC order regardless of completion order.
    assert result == "SECTION-0\n\n---\n\nSECTION-1\n\n---\n\nSECTION-2"


# ---------------------------------------------------------------------------
# Semaphore-bounded concurrency
# ---------------------------------------------------------------------------

def test_async_fanout_caps_concurrency_at_limit():
    """With limit=2 and 6 weak_points, peak concurrent in-flight calls must be <= 2."""
    from src.agent.nodes.lesson import generate_lesson_per_point_async
    import time

    in_flight = {"now": 0, "peak": 0}
    lock = __import__("threading").Lock()

    def _create(**kwargs):
        with lock:
            in_flight["now"] += 1
            if in_flight["now"] > in_flight["peak"]:
                in_flight["peak"] = in_flight["now"]
        time.sleep(0.02)  # hold the slot long enough to observe overlap
        with lock:
            in_flight["now"] -= 1
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "ok"
        return resp

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = _create

    weak_points = [{"name": f"kc-{i}", "description": f"d{i}"} for i in range(6)]

    with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
        asyncio.run(
            generate_lesson_per_point_async(
                topic="t",
                weak_points=weak_points,
                pedagogy_instructions="",
                knowledge_skills={f"kc-{i}": f"s{i}" for i in range(6)},
                limit=2,
            )
        )

    assert in_flight["peak"] <= 2, f"peak in-flight {in_flight['peak']} exceeded limit 2"
    assert mock_client.chat.completions.create.call_count == 6
