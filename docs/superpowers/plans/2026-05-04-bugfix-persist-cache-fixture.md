# Bugfix: session 持久化 / has_history 缓存 / fixture 环境 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-05-04 功能测试报告暴露的三个独立缺陷:(§4.1) `sessions` 表从未被建,SQLite 持久化全部静默失败; (§4.3) `_has_history_cache` 在写后不失效,导致诊断完到下一次 `/state` 之间最长 60 s 看不到"先复习一下"; (§4.5) `memory_service` fixture 用 `subprocess.Popen(env={...})` 完全替换环境变量,导致子服务启动慢于 6 s 健康检查窗口。

**Architecture:** 三个独立修复,各自独立提交。`store.py` 在 `save_session` / `load_session` 内直接 `CREATE TABLE IF NOT EXISTS` 后再写/读,删除死代码 `_get_db()`;`chat.py` 在 `submit_quiz` 与 `submit_review` 的 `_save(...)` 后立即 `_has_history_cache.pop(DEFAULT_USER_ID, None)`,不动 TTL;`tests/test_product_memory.py` 与 `tests/test_cross_session_memory.py` 把 `subprocess.Popen(env={...})` 改成 `env={**os.environ, ...}`。

**Tech Stack:** FastAPI / SQLite (sqlite3 stdlib) / pytest / httpx / `subprocess.Popen`。

**Spec:** `docs/superpowers/specs/2026-05-04-bugfix-persist-cache-fixture-design.md`

**关于一次性回归测试:** Task 1 / Task 2 都需要新建一次性回归测试文件用于验证修复确实生效 —— **这两个文件全程不进 git**。流程:写文件 → `uv run pytest` 跑通 → 手动 smoke check → `rm` 文件。Task 3 没有新增测试文件,只修复既有 fixture,fixture 修复属于既有测试代码,**保留并提交**。最终 git 历史只增加 3 个 fix 提交。

---

## 文件结构

| 文件 | 类型 | 责任 |
|------|------|------|
| `src/api/store.py` | 修改 | `save_session` / `load_session` 自带 `CREATE TABLE IF NOT EXISTS`;删除 `_get_db()` |
| `src/api/chat.py` | 修改 | `submit_quiz` 与 `submit_review` 在 `_save(...)` 之后 `pop` 掉 `_has_history_cache` 中 `DEFAULT_USER_ID` 条目 |
| `tests/test_product_memory.py` | 修改 | `import os`;fixture `subprocess.Popen` 的 `env` 改为 `{**os.environ, ...}` |
| `tests/test_cross_session_memory.py` | 修改 | 同上 |
| `tests/test_session_persistence.py` | 新建,**不进 git**,验完即删 | 验证 SQLite 表创建 + round-trip |
| `tests/test_has_history_invalidation.py` | 新建,**不进 git**,验完即删 | 验证 submit-quiz / submit-review 后 `_has_history_cache` 中 `DEFAULT_USER_ID` 被弹出 |

---

## Task 1: Fix §4.1 — `sessions` 表持久化

**Files:**
- Modify: `src/api/store.py:19-24` (删除 `_get_db()`)
- Modify: `src/api/store.py:99` (`save_session` 内加建表语句)
- Modify: `src/api/store.py:111` (`load_session` 内加建表语句)
- Test (one-shot, 不进 git): `tests/test_session_persistence.py`

### Step 1: 写一次性回归测试 `tests/test_session_persistence.py`

- [ ] **Step 1: 写一次性回归测试**

新建文件 `tests/test_session_persistence.py`,内容如下(完整文件):

