# Hybrid Agent Memory PR-3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire PR-2's reviewer / progress aggregator / fetch_snapshot into the main FastAPI app via a single dispatch helper. Three trigger points (submit-quiz / submit-review / generate-lesson × 3 entries) fan out three concurrent BG tasks; new sessions fetch a frozen Markdown snapshot once at the chat() / chat_stream() entry.

**Architecture:** `LearningState` gets two new fields (`memory_snapshot`, `memory_snapshot_loaded_at`); `chat.py` gets two helpers (`_ensure_memory_snapshot` async + `_dispatch_memory_writes` sync); `tasks.py`'s `run_lesson_generation` migrates from `run_in_executor` to the same dispatch helper. All new behavior is gated by `ENABLE_MEMORY_REVIEW=true`; env=off path produces zero new side effects.

**Tech Stack:** Python 3.11, FastAPI, asyncio (`asyncio.to_thread` + `asyncio.create_task` with strong-ref set), pytest-asyncio strict mode, httpx.AsyncClient with ASGITransport.

**Spec:** `docs/superpowers/specs/2026-05-10-hybrid-memory-pr3-write-path.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/agent/state.py` | modify | Add `memory_snapshot: str \| None` and `memory_snapshot_loaded_at: int \| None` to `LearningState` |
| `src/api/chat.py` | modify | `_DEFAULT_STATE` adds two fields; `_MEMORY_REVIEW_ENABLED` constant; `_ensure_memory_snapshot` (async) helper; `_bg_tasks` set + `_spawn` + `_dispatch_memory_writes` (sync) helper; three trigger points wired |
| `src/api/tasks.py` | modify | `run_lesson_generation` completion: replace `run_in_executor(ingest_session, …)` with `_dispatch_memory_writes(...)` (function-body import to dodge circular) |
| `tests/test_chat_integration.py` | create | Integration test matrix (env on/off, snapshot fetch, dispatch wiring, failure paths) |

Total ≈ 250 lines (180 in tests, 70 in src).

---

## Task 1: Add `memory_snapshot` fields to LearningState and `_DEFAULT_STATE`

**Files:**
- Modify: `src/agent/state.py` (append two TypedDict fields)
- Modify: `src/api/chat.py:77-106` (`_DEFAULT_STATE` dict)
- Test: `tests/test_chat_integration.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_integration.py`:

```python
"""PR-3 integration tests: write-path wiring of hybrid agent memory.

Covers state field plumbing, _ensure_memory_snapshot, _dispatch_memory_writes,
and three trigger-point integrations. Mock boundary: src.api.chat module-level
symbols (memory_client.fetch_snapshot, run_memory_review, refresh_progress,
ingest_session, ingest_review_session).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.api import chat as chat_module
from src.api.store import save_session, load_session


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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_chat_integration.py::test_default_state_has_memory_snapshot_fields -v
```

