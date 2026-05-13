# Code Review — Hybrid Agent Memory PR-1 (server-side infra)

- **Date:** 2026-05-08
- **Branch:** `demo+memory`
- **Commit range:** `44f512c^..HEAD` (22 commits, 27 files, +3433 / -106)
- **Plan:** `docs/superpowers/plans/2026-05-08-hybrid-memory-pr1-server.md`
- **Spec:** `docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`
- **Reviewer:** Codex (via MCP), facilitated by Claude
- **Focus:** correctness / bug, performance, maintainability (security explicitly out of scope)

## Headline

9 findings — **6 major · 3 minor · 0 critical · 0 nitpick**. All accepted as `to_fix`. The shape of the change is sound (schema, repo, routes, snapshot, notes index all line up with the plan), but several load-bearing pieces have correctness gaps that will bite under real concurrent use or after a restart, and a few endpoints diverge from the contract PR-2 will rely on.

## Findings

### 1. POST /notes — content_hash dedup is racy (major, correctness)
**File:** `student_memory/app/api/routes.py:307-315`

`POST /notes` does check-then-insert: `repo.hash_exists_active()` followed by `repo.add_note()`. Two concurrent requests for the same `(user, content)` can both pass the check and both commit, because `notes` only carries a non-unique partial index on `(user_id, content_hash)`. This breaks the spec's "409 = race" contract and silently allows duplicate active notes.

**Fix.** Sink uniqueness into SQLite and catch `IntegrityError`:
```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_user_hash_active_unique
    ON notes(user_id, content_hash) WHERE archived = 0;
```
Wrap `repo.add_note(...)` in `try/except sqlite3.IntegrityError`; on conflict, write a `REJECT` history row (`reason="race-hash-conflict"`) and return 409.

**Disposition:** to fix.

---

### 2. `abandoned` status transition is unreachable (major, correctness)
**File:** `student_memory/app/api/routes.py:493-501`

`_next_status()` is always called with `last_seen_at=now`, so `age_days` is permanently 0. A topic last seen >30 days ago will never transition to `abandoned`, even though the state machine documents that transition.

**Fix.** Pass the previous row's `last_seen_at`:
```python
previous_seen = existing.last_seen_at if existing else now
new_status = _next_status(
    curr=existing.status if existing else None,
    mastery=payload.mastery_overall,
    last_seen_at=previous_seen,
    now=now,
)
```
Add an API test that seeds `progress_snapshots.last_seen_at = now - 31 * 86400` and asserts the next low-mastery upsert returns `abandoned`.

**Disposition:** to fix.

---

### 3. Missing `POST /history` endpoint (major, correctness)
**File:** `student_memory/app/api/routes.py:634-640`

The server only exposes `GET /history`, but the spec's reviewer contract requires `memory_client.write_history(...)` over HTTP for every ADD/UPDATE/DELETE/NOOP/REJECT decision. PR-2 has no HTTP path to record `notes_history`; the e2e smoke test currently sidesteps this by reaching into `client.app.state.repo.add_history(...)`, which the real remote caller cannot do.

**Fix.** Add a write endpoint:
```python
class HistoryCreate(BaseModel):
    note_id: int | None = None
    event: Literal["ADD", "UPDATE", "DELETE", "NOOP", "REJECT"]
    old_content: str | None = None
    new_content: str | None = None
    reason: str = Field(..., min_length=1, max_length=200)
    source_session_id: str | None = None
    source_event: SourceEvent | None = None

@router.post("/history", status_code=204)
def write_history(payload: HistoryCreate, repo: SqliteRepo = Depends(get_repo)) -> None:
    repo.add_history(**payload.model_dump(), now_ts=_now_ts())
```
Update `test_e2e_pr1.py` to drive history through the endpoint instead of `app.state.repo`.

**Disposition:** to fix.

---

### 4. `notes_hnsw.bin` has no recovery path (major, correctness)
**File:** `student_memory/app/memory/notes_index.py:63-69`

If the file is missing, stale, empty, corrupt, or built with a previous embedding model, startup either silently keeps an empty notes index or throws during `load()`. Unlike the existing misconception index, notes are never rebuilt from SQLite — so `/notes/{user_id}/neighbors` can permanently return empty for users whose rows are intact in `notes`.

**Fix.** Mirror the existing `HnswIndex` startup behavior: catch load errors, expose `count`, add `NotesIndex.rebuild_from(notes, embedder)`, and in `wire_app()` rebuild when SQLite has active notes but index count is zero. Persist an embedding-model fingerprint and rebuild when it changes.

**Disposition:** to fix.

---

### 5. `NotesIndex` never resizes before add (major, performance)
**File:** `student_memory/app/memory/notes_index.py:48-75`

