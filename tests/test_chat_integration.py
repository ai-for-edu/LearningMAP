"""PR-3 integration tests: write-path wiring of hybrid agent memory.

Covers state field plumbing, _ensure_memory_snapshot, _dispatch_memory_writes,
and three trigger-point integrations. Mock boundary: src.api.chat module-level
symbols (memory_client.fetch_snapshot, run_memory_review, refresh_progress,
ingest_session, ingest_review_session).
"""

from __future__ import annotations

import uuid

import asyncio

import pytest
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# chat() / chat_stream() snapshot wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_endpoint_fetches_snapshot_on_new_session(env_on):
    """POST /api/chat with no session_id → fetch_snapshot called once;
    second call same session_id does NOT re-fetch."""
    from httpx import ASGITransport, AsyncClient
    from run.serve import app

    fake_graph_result = {
        "messages": [],
        "mode": "chat",
        "memory_snapshot": "## 进程\n- X",
        "memory_snapshot_loaded_at": 1000,
    }

    with patch.object(
        chat_module.memory_client, "fetch_snapshot", return_value="## 进程\n- X"
    ) as mock_fs, patch.object(chat_module, "_get_graph") as mock_graph:
        mock_graph.return_value.invoke.return_value = fake_graph_result
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r1 = await client.post("/api/chat", json={"message": "hello"})
            assert r1.status_code == 200
            sid = r1.json()["session_id"]
            r2 = await client.post(
                "/api/chat", json={"session_id": sid, "message": "again"}
            )
            assert r2.status_code == 200
    assert mock_fs.call_count == 1


@pytest.mark.asyncio
async def test_chat_endpoint_no_fetch_when_env_off(env_off):
    """env=off: fetch_snapshot is never called from chat()."""
    from httpx import ASGITransport, AsyncClient
    from run.serve import app

    with patch.object(chat_module.memory_client, "fetch_snapshot") as mock_fs, \
         patch.object(chat_module, "_get_graph") as mock_graph:
        mock_graph.return_value.invoke.return_value = {"messages": [], "mode": "chat"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post("/api/chat", json={"message": "hi"})
            assert r.status_code == 200
    mock_fs.assert_not_called()


# ---------------------------------------------------------------------------
# _dispatch_memory_writes helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_spawns_three_bg_tasks(env_on):
    """dispatch fires ingest + run_memory_review + refresh_progress concurrently."""
    state = {"topic": "X", "diagnosis_result": None}
    with patch.object(chat_module, "ingest_session") as mock_ingest, \
         patch.object(chat_module, "run_memory_review") as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress:
        chat_module._dispatch_memory_writes(
            "uid", "sid", state, source_event="test-event"
        )
        # asyncio.to_thread schedules in default ThreadPoolExecutor; let it run
        await asyncio.sleep(0.1)
    mock_ingest.assert_called_once()
    mock_review.assert_called_once()
    mock_progress.assert_called_once()

    # Verify args (state is shallow-copied so identity differs but contents equal)
    review_args = mock_review.call_args.args
    assert review_args[0] == "uid"
    assert review_args[1] == "sid"
    assert review_args[2] is not state          # shallow copy
    assert review_args[2] == state              # equal contents
    assert review_args[3] == "test-event"


@pytest.mark.asyncio
async def test_dispatch_uses_ingest_fn_param(env_on):
    """ingest_fn parameter overrides default (used by submit-review)."""
    custom_ingest = MagicMock()
    state = {"topic": "Y"}
    with patch.object(chat_module, "ingest_session") as default_ingest, \
         patch.object(chat_module, "run_memory_review"), \
         patch.object(chat_module, "refresh_progress"):
        chat_module._dispatch_memory_writes(
            "uid", "sid", state, source_event="test", ingest_fn=custom_ingest,
        )
        await asyncio.sleep(0.1)
    custom_ingest.assert_called_once()
    default_ingest.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_uses_extra_ingest_session_id(env_on):
    """extra_ingest_session_id only affects ingest_fn, not reviewer/progress."""
    state = {"topic": "Z"}
    with patch.object(chat_module, "ingest_session") as mock_ingest, \
         patch.object(chat_module, "run_memory_review") as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress:
        chat_module._dispatch_memory_writes(
            "uid", "sid", state, source_event="generate-lesson",
            extra_ingest_session_id="sid_lesson",
        )
        await asyncio.sleep(0.1)
    assert mock_ingest.call_args.args[1] == "sid_lesson"
    assert mock_review.call_args.args[1] == "sid"        # real sid for audit
    assert mock_progress.call_args.args[1] == "sid"


@pytest.mark.asyncio
async def test_dispatch_swallows_exceptions(env_on, caplog):
    """If asyncio.create_task fails (pathological), dispatch logs and returns."""
    state = {"topic": "W"}
    with patch.object(chat_module, "asyncio") as mock_asyncio:
        mock_asyncio.create_task.side_effect = RuntimeError("no running loop")
        # to_thread also accessed — let it return a coro stub
        mock_asyncio.to_thread = lambda *a, **kw: None
        # Should not raise even though create_task explodes
        chat_module._dispatch_memory_writes(
            "uid", "sid", state, source_event="t",
        )
