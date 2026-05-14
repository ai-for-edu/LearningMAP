"""Tests for intent_node mode-gating + chat_node CTA attachment."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    """Build a context manager that mocks intent_node's LLM call.

    instructor.Instructor.chat.completions.create() returns the Pydantic
    model directly (not a ChatCompletion wrapper), so the mock returns
    intent_result directly, not wrapped in choices[0].
    """
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
        assert result["pending_cta_topic"] == "微积分"
        assert "topic" not in result  # do NOT set top-level topic in chat mode

    def test_chat_mode_with_plain_chat_intent_no_cta(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="chat", topic=None, skill=None)
        ):
            result = intent_node(_state("chat", user_msg="今天天气怎么样"))
        assert result["mode"] == "chat"
        assert "pending_cta_topic" not in result

    def test_chat_mode_with_learn_topic_no_topic_no_cta(self) -> None:
        # Defensive: if classifier returned learn_topic without a topic, don't stash empty.
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic=None, skill=None)
        ):
            result = intent_node(_state("chat"))
        assert result["mode"] == "chat"
        assert "pending_cta_topic" not in result


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


from unittest.mock import MagicMock, patch as _patch

from langchain_core.messages import AIMessage as _AIMessage

from src.agent.nodes.chat import chat_node


def _mock_chat_llm(reply: str):
    """Mock the raw OpenAI client used by chat_node so we don't need a real LLM."""
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content=reply))]
    fake_client.chat.completions.create.return_value = fake_response
    return _patch("src.agent.nodes.chat.get_raw_client", return_value=fake_client)


class TestChatNodeCTA:
    def test_pending_cta_topic_attaches_additional_kwargs(self) -> None:
        state = {
            "messages": [HumanMessage(content="什么是微积分")],
            "pending_cta_topic": "微积分",
        }
        with _mock_chat_llm("微积分是研究变化率的数学分支……"), \
             _patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)

        msgs = result["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], _AIMessage)
        assert msgs[0].additional_kwargs == {
            "cta": {"type": "learn_topic", "topic": "微积分"}
        }

    def test_no_pending_cta_topic_means_empty_kwargs(self) -> None:
        state = {"messages": [HumanMessage(content="hi")]}
        with _mock_chat_llm("hello"), \
             _patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)
        assert result["messages"][0].additional_kwargs == {}

    def test_chat_node_clears_pending_cta_topic(self) -> None:
        state = {
            "messages": [HumanMessage(content="什么是微积分")],
            "pending_cta_topic": "微积分",
        }
        with _mock_chat_llm("ok"), \
             _patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)
        assert result["pending_cta_topic"] is None


class TestIntentNodeFallback:
    def test_missing_session_mode_defaults_to_chat(self) -> None:
        # Defensive: state without session_mode field should not blow up.
        state = {"messages": [HumanMessage(content="什么是导数")]}
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="导数", skill=None)
        ):
            result = intent_node(state)
        assert result["mode"] == "chat"
        assert result["pending_cta_topic"] == "导数"

    def test_llm_exception_falls_back_to_chat(self) -> None:
        with patch("src.agent.nodes.intent.get_llm_client") as mock_get:
            mock_get.return_value.chat.completions.create.side_effect = RuntimeError("timeout")
            result = intent_node(_state("learning"))
        assert result == {"mode": "chat"}

    def test_quiz_mode_short_circuits_without_llm_call(self) -> None:
        state = _state("learning")
        state["mode"] = "quiz"
        with patch("src.agent.nodes.intent.get_llm_client") as mock_get:
            result = intent_node(state)
            mock_get.assert_not_called()
        assert result == {"mode": "quiz"}