Initialized with a fixed `max_elements=10000`; never resizes before `add_items()`. Deleted/updated labels still consume hnswlib capacity, and LRU only archives SQLite rows. Long-term use will exhaust the index and turn note writes/updates into 500s.

**Fix.** Resize before adding, mirroring `HnswIndex.add_batch()`:
```python
with self._lock:
    current = self._index.get_current_count()
    cap = self._index.get_max_elements()
    if current + 1 > cap:
        self._index.resize_index(max(current + 1, cap * 2))
    self._index.add_items(vec.reshape(1, -1), np.array([note_id]))
```
Investigate whether the installed hnswlib version supports reusing deleted slots.

**Disposition:** to fix.

---

### 6. `/errors/{user_id}` applies filters after limit (major, performance)
**File:** `student_memory/app/api/routes.py:599-610`

The handler fetches the latest `limit` rows for the user, then applies `op` and `since` filters in Python. If the latest 100 rows are unrelated, a matching older row is dropped even when it satisfies the requested filter. SQLite also can't use op/timestamp indexes effectively.

**Fix.** Push filters into SQL and validate `limit` bounds:
```python
def list_errors_for_user(self, user_id: str, *, op: str | None, since: int | None, limit: int):
    sql = "SELECT * FROM op_errors WHERE user_id = ?"
    params = [user_id]
    if op is not None:
        sql += " AND op = ?"; params.append(op)
    if since is not None:
        sql += " AND ts >= ?"; params.append(since)
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"; params.append(limit)
```
Add a composite index `(user_id, op, ts DESC)` if `op` filtering is expected to be hot.

**Disposition:** to fix.

---

### 7. `list_weak_mastery()` ties on second-level ts (minor, correctness)
**File:** `student_memory/app/db/sqlite_repo.py:638-653`

Selecting by `MAX(ts)` while `/progress` writes second-level timestamps means two updates for the same `(user, topic, knowledge_point)` in the same second can both have the max ts, producing duplicate KP rows in the snapshot — and possibly surfacing an older score as "current."

**Fix.** Break ties by `id` with a window query:
```sql
WITH ranked AS (
  SELECT mc.*,
         ROW_NUMBER() OVER (
           PARTITION BY user_id, topic, knowledge_point
           ORDER BY ts DESC, id DESC
         ) AS rn
  FROM mastery_curve mc
  WHERE user_id = ?
)
SELECT *
FROM ranked
WHERE rn = 1 AND score < ?
ORDER BY score ASC;
```
Add a regression test that inserts two rows for the same key at the same ts and asserts the higher-id row wins.

**Disposition:** to fix.

---

### 8. Whitespace-only content passes validation (minor, correctness)
**File:** `student_memory/app/api/schemas.py:114-119`

Spec requires "非纯空白", but `NoteCreate.content` and `NotePatch.content` accept `"   "`. After `_content_hash(...strip())` it hashes as the empty string, polluting dedup.

**Fix.** Shared Pydantic v2 validator:
```python
from pydantic import field_validator

@field_validator("content")
@classmethod
def content_must_not_be_blank(cls, v: str) -> str:
    if not v.strip():
        raise ValueError("content must not be blank")
    return v
```
Apply on both `NoteCreate` and `NotePatch`. Test that `POST` / `PATCH` with `"   "` returns 422.

**Disposition:** to fix.

---

### 9. `GET /notes/by-id/{note_id}` doesn't match reviewer contract (minor, maintainability)
**File:** `student_memory/app/api/routes.py:333-348`

Spec says by-id reads return `user_id` and `archived`; the route returns the same `NoteOut` used by list responses, silently dropping both. Archived notes are returned without flagging them, so audit / update / delete callers reason from incomplete state.

**Fix.** Add a detail response model:
```python
class NoteDetailOut(NoteOut):
    user_id: str
    archived: bool
    source_session_id: str | None = None
    source_event: str
```
Return it from `get_note_by_id()`; map `archived=bool(note.archived)`. Update `test_api_notes.py` to assert the new fields.

**Disposition:** to fix.

---

## Disposition summary

| # | Severity | File | Disposition |
|---|---|---|---|
| 1 | major | `routes.py:307-315` | to fix |
| 2 | major | `routes.py:493-501` | to fix |
| 3 | major | `routes.py:634-640` | to fix |
| 4 | major | `notes_index.py:63-69` | to fix |
| 5 | major | `notes_index.py:48-75` | to fix |
| 6 | major | `routes.py:599-610` | to fix |
| 7 | minor | `sqlite_repo.py:638-653` | to fix |
| 8 | minor | `schemas.py:114-119` | to fix |
| 9 | minor | `routes.py:333-348` | to fix |

Skipped: 0. Won't-fix: 0.
