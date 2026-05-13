# Review Flow PR-1 Implementation Plan — Mastery API

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/mastery/{user_id}` aggregation endpoint to `student_memory` service + thin client wrapper in `src/memory_client.py`, so the upcoming review-flow (PR-2) can query per-user, per-subtopic learning history without touching the LLM.

**Architecture:** Pure additive changes. Reuses the existing `memories` table and `idx_memories_user_subtopic` index — no migrations. Repository method aggregates rows, FastAPI route exposes via dependency injection (matching existing `/retrieve` pattern). Client side mirrors `retrieve_memory`'s "fail silent → empty list" style. **Zero callers, zero regression risk** — the route can be merged before PR-2 lands.

**Tech Stack:** Python 3.11, FastAPI, sqlite3, pydantic 2, httpx, pytest

**Spec reference:** `docs/superpowers/specs/2026-04-30-review-flow-spec.md` §3, §9 (#1-5), §10 (PR-1)

---

## Architecture Decisions Locked in This Plan

1. **No new repository class.** `aggregate_mastery` becomes a method on `SqliteRepo` next to `list_by_user`. Wrapping it in a `MasteryQuerier` adds layers without users.
2. **No new dependency in `wire_app`.** Route reads `request.app.state.repo` via a thin `get_repo` getter (matching the `get_ingestor` / `get_retriever` overridable pattern).
3. **`since_days` cutoff is computed in Python, not via SQLite's `datetime('now', '-N days')`.** SQLite's datetime() returns `"YYYY-MM-DD HH:MM:SS"` (space, no Z); stored timestamps are ISO-8601 `"...Z"` — direct lexicographic compare across formats is wrong (`"T" > " "`). Computing cutoff in Python with `format_timestamp(utcnow() - timedelta(days=N))` keeps both sides identical.
4. **Filter `subtopic IS NOT NULL`.** Goal-level rows (no specific KP) shouldn't appear in mastery reports.
5. **Return list of dicts, not dataclasses.** The repo method's only caller is the route; pydantic does final validation. Dicts keep the boundary thin.

---

## File Structure

| File | Role | Touched in |
|---|---|---|
| `student_memory/app/db/sqlite_repo.py` | Add `aggregate_mastery` method | Task 1 |
| `student_memory/tests/test_sqlite_repo.py` | Add aggregation tests | Task 1 |
| `student_memory/app/api/schemas.py` | Add `MasteryItem`, `MasteryResponse` | Task 2 |
| `student_memory/app/api/routes.py` | Add `get_repo` dep + `GET /mastery/{user_id}` | Task 3 |
| `student_memory/tests/test_api.py` | Add `/mastery` HTTP tests | Task 3 |
| `src/memory_client.py` | Add `fetch_mastery()` | Task 4 |
| `tests/test_memory_client.py` | Add `fetch_mastery` tests | Task 4 |

Each task = one logical change + tests, ending in a commit. PR-1 = Tasks 1-5.

---

## Task 1: `aggregate_mastery` repository method

**Files:**
- Modify: `student_memory/app/db/sqlite_repo.py` (add method on `SqliteRepo`)
- Test: `student_memory/tests/test_sqlite_repo.py` (add test class `TestAggregateMastery`)

- [ ] **Step 1: Read `app/utils/time.py` to confirm timestamp utilities**

Run: `cat student_memory/app/utils/time.py`
Goal: Confirm `format_timestamp(dt)` and `utcnow()` signatures so cutoff computation matches stored format exactly.

- [ ] **Step 2: Write failing tests for `aggregate_mastery`**

Append to `student_memory/tests/test_sqlite_repo.py`:

```python
class TestAggregateMastery:
    """Aggregation queries for review-flow."""

    def test_empty_db_returns_empty_list(self, repo: SqliteRepo) -> None:
        assert repo.aggregate_mastery("u_001") == []

    def test_groups_by_subtopic_with_session_count(self, repo: SqliteRepo) -> None:
        # Two sessions on Discriminant, one on Factoring
        repo.insert_memories(
            [
                _make_record("m1", session_id="s1", subtopic="Discriminant",
                             timestamp="2026-04-01T10:00:00Z"),
                _make_record("m2", session_id="s2", subtopic="Discriminant",
                             timestamp="2026-04-15T10:00:00Z"),
                _make_record("m3", session_id="s3", subtopic="Factoring",
                             timestamp="2026-04-10T10:00:00Z"),
            ]
        )
        items = repo.aggregate_mastery("u_001")

        # Sorted by last_seen_at desc → Discriminant (Apr 15) first
        assert [i["subtopic"] for i in items] == ["Discriminant", "Factoring"]

        disc = items[0]
        assert disc["session_count"] == 2
        assert disc["last_seen_at"] == "2026-04-15T10:00:00Z"
        assert disc["total_questions"] == 4   # 2 questions per record
        assert disc["total_correct"] == 2
        assert disc["last_accuracy"] == 0.5   # from _make_record default

    def test_filters_other_users(self, repo: SqliteRepo) -> None:
        repo.insert_memories([
            _make_record("m1", user_id="u_001", subtopic="A"),
            _make_record("m2", user_id="u_002", subtopic="A"),
        ])
        items = repo.aggregate_mastery("u_001")
        assert len(items) == 1

    def test_excludes_null_subtopic_rows(self, repo: SqliteRepo) -> None:
        repo.insert_memories([
            _make_record("m1", subtopic="A"),
            _make_record("m2", subtopic=None),  # goal-level row, must skip
        ])
        items = repo.aggregate_mastery("u_001")
        assert {i["subtopic"] for i in items} == {"A"}

    def test_topic_filter(self, repo: SqliteRepo) -> None:
        repo.insert_memories([
            _make_record("m1", topic="Algebra", subtopic="A"),
            _make_record("m2", topic="Geometry", subtopic="B"),
        ])
        items = repo.aggregate_mastery("u_001", topic="Algebra")
        assert {i["subtopic"] for i in items} == {"A"}

    def test_min_seen_filter(self, repo: SqliteRepo) -> None:
        # Subtopic A: 1 session; B: 2 sessions
        repo.insert_memories([
            _make_record("m1", session_id="s1", subtopic="A"),
            _make_record("m2", session_id="s1", subtopic="B"),
            _make_record("m3", session_id="s2", subtopic="B"),
        ])
        items = repo.aggregate_mastery("u_001", min_seen=2)
        assert {i["subtopic"] for i in items} == {"B"}

    def test_since_days_filter(self, repo: SqliteRepo) -> None:
        # 100 days old → excluded; 1 day old → included
        from datetime import timedelta
        from app.utils.time import format_timestamp, utcnow
        old_ts = format_timestamp(utcnow() - timedelta(days=100))
        new_ts = format_timestamp(utcnow() - timedelta(days=1))
        repo.insert_memories([
            _make_record("m1", subtopic="Old", timestamp=old_ts),
            _make_record("m2", subtopic="New", timestamp=new_ts),
        ])
        items = repo.aggregate_mastery("u_001", since_days=30)
        assert {i["subtopic"] for i in items} == {"New"}

    def test_last_accuracy_uses_most_recent_session(self, repo: SqliteRepo) -> None:
        # Insert two records same subtopic; later one should win
        rec_old = MemoryRecord(
            memory_id="m1", user_id="u_001", session_id="s1",
            memory_type="subtopic", topic="T", subtopic="A",
            timestamp="2026-04-01T10:00:00Z",
            retrieval_text="x", raw_json="{}",
            question_count=2, correct_count=1, accuracy=0.5,
            error_types=None,
        )
        rec_new = MemoryRecord(
            memory_id="m2", user_id="u_001", session_id="s2",
            memory_type="subtopic", topic="T", subtopic="A",
            timestamp="2026-04-20T10:00:00Z",
            retrieval_text="y", raw_json="{}",
            question_count=2, correct_count=2, accuracy=1.0,
            error_types=None,
        )
        repo.insert_memories([rec_old, rec_new])
        items = repo.aggregate_mastery("u_001")
        assert items[0]["last_accuracy"] == 1.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd student_memory && uv run python -m pytest tests/test_sqlite_repo.py::TestAggregateMastery -v`
Expected: FAIL with `AttributeError: 'SqliteRepo' object has no attribute 'aggregate_mastery'`

- [ ] **Step 4: Implement `aggregate_mastery`**

Add to `student_memory/app/db/sqlite_repo.py`, near `list_by_user` (around line 256):

```python
    def aggregate_mastery(
        self,
        user_id: str,
        *,
        topic: str | None = None,
        since_days: int | None = None,
        min_seen: int = 1,
    ) -> list[dict]:
        """Aggregate mastery per subtopic for review-flow.

        Returns one row per (subtopic, topic) with:
          - last_seen_at: max timestamp
          - session_count: distinct sessions touched
          - total_questions / total_correct: across all sessions
          - last_accuracy: accuracy of the most recent session

        Sorted by last_seen_at DESC. Rows with NULL subtopic are excluded.
        Fails closed: returns [] for unknown user_id (no exception).
        """
        from datetime import timedelta

        from app.utils.time import format_timestamp, utcnow

        sql_parts = [
            """
            SELECT
                subtopic, topic,
                MAX(timestamp) AS last_seen_at,
                COUNT(DISTINCT session_id) AS session_count,
                COALESCE(SUM(question_count), 0) AS total_questions,
                COALESCE(SUM(correct_count), 0) AS total_correct,
                (SELECT accuracy FROM memories m2
                   WHERE m2.user_id = m.user_id
                     AND m2.subtopic = m.subtopic
                   ORDER BY timestamp DESC LIMIT 1) AS last_accuracy
            FROM memories m
            WHERE user_id = ?
              AND subtopic IS NOT NULL
            """
        ]
        params: list = [user_id]

        if topic is not None:
            sql_parts.append("AND topic = ?")
            params.append(topic)

        if since_days is not None:
            cutoff_iso = format_timestamp(utcnow() - timedelta(days=since_days))
            sql_parts.append("AND timestamp >= ?")
            params.append(cutoff_iso)

        sql_parts.append("GROUP BY subtopic, topic")
        sql_parts.append("HAVING session_count >= ?")
        params.append(min_seen)
        sql_parts.append("ORDER BY last_seen_at DESC")

        sql = "\n".join(sql_parts)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "subtopic": row["subtopic"],
                "topic": row["topic"],
                "last_seen_at": row["last_seen_at"],
                "session_count": row["session_count"],
                "total_questions": row["total_questions"],
                "total_correct": row["total_correct"],
                "last_accuracy": row["last_accuracy"],
            }
            for row in rows
        ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd student_memory && uv run python -m pytest tests/test_sqlite_repo.py::TestAggregateMastery -v`
Expected: 7 tests PASS.

- [ ] **Step 6: Run full sqlite_repo test file to verify no regressions**

Run: `cd student_memory && uv run python -m pytest tests/test_sqlite_repo.py -v`
Expected: All tests pass (existing + 7 new).

- [ ] **Step 7: Commit**

```bash
git add student_memory/app/db/sqlite_repo.py student_memory/tests/test_sqlite_repo.py
git commit -m "$(cat <<'EOF'
feat(memory): aggregate_mastery 仓库方法 + 7 项 TDD 测试

