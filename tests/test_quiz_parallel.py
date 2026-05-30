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
