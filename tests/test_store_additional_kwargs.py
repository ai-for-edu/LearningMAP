"""Round-trip test: store preserves AIMessage.additional_kwargs."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from src.api.store import _serialize_state, _deserialize_state


class TestStoreAdditionalKwargs:
    def test_ai_message_cta_kwargs_round_trip(self) -> None:
        state = {
            "messages": [
                HumanMessage(content="什么是微积分"),
                AIMessage(
                    content="微积分是研究变化率的数学分支……",
                    additional_kwargs={"cta": {"type": "learn_topic", "topic": "微积分"}},
                ),
            ],
        }
        raw = _serialize_state(state)
        restored = _deserialize_state(raw)

        msgs = restored["messages"]
        assert len(msgs) == 2
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].additional_kwargs == {
            "cta": {"type": "learn_topic", "topic": "微积分"}
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
                HumanMessage(content="什么是积分"),
                AIMessage(
                    content="积分是研究累积量的数学分支……",
                    additional_kwargs={"cta": {"type": "learn_topic", "topic": "积分"}},
                ),
            ],
        }
        restored = _deserialize_state(_serialize_state(state))
        assert len(restored["chat_messages"]) == 2
        assert isinstance(restored["chat_messages"][1], AIMessage)
        assert restored["chat_messages"][1].additional_kwargs == {
            "cta": {"type": "learn_topic", "topic": "积分"}
        }

    def test_both_lists_round_trip_independently(self) -> None:
        state = {
            "messages": [HumanMessage(content="学习 积分"), AIMessage(content="知识图谱已生成")],
            "chat_messages": [HumanMessage(content="今天天气怎么样")],
        }
        restored = _deserialize_state(_serialize_state(state))
        assert len(restored["messages"]) == 2
        assert len(restored["chat_messages"]) == 1
        assert restored["messages"][0].content == "学习 积分"
        assert restored["chat_messages"][0].content == "今天天气怎么样"

    def test_chat_messages_absent_gracefully_omitted(self) -> None:
        # Older sessions or states that never used chat track shouldn't crash.
        state = {"messages": [HumanMessage(content="hi")]}
        restored = _deserialize_state(_serialize_state(state))
        assert "chat_messages" not in restored or restored["chat_messages"] == []
