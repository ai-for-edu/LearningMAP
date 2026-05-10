"""PR-3 integration tests: write-path wiring of hybrid agent memory.

Covers state field plumbing, _ensure_memory_snapshot, _dispatch_memory_writes,
and three trigger-point integrations. Mock boundary: src.api.chat module-level
symbols (memory_client.fetch_snapshot, run_memory_review, refresh_progress,
ingest_session, ingest_review_session).
"""

from __future__ import annotations

import uuid

import pytest
from unittest.mock import patch

from src.api import chat as chat_module


def test_default_state_has_memory_snapshot_fields():
    """_DEFAULT_STATE includes the two PR-3 fields, both None."""
    state = dict(chat_module._DEFAULT_STATE)
    assert "memory_snapshot" in state
    assert "memory_snapshot_loaded_at" in state
    assert state["memory_snapshot"] is None
    assert state["memory_snapshot_loaded_at"] is None


def test_state_persistence_roundtrip_preserves_memory_snapshot(tmp_path, monkeypatch):
    """SQLite store roundtrip preserves memory_snapshot field as str (not None)."""
    # Redirect store to a temp DB
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    # Reimport store so it picks up the env var
    import importlib
    from src.api import store as store_module

    importlib.reload(store_module)

    sid = f"test-{uuid.uuid4().hex[:8]}"
    state = dict(chat_module._DEFAULT_STATE)
    state["memory_snapshot"] = "## 学习进程\n- 已开始: X"
    state["memory_snapshot_loaded_at"] = 1715000000
    state["messages"] = []
    store_module.save_session(sid, state)

    loaded = store_module.load_session(sid)
    assert loaded is not None
    assert loaded["memory_snapshot"] == "## 学习进程\n- 已开始: X"
    assert loaded["memory_snapshot_loaded_at"] == 1715000000


# ---------------------------------------------------------------------------
# Fixtures for env-on / env-off
# ---------------------------------------------------------------------------


@pytest.fixture
def env_on(monkeypatch):
    """Force-enable PR-3 gate via monkeypatch on the module-level constant.

    setenv alone is insufficient because the constant is computed at import
    time. Both setenv and setattr are applied so functions that re-read
    os.environ stay consistent.
    """
    monkeypatch.setenv("ENABLE_MEMORY_REVIEW", "true")
    monkeypatch.setattr(chat_module, "_MEMORY_REVIEW_ENABLED", True)


@pytest.fixture
def env_off(monkeypatch):
    monkeypatch.setenv("ENABLE_MEMORY_REVIEW", "false")
    monkeypatch.setattr(chat_module, "_MEMORY_REVIEW_ENABLED", False)


# ---------------------------------------------------------------------------
# _ensure_memory_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_memory_snapshot_noop_when_env_off(env_off):
    """env=off: helper is a no-op; fetch_snapshot is never called."""
    state = {"memory_snapshot": None, "memory_snapshot_loaded_at": None}
    with patch.object(chat_module.memory_client, "fetch_snapshot") as mock_fs:
        await chat_module._ensure_memory_snapshot(state)
    assert state["memory_snapshot"] is None
    mock_fs.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_memory_snapshot_fetches_when_field_is_none(env_on):
    """env=on, field=None: fetch_snapshot called once; state populated."""
    state = {"memory_snapshot": None, "memory_snapshot_loaded_at": None}
    with patch.object(
        chat_module.memory_client, "fetch_snapshot", return_value="## hello"
    ) as mock_fs:
        await chat_module._ensure_memory_snapshot(state)
    mock_fs.assert_called_once_with(chat_module.DEFAULT_USER_ID)
    assert state["memory_snapshot"] == "## hello"
    assert isinstance(state["memory_snapshot_loaded_at"], int)


@pytest.mark.asyncio
async def test_ensure_memory_snapshot_skips_when_field_already_set(env_on):
    """env=on, field is non-None (even ''): no fetch."""
    state = {"memory_snapshot": "", "memory_snapshot_loaded_at": 1000}
    with patch.object(chat_module.memory_client, "fetch_snapshot") as mock_fs:
        await chat_module._ensure_memory_snapshot(state)
    mock_fs.assert_not_called()
    assert state["memory_snapshot"] == ""  # unchanged


@pytest.mark.asyncio
async def test_ensure_memory_snapshot_failure_freezes_empty(env_on):
    """fetch_snapshot returns '' (PR-2 contract on failure): field becomes ''."""
    state = {"memory_snapshot": None, "memory_snapshot_loaded_at": None}
    with patch.object(
        chat_module.memory_client, "fetch_snapshot", return_value=""
    ) as mock_fs:
        await chat_module._ensure_memory_snapshot(state)
    mock_fs.assert_called_once()
    assert state["memory_snapshot"] == ""
    # Subsequent calls do not re-fetch
    mock_fs.reset_mock()
    await chat_module._ensure_memory_snapshot(state)
    mock_fs.assert_not_called()
