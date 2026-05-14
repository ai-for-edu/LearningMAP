"""Default-skill behavior: new sessions ship with feynman."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.chat import _DEFAULT_STATE, _ensure_skill_content
from run.serve import app


class TestDefaultSkill:
    def test_default_state_active_skill_is_feynman(self) -> None:
        assert _DEFAULT_STATE["active_skill"] == "feynman"

    def test_ensure_skill_content_materializes_sections(self) -> None:
        state = dict(_DEFAULT_STATE)
        state["active_skill_content"] = None
        _ensure_skill_content(state)
        assert state["active_skill_content"] is not None
        # feynman SKILL.md has chat/quiz/lesson sections
        assert "chat" in state["active_skill_content"]
        assert "quiz" in state["active_skill_content"]
        assert "lesson" in state["active_skill_content"]

    def test_ensure_skill_content_noop_when_skill_none(self) -> None:
        state = {"active_skill": None, "active_skill_content": None}
        _ensure_skill_content(state)
        assert state["active_skill_content"] is None

    def test_ensure_skill_content_noop_when_already_set(self) -> None:
        state = {
            "active_skill": "feynman",
            "active_skill_content": {"chat": "preset"},
        }
        _ensure_skill_content(state)
        assert state["active_skill_content"] == {"chat": "preset"}


@pytest.mark.asyncio
class TestNewSessionDefaultsViaAPI:
    async def test_new_session_state_has_feynman(self, tmp_path, monkeypatch) -> None:
        # Isolate sessions DB so the test doesn't pollute dev data.
        # store._DB_PATH is evaluated at import time, so reload after env change.
        import importlib
        monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "test.db"))
        from src.api import store as store_module
        importlib.reload(store_module)
        from src.api import chat as chat_api
        chat_api._sessions.clear()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Trigger session creation via set-skill (no LLM call needed)
            resp = await client.post(
                "/api/chat/test-session-1/set-skill",
                json={"skill": "feynman"},
            )
            assert resp.status_code == 200
            state_resp = await client.get("/api/chat/test-session-1/state")
            assert state_resp.status_code == 200
            data = state_resp.json()
            assert data.get("active_skill") == "feynman"
            # Verify _ensure_skill_content actually ran — content materialized.
            # The /state endpoint exposes active_skill_lesson_section when content
            # is loaded (see chat.py:864).
            assert data.get("active_skill_lesson_section"), \
                "active_skill_content should have been materialized via _ensure_skill_content"
