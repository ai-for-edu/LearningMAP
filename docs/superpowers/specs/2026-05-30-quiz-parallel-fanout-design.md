# Parallel Quiz Generation — Design

**Date:** 2026-05-30
**Branch:** `main`
**Status:** Draft — pending user review

## 1. Goal & Scope

Quiz generation currently runs one LLM call per knowledge point (KC) in a serial `for` loop. For a typical knowledge graph with 5–8 KCs and ~2 questions per KC at `max_tokens=4096`, wall-clock latency is roughly `N · t` (where `t` ≈ 4–10s). This spec parallelizes that loop using the same fan-out pattern already proven in `generate_lesson_per_point_async`, while preserving the final ordering of questions and the existing best-effort failure semantics.

### In scope

- New shared core `src/agent/nodes/quiz.py::generate_quiz_per_point_async` — async fan-out, semaphore-bounded.
- Refactor existing call sites to converge on the new core:
  - `quiz_node` (sync, LangGraph entry, `quiz.py:94`) — internal `for` loop replaced by `asyncio.run(generate_quiz_per_point_async(...))`.
  - `quiz_generate_progressive` (sync generator, `quiz.py:171`) — internals rewritten as a thin queue-bridging wrapper over the async core; signature unchanged so `run_quiz_generation` does not have to change.
  - `run_quiz_generation` (async background task, `src/api/tasks.py:124`) — unchanged loop structure; gains a final post-loop re-sort step so persisted `state["questions"]` ends in KC declaration order.
- New environment variable `QUIZ_FANOUT_LIMIT` (default `4`) controlling the `asyncio.Semaphore` upper bound.
- New tests under `tests/test_quiz_parallel.py` and a small smoke test for ordered final persistence in `run_quiz_generation`.

### Out of scope

- `_priority` / `_allocate_questions_per_node` (`quiz.py:27`, `:34`): per-KC question-count weighting stays unchanged. This refactor only parallelizes the LLM call layer.
- `generate_quiz_stream` SSE endpoint (`src/api/chat.py:588`): kept functionally as-is. The frontend does not consume it (it uses `startGeneration('quiz')` + polling). The only edit is mechanical: unpack the new `(kc_idx, batch)` tuple from `quiz_generate_progressive` and forward `kc_idx` in the SSE payload (additive field; no consumer cares). Deletion is left to a follow-up cleanup PR.
- Cross-session global throttling: `task_manager` already enforces session-internal mutex of `quiz`/`lesson`/`slides`. Cross-session fan-out caps are deferred until production traffic demands it.
- Retry/backoff for failed KCs: per-KC failures log and continue (best-effort), no retry layer added.
- LLM client construction: `get_llm_client()` returns an `instructor.Instructor` over a shared `httpx.Client`; `httpx.Client` is documented as thread-safe, so reusing the singleton from `asyncio.to_thread` workers is OK and matches the lesson fan-out pattern.

## 2. Architecture

### File layout

```
src/agent/nodes/quiz.py
├── _resolve_quiz_fanout_limit(limit)              # NEW — env+default+clamp helper
├── generate_quiz_per_point_async(...)             # NEW — async fan-out core
├── quiz_node(state)                                # MODIFIED — wraps async core with asyncio.run
└── quiz_generate_progressive(state)                # MODIFIED — thin queue bridge over async core

src/api/tasks.py
└── run_quiz_generation(...)                        # MODIFIED — final re-sort to KC order

tests/test_quiz_parallel.py                         # NEW
```

### Function signatures

```python
async def generate_quiz_per_point_async(
    state: dict,
    *,
    limit: int | None = None,
    on_point_done: Callable[[int, int, list[QuizQuestion]], None] | None = None,
) -> list[QuizQuestion]:
    """Generate quiz batches concurrently across knowledge points.

    Returns the full list in KC declaration order (i.e. as nodes appear in
    state['knowledge_graph'].nodes). Per-KC failures are logged and contribute
    an empty batch — the function never raises for an LLM error.

    on_point_done(done_count, kc_index, batch) fires once per completed KC,
    with done_count monotonically increasing 1..N in completion order
    (not KC order). kc_index is the original index into nodes — the consumer
    can use it to display "[kc-name]" or to reassemble its own ordering.
    """


def _resolve_quiz_fanout_limit(limit: int | None) -> int:
    """Resolution: explicit arg > QUIZ_FANOUT_LIMIT env > 4. Clamps to >= 1."""
```

`quiz_node` and `quiz_generate_progressive` keep their public signatures — `quiz_node(state: LearningState) -> dict` and `quiz_generate_progressive(state: dict)` (sync generator). All existing callers (LangGraph, `run_quiz_generation`, the SSE endpoint) keep working with no signature changes.

### Call graph