```python
"""One-shot regression test for §4.1 — sessions table not created.

DO NOT git add. Delete this file after Task 1 verification passes.
"""
from __future__ import annotations

import sqlite3

from src.api import store


def test_save_load_round_trip_creates_table(tmp_path, monkeypatch):
    """save_session 必须自建表,SELECT 必须命中。"""
    db_path = tmp_path / "x.db"
    monkeypatch.setattr(store, "_DB_PATH", db_path)

    store.save_session(
        "sid",
        {
            "messages": [],
            "mode": "chat",
            "topic": "勾股",
            "questions": [],
            "answers": [],
            "knowledge_graph": None,
            "diagnosis_result": None,
            "lesson_content": None,
        },
    )

    with sqlite3.connect(str(db_path)) as db:
        row = db.execute(
            "SELECT data FROM sessions WHERE id = ?", ("sid",)
        ).fetchone()
    assert row is not None, "sessions 表未建或写入未生效"


def test_load_after_save_returns_state(tmp_path, monkeypatch):
    """save → load 应能拿回相同 topic。"""
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "x.db")
    store.save_session("sid", {"topic": "勾股", "messages": []})
    loaded = store.load_session("sid")
    assert loaded is not None
    assert loaded["topic"] == "勾股"


def test_load_missing_session_returns_none(tmp_path, monkeypatch):
    """空 db 查不存在的 session 应返回 None,不抛。"""
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "x.db")
    assert store.load_session("nope") is None
```

- [ ] **Step 2: 运行测试,确认前两条 FAIL**

Run:
```bash
uv run python -m pytest tests/test_session_persistence.py -v
```

Expected:
- `test_save_load_round_trip_creates_table` **FAIL** (assertion `row is not None` 失败 — 因为 `save_session` 抛 `OperationalError: no such table: sessions` 被 `logger.exception` 吞掉,什么都没写入)
- `test_load_after_save_returns_state` **FAIL** (`loaded is None` → `loaded["topic"]` 抛 TypeError 或断言失败)
- `test_load_missing_session_returns_none` PASS (修复前 `load_session` 也是 catch 后返回 None — 该用例只是回归保护,修复前后都通过)

至少前两条必须 FAIL,否则说明假设错了,**停止并先 brainstorm 排查**。

- [ ] **Step 3: 修改 `src/api/store.py` — 删除 `_get_db()`,在 save/load 内自建表**

打开 `src/api/store.py`。

**改动 A:** 删除第 19–24 行(整个 `_get_db()` 函数定义)。

删除前:
```python
_DB_PATH = Path(__file__).resolve().parent.parent.parent / "sessions.db"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)"
    )
    return conn


def _serialize_state(state: dict[str, Any]) -> str:
```

删除后:
```python
_DB_PATH = Path(__file__).resolve().parent.parent.parent / "sessions.db"


def _serialize_state(state: dict[str, Any]) -> str:
```

**改动 B:** `save_session`(约第 95–105 行)在 `with sqlite3.connect(...)` 之后立即建表。

改动前:
```python
def save_session(session_id: str, state: dict[str, Any]) -> None:
    """Persist session state to SQLite."""
    try:
        data = _serialize_state(state)
        with sqlite3.connect(str(_DB_PATH)) as db:
            db.execute(
                "INSERT OR REPLACE INTO sessions (id, data) VALUES (?, ?)",
                (session_id, data),
            )
    except Exception:
        logger.exception("Failed to save session %s", session_id)
```

改动后:
```python
def save_session(session_id: str, state: dict[str, Any]) -> None:
    """Persist session state to SQLite."""
    try:
        data = _serialize_state(state)
        with sqlite3.connect(str(_DB_PATH)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)"
            )
            db.execute(
                "INSERT OR REPLACE INTO sessions (id, data) VALUES (?, ?)",
                (session_id, data),
            )
    except Exception:
        logger.exception("Failed to save session %s", session_id)
```

**改动 C:** `load_session`(约第 108–119 行)同样在 `with sqlite3.connect(...)` 之后立即建表。

改动前:
```python
def load_session(session_id: str) -> dict[str, Any] | None:
    """Load session state from SQLite."""
    try:
        with sqlite3.connect(str(_DB_PATH)) as db:
            row = db.execute(
                "SELECT data FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row:
            return _deserialize_state(row[0])
    except Exception:
        logger.exception("Failed to load session %s", session_id)
    return None
```