为 review-flow PR-1 引入按用户/小节聚合的查询能力. 复用 memories 表
+ idx_memories_user_subtopic, 零迁移. since_days 截止值在 Python 侧
计算以避免 SQLite datetime() 与 ISO-8601 Z 格式不一致.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Pydantic schemas (`MasteryItem`, `MasteryResponse`)

**Files:**
- Modify: `student_memory/app/api/schemas.py` (add classes after `RetrieveResponse`)

- [ ] **Step 1: Add schemas**

Append to `student_memory/app/api/schemas.py` before the `ErrorResponse` block:

```python
# ----------------------------------------------------------------------
# /mastery
# ----------------------------------------------------------------------
class MasteryItem(BaseModel):
    subtopic: str
    topic: str | None = None
    last_seen_at: str
    session_count: int
    total_questions: int
    total_correct: int
    last_accuracy: float | None = None


class MasteryResponse(BaseModel):
    user_id: str
    items: list[MasteryItem]
```

- [ ] **Step 2: Verify no import errors**

Run: `cd student_memory && uv run python -c "from app.api.schemas import MasteryItem, MasteryResponse; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add student_memory/app/api/schemas.py
git commit -m "$(cat <<'EOF'
feat(memory): MasteryItem / MasteryResponse pydantic schemas

review-flow PR-1: 数据传输层. 与 aggregate_mastery 返回的 dict 字段一一对应.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `GET /mastery/{user_id}` route + HTTP tests

**Files:**
- Modify: `student_memory/app/api/routes.py` (add `get_repo` dep + `GET /mastery/{user_id}`)
- Test: `student_memory/tests/test_api.py` (append HTTP tests)

- [ ] **Step 1: Write failing HTTP tests**

Append to `student_memory/tests/test_api.py`:

```python
def test_mastery_empty_returns_empty_items(settings_override: Settings) -> None:
    with _client(settings_override) as client:
        resp = client.get("/mastery/u_unknown")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"user_id": "u_unknown", "items": []}


