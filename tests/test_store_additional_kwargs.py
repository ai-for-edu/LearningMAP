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