改动后:
```python
def load_session(session_id: str) -> dict[str, Any] | None:
    """Load session state from SQLite."""
    try:
        with sqlite3.connect(str(_DB_PATH)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)"
            )
            row = db.execute(
                "SELECT data FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row:
            return _deserialize_state(row[0])
    except Exception:
        logger.exception("Failed to load session %s", session_id)
    return None
```

- [ ] **Step 4: 运行测试,确认全 PASS**

Run:
```bash
uv run python -m pytest tests/test_session_persistence.py -v
```

Expected: 3 PASS, 0 FAIL。

- [ ] **Step 5: 运行 lint + 全量回归,确认未破坏既有功能**

Run:
```bash
uv run ruff check src/api/store.py
uv run python -m pytest tests/ -v --ignore=tests/test_product_memory.py --ignore=tests/test_cross_session_memory.py 2>&1 | tail -30
```

(暂时 ignore 两个 fixture 受损的测试文件 — 它们在 Task 3 修复。)

Expected:
- ruff: 0 issues
- pytest: 与修复前同一基线(本仓库已知存在的红色用例不在本次修复范围;只要没有 **新** 的 FAIL 就算通过)

- [ ] **Step 6: 手动 smoke check — sessions.db 真的写入了**

Run(分两步;第二步依赖第一步,串行执行):

```bash
rm -f /tmp/smoke_session.db
uv run python -c "
from pathlib import Path
import sqlite3
from src.api import store
store._DB_PATH = Path('/tmp/smoke_session.db')
store.save_session('smoke_sid', {'topic': '勾股', 'messages': []})
with sqlite3.connect('/tmp/smoke_session.db') as db:
    row = db.execute('SELECT id, length(data) FROM sessions').fetchone()
print('row:', row)
"
```

Expected stdout:
```
row: ('smoke_sid', <some int > 0>)
```

如果输出 `row: None` 或抛异常,**停止并 debug**。

- [ ] **Step 7: 提交 store.py 修改 (一次性测试不入 git)**

Run:
```bash
git add src/api/store.py
git status
```

确认 `git status` 输出里 `tests/test_session_persistence.py` 还在 `Untracked files` 区,没被 add。

然后提交:
```bash
git commit -m "$(cat <<'EOF'
fix(store): create sessions table on connect, drop dead _get_db

§4.1: sessions.db was 0 bytes because save_session/load_session opened
sqlite3 connections directly without ever creating the table. _get_db
had the CREATE TABLE statement but was never called. Inline the
CREATE TABLE IF NOT EXISTS into both functions and delete the dead
helper.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: 删除一次性测试文件**

Run:
```bash
rm tests/test_session_persistence.py
git status
```

Expected: `git status` 干净(没有未跟踪文件,本次提交后没有遗留改动)。

---

## Task 2: Fix §4.3 — `has_history` 缓存写时失效

**Files:**
- Modify: `src/api/chat.py:462` 之后(`submit_quiz` 内 `_save(session_id, state)` 后)
- Modify: `src/api/chat.py:982` 之后(`submit_review` 内 `_save(session_id, state)` 后)
- Test (one-shot, 不进 git): `tests/test_has_history_invalidation.py`

### Step 1: 写一次性回归测试

- [ ] **Step 1: 写一次性回归测试 `tests/test_has_history_invalidation.py`**

新建文件 `tests/test_has_history_invalidation.py`,内容如下(完整文件):

```python
"""One-shot regression test for §4.3 — has_history cache not invalidated.

DO NOT git add. Delete this file after Task 2 verification passes.
"""
from __future__ import annotations

import time
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from run.serve import app
from src.api import chat as chat_module
from src.llm.schemas import (
    DiagnosisBreakdown,
    DiagnosisResult,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
    QuizQuestion,
)
from src.memory_client import DEFAULT_USER_ID


