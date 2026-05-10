# Hybrid Agent Memory — PR-3 写路径接入

**状态**：草案 / 待评审
**日期**：2026-05-10
**范围**：`src/agent/state.py`、`src/api/chat.py`、`src/api/tasks.py`、`tests/test_chat_integration.py`（新建）
**前置**：
- `docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`（总设计；本 spec 落地其 §9.3-9.4 + §11 PR-3 + §12 验收子集）
- `docs/superpowers/plans/2026-05-08-hybrid-memory-pr1-server.md`（PR-1 已上线）
- `docs/superpowers/plans/2026-05-08-hybrid-memory-pr2-client-reviewer.md`（PR-2 已上线，含 fix #1-#5）

---

## 1. 范围

PR-3 = **写路径接入** + **读路径首站（snapshot 拉取与冻结）**。把 PR-2 已落地的同步入口

- `src.reviewer.run_memory_review(user_id, session_id, state, source_event)`
- `src.progress_aggregator.refresh_progress(user_id, session_id, state)`
- `src.memory_client.fetch_snapshot(user_id, *, correlation_id=None) -> str`

通过 `asyncio.to_thread` 接到主应用的真实事件上：
- 学生进入新 session → 一次性拉 snapshot 写入 `LearningState.memory_snapshot`，**整 session 冻结**
- `submit-quiz` / `submit-review` / `generate-lesson`（共 3 个入口）→ 并发触发 `ingest_session` + `run_memory_review` + `refresh_progress`

PR-3 **不动** prompt 模板和 `src/agent/nodes/*` —— 那是 PR-4 读路径节点注入的事。

所有逻辑在 `ENABLE_MEMORY_REVIEW` env 门控之下；env=off 时 chat.py 行为相对改动前**无新增副作用**。

---

## 2. 接入图

```
新 session 入口                                旁路写路径
┌────────────────────────────┐                 ┌─────────────────────────────────┐
│ POST /chat                 │                 │  submit-quiz                    │
│ POST /chat/stream          │                 ├──► _dispatch_memory_writes(     │
│   └─ await asyncio.to_thread( │              │      ingest_session,            │
│        fetch_snapshot, uid)│  if state[      │      run_memory_review,         │
│      → state["memory_      │  "memory_       │      refresh_progress,          │
│         snapshot"] (冻结)  │  snapshot"]     │   ) × 3 并发 fire-and-forget    │
│      仅当 env=on 且字段为   │  is None        │                                 │
│      None 时执行           │                 │  submit-review                  │
└────────────────────────────┘                 ├──► 同上(ingest_review_session)  │
                                               │                                 │
                                               │  generate-lesson (3 入口)        │
                                               ├──► 同上                          │
                                               │   - chat.py POST /generate-lesson │
                                               │   - chat.py POST /…/stream      │
                                               │   - tasks.py run_lesson_generation │
                                               └─────────────────────────────────┘
```

---

## 3. `LearningState` 与 `_DEFAULT_STATE` 改动

### 3.1 `src/agent/state.py`

`LearningState` 末尾追加两字段（TypedDict 全 optional 语义）：

```python
class LearningState(MessagesState):
    # ... 已有字段 ...

    # Hybrid agent memory — frozen per-session snapshot (PR-3)
    memory_snapshot: str | None              # session 启动时填一次, 之后冻结; "" = 已尝试但失败
    memory_snapshot_loaded_at: int | None    # epoch seconds; 仅供调试/审计
```

### 3.2 双状态语义（关键）

| 字段值 | 语义 | 行为 |
|---|---|---|
| `None` | 尚未尝试拉取（新 session 或 PR-3 部署前的老 session 反序列化） | 进入 `chat()`/`chat_stream()` 时 fetch 一次 |
| `""` | 已尝试，结果为空（PR-2 的 `fetch_snapshot` 失败时返回 `""`；服务端 snapshot 为空时也返回空体） | **永不再 fetch**；同 session 内冻结 |
| 非空 str | 已尝试，fetch 成功且服务端有内容 | **永不再 fetch**；同 session 内冻结 |

这把 spec §12.3 三条不变量同时满足：
1. session 内 N 次 chat 字段不变
2. reviewer 写新 note 不影响当前 session（reviewer 只写 student_memory，不改这个字段）
3. 老 session 反序列化进来字段是 `None` → 第一次 chat() 时补 fetch（合理首次触发，不破坏冻结：fetch 完后字段从 `None` 转到 str，之后冻结）

### 3.3 `_DEFAULT_STATE` 改动（`src/api/chat.py`）

