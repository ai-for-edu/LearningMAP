# Bugfix: session 持久化、has_history 缓存、product_memory fixture

**日期**：2026-05-04
**分支**：`demo+memory`
**关联**：2026-05-04 功能测试报告 §4.1 / §4.3 / §4.5

## 背景

2026-05-04 的功能测试报告暴露了 3 个独立缺陷：

1. **§4.1（严重）**：`src/api/store.py` 中 `_get_db()` 是死代码，从未被调用；`save_session` / `load_session` 直接 `sqlite3.connect` 不建表，所有写入都抛 `sqlite3.OperationalError: no such table: sessions` 并被 `logger.exception` 吞掉。证据：`sessions.db` 大小 0 字节，`/tmp/main.log` 出现 16 次该异常，重启服务后旧 session 全部丢失。
2. **§4.3（中）**：`src/api/chat.py` 的 `_has_history_cache` 是单进程 dict，TTL 60 s。学生刚提交诊断后，前端在 60 s 内查 `/state` 仍 `has_history=false`，导致"先复习一下"按钮在最该出现的时刻不出现。
3. **§4.5（小）**：`tests/test_product_memory.py` / `tests/test_cross_session_memory.py` 的 `memory_service` fixture 用 `subprocess.Popen(env={...})` 完全替换环境变量，缺 `HOME`/`PATH`/`LANG`，启动慢于 6 s 健康检查窗口 → 21 个用例 ERROR。

## 目标

- 让 SQLite 会话持久化真正生效。
- 学生提交诊断/复习后，下一次 `/state` 调用立刻反映 `has_history` 真实值。
- `tests/test_product_memory.py` 与 `tests/test_cross_session_memory.py` 的 fixture 能稳定拉起 `student_memory` 子服务。

## 非目标

- 不重构 `store.py` 改用 ORM、迁移系统、连接池。
- 不调整 `_has_history_cache` 的 TTL 设计、不引入 Redis。
- 不修复 §4.2（SSE 输出可疑）和 §4.4（review 1 天阈值，是产品决策）。

## 设计

### 修复一：session 持久化（§4.1）

**改动文件**：`src/api/store.py`

- 在 `save_session` 与 `load_session` 内 `sqlite3.connect(...)` 之后立即执行：

  ```python
  db.execute(
      "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)"
  )
  ```

- 删除死代码 `_get_db()`。

理由：`CREATE TABLE IF NOT EXISTS` 在 SQLite 内部只查 `sqlite_master`，开销可忽略。让两个被调用的函数自给自足，避免任何模块导入副作用，也避免依赖 FastAPI startup hook（直接 import store 的脚本/测试也能用）。

### 修复二：`has_history` 缓存写时 invalidate（§4.3）

**改动文件**：`src/api/chat.py`

在 `submit_quiz` 与 `submit_review` 处理函数 `_save(...)` 之后、`return` 之前，追加：

```python
_has_history_cache.pop(DEFAULT_USER_ID, None)
```

理由：写后立即弹缓存，下一次 `_has_history` 会重新打 memory；高频 `/state` 轮询仍能命中 60 s TTL。不动 TTL，不去掉缓存。

### 修复三：`memory_service` fixture 保留系统环境（§4.5）

**改动文件**：`tests/test_product_memory.py`、`tests/test_cross_session_memory.py`（如其 fixture 同写法）

将 `subprocess.Popen(env={...})` 改为：

```python
env={**os.environ, "SM_USE_FAKE_EMBEDDER": "1", ...}
```

理由：保留 `HOME`/`PATH`/`LANG`/`uv` 缓存路径等。fixture 仍仅覆盖 `SM_*` 与 `SERVICE_PORT`，行为不变，但子进程启动稳定。

## 验证策略（一次性回归测试，不进 git）

新建两个测试文件用于本地验证，**全程不 `git add`**：写文件 → `uv run pytest` 跑通 → 验证手动 curl 路径 → `rm` 文件。Fixture 修复属于既有测试代码，**保留并提交**。

### `tests/test_session_persistence.py`（验完即删）

1. `test_save_load_round_trip_creates_table`：
   - 用 `tmp_path` 替换 `_DB_PATH`（`monkeypatch.setattr(store, "_DB_PATH", tmp_path/"x.db")`）。
   - 调 `save_session("sid", {"messages": [], "mode": "chat", "topic": "勾股", ...})`。
   - 新建独立 `sqlite3.connect`，断言 `sessions` 表存在且 `SELECT data FROM sessions WHERE id='sid'` 命中。
2. `test_load_after_save_returns_state`：
   - 同上 save → 调 `load_session("sid")`，断言返回 dict 的 `topic == "勾股"`。
3. `test_load_missing_session_returns_none`：
   - 空 db → `load_session("nope")` 返回 `None` 且不抛。

### `tests/test_has_history_invalidation.py`（验完即删）

1. `test_submit_quiz_invalidates_has_history_cache`：
   - 用 FastAPI `TestClient`（或现有 `httpx.AsyncClient + ASGITransport`）。
   - monkeypatch `src.api.chat._has_history_cache` 注入 `{DEFAULT_USER_ID: (time.monotonic(), False)}`。
   - 构造一个最小 `state` 直接 `_save(sid, ...)`（含 `knowledge_graph` + `questions`），跳过 LLM。
   - mock `src.agent.nodes.diagnosis.diagnosis_node` 返回固定 `DiagnosisResult`。
   - mock `src.memory_client.ingest_session`（避免真请求）。
   - 调 `POST /api/chat/{sid}/submit-quiz` → 断言 `_has_history_cache` 不再包含 `DEFAULT_USER_ID`。
2. `test_submit_review_invalidates_has_history_cache`：
   - 类似上面，预填 `state.review_questions`，mock `grade_review`，调 `/submit-review`。

### Fixture 修复的 smoke check（属于既有测试）

`uv run python -m pytest tests/test_product_memory.py tests/test_cross_session_memory.py -v --tb=line 2>&1 | head -40` —— 只要不再出现 21 个 `student_memory failed to start` ERROR，就算通过。本轮 8 个 FAIL（属于报告 §2.1 列出的过时断言/产品功能差距）不在本次修复范围。

## 影响面

- 持久化修复：用户层无可见行为变化（写仍走原路径），但**重启后会话不再丢**——这本身就是预期行为修复。
- has_history 修复：用户做完诊断后立即看到"先复习一下"按钮，从 60 s 延迟降到下一次 state 轮询命中。
- fixture 修复：仅影响测试套件，不影响生产代码。

## 风险

- 若用户的 `sessions.db` 已有同名但 schema 不一样的 `sessions` 表，`CREATE TABLE IF NOT EXISTS` 不会覆盖；当前文件 0 字节、无表，不会发生。
- has_history `pop` 没有线程锁；FastAPI 单 worker、dict 操作原子，无问题。

## 提交策略

按修复独立提交，便于 review / 回滚：

1. `fix(store): create sessions table on connect, drop dead _get_db`
2. `fix(api): invalidate has_history cache after submit-quiz/submit-review`
3. `test(fixture): preserve os.environ when starting student_memory subprocess`

新增的两个一次性测试文件（`tests/test_session_persistence.py`、`tests/test_has_history_invalidation.py`）**不进 git**：在工作区里跑通后直接 `rm`。git 历史只保留 1–3 三个提交。

## 后续

KG 节点 3 态掌握（mastered/fuzzy/unmastered）+ 复习迭代收敛是独立产品特性，不在本 spec 范围。等以上 3 个 bugfix 落地后单独 brainstorming。