def test_mastery_returns_aggregated_subtopics(
    settings_override: Settings, sample_session_payload: dict
) -> None:
    with _client(settings_override) as client:
        ingest_resp = client.post("/ingest", json=sample_session_payload)
        assert ingest_resp.status_code == 200

        resp = client.get("/mastery/u_001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "u_001"
    subtopics = {it["subtopic"] for it in body["items"]}
    assert subtopics == {"Discriminant", "Factoring"}

    disc = next(it for it in body["items"] if it["subtopic"] == "Discriminant")
    assert disc["topic"] == "Quadratic Equations"
    assert disc["session_count"] == 1
    assert disc["total_questions"] == 2
    assert disc["total_correct"] == 1   # 1 of 2 correct in fixture
    assert disc["last_accuracy"] == 0.5


def test_mastery_topic_query_param(
    settings_override: Settings, sample_session_payload: dict
) -> None:
    with _client(settings_override) as client:
        client.post("/ingest", json=sample_session_payload)
        resp = client.get("/mastery/u_001?topic=Quadratic%20Equations")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

        resp_other = client.get("/mastery/u_001?topic=Calculus")
        assert resp_other.status_code == 200
        assert resp_other.json()["items"] == []


def test_mastery_min_seen_query_param(
    settings_override: Settings, sample_session_payload: dict
) -> None:
    with _client(settings_override) as client:
        client.post("/ingest", json=sample_session_payload)
        # Each subtopic seen once → min_seen=2 should filter all out
        resp = client.get("/mastery/u_001?min_seen=2")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_mastery_invalid_min_seen_returns_422(settings_override: Settings) -> None:
    with _client(settings_override) as client:
        resp = client.get("/mastery/u_001?min_seen=0")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd student_memory && uv run python -m pytest tests/test_api.py -v -k mastery`
Expected: FAIL with 404 Not Found (route doesn't exist yet).

- [ ] **Step 3: Add `get_repo` dependency getter**

In `student_memory/app/api/routes.py`, after `get_retriever` (line ~53):

```python
def get_repo(request: Request) -> SqliteRepo:
    return request.app.state.repo
```

(`SqliteRepo` is already imported at line 27.)

- [ ] **Step 4: Add the `/mastery/{user_id}` route**

In `student_memory/app/api/routes.py`, after the `/retrieve` route (line ~116), add:

```python
@router.get("/mastery/{user_id}", response_model=MasteryResponse)
def mastery(
    user_id: str,
    topic: str | None = None,
    since_days: int | None = None,
    min_seen: int = 1,
    repo: SqliteRepo = Depends(get_repo),
) -> MasteryResponse:
    if min_seen < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_seen must be >= 1",
        )
    if since_days is not None and since_days < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="since_days must be >= 1",
        )
    rows = repo.aggregate_mastery(
        user_id, topic=topic, since_days=since_days, min_seen=min_seen,
    )
    return MasteryResponse(
        user_id=user_id,
        items=[MasteryItem(**r) for r in rows],
    )