```python
_DEFAULT_STATE = {
    # ... 已有字段 ...
    "memory_snapshot": None,
    "memory_snapshot_loaded_at": None,
}
```

`_DEFAULT_STATE` 是 dict 字面量，每次 `dict(_DEFAULT_STATE)` 浅拷贝即可（两字段都是不可变值）。

### 3.4 序列化与前端可见性

- `src/api/store.py` 的 SQLite 持久化用 `json.dumps`/`json.loads` 处理 state。新字段 `str | None` / `int | None` 原生 JSON 兼容，**不需要改 store.py**。
- `GET /chat/{session_id}/state` **不暴露**这两字段给前端 —— snapshot 是后端内部状态，PR-4 会用它进 prompt，前端只看渲染结果。`get_state` 不加新字段。

---

## 4. `chat.py` 两个新 helper

### 4.1 模块级常量

```python
_MEMORY_REVIEW_ENABLED = os.environ.get("ENABLE_MEMORY_REVIEW", "false").lower() == "true"
```

与现有 `_REVIEW_FLOW_ENABLED` 同模式，import 时一次性求值。

### 4.2 `_ensure_memory_snapshot` — 入口拉取

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
        return  # 已填过, 即使是空串也不再 fetch
    snap = await asyncio.to_thread(memory_client.fetch_snapshot, DEFAULT_USER_ID)
    state["memory_snapshot"] = snap or ""
    state["memory_snapshot_loaded_at"] = int(time.time())
```

**插入位置**：
- `chat()`（line 152 之后、添加 user message 之前）
- `chat_stream()`（line 234 之后、`intent_node` 调用之前）

```python
state = _get_session(session_id) or dict(_DEFAULT_STATE)
await _ensure_memory_snapshot(state)        # ← 新增
state["messages"] = list(state.get("messages", []))
state["messages"].append(HumanMessage(content=req.message))
```

### 4.3 `_dispatch_memory_writes` — 三件 BG 任务并发 fan-out

```python
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
    try/except (PR-2 §6.1 / §7), so failures stay inside the BG task and
    are logged + recorded in op_errors. The dispatch helper itself only
    catches RuntimeError from create_task in pathological event-loop states.

    extra_ingest_session_id: tasks.py run_lesson_generation passes
    f"{sid}_lesson" to namespace-separate the ingest row (matches today's
    behavior). reviewer + progress always use the real session_id.

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
            "memory_dispatch session=%s event=%s ingest_sid=%s tasks=ingest+review+progress",
            session_id, source_event, ingest_sid,
        )
    except Exception:
        logger.exception("memory_dispatch failed (events lost)")
```

**关键设计点**：

1. **shallow snapshot of state**：`dict(state)` 浅拷贝。三个 BG 任务读拷贝，主线程后续 `state["foo"] = ...` 不影响 BG。嵌套对象（messages / diagnosis_result / lesson_content）共享引用，但 BG 任务都只读，无并发冲突。这与 `tasks.py` 已有的 `state_snapshot = dict(state)` 模式一致。

2. **`asyncio.create_task` + 强引用 set**：没有 set 的话 `create_task` 返回的 task 仅被 loop 内部弱持有，存在 GC 期间被取消的潜在 bug。Python 文档明确推荐 set + `add_done_callback` 模式。

3. **三件并发**：彼此独立 —— ingest 写 L1 mastery 表 / reviewer 走 LLM call A+B / progress 走纯 SQL upsert。无共享 state 写。

4. **永不抛**：helper 顶层 try/except 防 dispatch 自己崩；BG 任务自己内部已有顶层 try/except（PR-2 §6.1 / §7）。

### 4.4 三处触发点改写（`chat.py`）

```python
# submit-quiz (替换 line 476-478 的 run_in_executor 调用)
_dispatch_memory_writes(DEFAULT_USER_ID, session_id, state, source_event="submit-quiz")

# submit-review (替换 line 1032-1034) — 注意 ingest_fn 传 ingest_review_session
_dispatch_memory_writes(
    DEFAULT_USER_ID, session_id, state,
    source_event="submit-review",
    ingest_fn=ingest_review_session,
)

# POST /generate-lesson (line 487-503, 在 _save 后追加)
_dispatch_memory_writes(DEFAULT_USER_ID, session_id, state, source_event="generate-lesson")

