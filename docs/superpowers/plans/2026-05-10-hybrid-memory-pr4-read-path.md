# Hybrid Agent Memory PR-4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire PR-3's frozen `memory_snapshot` into 4 agent nodes' prompts (chat / knowledge_graph / quiz / lesson), and inject misconception notes into the review path via real-time fetch. Add a `≥10`-char threshold to chat's `retrieve_memory` cleanup.

**Architecture:** A single `memory_snapshot_block(state) -> str` helper in `src/agent/nodes/_helpers.py` returns either `""` (env=off / no snapshot / failure) or `"\n\n<snapshot>"`. The 5 j2 templates append `{{- memory_snapshot_block }}` (leading `-` strips preceding newline so empty var → byte-identical to pre-PR-4). chat node uses Python concatenation (`system_prompt += memory_snapshot_block(state)`) because it has stream variant + runtime decisions. review path is the only env-gated fetch (others rely on PR-3 having frozen the field).

**Tech Stack:** Python 3.11, Jinja2 with `keep_trailing_newline=True`, pytest-asyncio strict mode, FastAPI, langchain.

**Spec:** `docs/superpowers/specs/2026-05-10-hybrid-memory-pr4-read-path.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/agent/nodes/_helpers.py` | **create** | `memory_snapshot_block(state) -> str` |
| `src/agent/nodes/chat.py` | modify | `chat_node` + `chat_node_stream`: append snapshot block; gate `retrieve_memory` on `≥10` chars |
| `src/agent/nodes/knowledge_graph.py` | modify | `_llm_generate_graph`: pass `memory_snapshot_block(state)` to render_prompt |
| `src/agent/nodes/quiz.py` | modify | 2 render_prompt sites pass kwarg |
| `src/agent/nodes/lesson.py` | modify | 6 render_prompt sites pass kwarg; helpers `generate_lesson_per_point` + `_generate_review_lesson` accept `state` |
| `src/api/tasks.py` | modify | `run_lesson_generation`: 1 render_prompt site passes kwarg; helper call passes `state=state_snapshot` |
| `src/agent/nodes/review.py` | modify | `generate_review_questions`: env-gated `fetch_active_notes('misconception')`, pass to render_prompt |
| `src/prompts/knowledge_graph.j2` | modify | Append `{{- memory_snapshot_block }}` |
| `src/prompts/quiz_generate.j2` | modify | Append `{{- memory_snapshot_block }}` |
| `src/prompts/lesson_plan.j2` | modify | Append `{{- memory_snapshot_block }}` |
| `src/prompts/lesson_point.j2` | modify | Append `{{- memory_snapshot_block }}` |
| `src/prompts/lesson_review.j2` | modify | Append `{{- memory_snapshot_block }}` |
| `src/prompts/review_generate_system.j2` | modify | Add `{% if misconceptions %}...{% endif %}` soft-guidance section |
| `tests/test_snapshot_injection.py` | **create** | Unit tests for helper, integration tests for env=on injection paths |
| `tests/test_baseline_prompts.py` | **create** | env=off byte-identity tests for 5 j2 templates + review template |

Total: 14 files (3 created, 11 modified). ~70 lines source + ~250 lines tests = ~320 lines.

---

## Task 1: Create `memory_snapshot_block` helper + unit tests

**Files:**
- Create: `src/agent/nodes/_helpers.py`
- Create: `tests/test_snapshot_injection.py`

- [ ] **Step 1: Create the test file with helper unit tests**

Create `tests/test_snapshot_injection.py`:

```python
"""PR-4 snapshot injection tests.

Covers:
- memory_snapshot_block helper (this task)
- per-template snapshot injection (Tasks 2-4)
- chat node snapshot + retrieve_memory threshold (Task 5)
- review path misconception injection (Task 6)
"""
from __future__ import annotations

import pytest

from src.agent.nodes._helpers import memory_snapshot_block


SAMPLE_SNAPSHOT = "## 该学生的学习进程\n- 已开始：勾股定理（3天前）"


# ---------------------------------------------------------------------------
# memory_snapshot_block helper
# ---------------------------------------------------------------------------


def test_helper_returns_empty_for_missing_field():
    """state without memory_snapshot key → empty string."""
    assert memory_snapshot_block({}) == ""


def test_helper_returns_empty_for_none_state():
    """state=None → empty string (defensive for callers passing None)."""
    assert memory_snapshot_block(None) == ""


def test_helper_returns_empty_for_none_value():
    """memory_snapshot=None → empty string (env=off path)."""
    assert memory_snapshot_block({"memory_snapshot": None}) == ""


def test_helper_returns_empty_for_empty_string():
    """memory_snapshot='' → empty string (fetch failure / empty payload)."""
    assert memory_snapshot_block({"memory_snapshot": ""}) == ""


def test_helper_returns_empty_for_whitespace():
    """memory_snapshot='  \\n  ' → empty string after strip."""
    assert memory_snapshot_block({"memory_snapshot": "  \n  "}) == ""


def test_helper_returns_block_with_leading_separator():
    """Non-empty snapshot returns with \\n\\n prefix for direct append."""
    out = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    assert out == f"\n\n{SAMPLE_SNAPSHOT}"


def test_helper_strips_leading_trailing_whitespace_in_content():
    """Content with surrounding whitespace is stripped before block render."""
    out = memory_snapshot_block({"memory_snapshot": "\n\n  ## X  \n\n"})
    assert out == "\n\n## X"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_snapshot_injection.py -v
```

