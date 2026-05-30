# Parallel Quiz Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Converge three quiz-generation entry points (`quiz_node`, `quiz_generate_progressive`, `run_quiz_generation`) onto a single async fan-out core that generates per-KC question batches concurrently (default 4 in-flight). Final `state["questions"]` stays in KC declaration order; per-KC failures stay best-effort (logged, others continue).

**Architecture:** New `generate_quiz_per_point_async` in `src/agent/nodes/quiz.py` mirrors the existing `generate_lesson_per_point_async` pattern (`asyncio.Semaphore` + `asyncio.to_thread` + KC-index sort post-`gather`). `quiz_node` wraps it with `asyncio.run`. `quiz_generate_progressive` bridges async→sync via a `queue.Queue` + daemon thread; its yielded shape changes from `list[QuizQuestion]` to `tuple[int, list[QuizQuestion]]`. `run_quiz_generation` consumes the new tuple shape and re-sorts to KC order before the final state write. SSE endpoint gets a mechanical unpack fix.

**Tech Stack:** Python 3.11+, `asyncio`, `queue`, `threading`, `pytest`, `instructor` over OpenAI SDK (sync client wrapped in `asyncio.to_thread`).

**Spec:** `docs/superpowers/specs/2026-05-30-quiz-parallel-fanout-design.md`

---

## File Structure

**Modified:**
- `src/agent/nodes/quiz.py` — adds `_resolve_quiz_fanout_limit`, adds `generate_quiz_per_point_async`, rewrites `quiz_node` body to call async core, rewrites `quiz_generate_progressive` as a sync queue-bridge over the async core (and changes its yield shape to `(kc_idx, batch)`).
- `src/api/tasks.py::run_quiz_generation` (lines 124-176) — updates the `for batch in quiz_generate_progressive(...)` loop to unpack the new tuple, then re-sorts collected batches by KC index before the final state write.
- `src/api/chat.py::generate_quiz_stream` (lines 588-636) — updates the loop to unpack the new tuple; adds `kc_idx` to the SSE event payload (additive; no consumer cares).
- `tests/test_quiz_node_weighting.py:175` — updates `batches = list(quiz_generate_progressive(graph))` call site to handle the new tuple shape.

**Created:**
- `tests/test_quiz_parallel.py` — unit tests for `_resolve_quiz_fanout_limit` and `generate_quiz_per_point_async`.
- `tests/test_quiz_background_task.py` — end-to-end smoke test for `run_quiz_generation` with mocked LLM, verifying final KC-order persistence.

**Out of scope (do NOT touch this plan):**
- `_priority`, `_allocate_questions_per_node`, `_coerce_node` in `src/agent/nodes/quiz.py` — per-KC weighting logic unchanged.
- `tests/test_product_memory.py::TestReadCoverage::test_R4_quiz_node_reads` — already failing on `main` before this refactor (grep for `retrieve_memory` in `quiz_node` source; current source uses `memory_snapshot_block` instead). Not introduced by this work; fix belongs in a separate PR.
- `static/diagnosis.html:238` progress copy reads `Question 1 / ?` and is computed from `allQuestions.length`, not from the `quiz_task_progress` string — no front-end change needed.

---

## Task 1: Add `_resolve_quiz_fanout_limit` helper

**Files:**
- Modify: `src/agent/nodes/quiz.py` (top of module, after the existing constants)
- Test: `tests/test_quiz_parallel.py` (new file)

- [ ] **Step 1: Create the test file with helper tests**

Create `tests/test_quiz_parallel.py`:

```python
"""Tests for the parallel quiz fan-out helper."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _resolve_quiz_fanout_limit
# ---------------------------------------------------------------------------

def test_resolve_quiz_fanout_limit_explicit_arg_wins(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "7")
    assert _resolve_quiz_fanout_limit(2) == 2


def test_resolve_quiz_fanout_limit_env_var(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "6")
    assert _resolve_quiz_fanout_limit(None) == 6


def test_resolve_quiz_fanout_limit_default(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.delenv("QUIZ_FANOUT_LIMIT", raising=False)
    assert _resolve_quiz_fanout_limit(None) == 4


def test_resolve_quiz_fanout_limit_bad_env_falls_back(monkeypatch):
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "not-a-number")
    assert _resolve_quiz_fanout_limit(None) == 4


def test_resolve_quiz_fanout_limit_clamps_zero(monkeypatch):
    """Zero must clamp to 1 — otherwise asyncio.Semaphore(0) would deadlock."""
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.delenv("QUIZ_FANOUT_LIMIT", raising=False)
    assert _resolve_quiz_fanout_limit(0) == 1


def test_resolve_quiz_fanout_limit_clamps_env_zero(monkeypatch):
    """Env QUIZ_FANOUT_LIMIT=0 must also clamp to 1."""
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.setenv("QUIZ_FANOUT_LIMIT", "0")
    assert _resolve_quiz_fanout_limit(None) == 1


def test_resolve_quiz_fanout_limit_clamps_negative(monkeypatch):
    """Negative values must also clamp to 1."""
    from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
    monkeypatch.delenv("QUIZ_FANOUT_LIMIT", raising=False)
    assert _resolve_quiz_fanout_limit(-5) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_quiz_parallel.py -v`

Expected: 7 errors / failures with `ImportError: cannot import name '_resolve_quiz_fanout_limit' from 'src.agent.nodes.quiz'`.

- [ ] **Step 3: Add the helper to `src/agent/nodes/quiz.py`**