# POST /generate-lesson/stream (line 340-383, 在 _save 后追加)
_dispatch_memory_writes(DEFAULT_USER_ID, session_id, state, source_event="generate-lesson")
```

**调用点必须在 `_save(session_id, state)` 之后**，保证 BG 读到的 state_snapshot 含最新 `diagnosis_result` / `review_result` / `lesson_content`。

### 4.5 `ingest_session` 风格统一

把 `submit-quiz` (line 476-478) / `submit-review` (line 1032-1034) 现有的 `asyncio.get_event_loop().run_in_executor(None, ingest_session, ...)` 整段替换成 `_dispatch_memory_writes(...)`。这同时完成两件事：
- ingest 风格从 `run_in_executor` 迁到 `asyncio.to_thread` + `create_task` + 持引用
- ingest + reviewer + progress 三件 BG 用同一 dispatch 路径，行为一致

---

## 5. `tasks.py` 改动

### 5.1 `run_lesson_generation` 完成处迁移（`src/api/tasks.py` line 277-283）

现状：

```python
asyncio.get_event_loop().run_in_executor(
    None, ingest_session, DEFAULT_USER_ID, f"{session_id}_lesson", state_snapshot,
)
```

替换为（仍在 `try` block 内、`save_session` 之后）：

```python
from src.api.chat import _dispatch_memory_writes
_dispatch_memory_writes(
    DEFAULT_USER_ID,
    session_id,
    state,
    source_event="generate-lesson",
    extra_ingest_session_id=f"{session_id}_lesson",
)
```

`extra_ingest_session_id` 保留 lesson 完成的 ingest row 命名分隔；reviewer / progress 用真 sid（audit trail 准确）。

### 5.2 循环 import 规避

`tasks.py` 现状已有循环规避模式 —— `from src.memory_client import ingest_session, DEFAULT_USER_ID` 写在 `run_lesson_generation` 函数体内（line 189）。新加的 `from src.api.chat import _dispatch_memory_writes` 沿用此模式，写在函数体内、紧邻已有 import。

### 5.3 不改 `run_slides_generation`

spec §13 第 2 项明确 "不做 generate-slides 触发：与 lesson 信号重复"。`run_slides_generation` 现状的 `run_in_executor(ingest_session, ...)` (line 351-358) **保留不动**。PR-3 范围保持紧凑。

### 5.4 dispatch 是同步函数 vs async 函数

`_dispatch_memory_writes` 是**同步**函数（不带 `async`），内部调 `asyncio.create_task(...)` —— 这要求**调用时必须在 event loop 里**。所有 4 个调用点（chat.py 三处 FastAPI handler + tasks.py 的 `run_lesson_generation`）均为 async 上下文，安全。

---

## 6. 验收不变量

### 6.1 灰度开关

- [ ] `ENABLE_MEMORY_REVIEW=false` 时：`fetch_snapshot` 不被调用（`_ensure_memory_snapshot` 早返）；`run_memory_review` / `refresh_progress` 即使被 dispatch 也立即早返（PR-2 内部 `_enabled()` 检查）
- [ ] env=off 下 SQLite 主库（`sessions.db`）和 student_memory 库不会因 PR-3 多写一行
- [ ] env=off 下 `_DEFAULT_STATE` 的两个新字段保留为 `None`，session 持久化 / 反序列化不报错

> 字节级 prompt 不变量是 PR-4 的事，PR-3 不动 prompt 模板。

### 6.2 Frozen snapshot

- [ ] 同 session 多次 chat：`memory_snapshot` 字段 N 个 turn 后值不变
- [ ] reviewer 在 turn N 写新 note 后，turn N+1 的 chat **仍读旧 snapshot**（reviewer 跑在 BG，`_ensure_memory_snapshot` 的 `is not None` 早返也不会重 fetch）
- [ ] PR-3 部署前的老 session 反序列化进来：字段是 `None` → 第一次 chat 时 fetch 一次 → 之后冻结

### 6.3 Dispatch 行为

- [ ] 三处触发点（submit-quiz / submit-review / generate-lesson × 3 个入口）调用 `_dispatch_memory_writes` 后**立即返回**给客户端，不等 BG 完成
- [ ] 三件 BG 任务在同一 dispatch 调用内各自独立 `_spawn`，互不依赖；任一 task 内部抛异常由各自顶层 try/except 吞掉（reviewer / progress 在 PR-2 已实现，ingest 已有现状），不影响另两件
- [ ] HTTP 主响应在 `_dispatch_memory_writes` 返回后立即继续，不等 BG 完成
- [ ] `_dispatch_memory_writes` 自身永不抛（顶层 try/except 兜底）
- [ ] BG 任务用 `_bg_tasks` set 持引用 + `add_done_callback` 移除，确保不被 GC 取消

### 6.4 状态读新鲜度

- [ ] `_dispatch_memory_writes` 调用点必须在 `_save(session_id, state)` 之后（state 已含 diagnosis_result / review_result / lesson_content）
- [ ] BG 任务读 `state_snapshot = dict(state)` 浅拷贝；后续主线程修改 state 不影响 BG 读

### 6.5 Snapshot 拉取

- [ ] `_ensure_memory_snapshot` 在 `chat()` / `chat_stream()` 入口被 `await`（不是 fire-and-forget）
- [ ] 仅当 `state["memory_snapshot"] is None` 且 env=on 时调用 `fetch_snapshot`；否则早返
- [ ] `fetch_snapshot` 失败时（PR-2 内部捕获返回 `""`），`memory_snapshot` 字段写入 `""`（不是 `None`），同 session 内**永不再尝试**
- [ ] 字段不在 `GET /chat/{sid}/state` 响应中暴露

### 6.6 三件 BG 任务隔离

- [ ] reviewer 与 progress aggregator 共享 dispatch 时机但**不共享数据**：reviewer 用 LLM 决策写 notes，progress 走 SQL upsert，两者互不依赖
- [ ] 三件任务的 ingest_session_id 命名规则保留现状：lesson 完成时用 `f"{sid}_lesson"`（仅 ingest_session 用），reviewer / progress 始终用真 sid

### 6.7 性能

- [ ] PR-3 加的额外延迟集中在 `_ensure_memory_snapshot`：仅新 session 一次 await（实测 student_memory 本地调用 < 200ms；PR-2 client 超时 10s）
- [ ] dispatch 自身不增加 HTTP 响应时间（fire-and-forget，无 await）

### 6.8 与总 spec §12 的对应索引

| 总 spec §12 子节 | PR-3 是否覆盖 |
|---|---|
| §12.1 失败静默 | PR-2 内置；PR-3 通过 helper 传递不破坏 |
| §12.2 灰度开关 | ✅ 6.1 |
| §12.3 Frozen snapshot | ✅ 6.2 |
| §12.4 Reviewer 行为 | PR-2 责任 |
| §12.5 retrieve_memory 门槛 | PR-4 责任 |
| §12.6 数据约束 | PR-1 责任 |
| §12.7 性能 | ✅ 6.7 |
| §12.8 模型与配置 | PR-2 责任 |
| §12.9 可观测性 | PR-2 + PR-3 dispatch 层日志（4.3 末尾的 `memory_dispatch` 行）|

---

## 7. 文件改动清单

| # | 文件 | 改动 | 估算行数 |
|---|---|---|---|
| 1 | `src/agent/state.py` | 加 `memory_snapshot` / `memory_snapshot_loaded_at` 字段 | +5 |
| 2 | `src/api/chat.py` | `_DEFAULT_STATE` 加两字段；`_MEMORY_REVIEW_ENABLED` 常量；`_ensure_memory_snapshot` / `_spawn` / `_bg_tasks` / `_dispatch_memory_writes`；3 处触发点替换 ingest 调用为 helper | +60 / -10 |
| 3 | `src/api/tasks.py` | `run_lesson_generation` 完成处 `run_in_executor` 改 `_dispatch_memory_writes`（函数体内 import） | +5 / -8 |
| 4 | `tests/test_chat_integration.py` | 新建；env on/off 完整流程；mock src/reviewer + progress_aggregator + memory_client | +180 |

合计 ~250 行（含测试 ~180 行）；非测试改动约 70 行。

---

## 8. 测试矩阵（`tests/test_chat_integration.py`）

`fastapi.testclient.TestClient` + `unittest.mock.patch`。每个测试函数独立 fixture。

### 8.1 共用 fixture

```python
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from run.serve import app
    return TestClient(app)