```
LangGraph                       run_quiz_generation              generate_quiz_stream (SSE, unused by FE)
   │                                    │                                 │
   ▼                                    ▼                                 ▼
quiz_node (sync)              for batch in progressive(state):     for batch in progressive(state):
   │ asyncio.run                        │                                 │
   ▼                                    ▼                                 ▼
   └──────────── generate_quiz_per_point_async (async) ────────── quiz_generate_progressive
                              │                                      (thin queue-bridge wrapper)
                              │                                              │
                              │ ◄────────── runs the async core in a
                              │              private asyncio loop;
                              │              on_point_done pushes batches
                              │              into a queue; sync generator
                              │              drains the queue.
                              ▼
              sem = asyncio.Semaphore(_resolve_quiz_fanout_limit(limit))
              tasks = [_one_point(i, node) for i, node in enumerate(nodes)]
              results = await asyncio.gather(*tasks)   # return_exceptions=True
                              │
                              ▼
              return list[QuizQuestion] flattened in KC index order
```

## 3. Concurrency & Failure Handling

### Semaphore-bounded fan-out

```python
async def generate_quiz_per_point_async(state, *, limit=None, on_point_done=None):
    graph = state.get("knowledge_graph")
    if not graph:
        return []

    raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    nodes = [_coerce_node(n) for n in raw_nodes]
    if not nodes:
        return []

    per_node = _allocate_questions_per_node(nodes)
    skill_instructions = get_skill_section(state, "quiz")
    kc_skills_state = state.get("kc_skills") or {}
    client = get_llm_client()
    sem = asyncio.Semaphore(_resolve_quiz_fanout_limit(limit))
    done_counter = {"n": 0}

    async def _one_point(i: int, node: KnowledgeNode) -> tuple[int, list[QuizQuestion]]:
        count = per_node.get(node.name, QUESTIONS_PER_POINT)
        if count <= 0:
            return i, []

        point = {"name": node.name, "description": node.description}
        kc_skill_block = render_kc_skill_block(kc_skills_state.get(node.name)) or ""
        prompt = render_prompt(
            "quiz_generate",
            knowledge_points=[point],
            questions_per_point=count,
            skill_instructions=skill_instructions,
            kc_skill_block=kc_skill_block,
            memory_snapshot_block=memory_snapshot_block(state),
        )

        batch: list[QuizQuestion] = []
        try:
            async with sem:
                batch = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model=get_model_name(),
                        max_tokens=4096,
                        messages=[{"role": "user", "content": prompt}],
                        response_model=list[QuizQuestion],
                    )
                )
            for q in batch:
                q.knowledge_point = node.name  # pin (same reason as in serial path)
        except Exception:
            logger.exception("Failed to generate quiz for knowledge point: %s", node.name)
            batch = []

        done_counter["n"] += 1
        if on_point_done is not None:
            on_point_done(done_counter["n"], i, batch)
        return i, batch

    tasks = [asyncio.create_task(_one_point(i, n)) for i, n in enumerate(nodes)]
    results = await asyncio.gather(*tasks)  # no return_exceptions needed — _one_point swallows
    results.sort(key=lambda pair: pair[0])
    return [q for _, batch in results for q in batch]
```

`done_counter["n"]` and `on_point_done(...)` execute *after* `await asyncio.to_thread(...)` returns — i.e. back on the event-loop thread, never inside a worker thread. The event loop is single-threaded and there is no `await` between the increment and the callback invocation, so no lock is needed.

### Failure semantics — best-effort

Per-KC try/except inside `_one_point` swallows the exception, logs it via `logger.exception(...)`, and yields an empty batch. `asyncio.gather` therefore never raises for an LLM error. The function returns a list with as many questions as succeeded — possibly fewer than `sum(per_node.values())`, possibly zero. This matches today's `quiz_generate_progressive` semantics exactly (`quiz.py:215-218`: `try / except / logger.exception / no yield`).

The `on_point_done` callback also fires on the failed-batch path (with `batch=[]`), so progress counters reach `N/N` instead of stalling at `N-1/N`.

