"""Tests for session_mode field and intent_node mode-gating."""
from __future__ import annotations

from src.agent.state import LearningState


class TestSessionModeField:
    def test_session_mode_field_declared_on_state(self) -> None:
        # LearningState is a TypedDict; presence of the key in __annotations__ is enough.
        assert "session_mode" in LearningState.__annotations__