@pytest.fixture
def env_on(monkeypatch):
    monkeypatch.setenv("ENABLE_MEMORY_REVIEW", "true")
    import src.api.chat as chat_mod
    monkeypatch.setattr(chat_mod, "_MEMORY_REVIEW_ENABLED", True)


@pytest.fixture
def env_off(monkeypatch):
    monkeypatch.setenv("ENABLE_MEMORY_REVIEW", "false")
    import src.api.chat as chat_mod
    monkeypatch.setattr(chat_mod, "_MEMORY_REVIEW_ENABLED", False)
```

> `_MEMORY_REVIEW_ENABLED` 是模块级常量，在 import 时计算。fixture 必须 `monkeypatch.setattr` 强制覆盖，单 setenv 不够。

### 8.2 测试场景（每个 ≈15 行）

| # | 测试函数 | 验收点 |
|---|---|---|
| 1 | `test_env_off_no_memory_calls` | env=off：`fetch_snapshot` / `run_memory_review` / `refresh_progress` 全部 `assert_not_called` |
| 2 | `test_env_on_new_session_fetches_snapshot_once` | env=on 新 session：`fetch_snapshot` 被 await 1 次；同 session 第二次 chat 不再 fetch |
| 3 | `test_env_on_existing_session_no_refetch` | 字段已是 str 时：`fetch_snapshot` 不被调 |
| 4 | `test_env_on_submit_quiz_dispatches_three_bg` | `submit-quiz`：ingest + reviewer + progress 各调 1 次 |
| 5 | `test_env_on_submit_review_uses_ingest_review_session` | `submit-review` 走 `ingest_review_session` 而非 `ingest_session`；reviewer/progress 仍调 |
| 6 | `test_env_on_generate_lesson_dispatches`（参数化两个 HTTP 入口） | 两个 lesson HTTP 入口都 dispatch；事件名 `"generate-lesson"` |
| 7 | `test_run_lesson_generation_dispatches` | `tasks.py` 的 background lesson 任务也 dispatch（直接 await coroutine 测） |
| 8 | `test_fetch_snapshot_failure_freezes_empty` | `fetch_snapshot` 返回 `""` → 字段写 `""`，下次不再调 |
| 9 | `test_dispatch_one_failing_others_continue` | 一件 BG 抛异常，另外两件仍被 spawn；HTTP 仍 200 |

### 8.3 Mock 实现要点

- 直接 patch 三个目标函数为 `MagicMock`：`src.api.chat.memory_client.fetch_snapshot` / `src.api.chat.run_memory_review` / `src.api.chat.refresh_progress`。它们仍能被 `asyncio.to_thread` 调（同步函数），且能 `assert_called_once_with(...)`
- **不要** patch `asyncio.to_thread` 本身 —— 那会破坏 `_ensure_memory_snapshot` 的 await
- 也 patch `src.api.chat._get_graph` 让 `graph.invoke` 返回固定 dict，避免真跑 LLM
- BG task 完成时序：dispatch `_spawn` 后立即返回，HTTP 也立即响应；MagicMock 在 thread pool 内被调但主线程已经回到测试代码。两种处理：
  - **推荐**：用 `httpx.AsyncClient(transport=ASGITransport(app=app))` 跑请求，请求返回后 `await asyncio.sleep(0.05)` 给 thread pool 一轮调度时间，再 assert mock 被调
  - **替代**：测试 `tasks.py` 那种"直接 await coroutine"路径不走 HTTP，对 `run_lesson_generation` 等 async 函数直接 `await`，dispatch 完后 `await asyncio.sleep(0.05)` 即可

### 8.4 测试是否进 git

进。`test_chat_integration.py` 是 PR-3 长期回归用例集，不是一次性回归测试，遵循默认（进 git）。

---

## 9. 不做的事

- ❌ **不动 `src/agent/nodes/*` 和 prompt 模板** —— PR-4 责任
- ❌ **不改 `run_slides_generation`** —— spec §13 明确不在 slides 触发 reviewer/progress
- ❌ **不抽独立 `src/api/memory_dispatch.py`** —— PR-3 共 4 个调用点（chat.py 三处 + tasks.py 一处），都在 api 包内，`tasks.py` 用函数体内 import 规避循环；当前规模放 chat.py 单文件 helper 够用
- ❌ **不引入 fetch_snapshot 重试** —— 失败一次即冻结为 `""`；下个 session 自然再试
- ❌ **不暴露 `memory_snapshot` 给前端** —— `GET /state` 不加新字段
- ❌ **不改 `src/api/store.py`** —— 新字段原生 JSON 兼容
- ❌ **不在 `set-skill` / `toggle-*` / `start-generation` 等 endpoint 拉 snapshot** —— 仅 chat() / chat_stream() 学生说话入口

---

## 10. 与 PR-4 的衔接

PR-3 写完后 `LearningState.memory_snapshot` 已经被填好（env=on 路径），前端节点（chat / knowledge_graph / quiz / lesson）尚未消费它。PR-4 接手：

- `src/agent/nodes/_helpers.py` 加 `memory_snapshot_block()`
- 4 个节点 prompt 末尾拼 `{{ memory_snapshot_block }}`
- chat_node 加 retrieve_memory ≥10 字门槛
- review 节点改用 `fetch_active_notes(category='misconception')` 实时 fetch（不读 snapshot）
- 基线快照测试 + 注入测试

PR-3 与 PR-4 的边界清晰：state 字段是**接口**，PR-3 负责填，PR-4 负责读。