Expected: 7 FAILS — `ModuleNotFoundError: src.agent.nodes._helpers`.

- [ ] **Step 3: Create the helper**

Create `src/agent/nodes/_helpers.py`:

```python
"""Shared helpers for agent nodes (PR-4)."""
from __future__ import annotations

from src.agent.state import LearningState


def memory_snapshot_block(state: LearningState | None) -> str:
    """Return the rendered memory snapshot block, or empty string.

    Empty when state is None / lacks the field / value is None / empty /
    whitespace — covering env=off (PR-3's _ensure_memory_snapshot is a
    no-op), fetch failure (field is ""), old sessions (field absent),
    and helper functions in lesson.py that accept state defaulting to
    None. Non-empty: returns a string with a leading double-newline so
    callers can append directly to a system prompt or drop into a Jinja
    template at the end. Env state is NOT checked here — it's encoded
    by PR-3 in the state field's value.
    """
    if not state:
        return ""
    snap = (state.get("memory_snapshot") or "").strip()
    if not snap:
        return ""
    return f"\n\n{snap}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_snapshot_injection.py -v
```

Expected: 7 PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/agent/nodes/_helpers.py tests/test_snapshot_injection.py
git add src/agent/nodes/_helpers.py tests/test_snapshot_injection.py
git commit -m "feat(memory-pr4): add memory_snapshot_block helper"
```

---

## Task 2: Inject snapshot into knowledge_graph node + template

**Files:**
- Modify: `src/prompts/knowledge_graph.j2`
- Modify: `src/agent/nodes/knowledge_graph.py:74-83` (the single `render_prompt` call)
- Create: `tests/test_baseline_prompts.py`
- Modify: `tests/test_snapshot_injection.py` (append injection test)

- [ ] **Step 1: Capture pre-modification baseline**

Run this to capture the env=off baseline (no snapshot var):

```bash
uv run python -c "
from src.prompts.loader import render_prompt
out = render_prompt(
    'knowledge_graph',
    topic='勾股定理',
    max_nodes=8,
    skill_instructions='',
    focus_directive='',
    review_weak_kps=[],
    review_strong_kps=[],
    existing_mastery=[],
)
print(repr(out))
"
```

Save the printed `repr(out)` string — you'll paste it as `EXPECTED_KG_BASELINE` in Step 4 below.

- [ ] **Step 2: Append `{{- memory_snapshot_block }}` to template**

Edit `src/prompts/knowledge_graph.j2`. The current last 3 lines are:

```jinja
3. Edges show prerequisite relationships (source → target means source is prerequisite of target)
4. Include 4-{{ max_nodes }} nodes covering prerequisites and core concepts

