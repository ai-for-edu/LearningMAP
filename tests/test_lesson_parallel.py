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
