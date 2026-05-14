"""Tests for intent_node mode-gating + chat_node CTA attachment."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langchain_core.messages import HumanMessage

from src.agent.nodes.intent import intent_node, IntentResult


def _state(session_mode: str, user_msg: str = "什么是微积分", **extras) -> dict:
    return {
        "session_mode": session_mode,
        "messages": [HumanMessage(content=user_msg)],
        "active_skill": None,
        "active_skill_content": None,
        **extras,
    }


def _mock_llm_returning(intent_result: IntentResult):
    """Build a context manager that mocks intent_node's LLM call."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = intent_result
    return patch("src.agent.nodes.intent.get_llm_client", return_value=fake_client)


class TestIntentNodeChatModeGating:
    def test_chat_mode_with_learn_topic_intent_stashes_cta(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="微积分", skill=None)
        ):
            result = intent_node(_state("chat"))
        assert result["mode"] == "chat"
        assert result["_pending_cta_topic"] == "微积分"
        assert "topic" not in result  # do NOT set top-level topic in chat mode

    def test_chat_mode_with_plain_chat_intent_no_cta(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="chat", topic=None, skill=None)
        ):
            result = intent_node(_state("chat", user_msg="今天天气怎么样"))
        assert result["mode"] == "chat"
        assert "_pending_cta_topic" not in result

    def test_chat_mode_with_learn_topic_no_topic_no_cta(self) -> None:
        # Defensive: if classifier returned learn_topic without a topic, don't stash empty.
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic=None, skill=None)
        ):
            result = intent_node(_state("chat"))
        assert result["mode"] == "chat"
        assert "_pending_cta_topic" not in result


class TestIntentNodeLearningModeRouting:
    def test_learning_mode_with_learn_topic_routes_to_graph(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="微积分", skill=None)
        ):
            result = intent_node(_state("learning"))
        assert result["mode"] == "graph"
        assert result["topic"] == "微积分"

    def test_learning_mode_with_start_diagnosis_routes_to_quiz(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="start_diagnosis", topic=None, skill=None)
        ):
            result = intent_node(_state("learning", user_msg="开始测验"))
        assert result["mode"] == "quiz"

    def test_learning_mode_with_chat_intent_routes_to_chat(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="chat", topic=None, skill=None)
        ):
            result = intent_node(_state("learning", user_msg="hi"))
        assert result["mode"] == "chat"


class TestIntentNodeGateRemoved:
    def test_learning_mode_no_skill_no_longer_gates(self) -> None:
        """Pre-change behavior: emitted GATE message + mode=chat. New: routes to graph."""
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="微积分", skill=None)
        ):
            result = intent_node(_state("learning", active_skill=None))
        assert result["mode"] == "graph"
        assert result["topic"] == "微积分"
        # No injected AI message that says "请先选择一种教学法"
        assert "messages" not in result


class TestIntentNodeFallback:
    def test_missing_session_mode_defaults_to_chat(self) -> None:
        # Defensive: state without session_mode field should not blow up.
        state = {"messages": [HumanMessage(content="什么是导数")]}
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="导数", skill=None)
        ):
            result = intent_node(state)
        assert result["mode"] == "chat"
        assert result["_pending_cta_topic"] == "导数"
