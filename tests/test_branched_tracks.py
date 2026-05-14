"""End-to-end tests for branched chat/learning tracks via the HTTP API."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from run.serve import app


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "test.db"))
    from src.api import store as store_module
    importlib.reload(store_module)
    from src.api import chat as chat_api
    chat_api._sessions.clear()
    yield
    chat_api._sessions.clear()


@pytest.mark.asyncio
class TestUserMessageDispatch:
    async def test_chat_mode_user_message_lands_in_chat_messages(
        self, isolated_db, monkeypatch
    ) -> None:
        # Stub the graph so we don't need an LLM. Echo state unchanged plus
        # a chat-mode AI reply (which the chat_node would normally write).
        from src.api import chat as chat_api
        from langchain_core.messages import AIMessage

        def fake_invoke(state):
            # Mimic chat_node returning to chat_messages
            state["chat_messages"] = list(state.get("chat_messages", []))
            state["chat_messages"].append(AIMessage(content="hi back"))
            state["mode"] = "chat"
            return state

        fake_graph = type("G", (), {"invoke": staticmethod(fake_invoke)})()
        monkeypatch.setattr(chat_api, "_get_graph", lambda: fake_graph)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat", json={"message": "hi", "mode": "chat"}
            )
            assert resp.status_code == 200
            sess_id = resp.json()["session_id"]

            state_resp = await client.get(f"/api/chat/{sess_id}/state")
            data = state_resp.json()
            assert data["session_mode"] == "chat"
            # Chat track has both user message and AI reply
            assert len(data.get("chat_messages", [])) == 2
            assert data["chat_messages"][0]["content"] == "hi"
            # Learning track unaffected
            assert data.get("messages") in (None, [])

    async def test_learning_mode_user_message_lands_in_messages(
        self, isolated_db, monkeypatch
    ) -> None:
        from src.api import chat as chat_api
        from langchain_core.messages import AIMessage

        def fake_invoke(state):
            state["messages"] = list(state.get("messages", []))
            state["messages"].append(AIMessage(content="ok"))
            state["mode"] = "chat"
            return state

        fake_graph = type("G", (), {"invoke": staticmethod(fake_invoke)})()
        monkeypatch.setattr(chat_api, "_get_graph", lambda: fake_graph)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat", json={"message": "学习 积分", "mode": "learning"}
            )
            sess_id = resp.json()["session_id"]

            state_resp = await client.get(f"/api/chat/{sess_id}/state")
            data = state_resp.json()
            assert data["session_mode"] == "learning"
            assert len(data.get("messages", [])) == 2
            assert data["messages"][0]["content"] == "学习 积分"
            assert data.get("chat_messages") in (None, [])

    async def test_toggle_dispatch_keeps_prior_track_intact(
        self, isolated_db, monkeypatch
    ) -> None:
        from src.api import chat as chat_api
        from langchain_core.messages import AIMessage

        def fake_invoke(state):
            mode = state.get("session_mode", "chat")
            key = "chat_messages" if mode == "chat" else "messages"
            state[key] = list(state.get(key, []))
            state[key].append(AIMessage(content="ai reply"))
            state["mode"] = "chat"
            return state

        fake_graph = type("G", (), {"invoke": staticmethod(fake_invoke)})()
        monkeypatch.setattr(chat_api, "_get_graph", lambda: fake_graph)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # First message in chat mode
            r1 = await client.post("/api/chat", json={"message": "什么是导数"})
            sess_id = r1.json()["session_id"]

            # Toggle to learning
            await client.post(f"/api/chat/{sess_id}/set-mode", json={"mode": "learning"})

            # Send a learning-mode message
            await client.post("/api/chat", json={
                "session_id": sess_id,
                "message": "学习 导数",
            })

            state_resp = await client.get(f"/api/chat/{sess_id}/state")
            data = state_resp.json()
            assert data["session_mode"] == "learning"
            chat_msgs = data.get("chat_messages") or []
            learn_msgs = data.get("messages") or []
            assert len(chat_msgs) == 2  # user + ai from chat turn
            assert len(learn_msgs) == 2  # user + ai from learning turn
            assert chat_msgs[0]["content"] == "什么是导数"
            assert learn_msgs[0]["content"] == "学习 导数"


@pytest.mark.asyncio
class TestStateExposesChatMessages:
    async def test_state_returns_chat_messages_field_with_additional_kwargs(
        self, isolated_db
    ) -> None:
        from src.api import chat as chat_api
        from langchain_core.messages import AIMessage, HumanMessage

        # Manually inject a session with content in both tracks
        sess_id = "track-sess"
        state = chat_api._new_session_state()
        state["chat_messages"] = [
            HumanMessage(content="hi"),
            AIMessage(
                content="hello",
                additional_kwargs={"cta": {"type": "learn_topic", "topic": "X"}},
            ),
        ]
        state["messages"] = [HumanMessage(content="学习 X")]
        chat_api._sessions[sess_id] = state

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/chat/{sess_id}/state")
            data = resp.json()
            assert "chat_messages" in data
            assert len(data["chat_messages"]) == 2
            assert data["chat_messages"][1]["additional_kwargs"] == {
                "cta": {"type": "learn_topic", "topic": "X"}
            }
            assert "messages" in data
            assert data["messages"][0]["content"] == "学习 X"