Return a structured knowledge graph.
```

Append immediately after `Return a structured knowledge graph.` (no extra blank line):

```jinja
Return a structured knowledge graph.
{{- memory_snapshot_block }}
```

The `{{-` leading dash strips the preceding newline when var is empty, preserving byte-identity. When var is non-empty (it has its own `\n\n` prefix), output is `Return a structured knowledge graph.\n\n## snap\n`.

- [ ] **Step 3: Pass kwarg from python**

Edit `src/agent/nodes/knowledge_graph.py`. Add import and modify the `render_prompt` call (around line 74):

```python
from src.agent.nodes._helpers import memory_snapshot_block

# ... existing code ...

prompt = render_prompt(
    "knowledge_graph",
    topic=topic,
    max_nodes=MAX_NODES,
    skill_instructions=skill_instructions,
    focus_directive=focus_directive,
    review_weak_kps=review_weak_kps,
    review_strong_kps=review_strong_kps,
    existing_mastery=existing_mastery or [],
    memory_snapshot_block=memory_snapshot_block(state),
)
```

- [ ] **Step 4: Create baseline test**

Create `tests/test_baseline_prompts.py`:

```python
"""PR-4 baseline byte-identity tests.

Each test renders a j2 template with empty memory_snapshot_block and
asserts the output exactly equals the captured pre-PR-4 baseline string.
The whitespace control `{{- memory_snapshot_block }}` in templates ensures
empty var produces byte-identical output to pre-PR-4.

Regenerate baselines with:
  uv run python -c "from src.prompts.loader import render_prompt; \\
                    print(repr(render_prompt('<name>', **kwargs)))"
"""
from __future__ import annotations

from src.prompts.loader import render_prompt


# Captured from pre-PR-4 knowledge_graph.j2 with empty var; regenerate if
# the template content (other than {{- memory_snapshot_block }}) ever changes.
EXPECTED_KG_BASELINE = (
    # PASTE the repr() output from Task 2 Step 1 here, removing outer quotes
    # Example: 'You are an educational knowledge graph builder...'
    EXPECTED_KG_BASELINE_PLACEHOLDER
)


def test_knowledge_graph_baseline_env_off():
    out = render_prompt(
        "knowledge_graph",
        topic="勾股定理",
        max_nodes=8,
        skill_instructions="",
        focus_directive="",
        review_weak_kps=[],
        review_strong_kps=[],
        existing_mastery=[],
        memory_snapshot_block="",
    )
    assert out == EXPECTED_KG_BASELINE
```

> **Implementation note**: The `EXPECTED_KG_BASELINE_PLACEHOLDER` literal will fail Python parsing — that's intentional. Replace it with the repr-captured string from Step 1. Remove the outer single quotes and convert escape sequences as needed (or wrap as `"""..."""` triple-quoted raw string if multiline is easier).

- [ ] **Step 5: Append injection test**

Append to `tests/test_snapshot_injection.py`:

```python
# ---------------------------------------------------------------------------
# Per-template snapshot injection
# ---------------------------------------------------------------------------


def test_knowledge_graph_template_injects_snapshot():
    """When memory_snapshot_block is non-empty, it appears in rendered output."""
    from src.prompts.loader import render_prompt

    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "knowledge_graph",
        topic="X",
        max_nodes=8,
        skill_instructions="",
        focus_directive="",
        review_weak_kps=[],
        review_strong_kps=[],
        existing_mastery=[],
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out
    assert "## 该学生的学习进程" in out
```

- [ ] **Step 6: Run all tests**

```bash
uv run python -m pytest tests/test_baseline_prompts.py tests/test_snapshot_injection.py -v
```

Expected: 7 helper tests (Task 1) + 1 baseline + 1 injection = 9 PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/agent/nodes/knowledge_graph.py tests/test_baseline_prompts.py tests/test_snapshot_injection.py
git add src/prompts/knowledge_graph.j2 src/agent/nodes/knowledge_graph.py tests/test_baseline_prompts.py tests/test_snapshot_injection.py
git commit -m "feat(memory-pr4): inject memory_snapshot_block into knowledge_graph"
```

---

## Task 3: Inject snapshot into quiz node + template

**Files:**
- Modify: `src/prompts/quiz_generate.j2`
- Modify: `src/agent/nodes/quiz.py:122` and `:193` (2 `render_prompt` sites)
- Modify: `tests/test_baseline_prompts.py` (append baseline test)
- Modify: `tests/test_snapshot_injection.py` (append injection test)

- [ ] **Step 1: Capture pre-modification baseline**

```bash
uv run python -c "
from src.prompts.loader import render_prompt
out = render_prompt(
    'quiz_generate',
    skill_instructions='',
    kc_skill_block='',
    knowledge_points=[{'name': 'KP1', 'description': 'desc1'}],
    questions_per_point=2,
)
print(repr(out))
"
```

Save the output for Step 4.

- [ ] **Step 2: Append to template**

Edit `src/prompts/quiz_generate.j2`. The current last line is `Return a list of questions.\n` (with trailing newline). Append:

```jinja
Return a list of questions.
{{- memory_snapshot_block }}
```

- [ ] **Step 3: Pass kwarg from both render_prompt sites**

Edit `src/agent/nodes/quiz.py`. Add import:

```python
from src.agent.nodes._helpers import memory_snapshot_block
```

Find both `render_prompt("quiz_generate", ...)` calls (around line 122 and line 193). Each likely has a kwargs block like:

```python
prompt = render_prompt(
    "quiz_generate",
    skill_instructions=skill_instructions,
    kc_skill_block=kc_skill_block,
    knowledge_points=knowledge_points,
    questions_per_point=questions_per_point,
)
```

Add `memory_snapshot_block=memory_snapshot_block(state)` as the last kwarg in both calls.

- [ ] **Step 4: Append baseline test**

Append to `tests/test_baseline_prompts.py`:

```python
EXPECTED_QUIZ_BASELINE = (
    # PASTE repr from Task 3 Step 1 here, parsed appropriately
    EXPECTED_QUIZ_BASELINE_PLACEHOLDER
)


def test_quiz_generate_baseline_env_off():
    out = render_prompt(
        "quiz_generate",
        skill_instructions="",
        kc_skill_block="",
        knowledge_points=[{"name": "KP1", "description": "desc1"}],
        questions_per_point=2,
        memory_snapshot_block="",
    )
    assert out == EXPECTED_QUIZ_BASELINE
```

- [ ] **Step 5: Append injection test**

Append to `tests/test_snapshot_injection.py`:

```python
def test_quiz_generate_template_injects_snapshot():
    from src.prompts.loader import render_prompt

    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "quiz_generate",
        skill_instructions="",
        kc_skill_block="",
        knowledge_points=[{"name": "KP1", "description": "desc1"}],
        questions_per_point=2,
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out
```

- [ ] **Step 6: Run all tests**

```bash
uv run python -m pytest tests/test_baseline_prompts.py tests/test_snapshot_injection.py -v
```

Expected: 11 PASS (9 prior + 1 baseline + 1 injection).

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/agent/nodes/quiz.py tests/
git add src/prompts/quiz_generate.j2 src/agent/nodes/quiz.py tests/test_baseline_prompts.py tests/test_snapshot_injection.py
git commit -m "feat(memory-pr4): inject memory_snapshot_block into quiz_generate"
```

---

## Task 4: Inject snapshot into lesson templates + nodes (3 templates, 7 sites)

**Files:**
- Modify: `src/prompts/lesson_plan.j2`
- Modify: `src/prompts/lesson_point.j2`
- Modify: `src/prompts/lesson_review.j2`
- Modify: `src/agent/nodes/lesson.py` (6 `render_prompt` sites + 2 helper signatures)
- Modify: `src/api/tasks.py:237` (1 `render_prompt` site + 1 `_generate_review_lesson` call site at :220)
- Modify: `tests/test_baseline_prompts.py` (3 baseline tests)
- Modify: `tests/test_snapshot_injection.py` (3 injection tests)

This is the largest task. Lots of touch points but mechanically simple.

- [ ] **Step 1: Capture 3 baselines**

```bash
uv run python -c "
from src.prompts.loader import render_prompt
print('=== lesson_plan ===')
print(repr(render_prompt('lesson_plan',
    skill_instructions='', focus_directive='', topic='X',
    weak_points=[{'name': 'WP1', 'description': 'd1'}])))
print('=== lesson_point ===')
print(repr(render_prompt('lesson_point',
    topic='X', knowledge_point_name='KP1', knowledge_point_description='d',
    pedagogy_instructions='', knowledge_skill='', point_index=1, total_points=1,
    kc_skill_block='', focus_directive='')))
print('=== lesson_review ===')
print(repr(render_prompt('lesson_review',
    topic='X', points=[{'name':'P1','description':'d'}], skill_instructions='')))
"
```

Save all three outputs for Step 6.

- [ ] **Step 2: Append `{{- memory_snapshot_block }}` to all 3 templates**

Edit each of `src/prompts/lesson_plan.j2`, `src/prompts/lesson_point.j2`, `src/prompts/lesson_review.j2`. At the very end of each file, append (no extra blank line):

```jinja
{{- memory_snapshot_block }}
```

(Each template's existing last line ends with `\n`; the `{{-` strips that newline when var is empty.)

- [ ] **Step 3: Modify `lesson.py` helper signatures**

Edit `src/agent/nodes/lesson.py`. Add import at top:

```python
from src.agent.nodes._helpers import memory_snapshot_block
```

Modify `generate_lesson_per_point` signature (around line 129) to accept `state`:

```python
def generate_lesson_per_point(
    topic: str,
    weak_points: list[dict],
    pedagogy_instructions: str,
    knowledge_skills: dict[str, str],
    kc_skills: dict | None = None,
    focus_directive: str = "",
    state: dict | None = None,
) -> str:
```

Modify `_generate_review_lesson` signature (around line 166):

```python
def _generate_review_lesson(
    topic: str,
    nodes: list,
    skill_instructions: str,
    state: dict | None = None,
) -> str:
```

- [ ] **Step 4: Add `memory_snapshot_block` kwarg at all 6 lesson.py render_prompt sites**

The 6 sites in `lesson.py` (verified by `grep -n "render_prompt(" src/agent/nodes/lesson.py`):

1. Line ~144 (`lesson_point`, inside `generate_lesson_per_point`):
   ```python
   prompt = render_prompt(
       "lesson_point",
       topic=topic,
       # ... existing kwargs ...
       focus_directive=focus_directive,
       memory_snapshot_block=memory_snapshot_block(state),
   )
   ```
   (Helper accepts `None` and returns `""`, so no `or {}` guard needed.)

2. Line ~177 (`lesson_review`, inside `_generate_review_lesson`):
   ```python
   prompt = render_prompt(
       "lesson_review",
       topic=topic,
       points=all_points,
       skill_instructions=skill_instructions,
       memory_snapshot_block=memory_snapshot_block(state),
   )
   ```

3. Line ~267 (`lesson_review`, inside `_build_lesson_prompt(state)`):
   ```python
   return render_prompt(
       "lesson_review",
       topic=topic,
       points=all_points,
       skill_instructions=skill_instructions,
       memory_snapshot_block=memory_snapshot_block(state),
   )
   ```

4. Line ~284 (`lesson_plan`, inside `_build_lesson_prompt(state)`):
   ```python
   return render_prompt(
       "lesson_plan",
       topic=topic,
       weak_points=weak_points,
       skill_instructions=skill_instructions,
       focus_directive=focus_directive,
       memory_snapshot_block=memory_snapshot_block(state),
   )
   ```

5. Line ~318 (`lesson_review`, inside `lesson_node_stream(state)`):
   ```python
   prompt = render_prompt(
       "lesson_review",
       topic=topic,
       points=all_points,
       skill_instructions=skill_instructions,
       memory_snapshot_block=memory_snapshot_block(state),
   )
   ```

6. Line ~347 (`lesson_point`, inside `lesson_node_stream(state)`):
   ```python
   prompt = render_prompt(
       "lesson_point",
       topic=topic,
       # ... existing kwargs ...
       focus_directive=focus_directive,
       memory_snapshot_block=memory_snapshot_block(state),
   )
   ```

- [ ] **Step 5: Update `lesson_node` callers to pass state**

Edit `src/agent/nodes/lesson.py`. In `lesson_node` (around line 192), update the two helper invocations:

```python
# Around line 207
lesson_text = _generate_review_lesson(topic, nodes, pedagogy_instructions, state=state)

# Around line 217
lesson_text = generate_lesson_per_point(
    topic, weak_points, pedagogy_instructions, knowledge_skills,
    kc_skills=state.get("kc_skills"),
    focus_directive=focus_directive,
    state=state,
)
```

- [ ] **Step 6: Update `tasks.py` (1 render_prompt + 1 helper call)**

Edit `src/api/tasks.py`. The `run_lesson_generation` async function has:

a) Around line 220, `_generate_review_lesson` call — pass state via kwarg (`asyncio.to_thread` accepts both positional args and kwargs):

```python
lesson_text = await asyncio.to_thread(
    _generate_review_lesson,
    state_snapshot.get("topic", ""),
    nodes,
    pedagogy_instructions,
    state=state_snapshot,
)
```

b) Around line 237, the `render_prompt("lesson_point", ...)` call — add kwarg:

```python
from src.agent.nodes._helpers import memory_snapshot_block

prompt = render_prompt(
    "lesson_point",
    topic=state_snapshot.get("topic", ""),
    knowledge_point_name=wp["name"],
    knowledge_point_description=wp["description"],
    pedagogy_instructions=pedagogy_instructions or "无特定教学法要求",
    knowledge_skill=knowledge_skills.get(wp["name"], "无额外教学技能"),
    point_index=i + 1,
    total_points=total,
    focus_directive=focus_directive,
    memory_snapshot_block=memory_snapshot_block(state_snapshot),
)
```

The import `from src.agent.nodes._helpers import memory_snapshot_block` should be at the top of the function body alongside the existing `from src.agent.nodes.lesson import retrieve_skills, _generate_review_lesson` (function-body import to follow tasks.py's circular-avoidance pattern).

- [ ] **Step 7: Append baseline tests**

Append to `tests/test_baseline_prompts.py`:

```python
EXPECTED_LESSON_PLAN_BASELINE = LESSON_PLAN_PLACEHOLDER  # paste from Step 1
EXPECTED_LESSON_POINT_BASELINE = LESSON_POINT_PLACEHOLDER  # paste from Step 1
EXPECTED_LESSON_REVIEW_BASELINE = LESSON_REVIEW_PLACEHOLDER  # paste from Step 1


def test_lesson_plan_baseline_env_off():
    out = render_prompt(
        "lesson_plan",
        skill_instructions="",
        focus_directive="",
        topic="X",
        weak_points=[{"name": "WP1", "description": "d1"}],
        memory_snapshot_block="",
    )
    assert out == EXPECTED_LESSON_PLAN_BASELINE


def test_lesson_point_baseline_env_off():
    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="KP1",
        knowledge_point_description="d",
        pedagogy_instructions="",
        knowledge_skill="",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="",
        memory_snapshot_block="",
    )
    assert out == EXPECTED_LESSON_POINT_BASELINE


def test_lesson_review_baseline_env_off():
    out = render_prompt(
        "lesson_review",
        topic="X",
        points=[{"name": "P1", "description": "d"}],
        skill_instructions="",
        memory_snapshot_block="",
    )
    assert out == EXPECTED_LESSON_REVIEW_BASELINE
```

- [ ] **Step 8: Append injection tests**

Append to `tests/test_snapshot_injection.py`:

```python
def test_lesson_plan_template_injects_snapshot():
    from src.prompts.loader import render_prompt
    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "lesson_plan",
        skill_instructions="",
        focus_directive="",
        topic="X",
        weak_points=[{"name": "WP1", "description": "d1"}],
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out


def test_lesson_point_template_injects_snapshot():
    from src.prompts.loader import render_prompt
    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="KP1",
        knowledge_point_description="d",
        pedagogy_instructions="",
        knowledge_skill="",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="",
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out


def test_lesson_review_template_injects_snapshot():
    from src.prompts.loader import render_prompt
    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "lesson_review",
        topic="X",
        points=[{"name": "P1", "description": "d"}],
        skill_instructions="",
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out
```

- [ ] **Step 9: Run all tests**

```bash
uv run python -m pytest tests/test_baseline_prompts.py tests/test_snapshot_injection.py -v
```

Expected: 17 PASS (11 prior + 3 baseline + 3 injection).

Also verify lesson regression tests don't break:

```bash
uv run python -m pytest tests/test_lesson_pipeline.py -v 2>&1 | tail -20
```

Expected: same pass/fail counts as parent commit.

- [ ] **Step 10: Lint and commit**

```bash
uv run ruff check src/agent/nodes/lesson.py src/api/tasks.py tests/
git add src/prompts/lesson_plan.j2 src/prompts/lesson_point.j2 src/prompts/lesson_review.j2
git add src/agent/nodes/lesson.py src/api/tasks.py
git add tests/test_baseline_prompts.py tests/test_snapshot_injection.py
git commit -m "feat(memory-pr4): inject memory_snapshot_block into lesson templates"
```

---

## Task 5: chat node — snapshot block + retrieve_memory threshold

**Files:**
- Modify: `src/agent/nodes/chat.py:70-113` (`chat_node`) and `:116-158` (`chat_node_stream`)
- Modify: `tests/test_snapshot_injection.py` (append 4 tests)

- [ ] **Step 1: Write the 4 failing tests**

Append to `tests/test_snapshot_injection.py`:

```python
# ---------------------------------------------------------------------------
# chat node — snapshot block + retrieve_memory threshold
# ---------------------------------------------------------------------------

from unittest.mock import patch
from langchain_core.messages import HumanMessage


def _capture_chat_system_prompt(state, *, retrieve_return=""):
    """Run chat_node with all LLM/IO mocked; return the captured system prompt."""
    from src.agent.nodes import chat as chat_mod

    captured = {}

    class _FakeChoice:
        message = type("M", (), {"content": "ok"})()

    class _FakeResp:
        choices = [_FakeChoice()]

    def _fake_create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _FakeResp()

    with patch.object(chat_mod, "retrieve_memory", return_value=retrieve_return) as mock_retrieve, \
         patch.object(chat_mod, "get_raw_client") as mock_client:
        mock_client.return_value.chat.completions.create.side_effect = _fake_create
        chat_mod.chat_node(state)

    return captured["messages"][0]["content"], mock_retrieve


def test_chat_node_appends_snapshot_when_present():
    """state has memory_snapshot → system prompt contains snapshot text."""
    state = {
        "messages": [HumanMessage(content="解释勾股定理是什么意思")],  # ≥10 chars
        "memory_snapshot": SAMPLE_SNAPSHOT,
        "memory_snapshot_loaded_at": 1000,
    }
    sys_prompt, _ = _capture_chat_system_prompt(state)
    assert SAMPLE_SNAPSHOT in sys_prompt


def test_chat_node_no_snapshot_when_field_is_none():
    """state has memory_snapshot=None → system prompt does NOT contain snapshot."""
    state = {
        "messages": [HumanMessage(content="解释勾股定理是什么意思")],
        "memory_snapshot": None,
    }
    sys_prompt, _ = _capture_chat_system_prompt(state)
    assert "## 该学生" not in sys_prompt


def test_chat_node_skips_retrieve_for_short_message():
    """User msg <10 chars → retrieve_memory NOT called."""
    state = {
        "messages": [HumanMessage(content="嗯")],   # 1 char
        "memory_snapshot": None,
    }
    _, mock_retrieve = _capture_chat_system_prompt(state)
    mock_retrieve.assert_not_called()


def test_chat_node_calls_retrieve_for_long_message():
    """User msg ≥10 chars → retrieve_memory called once."""
    state = {
        "messages": [HumanMessage(content="解释一下勾股定理的证明")],  # ≥10 chars
        "memory_snapshot": None,
    }
    _, mock_retrieve = _capture_chat_system_prompt(state)
    mock_retrieve.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_snapshot_injection.py -v -k "chat_node"
```

Expected: 4 FAILS.

- [ ] **Step 3: Modify `chat_node`**

Edit `src/agent/nodes/chat.py`. Add import at top (after existing imports):

```python
from src.agent.nodes._helpers import memory_snapshot_block
```

Add a NOTE comment after `_BASE_SYSTEM_PROMPT` (around line 12):

```python
_BASE_SYSTEM_PROMPT = "You are a friendly AI learning assistant. Help students with their questions. Respond in the same language as the student."

# NOTE (PR-4): chat is the only agent node that does NOT use a Jinja
# template. The streaming variant (chat_node_stream) and runtime decisions
# (≥10-char threshold for retrieve_memory) make Python construction
# more direct than j2 + filters. Other 3 nodes (knowledge_graph / quiz /
# lesson) use render_prompt(...) and inject {{- memory_snapshot_block }}
# at template end.
```

In `chat_node` (around line 70-113), find this block:

```python
system_prompt += f"\n\n{context}"

# ④ Retrieve past learning memory (graceful — empty string on failure)
user_msg = messages[-1].content if messages else ""
memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
if memory_context:
    system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答，但不要直接复述。"
```

Replace with:

```python
system_prompt += f"\n\n{context}"

# Frozen snapshot (PR-4): cache-friendly, before retrieve_memory
system_prompt += memory_snapshot_block(state)

# Per-turn L1 retrieval (existing). PR-4 adds ≥10-char threshold to skip
# tiny utterances ("嗯"/"继续") that produce noisy embeddings. NOT env-gated:
# pre-PR-4 this was unconditional, so env=off keeps L1 retrieval enabled.
user_msg = messages[-1].content if messages else ""
if len(user_msg.strip()) >= 10:
    memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
    if memory_context:
        system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答，但不要直接复述。"
```

Note: when `context` is empty (no learning context), the line `system_prompt += f"\n\n{context}"` only runs if `if context:` was True. The `memory_snapshot_block(state)` line is unconditional — it returns `""` when state has no snapshot, so no harm.

Wait, actually re-check the existing code structure:

```python
context = _build_context_summary(state)
if context:
    system_prompt += f"\n\n{context}"
```

The `if context:` gate is around the `+=`. The snapshot line goes AFTER that, also unconditional (helper handles empty case).

- [ ] **Step 4: Modify `chat_node_stream`**

In `chat_node_stream` (around line 116-158), find this block (it's identical structure to `chat_node`):

```python
context = _build_context_summary(state)
if context:
    system_prompt += f"\n\n{context}"

user_msg = messages[-1].content if messages and hasattr(messages[-1], "content") else ""
memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
if memory_context:
    system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答，但不要直接复述。"
```

Replace with:

```python
context = _build_context_summary(state)
if context:
    system_prompt += f"\n\n{context}"

# Frozen snapshot (PR-4) — same as chat_node
system_prompt += memory_snapshot_block(state)

user_msg = messages[-1].content if messages and hasattr(messages[-1], "content") else ""
if len(user_msg.strip()) >= 10:
    memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
    if memory_context:
        system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答，但不要直接复述。"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_snapshot_injection.py -v -k "chat_node"
```

Expected: 4 PASS.

Run full snapshot test file:

```bash
uv run python -m pytest tests/test_snapshot_injection.py tests/test_baseline_prompts.py -v
```

Expected: 21 PASS (17 prior + 4 chat).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/agent/nodes/chat.py tests/test_snapshot_injection.py
git add src/agent/nodes/chat.py tests/test_snapshot_injection.py
git commit -m "feat(memory-pr4): chat node snapshot block + retrieve_memory threshold"
```

---

## Task 6: review path — env-gated misconception fetch + template

**Files:**
- Modify: `src/prompts/review_generate_system.j2`
- Modify: `src/agent/nodes/review.py`
- Modify: `tests/test_baseline_prompts.py` (append baseline test)
- Modify: `tests/test_snapshot_injection.py` (append 2 tests)

- [ ] **Step 1: Capture pre-modification baseline**

```bash
uv run python -c "
from src.prompts.loader import render_prompt
out = render_prompt(
    'review_generate_system',
    candidates=[
        {'subtopic': 'KP1', 'last_accuracy': 0.5, 'days_ago': 3,
         'topic': 'T'}
    ],
)
print(repr(out))
"
```

Save the output for Step 5.

- [ ] **Step 2: Write 2 failing tests**

Append to `tests/test_snapshot_injection.py`:

```python
# ---------------------------------------------------------------------------
# review path — fetch_active_notes('misconception') + soft-guidance template
# ---------------------------------------------------------------------------


def _make_review_module_with_env(monkeypatch, enabled: bool):
    """Reload review module with monkeypatched env constant.

    `_MEMORY_REVIEW_ENABLED` is computed at import time, so we set both
    the env var (for completeness) and force the module-level constant.
    """
    from src.agent.nodes import review as review_mod
    monkeypatch.setenv("ENABLE_MEMORY_REVIEW", "true" if enabled else "false")
    monkeypatch.setattr(review_mod, "_MEMORY_REVIEW_ENABLED", enabled)
    return review_mod


def test_review_injects_misconceptions_env_on(monkeypatch):
    """env=on: fetch_active_notes called once, misconceptions appear in prompt."""
    review_mod = _make_review_module_with_env(monkeypatch, enabled=True)

    fake_notes = [
        {"content": "把两点距离误用为坐标差不平方"},
        {"content": "勾股定理只在直角三角形成立但学生在锐角也用"},
    ]
    candidates = [
        {"subtopic": "勾股定理", "last_seen_at": "2026-04-01T00:00:00Z",
         "last_accuracy": 0.5, "topic": "几何"}
    ]

    captured = {}
    class _FakeChoices:
        choices = []

    def _fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return []

    with patch("src.memory_client.fetch_active_notes", return_value=fake_notes) as mock_fetch, \
         patch("src.llm.client.get_llm_client") as mock_llm:
        mock_llm.return_value.chat.completions.create.side_effect = _fake_create
        review_mod.generate_review_questions(candidates)

    mock_fetch.assert_called_once_with(
        "default_user", "misconception"
    )
    assert "把两点距离误用为坐标差不平方" in captured["prompt"]
    assert "易混点" in captured["prompt"]


def test_review_does_not_fetch_env_off(monkeypatch):
    """env=off: fetch_active_notes never called, misconceptions absent from prompt."""
    review_mod = _make_review_module_with_env(monkeypatch, enabled=False)

    candidates = [
        {"subtopic": "X", "last_seen_at": "2026-04-01T00:00:00Z",
         "last_accuracy": 0.5, "topic": "T"}
    ]

    captured = {}
    def _fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return []

    with patch("src.memory_client.fetch_active_notes") as mock_fetch, \
         patch("src.llm.client.get_llm_client") as mock_llm:
        mock_llm.return_value.chat.completions.create.side_effect = _fake_create
        review_mod.generate_review_questions(candidates)

    mock_fetch.assert_not_called()
    assert "易混点" not in captured["prompt"]
```

> The `DEFAULT_USER_ID` value is `"default_user"` (from `src.memory_client`). The test hard-codes it for clarity.

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_snapshot_injection.py -v -k "review"
```

Expected: 2 FAILS — `_MEMORY_REVIEW_ENABLED` doesn't exist on review module yet.

- [ ] **Step 4: Modify `review_generate_system.j2`**

Edit `src/prompts/review_generate_system.j2`. After the `{% endfor %}` of the candidates loop and before `要求:`, insert:

```jinja
{% if misconceptions %}
该学生历史易混点（出干扰项时**优先**从中选取，但若与本题 KP 不相关则可不用）:
{% for m in misconceptions %}
- {{ m }}
{% endfor %}

{% endif %}
要求:
```

The `{% if misconceptions %}` early-returns when the list is empty (env=off path), preserving byte-identity.

- [ ] **Step 5: Modify `review.py` to fetch + pass misconceptions**

Edit `src/agent/nodes/review.py`. Update the imports section at top:

```python
import os
# ... existing imports ...

from src.memory_client import DEFAULT_USER_ID, fetch_active_notes

_MEMORY_REVIEW_ENABLED = (
    os.environ.get("ENABLE_MEMORY_REVIEW", "false").lower() == "true"
)
```

Modify `generate_review_questions` (around line 156). After the `enriched` candidate list construction and before `user_prompt = render_prompt(...)`, add:

```python
    # PR-4: live-fetch misconception notes (review path is the exception —
    # snapshot is too stale; reviewer-written notes must take effect on the
    # very next review). env=off skips the fetch entirely.
    misconceptions: list[str] = []
    if _MEMORY_REVIEW_ENABLED:
        notes = fetch_active_notes(DEFAULT_USER_ID, "misconception")
        misconceptions = [n["content"] for n in notes if n.get("content")]

    user_prompt = render_prompt(
        "review_generate_system",
        candidates=enriched,
        misconceptions=misconceptions,
    )
```

- [ ] **Step 6: Append review baseline test**

Append to `tests/test_baseline_prompts.py`:

```python
EXPECTED_REVIEW_BASELINE = REVIEW_BASELINE_PLACEHOLDER  # paste from Task 6 Step 1


def test_review_generate_system_baseline_env_off():
    """env=off: misconceptions=[] → byte-identical to pre-PR-4 (modulo {% if %} early return)."""
    out = render_prompt(
        "review_generate_system",
        candidates=[
            {"subtopic": "KP1", "last_accuracy": 0.5, "days_ago": 3,
             "topic": "T"}
        ],
        misconceptions=[],
    )
    assert out == EXPECTED_REVIEW_BASELINE
```

- [ ] **Step 7: Run all tests to verify pass**

```bash
uv run python -m pytest tests/test_snapshot_injection.py tests/test_baseline_prompts.py -v
```

Expected: 24 PASS (21 prior + 1 baseline + 2 review).

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/agent/nodes/review.py tests/
git add src/prompts/review_generate_system.j2 src/agent/nodes/review.py tests/
git commit -m "feat(memory-pr4): review path live-fetches misconception notes"
```

---

## Task 7: Final regression verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run full PR-4 test suite**

```bash
uv run python -m pytest tests/test_snapshot_injection.py tests/test_baseline_prompts.py -v
```

Expected: 24 PASS.

- [ ] **Step 2: Run PR-3 integration tests (no regression)**

```bash
uv run python -m pytest tests/test_chat_integration.py -v
```

Expected: 21 PASS (PR-3's tests should not be affected by PR-4).

- [ ] **Step 3: Run full project test suite**

```bash
uv run python -m pytest tests/ 2>&1 | tail -10
```

Expected: count of failures ≤ count at parent commit `2f21ad6`. Same 11 pre-existing failures (`test_product_memory.py`, `test_review_node.py`, `test_e2e.py`) — no NEW regressions.

To compare:

```bash
git stash 2>/dev/null
git checkout 2f21ad6 -- src/ tests/
uv run python -m pytest tests/ 2>&1 | tail -3
git checkout HEAD -- src/ tests/
git stash pop 2>/dev/null || true
```

- [ ] **Step 4: Manual smoke test (env=off — byte-identity)**

The 4 `test_*_baseline_env_off` tests already lock byte-identity. As an extra sanity check, render each modified template with `memory_snapshot_block=""` and grep for the snapshot marker:

```bash
uv run python -c "
from src.prompts.loader import render_prompt
for name, kw in [
    ('knowledge_graph', dict(topic='X', max_nodes=8, skill_instructions='', focus_directive='', review_weak_kps=[], review_strong_kps=[], existing_mastery=[], memory_snapshot_block='')),
    ('quiz_generate', dict(skill_instructions='', kc_skill_block='', knowledge_points=[], questions_per_point=2, memory_snapshot_block='')),
    ('lesson_plan', dict(skill_instructions='', focus_directive='', topic='X', weak_points=[], memory_snapshot_block='')),
]:
    out = render_prompt(name, **kw)
    print(f'{name}: snapshot marker present = {\"## 该学生\" in out}')
"
```

Expected output: all 3 lines should print `False`.

- [ ] **Step 5: Manual smoke test (env=on — snapshot injection works)**

```bash
uv run python -c "
from src.prompts.loader import render_prompt
out = render_prompt(
    'lesson_plan',
    skill_instructions='', focus_directive='', topic='勾股定理',
    weak_points=[{'name': 'X', 'description': 'd'}],
    memory_snapshot_block='\n\n## 该学生的学习进程\n- 已开始: 勾股定理',
)
assert '## 该学生的学习进程' in out
print('snapshot injection OK')
"
```

Expected: `snapshot injection OK`.

- [ ] **Step 6: Mark plan complete (no commit needed)**

PR-4 is complete. The 6 implementation commits + this verification are the deliverable.

---

## Plan Summary

**Tasks:** 7 (helper + 4 prompt nodes + chat + review + verification)

**Estimated commits:** 6 (Task 7 has no commit)

**Estimated lines:** ~320 (250 in tests, 70 in src)

**Risk areas:**
- Baseline byte-identity depends on Jinja whitespace control `{{- ... }}` working as expected. Tests catch any drift.
- `lesson.py` has 6 `render_prompt` sites + 2 helper signature changes; Task 4 is the largest task and most error-prone.
- `tasks.py` background lesson task path (Task 4 Step 6) requires careful handling because it uses `lambda` wrappers around `asyncio.to_thread`.
- Test mocking of `_MEMORY_REVIEW_ENABLED` requires `monkeypatch.setattr` (PR-3 pattern) — verified in helper `_make_review_module_with_env`.

**Out of scope:**
- Restructuring chat to use a Jinja template (deferred to future PR; spec §10 explicit)
- Prefix cache enablement (templates are designed cache-friendly but cache itself isn't activated)
- Frontend exposure of `memory_snapshot` (PR-3 already locked: not exposed)