def _seed_state_with_questions() -> dict:
    """Minimal state with knowledge_graph + 1 question, ready for submit-quiz."""
    state = dict(chat_module._DEFAULT_STATE)
    state["topic"] = "勾股"
    state["knowledge_graph"] = KnowledgeGraph(
        topic="勾股",
        nodes=[KnowledgeNode(id="n1", name="勾股", description="d", level="core")],
        edges=[],
    )
    state["questions"] = [
        QuizQuestion(
            id="q1", knowledge_point="勾股",
            question="3²+4²=?", options=["20", "25", "30", "35"], answer=1,
        )
    ]
    state["mode"] = "quiz"
    return state


def test_submit_quiz_invalidates_has_history_cache(monkeypatch):
    monkeypatch.setattr(chat_module, "_REVIEW_FLOW_ENABLED", True)
    chat_module._has_history_cache.clear()
    chat_module._has_history_cache[DEFAULT_USER_ID] = (time.monotonic(), False)

    sid = str(uuid.uuid4())
    chat_module._sessions[sid] = _seed_state_with_questions()

    fake_diag = DiagnosisResult(
        score=100, total=1, correct=1, incorrect=0,
        breakdown=[DiagnosisBreakdown(name="勾股", status="mastered", detail="1/1")],
    )
    monkeypatch.setattr(
        "src.agent.nodes.diagnosis.diagnosis_node",
        lambda s: {"diagnosis_result": fake_diag, "mode": "result"},
    )
    monkeypatch.setattr(chat_module, "ingest_session", lambda *a, **k: None)

    client = TestClient(app)
    r = client.post(f"/api/chat/{sid}/submit-quiz", json={"answers": [1]})
    assert r.status_code == 200, r.text
    assert DEFAULT_USER_ID not in chat_module._has_history_cache, (
        "submit-quiz 后 _has_history_cache 应弹出 DEFAULT_USER_ID"
    )


def test_submit_review_invalidates_has_history_cache(monkeypatch):
    monkeypatch.setattr(chat_module, "_REVIEW_FLOW_ENABLED", True)
    chat_module._has_history_cache.clear()
    chat_module._has_history_cache[DEFAULT_USER_ID] = (time.monotonic(), False)

    sid = str(uuid.uuid4())
    state = dict(chat_module._DEFAULT_STATE)
    state["review_questions"] = [
        QuizQuestion(
            id="rq1", knowledge_point="A",
            question="q", options=["a", "b", "c", "d"], answer=0,
        )
    ]
    chat_module._sessions[sid] = state

    # grade_review is imported inline inside submit_review; patch at source.
    from src.agent.nodes import review as review_mod

    class _FakeResult:
        def model_dump(self):
            return {
                "weak_kps": [],
                "strong_kps": ["A"],
                "focus_directive": "ok",
                "per_kp": [],
            }

    monkeypatch.setattr(review_mod, "grade_review", lambda qs, ans: _FakeResult())

    client = TestClient(app)
    r = client.post(f"/api/chat/{sid}/submit-review", json={"answers": [0]})
    assert r.status_code == 200, r.text
    assert DEFAULT_USER_ID not in chat_module._has_history_cache, (
        "submit-review 后 _has_history_cache 应弹出 DEFAULT_USER_ID"
    )
```

- [ ] **Step 2: 运行测试,确认 FAIL**

Run:
```bash
uv run python -m pytest tests/test_has_history_invalidation.py -v
```

Expected: 两条都 **FAIL** —— 修复前 `_save` 后没有 `pop` 操作,assertion `DEFAULT_USER_ID not in chat_module._has_history_cache` 失败。

如果有用例 PASS,**停止并 debug** —— 可能 fixture 状态污染或 import 路径不对。

- [ ] **Step 3: 修改 `src/api/chat.py` — `submit_quiz` 内加 invalidation**

打开 `src/api/chat.py`,定位 `submit_quiz`(约第 448–473 行)。

改动前(关键 5 行,第 460–467 行):
```python
    diag_result = await asyncio.to_thread(diagnosis_node, state)
    state.update(diag_result)
    _save(session_id, state)

    # Persist to student_memory service (background, non-blocking)
    asyncio.get_event_loop().run_in_executor(
        None, ingest_session, DEFAULT_USER_ID, session_id, state
    )