Expected: FAIL with `AssertionError: assert "memory_snapshot" in state` (the field doesn't exist yet in `_DEFAULT_STATE`).

- [ ] **Step 3: Add the LearningState fields**

Edit `src/agent/state.py`, append at the end of `LearningState` (after the `review_result` field on line 50):

```python
    # Hybrid agent memory — frozen per-session snapshot (PR-3)
    memory_snapshot: str | None  # session-startup snapshot Markdown; "" = empty/failed; frozen for session
    memory_snapshot_loaded_at: int | None  # epoch seconds; debug/audit only
```

- [ ] **Step 4: Add fields to `_DEFAULT_STATE`**

Edit `src/api/chat.py`, in the `_DEFAULT_STATE` dict (around line 77-106). Add these two entries before the closing brace:

```python
    "review_result": None,
    "memory_snapshot": None,
    "memory_snapshot_loaded_at": None,
}
```

(The `"review_result": None` line already exists — append the two new lines after it.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: both tests PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/agent/state.py src/api/chat.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): add memory_snapshot fields to LearningState"
```

---

## Task 2: Add `_MEMORY_REVIEW_ENABLED` module constant

**Files:**
- Modify: `src/api/chat.py:46-48` (after `_USE_KC_SKILLS_DEFAULT` / `_REVIEW_FLOW_ENABLED`)

This task adds plumbing only; the constant is exercised by Task 3's tests. No standalone test.

- [ ] **Step 1: Add the constant**

Edit `src/api/chat.py`, immediately after the existing `_REVIEW_FLOW_ENABLED` line (around line 48):

```python
_REVIEW_FLOW_ENABLED = os.environ.get("ENABLE_REVIEW_FLOW", "false").lower() == "true"

# Hybrid agent memory write-path gate (PR-3). Same env var also gates PR-2's
# reviewer + progress aggregator (they check internally). Module-level: a
# fresh process picks up env changes; tests use monkeypatch.setattr.
_MEMORY_REVIEW_ENABLED = os.environ.get("ENABLE_MEMORY_REVIEW", "false").lower() == "true"
```

- [ ] **Step 2: Verify imports still work**

```bash
uv run python -c "from src.api import chat; print(chat._MEMORY_REVIEW_ENABLED)"
```

Expected: `False` (env not set).

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check src/api/chat.py
git add src/api/chat.py
git commit -m "feat(memory-pr3): add _MEMORY_REVIEW_ENABLED gate constant"
```

---

## Task 3: Implement `_ensure_memory_snapshot` async helper

**Files:**
- Modify: `src/api/chat.py` (add helper near top of module, after `_has_history` around line 75)
- Test: `tests/test_chat_integration.py` (append fixtures + 3 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_integration.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "ensure_memory_snapshot"
```

Expected: 4 FAILS with `AttributeError: module 'src.api.chat' has no attribute '_ensure_memory_snapshot'`.

- [ ] **Step 3: Add module-level `memory_client` import**

The helper uses `memory_client.fetch_snapshot(...)` (rather than a direct `from … import fetch_snapshot`) so tests can patch `chat_module.memory_client.fetch_snapshot` cleanly. Edit `src/api/chat.py` line 23-28. Find:

```python
from src.memory_client import (
    DEFAULT_USER_ID,
    fetch_mastery,
    ingest_review_session,
    ingest_session,
)
```

Add a module-level package import above it:

```python
from src import memory_client
from src.memory_client import (
    DEFAULT_USER_ID,
    fetch_mastery,
    ingest_review_session,
    ingest_session,
)
```

- [ ] **Step 4: Implement the helper**

Edit `src/api/chat.py`. Add after the `_has_history` function (around line 75, before `_DEFAULT_STATE`):

```python
async def _ensure_memory_snapshot(state: dict[str, Any]) -> None:
    """If snapshot field is None and env is on, fetch once and freeze.

    Mutates state in place. Never raises — fetch_snapshot already swallows
    on its own (returns ""). After this returns, state["memory_snapshot"]
    is a str (possibly empty) and stays frozen for the rest of the session.
    """
    if not _MEMORY_REVIEW_ENABLED:
        return
    if state.get("memory_snapshot") is not None:
        return  # already filled (incl. ""); never re-fetch in same session
    snap = await asyncio.to_thread(memory_client.fetch_snapshot, DEFAULT_USER_ID)
    state["memory_snapshot"] = snap or ""
    state["memory_snapshot_loaded_at"] = int(time.time())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "ensure_memory_snapshot"
```

Expected: 4 PASS.

- [ ] **Step 6: Run full test file**

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: all tests pass (Task 1's 2 + Task 3's 4 = 6).

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/api/chat.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): add _ensure_memory_snapshot async helper"
```

---

## Task 4: Wire `_ensure_memory_snapshot` into `chat()` and `chat_stream()`

**Files:**
- Modify: `src/api/chat.py:147-219` (`chat()` handler)
- Modify: `src/api/chat.py:227-337` (`chat_stream()` handler)
- Test: `tests/test_chat_integration.py` (append 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_integration.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "chat_endpoint"
```

Expected: 1 FAIL (the env_on test fails because `chat()` doesn't yet call `_ensure_memory_snapshot`); the env_off test PASSES (no wiring exists, so no calls happen).

- [ ] **Step 3: Wire `chat()` handler**

Edit `src/api/chat.py`, in the `chat` function around line 152. Find:

```python
    # Get or init session state
    state = _get_session(session_id) or dict(_DEFAULT_STATE)

    # Add user message
    state["messages"] = list(state.get("messages", []))
```

Insert one line (the `await` call) between them:

```python
    # Get or init session state
    state = _get_session(session_id) or dict(_DEFAULT_STATE)
    await _ensure_memory_snapshot(state)

    # Add user message
    state["messages"] = list(state.get("messages", []))
```

- [ ] **Step 4: Wire `chat_stream()` handler**

Edit `src/api/chat.py`, in the `chat_stream` function around line 234. Find:

```python
    session_id = req.session_id or str(uuid.uuid4())
    state = _get_session(session_id) or dict(_DEFAULT_STATE)

    state["messages"] = list(state.get("messages", []))
```

Insert one line:

```python
    session_id = req.session_id or str(uuid.uuid4())
    state = _get_session(session_id) or dict(_DEFAULT_STATE)
    await _ensure_memory_snapshot(state)

    state["messages"] = list(state.get("messages", []))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "chat_endpoint"
```

Expected: both PASS.

Run the full file:

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 8 tests PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/api/chat.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): wire snapshot fetch into chat() and chat_stream()"
```

---

## Task 5: Implement `_dispatch_memory_writes` helper

**Files:**
- Modify: `src/api/chat.py` (add `_bg_tasks` set, `_spawn`, `_dispatch_memory_writes` near top)
- Modify: `src/api/chat.py:23-28` (import `run_memory_review`, `refresh_progress`)
- Test: `tests/test_chat_integration.py` (append 4 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_integration.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "dispatch"
```

Expected: 4 FAILS (`AttributeError: module 'src.api.chat' has no attribute '_dispatch_memory_writes'`).

- [ ] **Step 3: Add module-level imports for reviewer + progress**

Edit `src/api/chat.py`. Below the existing `from src.memory_client import …` block (already amended in Task 3 step 3), append:

```python
from src import memory_client
from src.memory_client import (
    DEFAULT_USER_ID,
    fetch_mastery,
    ingest_review_session,
    ingest_session,
)
from src.progress_aggregator import refresh_progress
from src.reviewer import run_memory_review
```

- [ ] **Step 4: Add `_bg_tasks` + `_spawn` + `_dispatch_memory_writes`**

Edit `src/api/chat.py`. After `_ensure_memory_snapshot` (added in Task 3), add:

```python
# ---------------------------------------------------------------------------
# Background-task dispatch for hybrid memory write path (PR-3).
#
# Three independent BG tasks fan out per trigger event: ingest_session
# (existing L1 vector store), run_memory_review (PR-2 LLM consolidation),
# refresh_progress (PR-2 SQL aggregation). Each has its own top-level
# try/except, so a failure inside one does not affect the other two.
# ---------------------------------------------------------------------------

_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro, *, name: str) -> None:
    """Spawn a task and hold a strong reference until it completes.

    Without this, asyncio.create_task() returns a Task that may be GC'd
    mid-flight and silently cancelled. The set + done_callback pattern
    is the documented Python recommendation.
    """
    t = asyncio.create_task(coro, name=name)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


def _dispatch_memory_writes(
    user_id: str,
    session_id: str,
    state: dict[str, Any],
    source_event: str,
    *,
    ingest_fn=ingest_session,
    extra_ingest_session_id: str | None = None,
) -> None:
    """Fire 3 background tasks in parallel: ingest + reviewer + progress.

    Contract: never raises. reviewer + progress have their own top-level
    try/except (PR-2 §6.1 / §7), and the ingest functions silently swallow.
    The dispatch helper itself only catches RuntimeError from create_task in
    pathological event-loop states.

    extra_ingest_session_id: tasks.py run_lesson_generation passes
    f"{sid}_lesson" to namespace-separate the ingest row; reviewer +
    progress always use the real session_id for audit accuracy.

    ingest_fn: defaults to ingest_session; submit-review passes
    ingest_review_session.
    """
    try:
        state_snapshot = dict(state)        # shallow copy → BG sees stable view
        ingest_sid = extra_ingest_session_id or session_id

        _spawn(
            asyncio.to_thread(ingest_fn, user_id, ingest_sid, state_snapshot),
            name=f"ingest:{session_id}:{source_event}",
        )
        _spawn(
            asyncio.to_thread(
                run_memory_review, user_id, session_id, state_snapshot, source_event,
            ),
            name=f"memory_review:{session_id}:{source_event}",
        )
        _spawn(
            asyncio.to_thread(
                refresh_progress, user_id, session_id, state_snapshot,
            ),
            name=f"refresh_progress:{session_id}:{source_event}",
        )
        logger.info(
            "memory_dispatch session=%s event=%s ingest_sid=%s "
            "tasks=ingest+review+progress",
            session_id, source_event, ingest_sid,
        )
    except Exception:
        logger.exception("memory_dispatch failed (events lost)")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "dispatch"
```

Expected: 4 PASS.

Run the full file:

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 12 tests PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/api/chat.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): add _dispatch_memory_writes fan-out helper"
```

---

## Task 6: Wire `submit-quiz` to `_dispatch_memory_writes`

**Files:**
- Modify: `src/api/chat.py:475-478` (replace `run_in_executor(ingest_session, …)`)
- Test: `tests/test_chat_integration.py` (append 1 test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_integration.py`:

```python
# ---------------------------------------------------------------------------
# Trigger point: submit-quiz
# ---------------------------------------------------------------------------


def _make_session(state_overrides: dict | None = None) -> str:
    """Create a session pre-populated for testing trigger points."""
    sid = f"test-{uuid.uuid4().hex[:8]}"
    state = dict(chat_module._DEFAULT_STATE)
    if state_overrides:
        state.update(state_overrides)
    chat_module._sessions[sid] = state
    return sid


@pytest.mark.asyncio
async def test_submit_quiz_dispatches_three_bg_tasks(env_on):
    """submit-quiz fires ingest_session + run_memory_review + refresh_progress."""
    from httpx import ASGITransport, AsyncClient
    from run.serve import app
    from src.llm.schemas import QuizQuestion

    # Need at least one question for submit-quiz; bypass diagnosis_node
    qs = [
        QuizQuestion(
            knowledge_point="X",
            question_text="Q?",
            options=["a", "b", "c", "d"],
            correct_index=0,
        )
    ]
    sid = _make_session({"questions": qs, "topic": "T"})

    with patch.object(chat_module, "ingest_session") as mock_ingest, \
         patch.object(chat_module, "run_memory_review") as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress, \
         patch("src.agent.nodes.diagnosis.diagnosis_node",
               return_value={"diagnosis_result": {"breakdown": []}}):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                f"/api/chat/{sid}/submit-quiz", json={"answers": [0]},
            )
            assert r.status_code == 200
        await asyncio.sleep(0.1)  # give thread pool time
    mock_ingest.assert_called_once()
    mock_review.assert_called_once()
    mock_progress.assert_called_once()
    assert mock_review.call_args.args[3] == "submit-quiz"  # source_event
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_chat_integration.py::test_submit_quiz_dispatches_three_bg_tasks -v
```

Expected: FAIL — `mock_review.assert_called_once()` fails because submit-quiz still uses `run_in_executor(ingest_session, …)` directly and doesn't call reviewer.

- [ ] **Step 3: Replace the dispatch in `submit_quiz`**

Edit `src/api/chat.py`, in the `submit_quiz` handler around line 475-478. Find:

```python
    diag_result = await asyncio.to_thread(diagnosis_node, state)
    state.update(diag_result)
    _save(session_id, state)
    _has_history_cache.pop(DEFAULT_USER_ID, None)

    # Persist to student_memory service (background, non-blocking)
    asyncio.get_event_loop().run_in_executor(
        None, ingest_session, DEFAULT_USER_ID, session_id, state
    )
```

Replace with:

```python
    diag_result = await asyncio.to_thread(diagnosis_node, state)
    state.update(diag_result)
    _save(session_id, state)
    _has_history_cache.pop(DEFAULT_USER_ID, None)

    # Fan out 3 BG tasks: ingest_session + reviewer + progress (PR-3).
    _dispatch_memory_writes(
        DEFAULT_USER_ID, session_id, state, source_event="submit-quiz",
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run python -m pytest tests/test_chat_integration.py::test_submit_quiz_dispatches_three_bg_tasks -v
```

Expected: PASS.

- [ ] **Step 5: Run full file**

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 13 tests PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/api/chat.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): wire submit-quiz to _dispatch_memory_writes"
```

---

## Task 7: Wire `submit-review` to `_dispatch_memory_writes` (with `ingest_review_session`)

**Files:**
- Modify: `src/api/chat.py:1031-1034` (replace `run_in_executor(ingest_review_session, …)`)
- Test: `tests/test_chat_integration.py` (append 1 test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_integration.py`:

```python
# ---------------------------------------------------------------------------
# Trigger point: submit-review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_review_uses_ingest_review_session(env_on, monkeypatch):
    """submit-review dispatches with ingest_review_session, not ingest_session."""
    monkeypatch.setattr(chat_module, "_REVIEW_FLOW_ENABLED", True)
    from httpx import ASGITransport, AsyncClient
    from run.serve import app
    from src.llm.schemas import QuizQuestion

    qs = [
        QuizQuestion(
            knowledge_point="X",
            question_text="Q?",
            options=["a", "b", "c", "d"],
            correct_index=0,
        )
    ]
    sid = _make_session({"review_questions": qs, "topic": "T"})

    fake_review_result = MagicMock()
    fake_review_result.model_dump.return_value = {"score": 1.0}

    with patch.object(chat_module, "ingest_session") as default_ingest, \
         patch.object(chat_module, "ingest_review_session") as review_ingest, \
         patch.object(chat_module, "run_memory_review") as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress, \
         patch("src.agent.nodes.review.grade_review", return_value=fake_review_result):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                f"/api/chat/{sid}/submit-review", json={"answers": [0]},
            )
            assert r.status_code == 200
        await asyncio.sleep(0.1)
    review_ingest.assert_called_once()
    default_ingest.assert_not_called()
    mock_review.assert_called_once()
    mock_progress.assert_called_once()
    assert mock_review.call_args.args[3] == "submit-review"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_chat_integration.py::test_submit_review_uses_ingest_review_session -v
```

Expected: FAIL.

- [ ] **Step 3: Replace the dispatch in `submit_review`**

Edit `src/api/chat.py`, in the `submit_review` handler around line 1031-1034. Find:

```python
    state["review_answers"] = req.answers
    state["review_result"] = result.model_dump()
    _save(session_id, state)
    _has_history_cache.pop(DEFAULT_USER_ID, None)

    # Persist review answers as memory rows (one ingest per touched topic).
    # Background, non-blocking — same pattern as submit-quiz.
    asyncio.get_event_loop().run_in_executor(
        None, ingest_review_session, DEFAULT_USER_ID, session_id, state
    )
```

Replace with:

```python
    state["review_answers"] = req.answers
    state["review_result"] = result.model_dump()
    _save(session_id, state)
    _has_history_cache.pop(DEFAULT_USER_ID, None)

    # Fan out 3 BG tasks: ingest_review_session + reviewer + progress (PR-3).
    _dispatch_memory_writes(
        DEFAULT_USER_ID, session_id, state,
        source_event="submit-review",
        ingest_fn=ingest_review_session,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run python -m pytest tests/test_chat_integration.py::test_submit_review_uses_ingest_review_session -v
```

Expected: PASS.

- [ ] **Step 5: Run full file**

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 14 tests PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/api/chat.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): wire submit-review to _dispatch_memory_writes"
```

---

## Task 8: Wire `POST /generate-lesson` and `/generate-lesson/stream` to `_dispatch_memory_writes`

**Files:**
- Modify: `src/api/chat.py:487-503` (`generate_lesson` handler)
- Modify: `src/api/chat.py:340-383` (`generate_lesson_stream` handler)
- Test: `tests/test_chat_integration.py` (append 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_integration.py`:

```python
# ---------------------------------------------------------------------------
# Trigger point: generate-lesson (HTTP entries)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_lesson_dispatches_three_bg_tasks(env_on):
    """POST /generate-lesson dispatches ingest + reviewer + progress."""
    from httpx import ASGITransport, AsyncClient
    from run.serve import app

    sid = _make_session({"topic": "T", "diagnosis_result": {"breakdown": []}})

    with patch.object(chat_module, "ingest_session") as mock_ingest, \
         patch.object(chat_module, "run_memory_review") as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress, \
         patch("src.agent.nodes.lesson.lesson_node",
               return_value={"lesson_content": "## Lesson"}):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(f"/api/chat/{sid}/generate-lesson")
            assert r.status_code == 200
        await asyncio.sleep(0.1)
    mock_ingest.assert_called_once()
    mock_review.assert_called_once()
    mock_progress.assert_called_once()
    assert mock_review.call_args.args[3] == "generate-lesson"


@pytest.mark.asyncio
async def test_generate_lesson_stream_dispatches_three_bg_tasks(env_on):
    """POST /generate-lesson/stream also dispatches the three BG tasks."""
    from httpx import ASGITransport, AsyncClient
    from run.serve import app

    sid = _make_session({"topic": "T", "diagnosis_result": {"breakdown": []}})

    def _fake_token_stream(_state):
        yield "## Lesson"

    with patch.object(chat_module, "ingest_session") as mock_ingest, \
         patch.object(chat_module, "run_memory_review") as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress, \
         patch("src.agent.nodes.lesson.lesson_node_stream", side_effect=_fake_token_stream):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(f"/api/chat/{sid}/generate-lesson/stream")
            assert r.status_code == 200
            # Drain SSE body
            await r.aread()
        await asyncio.sleep(0.1)
    mock_ingest.assert_called_once()
    mock_review.assert_called_once()
    mock_progress.assert_called_once()
    assert mock_review.call_args.args[3] == "generate-lesson"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "generate_lesson"
```

Expected: 2 FAIL.

- [ ] **Step 3: Wire `generate_lesson` (blocking handler)**

Edit `src/api/chat.py`, around line 487-503. Find:

```python
@router.post("/chat/{session_id}/generate-lesson")
async def generate_lesson(session_id: str) -> dict:
    """Generate lesson for weak points (called from lesson page)."""
    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.agent.nodes.lesson import lesson_node

    lesson_result = await asyncio.to_thread(lesson_node, state)
    state.update(lesson_result)
    _save(session_id, state)

    return {
        "session_id": session_id,
        "lesson_content": state.get("lesson_content", ""),
    }
```

Replace with:

```python
@router.post("/chat/{session_id}/generate-lesson")
async def generate_lesson(session_id: str) -> dict:
    """Generate lesson for weak points (called from lesson page)."""
    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.agent.nodes.lesson import lesson_node

    lesson_result = await asyncio.to_thread(lesson_node, state)
    state.update(lesson_result)
    _save(session_id, state)

    # Fan out 3 BG tasks (PR-3).
    _dispatch_memory_writes(
        DEFAULT_USER_ID, session_id, state, source_event="generate-lesson",
    )

    return {
        "session_id": session_id,
        "lesson_content": state.get("lesson_content", ""),
    }
```

- [ ] **Step 4: Wire `generate_lesson_stream` (SSE handler)**

Edit `src/api/chat.py`, in the `generate_lesson_stream` SSE handler around line 376-381. Find:

```python
        state["lesson_content"] = full_text
        state["mode"] = "lesson"
        state["lesson_format"] = "markdown"
        _save(session_id, state)

        yield _sse_event({"type": "done", "session_id": session_id})
```

Insert dispatch call between `_save` and the final `yield`:

```python
        state["lesson_content"] = full_text
        state["mode"] = "lesson"
        state["lesson_format"] = "markdown"
        _save(session_id, state)

        # Fan out 3 BG tasks (PR-3).
        _dispatch_memory_writes(
            DEFAULT_USER_ID, session_id, state, source_event="generate-lesson",
        )

        yield _sse_event({"type": "done", "session_id": session_id})
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "generate_lesson"
```

Expected: 2 PASS.

Run the full file:

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 16 tests PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/api/chat.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): wire generate-lesson HTTP entries to _dispatch_memory_writes"
```

---

## Task 9: Wire `tasks.py` `run_lesson_generation` to `_dispatch_memory_writes`

**Files:**
- Modify: `src/api/tasks.py:179-289` (`run_lesson_generation` completion path, line 277-283)
- Test: `tests/test_chat_integration.py` (append 1 test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_integration.py`:

```python
# ---------------------------------------------------------------------------
# Trigger point: tasks.py run_lesson_generation (background)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tasks_run_lesson_generation_dispatches(env_on, monkeypatch):
    """Background lesson task also fans out the three BG tasks via dispatch.

    Drives run_lesson_generation directly (not via HTTP). Verifies that the
    function-body import + dispatch call replace the prior run_in_executor
    pattern.
    """
    from src.api import tasks as tasks_module

    sid = _make_session({"topic": "T"})
    state_snapshot = dict(chat_module._sessions[sid])
    state_snapshot["topic"] = "T"
    state_snapshot["knowledge_graph"] = None  # short-circuit deeper logic

    fake_get_session = lambda _sid: chat_module._sessions[sid]
    fake_save_session = lambda _sid, _s: None

    with patch.object(chat_module, "ingest_session") as mock_ingest, \
         patch.object(chat_module, "run_memory_review") as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress, \
         patch("src.agent.nodes.lesson.retrieve_skills",
               return_value=("", {}, [])), \
         patch("src.agent.nodes.lesson._generate_review_lesson",
               return_value="## Lesson"):
        # No weak_points (third arg = []) hits the all-mastered branch; needs a
        # knowledge_graph for that path to read .nodes — provide a stub
        state_snapshot["knowledge_graph"] = type("KG", (), {"nodes": []})()
        await tasks_module.run_lesson_generation(
            sid, state_snapshot, fake_get_session, fake_save_session,
        )
        await asyncio.sleep(0.1)

    mock_ingest.assert_called_once()
    mock_review.assert_called_once()
    mock_progress.assert_called_once()
    assert mock_review.call_args.args[3] == "generate-lesson"
    # ingest gets sid_lesson; reviewer/progress get real sid
    assert mock_ingest.call_args.args[1] == f"{sid}_lesson"
    assert mock_review.call_args.args[1] == sid
    assert mock_progress.call_args.args[1] == sid
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_chat_integration.py::test_tasks_run_lesson_generation_dispatches -v
```

Expected: FAIL — `tasks.py` still uses `run_in_executor(ingest_session, …)`.

- [ ] **Step 3: Replace the dispatch in `tasks.py`**

Edit `src/api/tasks.py`, in `run_lesson_generation` around line 277-283. Find:

```python
        save_session(session_id, state)

        # Memory ingest
        asyncio.get_event_loop().run_in_executor(
            None,
            ingest_session,
            DEFAULT_USER_ID,
            f"{session_id}_lesson",
            state_snapshot,
        )

    except Exception:
```

Replace with:

```python
        save_session(session_id, state)

        # Fan out 3 BG tasks (PR-3): ingest + reviewer + progress.
        # Function-body import dodges the api.chat ↔ api.tasks circular.
        from src.api.chat import _dispatch_memory_writes
        _dispatch_memory_writes(
            DEFAULT_USER_ID,
            session_id,
            state,
            source_event="generate-lesson",
            extra_ingest_session_id=f"{session_id}_lesson",
        )

    except Exception:
```

The original `from src.memory_client import ingest_session, DEFAULT_USER_ID` line at line 189 already imports `DEFAULT_USER_ID`; the new `from src.api.chat import _dispatch_memory_writes` sits in the same function body for circular-import safety.

> Note: `ingest_session` is no longer called directly here — the dispatch helper invokes it. The line-189 import becomes vestigial for `ingest_session` but `DEFAULT_USER_ID` is still used. Leave the import as-is (removing it adds churn without benefit).

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run python -m pytest tests/test_chat_integration.py::test_tasks_run_lesson_generation_dispatches -v
```

Expected: PASS.

Run the full file:

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 17 tests PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add src/api/tasks.py tests/test_chat_integration.py
git commit -m "feat(memory-pr3): wire tasks.run_lesson_generation to _dispatch_memory_writes"
```

---

## Task 10: Failure-path tests (one BG task fails; full env=off integration)

**Files:**
- Test: `tests/test_chat_integration.py` (append 2 tests)

- [ ] **Step 1: Write the tests**

Append to `tests/test_chat_integration.py`:

```python
# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_bg_task_failing_does_not_block_others(env_on):
    """If run_memory_review raises, ingest + progress still run.

    Note: real PR-2 reviewer has its own top-level try/except so it would
    never propagate. This test simulates a violation of that contract by
    having the mock raise, and verifies dispatch's _spawn isolation."""
    state = {"topic": "X"}
    with patch.object(chat_module, "ingest_session") as mock_ingest, \
         patch.object(chat_module, "run_memory_review",
                      side_effect=RuntimeError("boom")) as mock_review, \
         patch.object(chat_module, "refresh_progress") as mock_progress:
        chat_module._dispatch_memory_writes(
            "uid", "sid", state, source_event="t",
        )
        await asyncio.sleep(0.1)
    # Even though review raised, the other two were spawned + ran
    mock_ingest.assert_called_once()
    mock_progress.assert_called_once()
    mock_review.assert_called_once()


@pytest.mark.asyncio
async def test_env_off_chat_does_not_fetch_snapshot(env_off):
    """env=off: chat() does NOT call fetch_snapshot.

    Note: _dispatch_memory_writes does NOT check env (only the inner
    helpers run_memory_review / refresh_progress check via their own
    `_enabled()` early-return — that's PR-2's responsibility, exercised
    in test_reviewer.py / test_progress_aggregator.py). So in env=off
    submit-quiz, the dispatch helper STILL spawns 3 BG tasks; the
    real reviewer/progress functions early-return inside their own
    bodies. PR-3's env-off invariant covers fetch_snapshot only —
    that's what this test asserts.
    """
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
```

> Important: the env=off invariant tested here is **only** `fetch_snapshot` zero-call. `_dispatch_memory_writes` is intentionally env-agnostic — it always spawns 3 BG tasks. The "no SQLite write under env=off" guarantee in spec §6.1 comes from `run_memory_review` and `refresh_progress` early-returning when `_enabled()` is false; those early returns are validated by PR-2's `test_reviewer.py::test_run_memory_review_noop_when_env_disabled` and `test_progress_aggregator.py::test_refresh_progress_noop_when_env_disabled`, not re-validated here.

- [ ] **Step 2: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat_integration.py -v -k "one_bg_task or env_off_chat"
```

Expected: 2 PASS (no implementation changes needed; these validate dispatch isolation + env-off snapshot invariant).

- [ ] **Step 3: Run the full integration suite + the rest of the test suite**

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 19 tests PASS.

```bash
uv run python -m pytest tests/ -v
```

Expected: full project test suite passes (no regression in other tests).

- [ ] **Step 4: Lint and final commit**

```bash
uv run ruff check src/ tests/test_chat_integration.py
uv run ruff format src/ tests/test_chat_integration.py
git add tests/test_chat_integration.py
git commit -m "test(memory-pr3): add failure-isolation and env-off integration tests"
```

---

## Final Verification

- [ ] Run full project test suite

```bash
uv run python -m pytest tests/ -v
```

Expected: all tests pass, no warnings about unhandled task exceptions.

- [ ] Manual smoke (env=off → no behavior change)

```bash
unset ENABLE_MEMORY_REVIEW
uv run python run/serve.py &
sleep 3
curl -s -X POST http://localhost:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "hello"}' | head -c 500
kill %1
```

Expected: 200 response; SQLite `sessions.db` has new row; student_memory DB unchanged.

- [ ] Manual smoke (env=on → snapshot fetch + dispatch fires)

Start `student_memory` service first:

```bash
cd student_memory && uv run python -m student_memory.app.main &
sleep 2
cd ..
ENABLE_MEMORY_REVIEW=true uv run python run/serve.py &
sleep 3
curl -s -X POST http://localhost:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "我想学勾股定理"}' | head -c 500
sleep 5  # let BG tasks finish
sqlite3 student_memory/data/memory.db "SELECT COUNT(*) FROM notes_history;"
sqlite3 student_memory/data/memory.db "SELECT COUNT(*) FROM op_errors;"
kill %1 %2
```

Expected: `notes_history` count ≥ 0 (may be 0 if no candidates extracted from "我想学勾股定理"); `op_errors` count = 0 (no failures); main app responds normally.

- [ ] Verify spec invariants by inspection

| Spec §6 invariant | Verified by |
|---|---|
| 6.1 env=off no fetch_snapshot | `test_env_off_chat_does_not_fetch_snapshot` |
| 6.1 env=off no reviewer/progress writes | PR-2 unit tests: `test_reviewer.py::test_run_memory_review_noop_when_env_disabled`, `test_progress_aggregator.py::test_refresh_progress_noop_when_env_disabled` |
| 6.2 frozen snapshot N turns | `test_chat_endpoint_fetches_snapshot_on_new_session` |
| 6.3 dispatch never raises; 3 BG independent | `test_dispatch_swallows_exceptions`, `test_one_bg_task_failing_does_not_block_others` |
| 6.4 dispatch after _save | code inspection of submit_quiz / submit_review / generate_lesson handlers |
| 6.5 fetch_snapshot failure freezes "" | `test_ensure_memory_snapshot_failure_freezes_empty` |
| 6.6 reviewer + progress use real sid; ingest uses extra | `test_dispatch_uses_extra_ingest_session_id`, `test_tasks_run_lesson_generation_dispatches` |

---

## Plan Summary

**Tasks:** 10 (1 plumbing + 1 constant + 1 helper + 1 wiring + 1 dispatch + 4 trigger-point wirings + 1 failure-path)

**Estimated commits:** 10 (one per task)

**Estimated lines:** ~250 (180 in tests, 70 in src)

**Risk areas:**
- Circular import between `chat.py` and `tasks.py` (mitigated by function-body import in tasks.py)
- BG task timing in tests (mitigated by `await asyncio.sleep(0.1)` after dispatch)
- `_MEMORY_REVIEW_ENABLED` is module-level (mitigated by `monkeypatch.setattr` in fixtures)

**Out of scope:** PR-4 read-path (node prompt injection) — handled separately.
