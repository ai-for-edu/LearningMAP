"""Tests for intent_node mode-gating + chat_node CTA attachment."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage, AIMessage

from src.agent.nodes.intent import intent_node, IntentResult


def _state(session_mode: str, user_msg: str = "What is calculus", **extras) -> dict:
    """Build a minimal LearningState dict for intent_node tests.

    Routes user_msg to the active track: chat_messages for chat mode,
    messages for learning mode — matching intent_node's track selection.
    """
    msg = HumanMessage(content=user_msg)
    base = {
        "session_mode": session_mode,
        "messages": [] if session_mode == "chat" else [msg],
        "chat_messages": [msg] if session_mode == "chat" else [],
        "active_skill": None,
        "active_skill_content": None,
    }
    base.update(extras)
    return base


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
            IntentResult(intent="learn_topic", topic="calculus", skill=None)
        ):
            result = intent_node(_state("chat"))
        assert result["mode"] == "chat"
        assert result["pending_cta_topic"] == "calculus"
        assert "topic" not in result  # do NOT set top-level topic in chat mode

    def test_chat_mode_with_plain_chat_intent_no_cta(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="chat", topic=None, skill=None)
        ):
            result = intent_node(_state("chat", user_msg="How is the weather today"))
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
            IntentResult(intent="learn_topic", topic="calculus", skill=None)
        ):
            result = intent_node(_state("learning"))
        assert result["mode"] == "graph"
        assert result["topic"] == "calculus"

    def test_learning_mode_with_start_diagnosis_routes_to_quiz(self) -> None:
        with _mock_llm_returning(
            IntentResult(intent="start_diagnosis", topic=None, skill=None)
        ):
            result = intent_node(_state("learning", user_msg="Start quiz"))
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
            IntentResult(intent="learn_topic", topic="calculus", skill=None)
        ):
            result = intent_node(_state("learning", active_skill=None))
        assert result["mode"] == "graph"
        assert result["topic"] == "calculus"
        # No injected AI message that says "Please select a teaching method first"
        assert "messages" not in result


from src.agent.nodes.chat import chat_node


def _mock_chat_llm(reply: str):
    """Mock the raw OpenAI client used by chat_node so we don't need a real LLM."""
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content=reply))]
    fake_client.chat.completions.create.return_value = fake_response
    return patch("src.agent.nodes.chat.get_raw_client", return_value=fake_client)


class TestChatNodeCTA:
    def test_chat_mode_pending_cta_attaches_to_chat_messages(self) -> None:
        state = {
            "session_mode": "chat",
            "messages": [],
            "chat_messages": [HumanMessage(content="What is calculus")],
            "pending_cta_topic": "calculus",
        }
        with _mock_chat_llm("Calculus is the mathematical study of change and rates..."), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)

        assert "chat_messages" in result
        assert "messages" not in result  # learning track untouched
        assert len(result["chat_messages"]) == 1
        assert isinstance(result["chat_messages"][0], AIMessage)
        assert result["chat_messages"][0].additional_kwargs == {
            "cta": {"type": "learn_topic", "topic": "calculus"}
        }

    def test_chat_mode_no_pending_cta_means_empty_kwargs(self) -> None:
        state = {
            "session_mode": "chat",
            "messages": [],
            "chat_messages": [HumanMessage(content="hi")],
        }
        with _mock_chat_llm("hello"), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)
        assert result["chat_messages"][0].additional_kwargs == {}

    def test_chat_node_clears_pending_cta_topic(self) -> None:
        state = {
            "session_mode": "chat",
            "messages": [],
            "chat_messages": [HumanMessage(content="What is calculus")],
            "pending_cta_topic": "calculus",
        }
        with _mock_chat_llm("ok"), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)
        assert result["pending_cta_topic"] is None

    def test_empty_string_pending_cta_topic_means_empty_kwargs(self) -> None:
        state = {
            "session_mode": "chat",
            "messages": [],
            "chat_messages": [HumanMessage(content="hi")],
            "pending_cta_topic": "",
        }
        with _mock_chat_llm("hello"), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)
        assert result["chat_messages"][0].additional_kwargs == {}
        assert result["pending_cta_topic"] is None

    def test_learning_mode_returns_to_messages_not_chat_messages(self) -> None:
        state = {
            "session_mode": "learning",
            "messages": [HumanMessage(content="Tell me more about continuity")],
            "chat_messages": [],
        }
        with _mock_chat_llm("Continuity means..."), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            result = chat_node(state)

        assert "messages" in result
        assert "chat_messages" not in result  # chat track untouched
        assert isinstance(result["messages"][0], AIMessage)
        # CTA is never attached in learning mode (pending_cta_topic is None)
        assert result["messages"][0].additional_kwargs == {}

    def test_chat_node_reads_active_track_for_llm_context(self) -> None:
        # In chat mode, api_messages should be built from chat_messages,
        # NOT from the learning track's messages.
        captured_messages = []

        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="Weather is the state of the atmosphere"))]

        def capture_create(*, model, max_tokens, messages, **_):
            captured_messages.extend(messages)
            return fake_response

        fake_client.chat.completions.create.side_effect = capture_create

        state = {
            "session_mode": "chat",
            "messages": [HumanMessage(content="Learn integral"), AIMessage(content="Knowledge graph of integrals")],
            "chat_messages": [HumanMessage(content="What is weather")],
        }
        with patch("src.agent.nodes.chat.get_raw_client", return_value=fake_client), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            chat_node(state)

        # Find the user message in api_messages — should be from chat_messages,
        # not the learning track's "Learn integral".
        user_msgs = [m for m in captured_messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "What is weather"


class TestIntentNodeReadsActiveTrack:
    def test_chat_mode_reads_from_chat_messages(self) -> None:
        state = {
            "session_mode": "chat",
            "messages": [],  # learning track empty
            "chat_messages": [HumanMessage(content="What is integral")],
        }
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="integral", skill=None)
        ):
            result = intent_node(state)
        assert result["mode"] == "chat"
        assert result["pending_cta_topic"] == "integral"

    def test_learning_mode_reads_from_messages(self) -> None:
        state = {
            "session_mode": "learning",
            "messages": [HumanMessage(content="Learn integral")],
            "chat_messages": [],  # chat track empty
        }
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="integral", skill=None)
        ):
            result = intent_node(state)
        assert result["mode"] == "graph"
        assert result["topic"] == "integral"

    def test_chat_mode_with_only_learning_history_treats_chat_track_empty(self) -> None:
        # User has a learning history but switches to chat with no chat messages yet —
        # intent_node should return early (no message in active track).
        state = {
            "session_mode": "chat",
            "messages": [HumanMessage(content="Learn integral"), AIMessage(content="Knowledge graph")],
            "chat_messages": [],
        }
        result = intent_node(state)
        assert result == {"mode": "chat"}