`asyncio.CancelledError` (raised when `run_quiz_generation`'s top-level task is cancelled) is **not** caught by the try/except — `CancelledError` subclasses `BaseException` in Python 3.8+, not `Exception`. Cancellation propagates correctly through the `gather`.

### `quiz_generate_progressive` — sync generator over async core

```python
def quiz_generate_progressive(state: dict):
    """Yield list[QuizQuestion] batches in completion order (not KC order)."""
    import queue as _queue
    q: _queue.Queue = _queue.Queue()
    SENTINEL = object()

    def _on_point_done(done: int, kc_idx: int, batch):
        q.put(batch)

    async def _runner():
        try:
            await generate_quiz_per_point_async(state, on_point_done=_on_point_done)
        finally:
            q.put(SENTINEL)

    def _run_in_thread():
        asyncio.run(_runner())

    import threading
    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()

    while True:
        item = q.get()
        if item is SENTINEL:
            break
        yield item
    t.join()
```

Why a thread instead of `asyncio.run` directly in the generator?
- Sync generators cannot interleave `yield` with an event loop running on the same thread without re-entering the loop. A background thread is the simplest decoupling.
- The thread is daemon-ized so an interpreter exit will not hang waiting for a stuck LLM call.
- Completion order (not KC order) is what `run_quiz_generation` writes into intermediate `state["questions"]` between polls — fine, because the final overwrite (below) re-sorts.

### `run_quiz_generation` — final re-sort

`run_quiz_generation` (`src/api/tasks.py:124`) keeps its `for batch in quiz_generate_progressive(...)` loop. The intermediate `state["questions"]` writes during the loop hold partial, completion-ordered questions (good for polling latency). After the loop completes, we re-run `generate_quiz_per_point_async`'s ordering by piggy-backing on the fact that the loop already collected all batches, then sort by KC index.

Implementation choice: change `quiz_generate_progressive` to yield `(kc_idx, batch)` tuples instead of bare `batch` lists, so the consumer can re-sort at the end. `run_quiz_generation` becomes:

```python
collected: list[tuple[int, list]] = []
for kc_idx, batch in quiz_generate_progressive(state_snapshot):
    if task and task.cancelled:
        break
    collected.append((kc_idx, batch))
    # Intermediate write: completion order, fine for polling.
    flat = [q for _, b in collected for q in b]
    state = get_session(session_id) or {}
    state["questions"] = [q.model_dump() for q in flat]
    state["quiz_task_progress"] = f"{len(collected)}/{total}"
    save_session(session_id, state)

# Final write: KC declaration order.
collected.sort(key=lambda pair: pair[0])
final_flat = [q for _, b in collected for q in b]
state = get_session(session_id) or {}
state["questions"] = [q.model_dump() for q in final_flat]
state["current_question_index"] = 0
state["answers"] = []
state["mode"] = "quiz"
state["quiz_task_status"] = "done" if not (task and task.cancelled) else None
state["quiz_task_progress"] = None
save_session(session_id, state)
```

This is a **breaking change to `quiz_generate_progressive`'s yielded shape** (`list[QuizQuestion]` → `tuple[int, list[QuizQuestion]]`). Callers:

- `src/api/tasks.py::run_quiz_generation` — updated as above.
- `src/api/chat.py::generate_quiz_stream` — SSE endpoint, currently unpacks `batch` as a plain list. Updated to unpack `(kc_idx, batch)` and include `kc_idx` in the SSE payload (frontend doesn't consume it, so this is purely a contract correction).

No other production callers (`grep -rn quiz_generate_progressive`).

### Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Default `QUIZ_FANOUT_LIMIT` | `4` | Aligns with `LESSON_FANOUT_LIMIT`. Sessions can run quiz+lesson+slides concurrently (per `task_manager`), so total in-flight LLM calls cap at ~12 — comfortable for tier-1 RPS. |
| Helper duplication | new `_resolve_quiz_fanout_limit` (not imported from lesson) | Keeps modules decoupled; helper is 6 lines; lets the two knobs evolve independently. |
| Failure mode | best-effort, per-KC | Matches existing `quiz_generate_progressive` behavior (`quiz.py:215-218`). All-or-nothing would be a behavioral regression. |
| Final question ordering | KC declaration order, sorted post-gather | Same KC index → same question position across runs. Stable for regression tests and for student UX (consecutive sessions feel coherent). |
| Intermediate ordering during polling | completion order | Maximizes time-to-first-question. The final overwrite corrects to KC order before the user transitions to the quiz screen. |
| Progress callback shape | `(done_count, kc_idx, batch)` | `done_count` for progress bars; `kc_idx` for completion-order rendering; `batch` for in-place persistence. |
| `quiz_generate_progressive` yielded shape | `(kc_idx, batch)` (breaking) | Lets the consumer reconstruct KC order without re-querying the graph. Only two production call sites — both updated. |
| Sync wrapper strategy for `quiz_node` | `asyncio.run` | `quiz_node` runs in the LangGraph sync invocation path, not inside an event loop. Same pattern as lesson's sync wrapper. |
| Cancellation strategy | native asyncio cancellation | Top-level task cancel → `CancelledError` propagates through `gather` and into `_one_point`'s `await asyncio.to_thread`. In-flight HTTP requests run to completion in the worker thread but their results are discarded. |

## 4. Testing & Compatibility

### New: `tests/test_quiz_parallel.py`

Mirror `tests/test_lesson_parallel.py`'s structure with quiz-shaped fixtures.

**`_resolve_quiz_fanout_limit` (7 cases):**

1. Explicit arg wins over env.
2. Env `QUIZ_FANOUT_LIMIT` is read when arg is `None`.
3. Default `4` when neither arg nor env set.
4. Bad env value (non-integer) falls back to default `4`.
5. Explicit `0` clamps to `1`.
6. Env `QUIZ_FANOUT_LIMIT=0` clamps to `1`.
7. Negative value clamps to `1`.

**`generate_quiz_per_point_async` behavior:**

8. **KC order preservation** — 3 KCs with delays `[0.05, 0.02, 0.0]` (KC-0 slowest). Mock returns distinct `QuizQuestion` lists. Assert returned list is `[KC-0 questions..., KC-1 questions..., KC-2 questions...]` regardless of completion order.
9. **Semaphore cap** — `limit=2`, 6 KCs, mock records `in_flight` peak via thread-safe counter. Assert peak ≤ 2 and call count == 6.
10. **`on_point_done` monotonic** — 5 KCs with reversed delays. Collect callback invocations. Assert `done_count` arguments are exactly `[1, 2, 3, 4, 5]`. Assert `kc_idx` arguments are some permutation of `[0, 1, 2, 3, 4]` (order non-deterministic).
11. **Best-effort failure** — `_create` for KC index 1 raises `RuntimeError`. Assert function does **not** raise. Assert returned list has questions only for KCs 0, 2, 3 (KC 1 contributes empty). Assert `on_point_done` was called 4 times (`done_count=1..4`), including once for the failed KC with `batch=[]`.
12. **Empty knowledge graph** — `state={"knowledge_graph": None}` → `[]`, zero LLM calls. Also test missing key.
13. **No nodes** — graph present, `nodes=[]` → `[]`, zero LLM calls.

**`quiz_generate_progressive` bridging behavior:**

14. Yields `(kc_idx, batch)` tuples in completion order with mocked delays; sum of yielded batches equals total questions.
15. SENTINEL drain: generator exits cleanly after async runner finishes (no hang).

**Smoke test for `run_quiz_generation` final ordering:**

16. New file `tests/test_quiz_background_task.py`: drive `run_quiz_generation` with a mocked LLM where KC 0 is the slowest. Use an in-memory dict as the session store via fake `get_session` / `save_session` callbacks that record every write. Assert the **final** `state["questions"]` is in KC declaration order. Assert `quiz_task_status` ends at `"done"`. Assert `quiz_task_progress` was non-None for at least one intermediate write and is `None` terminally.

### Regression protection

- `tests/test_quiz_node_weighting.py` — calls both `quiz_node` and `quiz_generate_progressive` via mocked client. With this refactor, `quiz_generate_progressive` now yields tuples instead of lists. The test at `tests/test_quiz_node_weighting.py:175` does `batches = list(quiz_generate_progressive(...))` — needs an update: either change to `batches = [b for _, b in quiz_generate_progressive(...)]` or accept the new shape. Plan call this out explicitly.
- `tests/test_mastery_annotator.py:248` references `quiz_node` for the knowledge-point pinning regression. The async core still pins `q.knowledge_point = node.name` inside `_one_point` (same line as serial). Test should pass unchanged.
- `tests/test_product_memory.py:240-244` greps for `"retrieve_memory"` in `quiz_node`'s source. The refactored `quiz_node` calls `generate_quiz_per_point_async`, which itself calls `memory_snapshot_block(state)` (which internally retrieves memory). The string `"retrieve_memory"` must still appear *somewhere* in the call chain visible to that test. Plan to either (a) keep an explicit reference comment in `quiz_node`'s body, or (b) update the test to grep `generate_quiz_per_point_async` instead. Decision deferred to plan-writing step.
- Full `uv run pytest` must be green before commit.

### Behavior change matrix (user-visible)

| Aspect | Serial (current) | Parallel (new) | User-visible? |
|---|---|---|---|
| Final `state["questions"]` order | KC declaration order | KC declaration order (sorted post-gather) | no |
| Intermediate `state["questions"]` during polling | KC order (because serial) | completion order until final write | weakly — polling student may briefly see KC-2 before KC-0 |
| `quiz_task_progress` semantics | `"i/N"` = "generating KC i" | `"k/N"` = "completed k of N" | yes — front-end label may need wording check (`static/diagnosis.html:238`) |
| Per-KC failure | logs + skips, others continue | logs + skips, others continue | no |
| Wall-clock latency for N KCs | `N · t` | `⌈N/4⌉ · t` (≈25% for N=8) | yes — much faster |
| `quiz_generate_progressive` yielded shape | `list[QuizQuestion]` | `tuple[int, list[QuizQuestion]]` | no — internal API; two callers both updated |
| Cancellation latency | next loop iteration | flight-time of in-flight requests (up to `limit` concurrent) | weakly |
| Tokens spent on cancelled tasks | up to one KC in flight | up to `limit` KCs in flight | billing-only |
