"""Round-trip test: store preserves AIMessage.additional_kwargs."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from src.api.store import _serialize_state, _deserialize_state


class TestStoreAdditionalKwargs:
    def test_ai_message_cta_kwargs_round_trip(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="What is calculus"),
                AIMessage(
                    content="Calculus is the mathematical study of change and rates...",
                    additional_kwargs={"cta": {"type": "learn_topic", "topic": "calculus"}},
                ),
            ],
        }
        raw = _serialize_state(state)
        restored = _deserialize_state(raw)

        msgs = restored["messages"]
        assert len(msgs) == 2
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].additional_kwargs == {
            "cta": {"type": "learn_topic", "topic": "calculus"}
        }

    def test_message_without_kwargs_round_trips_to_empty_dict(self) -> None:
        state = {"messages": [HumanMessage(content="hi"), AIMessage(content="hello")]}
        restored = _deserialize_state(_serialize_state(state))
        assert restored["messages"][0].additional_kwargs == {}
        assert restored["messages"][1].additional_kwargs == {}


class TestChatMessagesRoundTrip:
    def test_chat_messages_round_trip_preserves_cta(self) -> None:
        state = {
            "chat_messages": [
                HumanMessage(content="What is integral"),
                AIMessage(
                    content="Integral calculus is the study of accumulated quantities...",
                    additional_kwargs={"cta": {"type": "learn_topic", "topic": "integral"}},
                ),
            ],
        }
        restored = _deserialize_state(_serialize_state(state))
        assert len(restored["chat_messages"]) == 2
        assert isinstance(restored["chat_messages"][1], AIMessage)
        assert restored["chat_messages"][1].additional_kwargs == {
            "cta": {"type": "learn_topic", "topic": "integral"}
        }

    def test_both_lists_round_trip_independently(self) -> None:
        state = {
            "messages": [HumanMessage(content="Learn integral"), AIMessage(content="Knowledge graph generated")],
            "chat_messages": [HumanMessage(content="How is the weather today")],
        }
        restored = _deserialize_state(_serialize_state(state))
        assert len(restored["messages"]) == 2
        assert len(restored["chat_messages"]) == 1
        assert restored["messages"][0].content == "Learn integral"
        assert restored["chat_messages"][0].content == "How is the weather today"

    def test_chat_messages_absent_gracefully_omitted(self) -> None:
        # Older sessions or states that never used chat track shouldn't crash.
        state = {"messages": [HumanMessage(content="hi")]}
        restored = _deserialize_state(_serialize_state(state))
        assert "chat_messages" not in restored or restored["chat_messages"] == []
