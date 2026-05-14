"""Tests for the session_mode HTTP surface."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from run.serve import app


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "test.db"))
    from src.api import chat as chat_api
    chat_api._sessions.clear()
    yield
    chat_api._sessions.clear()


@pytest.mark.asyncio
class TestSetModeEndpoint:
    async def test_set_mode_to_learning(self, isolated_db) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Auto-create session via set-skill
            await client.post("/api/chat/sess-1/set-skill", json={"skill": "feynman"})
            resp = await client.post("/api/chat/sess-1/set-mode", json={"mode": "learning"})
            assert resp.status_code == 200
            assert resp.json() == {"session_id": "sess-1", "session_mode": "learning"}

            # Confirm via /state
            state = await client.get("/api/chat/sess-1/state")
            assert state.json()["session_mode"] == "learning"

    async def test_set_mode_to_chat(self, isolated_db) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/chat/sess-2/set-skill", json={"skill": "feynman"})
            resp = await client.post("/api/chat/sess-2/set-mode", json={"mode": "chat"})
            assert resp.status_code == 200
            assert resp.json()["session_mode"] == "chat"

    async def test_invalid_mode_returns_400(self, isolated_db) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/chat/sess-3/set-skill", json={"skill": "feynman"})
            resp = await client.post("/api/chat/sess-3/set-mode", json={"mode": "bogus"})
            assert resp.status_code == 400

    async def test_unknown_session_returns_404(self, isolated_db) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/no-such-session/set-mode", json={"mode": "chat"}
            )
            assert resp.status_code == 404


@pytest.mark.asyncio
class TestStateExposesSessionMode:
    async def test_new_session_state_session_mode_is_chat(self, isolated_db) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/chat/sess-4/set-skill", json={"skill": "feynman"})
            resp = await client.get("/api/chat/sess-4/state")
            assert resp.json()["session_mode"] == "chat"


@pytest.mark.asyncio
class TestChatRequestMode:
    async def test_first_message_with_mode_creates_learning_session(
        self, isolated_db, monkeypatch
    ) -> None:
        # Mock the LangGraph invoke so we don't need a real LLM.
        from src.api import chat as chat_api

        def fake_invoke(state):
            return {**state, "mode": "chat", "messages": state["messages"]}

        fake_graph = type("G", (), {"invoke": staticmethod(fake_invoke)})()
        monkeypatch.setattr(chat_api, "_get_graph", lambda: fake_graph)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat", json={"message": "hi", "mode": "learning"}
            )
            assert resp.status_code == 200
            sess_id = resp.json()["session_id"]
            state = await client.get(f"/api/chat/{sess_id}/state")
            assert state.json()["session_mode"] == "learning"

    async def test_existing_session_ignores_mode_field(
        self, isolated_db, monkeypatch
    ) -> None:
        from src.api import chat as chat_api

        def fake_invoke(state):
            return {**state, "mode": "chat", "messages": state["messages"]}

        fake_graph = type("G", (), {"invoke": staticmethod(fake_invoke)})()
        monkeypatch.setattr(chat_api, "_get_graph", lambda: fake_graph)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create session in chat mode (default)
            r1 = await client.post("/api/chat", json={"message": "hi"})
            sess_id = r1.json()["session_id"]
            # Try to override via mode field on existing session
            await client.post(
                "/api/chat",
                json={"session_id": sess_id, "message": "again", "mode": "learning"},
            )
            state = await client.get(f"/api/chat/{sess_id}/state")
            assert state.json()["session_mode"] == "chat"  # not overridden