```

改动后:
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

- [ ] **Step 4: 修改 `src/api/chat.py` — `submit_review` 内加 invalidation**

定位 `submit_review`(约第 958–984 行)。

改动前(关键 4 行,第 980–984 行):
```python
    state["review_answers"] = req.answers
    state["review_result"] = result.model_dump()
    _save(session_id, state)

    return {"review_result": result.model_dump()}
```

改动后:
```python
    state["review_answers"] = req.answers
    state["review_result"] = result.model_dump()
    _save(session_id, state)
    _has_history_cache.pop(DEFAULT_USER_ID, None)

    return {"review_result": result.model_dump()}
```

- [ ] **Step 5: 运行测试,确认 PASS**

Run:
```bash
uv run python -m pytest tests/test_has_history_invalidation.py -v
```

Expected: 2 PASS, 0 FAIL。

- [ ] **Step 6: 跑既有 review-flow 端到端测试,确认没有回归**

Run:
```bash
uv run python -m pytest tests/test_chat_api_review.py -v
```

Expected: 与修复前同一结果(全绿应保持全绿)。`_has_history_cache` 在每个 fixture 内已 `clear()`,新增的 `pop` 对其无影响。

- [ ] **Step 7: lint**

Run:
```bash
uv run ruff check src/api/chat.py
```

Expected: 0 issues。

- [ ] **Step 8: 提交 chat.py 修改 (一次性测试不入 git)**

```bash
git add src/api/chat.py
git status
```

确认 `tests/test_has_history_invalidation.py` 还在 `Untracked files` 区。

```bash
git commit -m "$(cat <<'EOF'
fix(api): invalidate has_history cache after submit-quiz/submit-review

§4.3: _has_history_cache has 60s TTL, so after a student submits a
quiz the front-end keeps seeing has_history=false on /state polls
for up to a minute — exactly the moment the "先复习一下" banner
should appear. Pop the user's entry from _has_history_cache right
after _save() so the next /state recomputes; high-frequency polling
still hits the TTL on subsequent calls.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: 删除一次性测试文件**

```bash
rm tests/test_has_history_invalidation.py
git status
```

Expected: 干净。

---

## Task 3: Fix §4.5 — `memory_service` fixture 保留系统环境

**Files:**
- Modify: `tests/test_product_memory.py:11-19` (加 `import os`)
- Modify: `tests/test_product_memory.py:53-67` (`subprocess.Popen` 的 `env`)
- Modify: `tests/test_cross_session_memory.py:17-25` (加 `import os`)
- Modify: `tests/test_cross_session_memory.py:53-73` (`subprocess.Popen` 的 `env`)

### Step 1: 修改前先 baseline,确认两个文件目前 ERROR

- [ ] **Step 1: Baseline — 确认现状是大量 ERROR(`student_memory failed to start`)**

先验证 `student_memory` venv 已就绪:
```bash
ls student_memory/.venv/bin/python
```

如果不存在:
```bash
cd student_memory && uv sync --extra dev && cd ..
```

跑既有 fixture 受损的两个测试文件:
```bash
uv run python -m pytest tests/test_product_memory.py tests/test_cross_session_memory.py -v --tb=line 2>&1 | tail -40
```

Expected:
- 大量 `ERROR ... student_memory failed to start` 或 `ConnectError` 类堆栈(spec §4.5 描述的 21 个 ERROR)
- 这些 ERROR 都来自 `memory_service` fixture 的 30 次重试超时

把 ERROR 数量记下来作为修复后的对照。

- [ ] **Step 2: 修改 `tests/test_product_memory.py`**

**改动 A:** 加 `import os`。

定位文件顶部 import 区(第 11–19 行):
```python
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
```