class TestChatNodeContextIsolation:
    def test_chat_mode_does_not_inject_learning_artifacts(self) -> None:
        """Chat mode LLM call must not see learning topic / graph / diagnosis."""
        captured_system = []
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="ok"))]

        def capture_create(*, model, max_tokens, messages, **_):
            for m in messages:
                if m["role"] == "system":
                    captured_system.append(m["content"])
            return fake_response

        fake_client.chat.completions.create.side_effect = capture_create

        from src.llm.schemas import KnowledgeGraph
        state = {
            "session_mode": "chat",
            "messages": [],
            "chat_messages": [HumanMessage(content="How is the weather today")],
            "topic": "calculus",
            "knowledge_graph": None,  # full KG omitted for test simplicity
        }
        with patch("src.agent.nodes.chat.get_raw_client", return_value=fake_client), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            chat_node(state)

        assert len(captured_system) == 1
        sys_prompt = captured_system[0]
        # Learning artifacts must NOT leak into chat-mode system prompt
        assert "calculus" not in sys_prompt
        assert "Learning topic" not in sys_prompt

    def test_learning_mode_still_injects_learning_artifacts(self) -> None:
        """Learning mode keeps the existing behavior — context is included."""
        captured_system = []
        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="ok"))]

        def capture_create(*, model, max_tokens, messages, **_):
            for m in messages:
                if m["role"] == "system":
                    captured_system.append(m["content"])
            return fake_response

        fake_client.chat.completions.create.side_effect = capture_create

        state = {
            "session_mode": "learning",
            "messages": [HumanMessage(content="Tell me more about continuity")],
            "chat_messages": [],
            "topic": "calculus",
        }
        with patch("src.agent.nodes.chat.get_raw_client", return_value=fake_client), \
             patch("src.agent.nodes.chat.retrieve_memory", return_value=""):
            chat_node(state)

        sys_prompt = captured_system[0]
        assert "calculus" in sys_prompt
        assert "Learning topic" in sys_prompt


class TestIntentNodeFallback:
    def test_missing_session_mode_defaults_to_chat(self) -> None:
        # Defensive: state without session_mode field should not blow up.
        # Default mode is "chat", so message must be in chat_messages track.
        state = {"chat_messages": [HumanMessage(content="What is a derivative")]}
        with _mock_llm_returning(
            IntentResult(intent="learn_topic", topic="derivative", skill=None)
        ):
            result = intent_node(state)
        assert result["mode"] == "chat"
        assert result["pending_cta_topic"] == "derivative"

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