Add this near the top of `src/agent/nodes/quiz.py`, right after the existing imports and before the `QUESTIONS_PER_POINT` constant (so it sits around line 14, between `logger = logging.getLogger(__name__)` and `QUESTIONS_PER_POINT = 2`):

```python
import os  # add to the existing imports at the top if not present


def _resolve_quiz_fanout_limit(arg: int | None) -> int:
    """Resolve the quiz fan-out concurrency limit.

    Precedence: explicit `arg` > env var `QUIZ_FANOUT_LIMIT` > default 4.
    A non-integer env value silently falls back to the default.
    Values < 1 are clamped to 1 to prevent asyncio.Semaphore(0) deadlock.
    """
    if arg is not None:
        return max(1, arg)
    env_val = os.environ.get("QUIZ_FANOUT_LIMIT")
    if env_val:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    return 4
```

Note: `src/agent/nodes/quiz.py` does not currently import `os` — add `import os` to the top-of-module imports block. The existing `from __future__ import annotations` line must stay first.

- [ ] **Step 4: Run the helper tests to verify they pass**

Run: `uv run pytest tests/test_quiz_parallel.py -v`

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agent/nodes/quiz.py tests/test_quiz_parallel.py
git commit -m "feat(quiz): add _resolve_quiz_fanout_limit helper

Mirror of lesson's helper. Reads QUIZ_FANOUT_LIMIT env, default 4,
clamps to >= 1. Standalone helper (not imported from lesson) so the
two knobs can evolve independently.
"
```

---

## Task 2: Add `generate_quiz_per_point_async` core

**Files:**
- Modify: `src/agent/nodes/quiz.py` (add new async function near end of module)
- Test: `tests/test_quiz_parallel.py` (append cases)

- [ ] **Step 1: Add empty-input tests**

Append to `tests/test_quiz_parallel.py`:

```python
# ---------------------------------------------------------------------------
# generate_quiz_per_point_async — empty / degenerate inputs
# ---------------------------------------------------------------------------

def test_async_returns_empty_when_no_graph():
    """state has no knowledge_graph at all."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    result = asyncio.run(generate_quiz_per_point_async({}))
    assert result == []


def test_async_returns_empty_when_graph_is_none():
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    result = asyncio.run(generate_quiz_per_point_async({"knowledge_graph": None}))
    assert result == []


def test_async_returns_empty_when_nodes_list_empty():
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    graph = MagicMock()
    graph.nodes = []
    result = asyncio.run(generate_quiz_per_point_async({"knowledge_graph": graph}))
    assert result == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_quiz_parallel.py -v -k "async"`

Expected: 3 errors with `ImportError: cannot import name 'generate_quiz_per_point_async'`.

- [ ] **Step 3: Add a minimal stub for `generate_quiz_per_point_async`**

Append to `src/agent/nodes/quiz.py` (at the end of the file, after `quiz_generate_progressive`):

```python
async def generate_quiz_per_point_async(
    state: dict,
    *,
    limit: int | None = None,
    on_point_done=None,
) -> list:
    """Generate quiz batches concurrently across knowledge points.

    Returns the full list of QuizQuestion in KC declaration order
    (i.e. as nodes appear in state['knowledge_graph'].nodes). Per-KC
    failures are logged and contribute an empty batch — the function
    never raises for an LLM error.

    on_point_done(done_count, kc_index, batch) fires once per completed
    KC, with done_count monotonically increasing 1..N in completion
    order (not KC order). kc_index is the original index into nodes —
    the consumer can use it to display "[kc-name]" or to reassemble
    its own ordering.
    """
    graph = state.get("knowledge_graph")
    if not graph:
        return []
    raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    if not raw_nodes:
        return []
    # Full fan-out arrives in Step 5.
    return []
```

- [ ] **Step 4: Run empty-input tests, verify pass**

Run: `uv run pytest tests/test_quiz_parallel.py -v -k "async"`

Expected: 3 passed.

- [ ] **Step 5: Add the full behavior tests**

Append to `tests/test_quiz_parallel.py`:

```python
# ---------------------------------------------------------------------------
# generate_quiz_per_point_async — full behavior
# ---------------------------------------------------------------------------

def _make_kg_state(num_nodes: int) -> dict:
    """Build a state dict whose knowledge_graph has N named KCs."""
    from src.llm.schemas import KnowledgeNode
    nodes = [
        KnowledgeNode(name=f"kc-{i}", description=f"desc-{i}", mastery=None)
        for i in range(num_nodes)
    ]
    graph = MagicMock()
    graph.nodes = nodes
    return {"knowledge_graph": graph, "kc_skills": {}}


def _make_mock_question(kc_name: str, text: str):
    """Build a mock QuizQuestion-like object the async core can re-tag."""
    from src.llm.schemas import QuizQuestion
    return QuizQuestion(
        knowledge_point=kc_name,
        question=text,
        options=["A", "B", "C", "D"],
        correct_answer="A",
        explanation="x",
    )


def _patch_quiz_client(side_effect):
    """Install a mock instructor client at src.agent.nodes.quiz.get_llm_client.

    side_effect is the function called for each .chat.completions.create(...).
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = side_effect
    return patch("src.agent.nodes.quiz.get_llm_client", return_value=mock_client), mock_client