```

Also extend the schema imports at the top of `routes.py`:

```python
from app.api.schemas import (
    HealthResponse,
    IngestRequest,
    IngestResponse,
    MasteryItem,         # NEW
    MasteryResponse,     # NEW
    RetrievedMemory,
    RetrieveRequest,
    RetrieveResponse,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd student_memory && uv run python -m pytest tests/test_api.py -v -k mastery`
Expected: 5 tests PASS.

- [ ] **Step 6: Run full student_memory test suite for regressions**

Run: `cd student_memory && uv run python -m pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add student_memory/app/api/routes.py student_memory/tests/test_api.py
git commit -m "$(cat <<'EOF'
feat(memory): GET /mastery/{user_id} 端点 + HTTP 测试

review-flow PR-1: 暴露聚合查询. 支持 topic / since_days / min_seen 过滤.
get_repo dependency 与 get_ingestor / get_retriever 同模式, 测试可覆盖.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `fetch_mastery()` client + tests

**Files:**
- Modify: `src/memory_client.py` (add function near `retrieve_memory`)
- Test: `tests/test_memory_client.py` (append `TestFetchMastery` class)

- [ ] **Step 1: Write failing client tests**

Append to `tests/test_memory_client.py`:

```python
# ------------------------------------------------------------------
# fetch_mastery
# ------------------------------------------------------------------

class TestFetchMastery:

    @patch("src.memory_client._get_client")
    def test_happy_path(self, mock_get_client: MagicMock) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "user_id": "u1",
            "items": [
                {
                    "subtopic": "判别式", "topic": "二次方程",
                    "last_seen_at": "2026-04-15T10:00:00Z",
                    "session_count": 2, "total_questions": 4,
                    "total_correct": 2, "last_accuracy": 0.5,
                }
            ],
        }
        mock_get_client.return_value.get.return_value = mock_resp

        items = fetch_mastery("u1")
        assert len(items) == 1
        assert items[0]["subtopic"] == "判别式"

        mock_get_client.return_value.get.assert_called_once_with(
            "/mastery/u1", params={}
        )

    @patch("src.memory_client._get_client")
    def test_passes_topic_and_since_days_as_params(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"user_id": "u1", "items": []}
        mock_get_client.return_value.get.return_value = mock_resp

        fetch_mastery("u1", topic="代数", since_days=14)

        mock_get_client.return_value.get.assert_called_once_with(
            "/mastery/u1", params={"topic": "代数", "since_days": 14}
        )

    @patch("src.memory_client._get_client")
    def test_http_error_returns_empty(self, mock_get_client: MagicMock) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )
        mock_get_client.return_value.get.return_value = mock_resp

        assert fetch_mastery("u1") == []

    @patch("src.memory_client._get_client")
    def test_connection_error_returns_empty(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import fetch_mastery

        mock_get_client.return_value.get.side_effect = httpx.ConnectError(
            "refused"
        )

        assert fetch_mastery("u1") == []

    @patch("src.memory_client._get_client")
    def test_malformed_response_returns_empty(
        self, mock_get_client: MagicMock
    ) -> None:
        from src.memory_client import fetch_mastery

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"unexpected": "shape"}
        mock_get_client.return_value.get.return_value = mock_resp

        # Missing "items" key — should degrade to []
        assert fetch_mastery("u1") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_memory_client.py::TestFetchMastery -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_mastery'`

- [ ] **Step 3: Implement `fetch_mastery`**

Add to `src/memory_client.py`, after `retrieve_memory` (line ~63):

```python
# ------------------------------------------------------------------
# Mastery (review-flow)
# ------------------------------------------------------------------

def fetch_mastery(
    user_id: str,
    topic: str | None = None,
    since_days: int | None = None,
) -> list[dict[str, Any]]:
    """GET /mastery/{user_id} → list of subtopic-level mastery rows.

    Returns empty list on any failure so the caller can proceed without
    historical context (graceful degradation, same style as retrieve_memory).
    """
    params: dict[str, Any] = {}
    if topic is not None:
        params["topic"] = topic
    if since_days is not None:
        params["since_days"] = since_days

    try:
        resp = _get_client().get(f"/mastery/{user_id}", params=params)
        resp.raise_for_status()
        body = resp.json()
        items = body.get("items")
        if not isinstance(items, list):
            return []
        return items
    except httpx.HTTPError:
        logger.warning(
            "Memory service /mastery HTTP error, skipping", exc_info=True
        )
        return []
    except Exception:
        logger.error("Unexpected error in fetch_mastery", exc_info=True)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_memory_client.py::TestFetchMastery -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Run full memory_client test file for regressions**

Run: `uv run python -m pytest tests/test_memory_client.py -v`
Expected: all tests PASS (existing + 5 new).

- [ ] **Step 6: Commit**

```bash
git add src/memory_client.py tests/test_memory_client.py
git commit -m "$(cat <<'EOF'
feat(memory-client): fetch_mastery 客户端 + 单测

review-flow PR-1: 主项目侧调用 student_memory 的 /mastery 接口.
失败静默 → 返回 []. 与 retrieve_memory 同风格: 不抛异常, 调用方自行降级.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: PR-1 verification & manual smoke test

- [ ] **Step 1: Run all relevant tests**

Run two commands:
```bash
cd student_memory && uv run python -m pytest tests/ -v
cd .. && uv run python -m pytest tests/test_memory_client.py -v
```
Expected: every test PASSES; total count = (pre-existing + 7 + 5 + 5) = pre-existing + 17.

- [ ] **Step 2: Lint check**

Run:
```bash
uv run ruff check student_memory/app/ student_memory/tests/ src/memory_client.py tests/test_memory_client.py
```
Expected: no issues.

- [ ] **Step 3: Manual smoke test against running service**

Start service:
```bash
cd student_memory && uv run uvicorn app.main:app --port 8090 &
```

Wait 2 seconds, then test:
```bash
# Expect empty for unknown user
curl -s http://localhost:8090/mastery/never_seen
# → {"user_id":"never_seen","items":[]}

# Expect aggregated rows for the default user (assuming prior ingest)
curl -s http://localhost:8090/mastery/default_user | head -c 500
# → {"user_id":"default_user","items":[...]}

# Topic filter
curl -s "http://localhost:8090/mastery/default_user?topic=有理数运算"

# Stop the test server
kill %1
```

Verify responses are well-formed JSON, status 200, items list shape matches `MasteryResponse`.

- [ ] **Step 4: PR-1 final check (no commit needed)**

Inspect: `git log --oneline -5` should show four new commits on top of `2fad99d` (the spec commit):
1. `feat(memory): aggregate_mastery 仓库方法 + 7 项 TDD 测试`
2. `feat(memory): MasteryItem / MasteryResponse pydantic schemas`
3. `feat(memory): GET /mastery/{user_id} 端点 + HTTP 测试`
4. `feat(memory-client): fetch_mastery 客户端 + 单测`

PR-1 is complete and ready to be opened against `main` (or stay on `demo+memory` if working incrementally).

---

## Acceptance Checklist (from spec §11)

- [ ] `fetch_mastery` HTTP failure → `[]`, never raises (Task 4 step 4)
- [ ] `aggregate_mastery` is deterministic for same input (no LLM calls; Task 1 entire test suite)
- [ ] Subtopic IS NULL rows excluded (Task 1 Step 2 `test_excludes_null_subtopic_rows`)
- [ ] `min_seen` and `since_days` work via query params (Task 3 tests)
- [ ] Existing memory_service tests unaffected (Task 3 Step 6)
- [ ] Existing main-project tests unaffected (Task 4 Step 5)

---

## Out of Scope for PR-1

- Wiring `fetch_mastery` into any caller (PR-2)
- Adding `mastery_snapshot` to `LearningState` (PR-2)
- Frontend changes (PR-3)
- LRU pressure or cache layer on the mastery query (premature; SQLite is fast enough at this row count)