改为:
```python
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
```

**改动 B:** fixture `env=` 参数改用 `**os.environ`。

定位 `memory_service` fixture(第 53–67 行)。

改动前:
```python
    proc = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(SERVICE_PORT)],
        cwd=str(STUDENT_MEMORY_DIR),
        env={
            "PATH": str(venv_python.parent),
            "SM_USE_FAKE_EMBEDDER": "1",
            "SM_DATA_DIR": str(tmp_dir),
            "SM_SQLITE_PATH": str(tmp_dir / "memory.db"),
            "SM_INDEX_PATH": str(tmp_dir / "hnsw.bin"),
            "SM_EMBEDDING_DIM": "16",
            "SM_EMBEDDING_MODEL": "fake-test",
            "SM_SERVICE_PORT": str(SERVICE_PORT),
        },
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
```

改动后:
```python
    proc = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(SERVICE_PORT)],
        cwd=str(STUDENT_MEMORY_DIR),
        env={
            **os.environ,
            "SM_USE_FAKE_EMBEDDER": "1",
            "SM_DATA_DIR": str(tmp_dir),
            "SM_SQLITE_PATH": str(tmp_dir / "memory.db"),
            "SM_INDEX_PATH": str(tmp_dir / "hnsw.bin"),
            "SM_EMBEDDING_DIM": "16",
            "SM_EMBEDDING_MODEL": "fake-test",
            "SM_SERVICE_PORT": str(SERVICE_PORT),
        },
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
```

注意:删掉了 `"PATH": str(venv_python.parent)` 这一行 —— `**os.environ` 已经提供 `PATH`,而 venv 的 python 是用绝对路径 `str(venv_python)` 直接调用的,不依赖 PATH 解析。

- [ ] **Step 3: 修改 `tests/test_cross_session_memory.py`**

**改动 A:** 加 `import os`。

定位文件顶部 import 区(第 17–25 行):
```python
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
```

改为:
```python
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
```

**改动 B:** fixture `env=` 参数改用 `**os.environ`。

定位 `memory_service` fixture(第 53–73 行)。

改动前:
```python
    proc = subprocess.Popen(
        [
            str(venv_python), "-m", "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(SERVICE_PORT),
        ],
        cwd=str(STUDENT_MEMORY_DIR),
        env={
            "PATH": str(venv_python.parent),
            "SM_USE_FAKE_EMBEDDER": "1",
            "SM_DATA_DIR": str(tmp_dir),
            "SM_SQLITE_PATH": str(tmp_dir / "memory.db"),
            "SM_INDEX_PATH": str(tmp_dir / "hnsw.bin"),
            "SM_EMBEDDING_DIM": "16",
            "SM_EMBEDDING_MODEL": "fake-test",
            "SM_SERVICE_PORT": str(SERVICE_PORT),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
```

改动后:
```python
    proc = subprocess.Popen(
        [
            str(venv_python), "-m", "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(SERVICE_PORT),
        ],
        cwd=str(STUDENT_MEMORY_DIR),
        env={
            **os.environ,
            "SM_USE_FAKE_EMBEDDER": "1",
            "SM_DATA_DIR": str(tmp_dir),
            "SM_SQLITE_PATH": str(tmp_dir / "memory.db"),
            "SM_INDEX_PATH": str(tmp_dir / "hnsw.bin"),
            "SM_EMBEDDING_DIM": "16",
            "SM_EMBEDDING_MODEL": "fake-test",
            "SM_SERVICE_PORT": str(SERVICE_PORT),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
```

- [ ] **Step 4: 跑两个测试文件,确认 fixture 不再 ERROR**

Run:
```bash
uv run python -m pytest tests/test_product_memory.py tests/test_cross_session_memory.py -v --tb=line 2>&1 | tail -60
```