def test_async_preserves_kc_order_when_completions_finish_out_of_order():
    """KC-0 sleeps longest, KC-2 finishes first; final list is still KC-0, KC-1, KC-2."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    import time

    delays = [0.05, 0.02, 0.0]
    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        # quiz core re-pins knowledge_point to the KG node name, so the
        # name we put here is irrelevant — text disambiguates per call.
        return [_make_mock_question("paraphrased", f"Q-from-call-{i}")]

    state = _make_kg_state(3)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        result = asyncio.run(generate_quiz_per_point_async(state))

    # Order of LLM calls matches enumeration order over nodes (call 0 → KC-0,
    # call 1 → KC-1, call 2 → KC-2) because each coroutine builds its prompt
    # before awaiting. Completion order is the reverse of declaration. Final
    # list must still be KC-0 question, KC-1 question, KC-2 question.
    assert [q.knowledge_point for q in result] == ["kc-0", "kc-1", "kc-2"]
    assert [q.question for q in result] == [
        "Q-from-call-0",
        "Q-from-call-1",
        "Q-from-call-2",
    ]


def test_async_caps_concurrency_at_limit():
    """limit=2 and 6 KCs → peak concurrent in-flight calls must be <= 2."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    import threading
    import time

    in_flight = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def _create(**kwargs):
        with lock:
            in_flight["now"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        time.sleep(0.02)
        with lock:
            in_flight["now"] -= 1
        return [_make_mock_question("x", "q")]

    state = _make_kg_state(6)
    ctx, mock_client = _patch_quiz_client(_create)
    with ctx:
        asyncio.run(generate_quiz_per_point_async(state, limit=2))

    assert in_flight["peak"] <= 2, f"peak {in_flight['peak']} exceeded limit 2"
    assert mock_client.chat.completions.create.call_count == 6


def test_async_on_point_done_monotonic_done_count():
    """on_point_done is called N times with done_count = 1..N (any kc_idx order)."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async
    import time

    N = 5
    delays = [0.01 * (N - i) for i in range(N)]  # KC-0 slowest
    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        return [_make_mock_question("x", f"q-{i}")]

    callbacks = []

    def _on_done(done_count, kc_index, batch):
        callbacks.append((done_count, kc_index, len(batch)))

    state = _make_kg_state(N)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        asyncio.run(
            generate_quiz_per_point_async(state, on_point_done=_on_done)
        )

    done_counts = [c[0] for c in callbacks]
    kc_indices = [c[1] for c in callbacks]
    batch_sizes = [c[2] for c in callbacks]
    assert done_counts == [1, 2, 3, 4, 5]
    assert sorted(kc_indices) == [0, 1, 2, 3, 4]
    assert all(s == 1 for s in batch_sizes)


def test_async_best_effort_on_single_kc_failure():
    """KC-1 raises; KC-0, KC-2, KC-3 succeed; function returns N-1 questions, no raise."""
    from src.agent.nodes.quiz import generate_quiz_per_point_async

    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        if i == 1:
            raise RuntimeError("synthetic LLM failure")
        return [_make_mock_question("x", f"q-{i}")]

    callbacks = []

    def _on_done(done_count, kc_index, batch):
        callbacks.append((done_count, kc_index, len(batch)))

    state = _make_kg_state(4)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        result = asyncio.run(
            generate_quiz_per_point_async(state, on_point_done=_on_done)
        )

    # Function does NOT raise. Returns questions for KC-0, KC-2, KC-3.
    assert [q.knowledge_point for q in result] == ["kc-0", "kc-2", "kc-3"]
    # Callback fires N times including the failed KC (with batch=[]).
    assert len(callbacks) == 4
    done_counts = [c[0] for c in callbacks]
    assert done_counts == [1, 2, 3, 4]
    # Exactly one callback has an empty batch — the failed KC.
    failed = [c for c in callbacks if c[2] == 0]
    assert len(failed) == 1
    assert failed[0][1] == 1  # kc_index of the failure
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_quiz_parallel.py -v -k "preserves or caps or monotonic or best_effort"`

Expected: 4 failures (the stub returns `[]` for everything).

- [ ] **Step 7: Replace the stub with the full implementation**

Replace the `generate_quiz_per_point_async` function in `src/agent/nodes/quiz.py` with the full version. Also add the required imports at the top of the file: `import asyncio` and update the `_helpers` / `prompts.loader` / `skills.loader` imports (they're already present from the serial code path — verify before adding). Final function body:

```python
async def generate_quiz_per_point_async(
    state: dict,
    *,
    limit: int | None = None,
    on_point_done=None,
) -> list:
    """Generate quiz batches concurrently across knowledge points.

    Returns the full list of QuizQuestion in KC declaration order
    (i.e. as nodes appear in state['knowledge_graph'].nodes). Per-KC
    failures are logged and contribute an empty batch — the function
    never raises for an LLM error.

    on_point_done(done_count, kc_index, batch) fires once per completed
    KC, with done_count monotonically increasing 1..N in completion
    order (not KC order). kc_index is the original index into nodes.
    """
    graph = state.get("knowledge_graph")
    if not graph:
        return []
    raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    if not raw_nodes:
        return []

    nodes = [_coerce_node(n) for n in raw_nodes]
    per_node = _allocate_questions_per_node(nodes)
    skill_instructions = get_skill_section(state, "quiz")
    kc_skills_state = state.get("kc_skills") or {}
    client = get_llm_client()
    sem = asyncio.Semaphore(_resolve_quiz_fanout_limit(limit))
    done_counter = {"n": 0}

    async def _one_point(i: int, node: KnowledgeNode):
        count = per_node.get(node.name, QUESTIONS_PER_POINT)
        if count <= 0:
            batch: list[QuizQuestion] = []
        else:
            point = {"name": node.name, "description": node.description}
            kc_skill_block = render_kc_skill_block(
                kc_skills_state.get(node.name)
            ) or ""
            prompt = render_prompt(
                "quiz_generate",
                knowledge_points=[point],
                questions_per_point=count,
                skill_instructions=skill_instructions,
                kc_skill_block=kc_skill_block,
                memory_snapshot_block=memory_snapshot_block(state),
            )
            try:
                async with sem:
                    batch = await asyncio.to_thread(
                        lambda p=prompt: client.chat.completions.create(
                            model=get_model_name(),
                            max_tokens=4096,
                            messages=[{"role": "user", "content": p}],
                            response_model=list[QuizQuestion],
                        )
                    )
                # Pin knowledge_point to the KG node name. The LLM has been
                # observed to paraphrase or append the description into this
                # field, which then becomes the subtopic key in memory and
                # breaks cross-session matching.
                for q in batch:
                    q.knowledge_point = node.name
            except Exception:
                logger.exception(
                    "Failed to generate quiz for knowledge point: %s", node.name
                )
                batch = []

        done_counter["n"] += 1
        if on_point_done is not None:
            on_point_done(done_counter["n"], i, batch)
        return i, batch

    tasks = [asyncio.create_task(_one_point(i, n)) for i, n in enumerate(nodes)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda pair: pair[0])
    return [q for _, batch in results for q in batch]
```

Also add `import asyncio` to the top of `src/agent/nodes/quiz.py` if not already present (verify the existing import block — it currently does NOT import `asyncio`).

- [ ] **Step 8: Run all tests in the new file**

Run: `uv run pytest tests/test_quiz_parallel.py -v`

Expected: 13 passed (7 helper + 3 empty-input + 3 full-behavior).

Wait — 4 full-behavior tests, not 3. Total should be 14 passed. If the count is wrong, the test list at the top of Task 2 step 5 lists: preserves_kc_order, caps_concurrency, on_point_done_monotonic, best_effort_on_single_kc_failure → 4 tests. Plus 3 empty-input + 7 helper = 14.

- [ ] **Step 9: Commit**

```bash
git add src/agent/nodes/quiz.py tests/test_quiz_parallel.py
git commit -m "feat(quiz): add generate_quiz_per_point_async core

Async fan-out for per-KC quiz generation. Semaphore-bounded
(QUIZ_FANOUT_LIMIT, default 4). Best-effort: single KC failure
logs and contributes an empty batch; gather never raises for an
LLM error. Final list is in KC declaration order via
sort-by-original-index post-gather. on_point_done(done_count,
kc_index, batch) callback fires monotonically in completion order.
Not yet wired into quiz_node / progressive / background task.
"
```

---

## Task 3: Refactor `quiz_node` to use the async core

**Files:**
- Modify: `src/agent/nodes/quiz.py::quiz_node` (lines 94-153, the generation branch)
- Verify: `tests/test_quiz_node_weighting.py::test_quiz_node_overwrites_paraphrased_kp_name` and `test_mastery_annotator.py:248` (regression coverage)

- [ ] **Step 1: Verify current quiz_node tests pass before refactor**

Run: `uv run pytest tests/test_quiz_node_weighting.py tests/test_mastery_annotator.py -v`

Note which tests pass. Goal: identical set passes after the refactor. Record any pre-existing failures so we don't blame them on this work.

- [ ] **Step 2: Rewrite the generation branch in `quiz_node`**

In `src/agent/nodes/quiz.py`, replace the body of `quiz_node` from line ~99 (the `if not questions:` block) through the inner question-loop. Specifically, replace this region:

```python
    if not questions:
        graph = state.get("knowledge_graph")
        if not graph:
            return {
                "mode": "chat",
                "messages": [AIMessage(content="Please select a learning topic first.")],
            }

        raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        nodes = [_coerce_node(n) for n in raw_nodes]
        per_node = _allocate_questions_per_node(nodes)

        skill_instructions = get_skill_section(state, "quiz")
        kc_skills_state = state.get("kc_skills") or {}

        all_generated: list[QuizQuestion] = []
        client = get_llm_client()
        for node in nodes:
            count = per_node.get(node.name, QUESTIONS_PER_POINT)
            if count <= 0:
                continue
            point = {"name": node.name, "description": node.description}
            block = render_kc_skill_block(kc_skills_state.get(node.name))
            kc_skill_block = block or ""
            prompt = render_prompt(
                "quiz_generate",
                knowledge_points=[point],
                questions_per_point=count,
                skill_instructions=skill_instructions,
                kc_skill_block=kc_skill_block,
                memory_snapshot_block=memory_snapshot_block(state),
            )
            batch = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                response_model=list[QuizQuestion],
            )
            # Pin knowledge_point to the KG node name. The LLM has been
            # observed to paraphrase or append the description into this
            # field (e.g. "multiplication principle: explanation of step-by-step counting..."),
            # which then becomes the subtopic key in memory and breaks cross-session matching.
            for q in batch:
                q.knowledge_point = node.name
            all_generated.extend(batch)

        return {
            "questions": all_generated,
            "current_question_index": 0,
            "answers": [],
            "mode": "quiz",
            "messages": [
                AIMessage(content=f"{len(all_generated)} questions ready. Let's begin!")
            ],
        }
```

with:

```python
    if not questions:
        graph = state.get("knowledge_graph")
        if not graph:
            return {
                "mode": "chat",
                "messages": [AIMessage(content="Please select a learning topic first.")],
            }

        all_generated = asyncio.run(generate_quiz_per_point_async(state))
        return {
            "questions": all_generated,
            "current_question_index": 0,
            "answers": [],
            "mode": "quiz",
            "messages": [
                AIMessage(content=f"{len(all_generated)} questions ready. Let's begin!")
            ],
        }
```

Reasoning: empty-graph short-circuit stays here (it returns a user-facing chat message, not just `[]`). Everything below — node coercion, weighting, prompt, LLM call, knowledge_point pinning — moves into the async core. The async core already handles `graph is None` and empty `nodes` by returning `[]`, but the chat-message branch needs to stay in `quiz_node` for the user-facing nudge.

- [ ] **Step 3: Run the existing quiz_node tests**

Run: `uv run pytest tests/test_quiz_node_weighting.py tests/test_mastery_annotator.py -v`

Expected: same set as Step 1 passes. If a test fails that previously passed, it's a regression — investigate before continuing.

Common gotcha: `tests/test_quiz_node_weighting.py` patches `src.agent.nodes.quiz.get_llm_client`. The async core also reads from this same name, so the patch still hits. The patch also relies on `client.chat.completions.create` being called once per node — the async core calls it the same way (just concurrently). Both should still pass.

- [ ] **Step 4: Run the full test suite as a regression gate**

Run: `uv run pytest -x 2>&1 | tail -30`

Expected: same set of failures as before (notably `test_R4_quiz_node_reads` still fails — pre-existing). If anything new fails, fix or revert.

- [ ] **Step 5: Commit**

```bash
git add src/agent/nodes/quiz.py
git commit -m "refactor(quiz): wire quiz_node to async fan-out core

LangGraph entry point now generates per-KC batches in parallel via
asyncio.run(generate_quiz_per_point_async(state)). User-facing
empty-graph chat message stays in quiz_node; everything else
delegates to the shared core. No new tests — covered by existing
test_quiz_node_weighting + test_mastery_annotator.
"
```

---

## Task 4: Refactor `quiz_generate_progressive` to bridge async core

**Files:**
- Modify: `src/agent/nodes/quiz.py::quiz_generate_progressive` (lines 171-218)
- Modify: `tests/test_quiz_node_weighting.py:175` (consumer of the generator)
- Test: `tests/test_quiz_parallel.py` (append bridging tests)

- [ ] **Step 1: Add bridging tests**

Append to `tests/test_quiz_parallel.py`:

```python
# ---------------------------------------------------------------------------
# quiz_generate_progressive — sync queue-bridge over async core
# ---------------------------------------------------------------------------

def test_progressive_yields_kc_idx_batch_tuples():
    """New shape: yields (kc_idx, list[QuizQuestion]) per completed KC."""
    from src.agent.nodes.quiz import quiz_generate_progressive
    import time

    delays = [0.03, 0.0, 0.02]  # KC-1 finishes first, then KC-2, then KC-0
    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        return [_make_mock_question("x", f"q-{i}")]

    state = _make_kg_state(3)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        items = list(quiz_generate_progressive(state))

    # Three tuples, one per KC.
    assert len(items) == 3
    for item in items:
        assert isinstance(item, tuple)
        assert len(item) == 2
        kc_idx, batch = item
        assert isinstance(kc_idx, int)
        assert isinstance(batch, list)

    # kc_idx covers 0..N-1, in completion order (not KC declaration order).
    kc_indices = [k for k, _ in items]
    assert sorted(kc_indices) == [0, 1, 2]
    # Combined batches contain one question per KC.
    all_qs = [q for _, b in items for q in b]
    assert len(all_qs) == 3


def test_progressive_completes_when_async_runner_finishes():
    """Generator must terminate (not hang) once all KCs are done."""
    from src.agent.nodes.quiz import quiz_generate_progressive

    def _create(**kwargs):
        return [_make_mock_question("x", "q")]

    state = _make_kg_state(2)
    ctx, _ = _patch_quiz_client(_create)
    with ctx:
        # If this hangs, the SENTINEL drain logic is broken.
        items = list(quiz_generate_progressive(state))

    assert len(items) == 2
```

- [ ] **Step 2: Run the new tests, verify they fail**

Run: `uv run pytest tests/test_quiz_parallel.py::test_progressive_yields_kc_idx_batch_tuples tests/test_quiz_parallel.py::test_progressive_completes_when_async_runner_finishes -v`

Expected: 2 failures — the current `quiz_generate_progressive` yields bare lists (not tuples) and is still the serial implementation.

- [ ] **Step 3: Replace `quiz_generate_progressive` with the queue bridge**

In `src/agent/nodes/quiz.py`, replace the entire `quiz_generate_progressive` function (lines 171-218) with:

```python
def quiz_generate_progressive(state: dict):
    """Yield (kc_idx, list[QuizQuestion]) tuples in COMPLETION order.

    Generates per-KC batches concurrently via generate_quiz_per_point_async.
    The async core runs on a private event loop inside a daemon thread;
    completed batches are pushed into a queue that this sync generator
    drains. Yield shape is (kc_idx, batch) so the consumer can re-sort
    back to KC declaration order at the end.
    """
    import queue as _queue
    import threading as _threading

    q: _queue.Queue = _queue.Queue()
    SENTINEL = object()

    def _on_point_done(done_count, kc_idx, batch):
        q.put((kc_idx, batch))

    async def _runner():
        try:
            await generate_quiz_per_point_async(state, on_point_done=_on_point_done)
        finally:
            q.put(SENTINEL)

    def _run_in_thread():
        asyncio.run(_runner())

    t = _threading.Thread(target=_run_in_thread, daemon=True)
    t.start()
    try:
        while True:
            item = q.get()
            if item is SENTINEL:
                break
            yield item
    finally:
        t.join()
```

The `try/finally` around the consumer loop guarantees the thread is joined even if the consumer breaks early (e.g., on cancellation in `run_quiz_generation`). The thread is daemon so an interpreter exit won't hang on a stuck LLM call.

- [ ] **Step 4: Run the bridging tests, verify pass**

Run: `uv run pytest tests/test_quiz_parallel.py::test_progressive_yields_kc_idx_batch_tuples tests/test_quiz_parallel.py::test_progressive_completes_when_async_runner_finishes -v`

Expected: 2 passed.

- [ ] **Step 5: Fix the call site in `tests/test_quiz_node_weighting.py`**

Look at `tests/test_quiz_node_weighting.py` around line 175 — the current line is:

```python
            batches = list(quiz_generate_progressive(graph))
```

Replace with:

```python
            # quiz_generate_progressive now yields (kc_idx, batch) tuples.
            batches = [batch for _kc_idx, batch in quiz_generate_progressive(graph)]
```

This preserves the existing assertion semantics: `batches` is still a list of `list[QuizQuestion]`, indexable as before.

If the test also asserts anything about KC ordering of yielded batches (the serial version yielded in KC declaration order; the new version yields in completion order), the test must be updated. Inspect the assertions starting at line 175 and adjust. Specifically: if there is any assertion like `assert batches[0][0].knowledge_point == "first-kc"`, change to a set-based assertion: `assert {b[0].knowledge_point for b in batches} == {"kc-0", "kc-1", ...}`.

For mocked LLMs running with no artificial delay, completion order ≈ submission order ≈ declaration order in practice (asyncio runs each coroutine to its first `await` before context-switching), so most existing assertions will still pass. But don't rely on that — make the assertion explicitly order-insensitive when the test was checking ordering.

- [ ] **Step 6: Run the quiz weighting tests**

Run: `uv run pytest tests/test_quiz_node_weighting.py -v`

Expected: same passing set as before this PR. Any new failures → revisit Step 5.

- [ ] **Step 7: Commit**

```bash
git add src/agent/nodes/quiz.py tests/test_quiz_parallel.py tests/test_quiz_node_weighting.py
git commit -m "refactor(quiz): bridge progressive generator over async core

quiz_generate_progressive is now a thin sync wrapper that drives
generate_quiz_per_point_async on a daemon thread and drains a
queue. Yields (kc_idx, batch) tuples in completion order (was:
bare batch in KC order). Two consumers — run_quiz_generation and
generate_quiz_stream — updated in follow-up commits. Test call
site in test_quiz_node_weighting updated to unpack the tuple.
"
```

---

## Task 5: Update `run_quiz_generation` for new tuple shape + final re-sort

**Files:**
- Modify: `src/api/tasks.py::run_quiz_generation` (lines 124-176)
- Test: `tests/test_quiz_background_task.py` (new file)

- [ ] **Step 1: Write the smoke test FIRST**

Create `tests/test_quiz_background_task.py`:

```python
"""End-to-end smoke test for run_quiz_generation with parallel fan-out."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch


def _make_kg_state(num_nodes: int) -> dict:
    """Build a minimal state dict that run_quiz_generation can consume."""
    from src.llm.schemas import KnowledgeNode
    nodes = [
        KnowledgeNode(name=f"kc-{i}", description=f"desc-{i}", mastery=None)
        for i in range(num_nodes)
    ]
    graph = MagicMock()
    graph.nodes = nodes
    return {"knowledge_graph": graph, "kc_skills": {}}


def _make_question(kc_name: str, text: str):
    from src.llm.schemas import QuizQuestion
    return QuizQuestion(
        knowledge_point=kc_name,
        question=text,
        options=["A", "B", "C", "D"],
        correct_answer="A",
        explanation="x",
    )


def test_run_quiz_generation_final_state_is_kc_ordered_even_when_kc0_slowest():
    """run_quiz_generation must persist state['questions'] in KC declaration order.

    KC-0 completes last; intermediate writes will show completion-order
    questions (which is fine for polling latency), but the FINAL state
    must be re-sorted to KC order.
    """
    from src.api.tasks import run_quiz_generation

    delays = [0.03, 0.0, 0.0]  # KC-0 slowest, KC-1 + KC-2 instant

    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        # Use the call index as the question text so we can tell which
        # call produced which question.
        return [_make_question(f"will-be-pinned-to-kc-{i}", f"Q-from-call-{i}")]

    session_id = "test-quiz-bg-1"
    state_snapshot = _make_kg_state(3)

    # In-memory session store.
    store: dict[str, dict] = {session_id: dict(state_snapshot)}
    writes: list[dict] = []

    def _get_session(sid):
        return dict(store.get(sid, {}))

    def _save_session(sid, s):
        store[sid] = dict(s)
        writes.append(dict(s))

    # run_quiz_generation handles task_manager.get_task() returning None
    # via `if task and task.cancelled:` — no registration needed.

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = _create

    with patch("src.agent.nodes.quiz.get_llm_client", return_value=mock_client):
        asyncio.run(
            run_quiz_generation(
                session_id=session_id,
                state_snapshot=state_snapshot,
                get_session=_get_session,
                save_session=_save_session,
            )
        )

    final = store[session_id]
    assert final["quiz_task_status"] == "done"
    assert final["quiz_task_progress"] is None
    assert final["mode"] == "quiz"
    assert final["current_question_index"] == 0
    assert final["answers"] == []

    # Crucial: final questions are in KC declaration order. The async core
    # pins q.knowledge_point to the KG node name (kc-0, kc-1, kc-2), so we
    # assert on knowledge_point, not on question text.
    assert [q["knowledge_point"] for q in final["questions"]] == [
        "kc-0", "kc-1", "kc-2",
    ]

    # Progress was non-None at least once during the run.
    intermediate_progress = [w.get("quiz_task_progress") for w in writes]
    assert any(p is not None for p in intermediate_progress)
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `uv run pytest tests/test_quiz_background_task.py -v`

Expected: Either an unpacking error (current loop does `for batch in ...` but new generator yields tuples) OR a wrong-order assertion failure (intermediate write order is completion order, and there's no final re-sort yet).

- [ ] **Step 3: Update `run_quiz_generation` for the new tuple shape + final re-sort**

In `src/api/tasks.py`, replace the body of `run_quiz_generation` (currently lines 124-176) — specifically the section starting at the `for batch in quiz_generate_progressive(state_snapshot):` loop and through the final state write. Replace this block:

```python
        all_questions = []
        nodes = state_snapshot.get("knowledge_graph")
        if nodes:
            nodes = nodes.nodes if hasattr(nodes, "nodes") else nodes.get("nodes", [])
        total = len(nodes) if nodes else 0

        for batch in quiz_generate_progressive(state_snapshot):
            if task and task.cancelled:
                logger.info("Quiz generation cancelled for %s", session_id)
                break

            all_questions.extend(batch)
            point_idx = len(all_questions) // 2  # ~2 questions per point

            # Save intermediate results
            state = get_session(session_id) or {}
            state["questions"] = [q.model_dump() for q in all_questions]
            state["quiz_task_progress"] = f"{point_idx}/{total}"
            save_session(session_id, state)

        # Finalize
        state = get_session(session_id) or {}
        state["questions"] = [q.model_dump() for q in all_questions]
        state["current_question_index"] = 0
        state["answers"] = []
        state["mode"] = "quiz"
        state["quiz_task_status"] = "done" if not (task and task.cancelled) else None
        state["quiz_task_progress"] = None
        save_session(session_id, state)
```

with:

```python
        nodes = state_snapshot.get("knowledge_graph")
        if nodes:
            nodes = nodes.nodes if hasattr(nodes, "nodes") else nodes.get("nodes", [])
        total = len(nodes) if nodes else 0

        # Collected as (kc_idx, batch) in completion order; re-sorted at end.
        collected: list[tuple[int, list]] = []

        for kc_idx, batch in quiz_generate_progressive(state_snapshot):
            if task and task.cancelled:
                logger.info("Quiz generation cancelled for %s", session_id)
                break
            collected.append((kc_idx, batch))

            # Intermediate write: completion order. Good for polling latency;
            # final write below corrects to KC declaration order.
            flat = [q for _, b in collected for q in b]
            state = get_session(session_id) or {}
            state["questions"] = [q.model_dump() for q in flat]
            state["quiz_task_progress"] = f"{len(collected)}/{total}"
            save_session(session_id, state)

        # Finalize: re-sort to KC declaration order before the terminal write.
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

Two real changes:
1. Loop unpacks `(kc_idx, batch)` instead of `batch`.
2. Final write rebuilds `state["questions"]` from `collected` sorted by `kc_idx`. (Removed the heuristic `point_idx = len(all_questions) // 2` — `len(collected)` is the exact count of completed KCs.)

- [ ] **Step 4: Run the smoke test, verify it passes**

Run: `uv run pytest tests/test_quiz_background_task.py -v`

Expected: 1 passed.

- [ ] **Step 5: Run the wider quiz test suite as a regression gate**

Run: `uv run pytest tests/test_quiz_parallel.py tests/test_quiz_node_weighting.py tests/test_quiz_background_task.py -v`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/api/tasks.py tests/test_quiz_background_task.py
git commit -m "feat(quiz): parallel fan-out for run_quiz_generation

Consume (kc_idx, batch) tuples from quiz_generate_progressive.
Intermediate state['questions'] writes stay in completion order
for polling latency; final write re-sorts to KC declaration order.
Smoke test asserts final ordering when KC-0 is slowest. Default
QUIZ_FANOUT_LIMIT=4 → typical 5-8 KC quiz finishes in ~25%
wall-clock of the serial version.
"
```

---

## Task 6: Update SSE endpoint for new tuple shape

**Files:**
- Modify: `src/api/chat.py::generate_quiz_stream` (lines 588-636)

- [ ] **Step 1: Inspect the current SSE handler**

Read `src/api/chat.py` lines 588-636 to confirm the current loop shape. It iterates `for batch in quiz_generate_progressive(state):` and emits SSE events of `{"type": "questions", "batch": ..., "point_index": point_idx, "total_points": total_points}`.

- [ ] **Step 2: Update the loop to unpack the tuple and forward `kc_index`**

In `src/api/chat.py`, find the loop inside `generate_quiz_stream`'s `stream_sse` (around line 612):

```python
        for batch in quiz_generate_progressive(state):
            questions_data = [q.model_dump() for q in batch]
            all_questions.extend(batch)
            point_idx += 1
            yield _sse_event(
                {
                    "type": "questions",
                    "batch": questions_data,
                    "point_index": point_idx,
                    "total_points": total_points,
                }
            )
```

Replace with:

```python
        for kc_index, batch in quiz_generate_progressive(state):
            questions_data = [q.model_dump() for q in batch]
            all_questions.extend(batch)
            point_idx += 1
            yield _sse_event(
                {
                    "type": "questions",
                    "batch": questions_data,
                    "point_index": point_idx,
                    "kc_index": kc_index,  # additive field
                    "total_points": total_points,
                }
            )
```

The `state["questions"] = all_questions` save block AFTER the loop already exists at lines ~625-629; it persists in completion order. Since this endpoint is not consumed by the frontend (per spec), do NOT add the final re-sort here — the SSE is a streaming protocol where order represents arrival order. If a future consumer needs KC order, they can use `kc_index` to reassemble.

- [ ] **Step 3: Run any existing SSE coverage**

Run: `uv run pytest -v -k "quiz_stream or generate_quiz_stream" 2>&1 | tail -10`

Expected: no tests collected (the endpoint is uncovered) OR existing tests pass. Either is fine — this is a mechanical contract change with no frontend consumer.

- [ ] **Step 4: Full pytest as final regression gate**

Run: `uv run pytest 2>&1 | tail -20`

Expected: same set of failures as before this PR (notably `test_R4_quiz_node_reads` — pre-existing). No new failures.

- [ ] **Step 5: Commit**

```bash
git add src/api/chat.py
git commit -m "chore(quiz): SSE endpoint unpacks new (kc_idx, batch) tuple

generate_quiz_stream now unpacks the tuple shape from
quiz_generate_progressive and forwards kc_index as an additive
SSE field. No consumer change (frontend uses polling not SSE).
Streaming preserves arrival order; consumers wanting KC order
can reassemble using kc_index.
"
```

---

## Task 7: Verify final state and update development_log

**Files:**
- Read: `development_log.md` (root)
- Modify: `development_log.md` (append a brief entry)

- [ ] **Step 1: Verify the green test surface**

Run: `uv run pytest 2>&1 | tail -30`

Expected: all tests passing except the pre-existing failures recorded in Task 3 Step 1. Specifically, `test_R4_quiz_node_reads` should still fail with the same message as before — that's not introduced by this PR.

- [ ] **Step 2: Quick smoke check — confirm `_resolve_quiz_fanout_limit` reads env at runtime**

Run:
```bash
QUIZ_FANOUT_LIMIT=2 uv run python -c "
from src.agent.nodes.quiz import _resolve_quiz_fanout_limit
print('resolved:', _resolve_quiz_fanout_limit(None))
assert _resolve_quiz_fanout_limit(None) == 2
print('OK')
"
```

Expected output:
```
resolved: 2
OK
```

- [ ] **Step 3: Append a brief entry to `development_log.md`**

Open `development_log.md` and append a new dated section. Match the existing format (look at the most recent entry for the style). The entry should mention:
- Date (2026-05-30)
- What changed (quiz generation now fans out per-KC)
- Knob (`QUIZ_FANOUT_LIMIT`, default 4)
- Why (latency: serial `N · t` → bounded `⌈N/limit⌉ · t`)

Keep it under 5 lines. Match the project's existing log style.

- [ ] **Step 4: Final commit**

```bash
git add development_log.md
git commit -m "docs(log): record quiz parallel fan-out change"
```

- [ ] **Step 5: Confirm git log shape**

Run: `git log --oneline -8`

Expected: 6 new commits on top of the spec commit (`16afe16`):
1. `feat(quiz): add _resolve_quiz_fanout_limit helper`
2. `feat(quiz): add generate_quiz_per_point_async core`
3. `refactor(quiz): wire quiz_node to async fan-out core`
4. `refactor(quiz): bridge progressive generator over async core`
5. `feat(quiz): parallel fan-out for run_quiz_generation`
6. `chore(quiz): SSE endpoint unpacks new (kc_idx, batch) tuple`
7. `docs(log): record quiz parallel fan-out change`

(7 commits total this PR. Spec commit was pre-PR.)

---

## Self-Review Notes (already applied)

**Spec coverage check:** Every requirement in `docs/superpowers/specs/2026-05-30-quiz-parallel-fanout-design.md` Section 1-4 maps to at least one task above. The deferred decision in spec Section 4 about `test_R4_quiz_node_reads` is resolved: pre-existing failure, out of scope, documented in "Out of scope" at the top of this plan.

**Type consistency check:**
- `on_point_done(done_count, kc_index, batch)` signature used identically in Task 2 (implementation) and Task 4 (the bridge wraps it).
- Generator yield shape `(kc_idx, batch)` used identically in Task 4 (implementation), Task 5 (run_quiz_generation consumer), and Task 6 (SSE consumer).
- `_resolve_quiz_fanout_limit` name used identically in Task 1 (definition) and Task 2 (consumption inside `generate_quiz_per_point_async`).

**Placeholder scan:** No TBD / TODO / "implement later" markers. Every code step shows complete code. Every test step shows complete test code.