Expected:
- 不再出现 `student_memory failed to start` ERROR
- 套件里仍可能有 FAIL(spec §2.1 列出的过时断言/产品功能差距,**不在本次修复范围**),但 FAIL 数量应该明显少于 baseline 的 21 个 ERROR
- 关键判断点:`====== ... passed, ... failed in ... ======` 这一行的 **errors=0**(或显著减少且都不是 fixture 启动相关)

如果还在 fixture 启动失败,**停止并诊断** —— 可能 student_memory venv 缺包,或端口被占。

- [ ] **Step 5: lint**

Run:
```bash
uv run ruff check tests/test_product_memory.py tests/test_cross_session_memory.py
```

Expected: 0 issues。

- [ ] **Step 6: 提交 fixture 修改**

```bash
git add tests/test_product_memory.py tests/test_cross_session_memory.py
git status
git commit -m "$(cat <<'EOF'
test(fixture): preserve os.environ when starting student_memory subprocess

§4.5: memory_service fixture passed env={"PATH": ..., "SM_*": ...}
which wiped HOME/LANG/uv-cache paths from the child env, making
uvicorn cold-start slower than the 6 s health-check window and
producing 21 ERRORs across test_product_memory.py /
test_cross_session_memory.py. Spread os.environ first so the child
inherits real env vars; the SM_* overrides still take precedence.
The explicit PATH override is dropped — the venv python is invoked
by absolute path, so PATH only matters for grandchildren and
os.environ's PATH is fine.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 收尾验证

**Files:** 无修改;只是验证。

- [ ] **Step 1: 确认 git 历史只多了 3 个 fix 提交**

Run:
```bash
git log --oneline -5
```

Expected:
```
<sha3> test(fixture): preserve os.environ when starting student_memory subprocess
<sha2> fix(api): invalidate has_history cache after submit-quiz/submit-review
<sha1> fix(store): create sessions table on connect, drop dead _get_db
a1190aa docs(spec): bugfix 测试不进 git + 备注 KG 掌握特性单独 brainstorm
dbb32f6 docs(spec): bugfix design — session persist + has_history cache + fixture
```

- [ ] **Step 2: 确认一次性测试文件已经删除,工作树干净**

Run:
```bash
git status
ls tests/test_session_persistence.py tests/test_has_history_invalidation.py 2>&1
```

Expected:
- `git status` 没有未跟踪/未提交的本计划相关改动(spec/无关文件不算)
- `ls` 输出 `No such file or directory`

- [ ] **Step 3: 全量回归 — 跑所有非 LLM 测试,与 baseline 对比**

Run:
```bash
uv run python -m pytest tests/ -v 2>&1 | tail -40
```

Expected:
- 与修复前 baseline 相比:
  - errors 数量从 21 → 0(或显著下降,不留 fixture 启动错误)
  - 不引入 **新** 的 FAIL(老 FAIL 不在修复范围,继续存在不影响验收)
  - 通过用例数 ≥ baseline

- [ ] **Step 4: 重启 smoke check — sessions.db 确实持久化**

Run:
```bash
rm -f sessions.db
uv run python -c "
from src.api.store import save_session, load_session
save_session('persist_smoke', {'topic': '勾股', 'messages': []})
print('after save:', load_session('persist_smoke'))
"
ls -la sessions.db
uv run python -c "
from src.api.store import load_session
print('after restart:', load_session('persist_smoke'))
"
```

Expected:
- 第一次输出含 `'topic': '勾股'`
- `sessions.db` 大小 > 0 字节(不再是 0 字节)
- 第二次输出同样含 `'topic': '勾股'`(模拟重启后 reload 成功)

清理 smoke db:
```bash
rm -f sessions.db
```

> 注意:`sessions.db` 在仓库根目录,但已在 `.gitignore`(查 `.gitignore` 确认 — 若未 ignore,这是另一个独立 bug,不在本计划修复范围,只需告诉用户)。

- [ ] **Step 5: 完成**

到此,3 个独立 bugfix 已全部落地并通过验证。后续:KG 节点 3 态掌握 + 复习迭代收敛 是独立产品特性,等本计划合并后再 brainstorm。
