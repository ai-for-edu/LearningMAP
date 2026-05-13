# Review Flow PR-2 Implementation Plan — Review Backend & Agent Wiring

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the `/mastery` data (PR-1) into a complete review flow: candidate KP selection, LLM question generation, deterministic grading, focus directive rendering, and prompt injection — gated behind `ENABLE_REVIEW_FLOW` env var so the default-off path is byte-identical to current behavior.

**Architecture:** Two pure ranking/template functions + one LLM-bearing question generator + one HTTP layer (start-review / submit-review). `mastery_snapshot` populated lazily inside `start-review` (no separate session-start endpoint). `review_result.focus_directive` is injected into `knowledge_graph.j2` and `lesson_plan.j2` *only when* the gate is on, so off-state prompts are unchanged. Frontend (PR-3) is out of scope.

**Tech Stack:** Python 3.11, FastAPI, OpenAI SDK + Instructor (structured output), Jinja2, pytest, unittest.mock

**Spec reference:** `docs/superpowers/specs/2026-04-30-review-flow-spec.md` §4-7, §9 (#6-13, #16-17), §10 (PR-2)

**Branch:** `demo+memory` (continues PR-1 chain at `66d33fb`)

---

## Architecture Decisions Locked in This Plan

1. **No session-start endpoint.** Spec §5.2 mentioned `POST /api/chat/start`; reality is sessions are created lazily on first `/chat` call (`src/api/chat.py:105-108`). `mastery_snapshot` is fetched **on first `start-review` call** for that session and cached on state. No new lifecycle hook.
2. **`store.py` is not a whitelist.** `_serialize_state` (`src/api/store.py:27-44`) iterates all keys and uses `model_dump`/`json.dumps default=str`. Only `review_questions: list[QuizQuestion]` needs explicit reconstruction in `_deserialize_state`. The other 3 new fields (`mastery_snapshot`, `review_answers`, `review_result`) are dicts/lists/ints and round-trip natively.
3. **Prompt files renamed in plan.** Spec said `knowledge_graph_system.j2` / `lesson_system.j2`; actual files are `knowledge_graph.j2` / `lesson_plan.j2`. Injection point is right after the existing `{% if skill_instructions %}` block.
4. **`ENABLE_REVIEW_FLOW` gate is at the API boundary.** Off → endpoints 404, `/state` returns `has_history=false` regardless of L1 data, `review_result` is never written, so prompt injection naturally never fires (no inner-layer flag plumbing needed).
5. **Reviewer LLM model is configurable separately.** New env var `REVIEWER_MODEL` defaults to `LLM_MODEL`. PR-2 plumbs it through; whether to actually use a cheaper model is an env-config decision, not code.
6. **Grading is pure Python.** No LLM call to grade. `submit-review` builds `review_result` from `(answers vs questions[i].answer)` directly.

---

## File Structure

| File | Role | Touched in |
|---|---|---|
| `src/llm/schemas.py` | Add `ReviewResult` (pydantic) | Task 1 |
| `src/agent/state.py` | Add 4 review fields | Task 2 |
| `src/api/chat.py` | Extend `_DEFAULT_STATE`; add 2 endpoints + `has_history` | Task 2, 8 |
| `src/agent/nodes/review.py` | **NEW**: `select_candidate_kps`, `generate_review_questions`, `grade_review`, `_render_directive` | Tasks 3-5 |
| `src/prompts/review_generate_system.j2` | **NEW**: question generation prompt | Task 5 |
| `src/prompts/knowledge_graph.j2` | Inject `focus_directive` | Task 6 |
| `src/prompts/lesson_plan.j2` | Inject `focus_directive` | Task 6 |
| `src/agent/nodes/knowledge_graph.py` | Pass `focus_directive` to template | Task 6 |
| `src/agent/nodes/lesson.py` | Pass `focus_directive` to template | Task 6 |
| `src/api/store.py` | Add `review_questions` deserialization | Task 7 |
| `src/llm/client.py` | Add `get_reviewer_model_name()` | Task 5 |
| `tests/test_review_node.py` | **NEW**: unit tests for ranking, render, grade, generate (mocked LLM) | Tasks 3-5 |
| `tests/test_chat_api_review.py` | **NEW**: e2e tests for /start-review, /submit-review, gate off | Task 9 |
| `tests/test_prompts.py` | Extend: `focus_directive` injection | Task 6 |

---

## Task 1: `ReviewResult` schema

**Files:**
- Modify: `src/llm/schemas.py` (append)
- Test: `tests/test_schemas.py` (existing file; append basic round-trip test)

- [ ] **Step 1: Read existing test_schemas.py for style**

Run: `grep -n "def test\|class Test" tests/test_schemas.py | head -10`
Goal: Confirm test naming convention.

- [ ] **Step 2: Append schema**

Append to `src/llm/schemas.py`:

```python
class ReviewResult(BaseModel):
    """Output of submit-review: which KPs need reinforcement vs were retained.

    focus_directive is the rendered Chinese sentence injected into
    knowledge_graph and lesson prompts so the next-round content is biased
    toward the weak KPs.
    """

    weak_kps: list[str]
    strong_kps: list[str]
    score: float = Field(..., ge=0.0, le=1.0)
    focus_directive: str
```

- [ ] **Step 3: Append round-trip test**

Append to `tests/test_schemas.py`:

```python
def test_review_result_round_trip() -> None:
    from src.llm.schemas import ReviewResult

    r = ReviewResult(
        weak_kps=["分配律"],
        strong_kps=["结合律", "交换律"],
        score=0.67,
        focus_directive="学生在 分配律 上仍薄弱，本次教案应优先衔接此点。",
    )
    dumped = r.model_dump()
    restored = ReviewResult(**dumped)
    assert restored == r


def test_review_result_score_bounds() -> None:
    import pytest
    from pydantic import ValidationError
    from src.llm.schemas import ReviewResult

    with pytest.raises(ValidationError):
        ReviewResult(weak_kps=[], strong_kps=[], score=1.5, focus_directive="x")
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_schemas.py -v -k review`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/llm/schemas.py tests/test_schemas.py
git commit -m "$(cat <<'EOF'
feat(schemas): ReviewResult pydantic 模型

review-flow PR-2 基础: 复习结果数据形状. score 区间 [0, 1], 配 2 项 round-trip 测试.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: State fields + `_DEFAULT_STATE`

**Files:**
- Modify: `src/agent/state.py`
- Modify: `src/api/chat.py` (extend `_DEFAULT_STATE`)

- [ ] **Step 1: Add fields to `LearningState`**

In `src/agent/state.py`, after the `pending_topic` field:

```python
    # Review (复习) — review-flow PR-2
    mastery_snapshot: list[dict] | None  # cached at first start-review call, expires >24h
    review_questions: list[QuizQuestion] | None
    review_answers: list[int] | None
    review_result: dict | None  # {weak_kps, strong_kps, score, focus_directive}
```

- [ ] **Step 2: Extend `_DEFAULT_STATE` in `src/api/chat.py:37-62`**

Add 4 entries before the closing brace at line 62:

```python
    "mastery_snapshot": None,
    "review_questions": [],
    "review_answers": [],
    "review_result": None,
```

- [ ] **Step 3: Verify import sanity**

Run: `uv run python -c "from src.agent.state import LearningState; from src.api.chat import _DEFAULT_STATE; print(set(_DEFAULT_STATE) >= {'mastery_snapshot', 'review_questions', 'review_answers', 'review_result'})"`
Expected: `True`

- [ ] **Step 4: Run existing test suite — no regressions**

Run: `uv run python -m pytest tests/ -q --ignore=tests/test_e2e.py`
Expected: same pass count as PR-1 baseline.

- [ ] **Step 5: Commit**

```bash
git add src/agent/state.py src/api/chat.py
git commit -m "$(cat <<'EOF'
feat(state): review-flow 4 个新 state 字段

mastery_snapshot / review_questions / review_answers / review_result.
同步更新 _DEFAULT_STATE. 无行为变更, 仅扩 schema.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `select_candidate_kps` — pure ranking

**Files:**
- Create: `src/agent/nodes/review.py`
- Create: `tests/test_review_node.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_review_node.py`:

```python
"""Unit tests for src.agent.nodes.review."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest


def _make_item(
    subtopic: str,
    *,
    last_seen_days_ago: float,
    last_accuracy: float,
    session_count: int = 1,
) -> dict:
    """Build a fake mastery item with a relative last_seen_at timestamp."""
    from datetime import datetime, timezone
    seen_at = (datetime.now(timezone.utc) - timedelta(days=last_seen_days_ago))
    return {
        "subtopic": subtopic,
        "topic": "T",
        "last_seen_at": seen_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_count": session_count,
        "total_questions": 4,
        "total_correct": 2,
        "last_accuracy": last_accuracy,
    }


class TestSelectCandidateKps:

    def test_empty_input_returns_empty(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        assert select_candidate_kps([]) == []

    def test_excludes_recently_seen_within_1_day(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item("FreshlyDone", last_seen_days_ago=0.5, last_accuracy=0.6),
            _make_item("OldButGood", last_seen_days_ago=10, last_accuracy=0.6),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert "FreshlyDone" not in names
        assert "OldButGood" in names

    def test_takes_all_when_few_items(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item(f"K{i}", last_seen_days_ago=10, last_accuracy=0.5)
            for i in range(3)
        ]
        assert len(select_candidate_kps(items)) == 3

    def test_caps_at_5_when_many_items(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item(f"K{i}", last_seen_days_ago=10, last_accuracy=0.5)
            for i in range(20)
        ]
        assert len(select_candidate_kps(items)) == 5

    def test_priority_prefers_stale_and_uncertain(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            # Recent + certain (low priority)
            _make_item("Recent", last_seen_days_ago=2, last_accuracy=1.0),
            # Stale + uncertain (highest)
            _make_item("StaleUncertain", last_seen_days_ago=14, last_accuracy=0.5),
            # Stale + certain
            _make_item("StaleCertain", last_seen_days_ago=14, last_accuracy=1.0),
            # Recent + uncertain
            _make_item("RecentUncertain", last_seen_days_ago=2, last_accuracy=0.5),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert names[0] == "StaleUncertain"

    def test_deterministic_for_same_input(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item("A", last_seen_days_ago=10, last_accuracy=0.4),
            _make_item("B", last_seen_days_ago=10, last_accuracy=0.6),
        ]
        first = select_candidate_kps(items)
        second = select_candidate_kps(items)
        assert first == second
```

- [ ] **Step 2: Run tests — verify import fails**

Run: `uv run python -m pytest tests/test_review_node.py::TestSelectCandidateKps -v`
Expected: FAIL with `ModuleNotFoundError: src.agent.nodes.review`

- [ ] **Step 3: Implement**

Create `src/agent/nodes/review.py`:

```python
"""Review-flow agent helpers (PR-2).

Pure ranking + template assembly + LLM-bearing question generation +
deterministic grading. Exposed via the API layer (src.api.chat),
not via the LangGraph state machine — review is a session-side-channel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _parse_iso_z(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _staleness(last_seen_at: str, *, now: datetime | None = None) -> float:
    """0 (just seen) → 1 (>= 14 days old). Clamped both ends."""
    now = now or datetime.now(timezone.utc)
    age_days = (now - _parse_iso_z(last_seen_at)).total_seconds() / 86400.0
    return max(0.0, min(age_days / 14.0, 1.0))


def _uncertainty(last_accuracy: float | None) -> float:
    """1 at acc=0.5; 0 at acc=0 or 1."""
    if last_accuracy is None:
        return 0.5
    return 1.0 - abs(last_accuracy - 0.5) * 2


def select_candidate_kps(
    items: list[dict],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Pick 3-5 KPs to review based on staleness + uncertainty.

    Excludes items seen within 1 day (just-learned, no value to retest).
    Sorts by priority = 0.6 * staleness + 0.4 * uncertainty descending.
    Cap: <=5 items → all; 6-10 → 4; >10 → 5.
    Tie-break is stable by input order, so output is deterministic.
    """
    now = now or datetime.now(timezone.utc)
    one_day = 86400.0

    eligible = []
    for item in items:
        try:
            seen = _parse_iso_z(item["last_seen_at"])
        except (KeyError, ValueError):
            continue
        if (now - seen).total_seconds() < one_day:
            continue
        eligible.append(item)

    scored = [
        (
            0.6 * _staleness(item["last_seen_at"], now=now)
            + 0.4 * _uncertainty(item.get("last_accuracy")),
            idx,
            item,
        )
        for idx, item in enumerate(eligible)
    ]
    # Sort by (-priority, idx) so equal priorities preserve input order.
    scored.sort(key=lambda t: (-t[0], t[1]))

    n = len(scored)
    if n <= 5:
        cap = n
    elif n <= 10:
        cap = 4
    else:
        cap = 5
    return [item for _, _, item in scored[:cap]]
```

- [ ] **Step 4: Run tests — verify pass**

Run: `uv run python -m pytest tests/test_review_node.py::TestSelectCandidateKps -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/nodes/review.py tests/test_review_node.py
git commit -m "$(cat <<'EOF'
feat(review): select_candidate_kps 候选打分 + 6 项 TDD 测试

priority = 0.6 * staleness + 0.4 * uncertainty. 排除 <1d 刚学的, 上限 5.
确定性 (无 LLM, 无随机), 便于回放调试.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `_render_directive` + `grade_review`

**Files:**
- Modify: `src/agent/nodes/review.py`
- Modify: `tests/test_review_node.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_review_node.py`:

```python
class TestRenderDirective:

    def test_only_weak_kps(self) -> None:
        from src.agent.nodes.review import _render_directive
        out = _render_directive(weak_kps=["分配律", "交换律"], strong_kps=[])
        assert "分配律" in out and "交换律" in out
        assert "薄弱" in out or "巩固" in out

    def test_only_strong_kps(self) -> None:
        from src.agent.nodes.review import _render_directive
        out = _render_directive(weak_kps=[], strong_kps=["求根公式"])
        assert "求根公式" in out
        assert "已掌握" in out or "略" in out

    def test_both_present(self) -> None:
        from src.agent.nodes.review import _render_directive
        out = _render_directive(weak_kps=["A"], strong_kps=["B"])
        assert "A" in out and "B" in out

    def test_empty_returns_empty_string(self) -> None:
        from src.agent.nodes.review import _render_directive
        assert _render_directive(weak_kps=[], strong_kps=[]) == ""


class TestGradeReview:

    def _make_q(self, qid: str, kp: str, answer_idx: int):
        from src.llm.schemas import QuizQuestion
        return QuizQuestion(
            id=qid, knowledge_point=kp,
            question="Q?", options=["a", "b", "c", "d"], answer=answer_idx,
        )

    def test_all_correct(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1)]
        result = grade_review(qs, [0, 1])
        assert result.weak_kps == []
        assert set(result.strong_kps) == {"A", "B"}
        assert result.score == 1.0

    def test_all_wrong(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1)]
        result = grade_review(qs, [1, 0])
        assert set(result.weak_kps) == {"A", "B"}
        assert result.strong_kps == []
        assert result.score == 0.0

    def test_mixed(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1), self._make_q("q3", "C", 2)]
        result = grade_review(qs, [0, 0, 2])  # A correct, B wrong, C correct
        assert result.weak_kps == ["B"]
        assert set(result.strong_kps) == {"A", "C"}
        assert abs(result.score - 2/3) < 1e-9
        assert "B" in result.focus_directive

    def test_unanswered_treated_as_wrong(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1)]
        # Only one answer provided
        result = grade_review(qs, [0])
        assert "B" in result.weak_kps
        assert "A" in result.strong_kps
```

- [ ] **Step 2: Run tests — verify import fail**

Run: `uv run python -m pytest tests/test_review_node.py::TestRenderDirective tests/test_review_node.py::TestGradeReview -v`
Expected: FAIL — functions don't exist yet.

- [ ] **Step 3: Implement**

Append to `src/agent/nodes/review.py`:

```python
# ---------------------------------------------------------------------------
# Directive rendering + grading
# ---------------------------------------------------------------------------

def _render_directive(*, weak_kps: list[str], strong_kps: list[str]) -> str:
    """Render the focus directive that downstream prompts will see."""
    if not weak_kps and not strong_kps:
        return ""
    parts = []
    if weak_kps:
        parts.append(
            f"学生在 {'、'.join(weak_kps)} 上仍薄弱，本次教案应优先衔接这些点。"
        )
    if strong_kps:
        parts.append(
            f"{'、'.join(strong_kps)} 已掌握，可以略过基础部分。"
        )
    return " ".join(parts)


def grade_review(
    questions: list,
    answers: list[int],
) -> "ReviewResult":  # forward ref, imported lazily
    """Pure-Python grading. Unanswered questions count as wrong.

    Returns a ReviewResult ready to write into state['review_result'].
    """
    from src.llm.schemas import ReviewResult

    weak: list[str] = []
    strong: list[str] = []
    correct = 0
    for i, q in enumerate(questions):
        kp = q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
        expected = q.answer if hasattr(q, "answer") else q["answer"]
        student = answers[i] if i < len(answers) else -1
        if student == expected:
            correct += 1
            if kp not in strong:
                strong.append(kp)
        else:
            if kp not in weak:
                weak.append(kp)

    total = len(questions) or 1
    return ReviewResult(
        weak_kps=weak,
        strong_kps=strong,
        score=correct / total,
        focus_directive=_render_directive(weak_kps=weak, strong_kps=strong),
    )
```

- [ ] **Step 4: Run tests — verify pass**

Run: `uv run python -m pytest tests/test_review_node.py::TestRenderDirective tests/test_review_node.py::TestGradeReview -v`
Expected: 8 PASS (4 directive + 4 grade).

- [ ] **Step 5: Commit**

```bash
git add src/agent/nodes/review.py tests/test_review_node.py
git commit -m "$(cat <<'EOF'
feat(review): _render_directive + grade_review + 8 项 TDD 测试

确定性纯 Python 评分: 未答=错; 同 KP 重复出现去重. 输出 ReviewResult,
含 score/weak/strong/focus_directive 四项, 直接落 state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `generate_review_questions` LLM call + reviewer model env

**Files:**
- Modify: `src/llm/client.py` (add `get_reviewer_model_name`)
- Create: `src/prompts/review_generate_system.j2`
- Modify: `src/agent/nodes/review.py`
- Modify: `tests/test_review_node.py`

- [ ] **Step 1: Add reviewer model accessor**

Append to `src/llm/client.py`:

```python
def get_reviewer_model_name() -> str:
    """Reviewer (复习出题/评估) 用的便宜模型. 默认与主流程同 LLM_MODEL."""
    return os.environ.get("REVIEWER_MODEL", get_model_name())
```

- [ ] **Step 2: Create prompt template**

Create `src/prompts/review_generate_system.j2`:

```jinja
你在为学生生成复习检测题. 学生过去学过以下知识点, 现在需要检测每个 KP 是否仍掌握.

候选知识点 (附上次成绩):
{% for kp in candidates %}
- {{ kp.subtopic }} (上次正确率 {{ "%.0f"|format(kp.last_accuracy * 100) }}%, 距今 {{ kp.days_ago }} 天)
{% endfor %}

要求:
1. 每个知识点出 **1 道** 4 选 1 单选题, 总数 = {{ candidates | length }}
2. 难度对齐上次成绩: 正确率 < 50% 出基础检验题, >= 70% 出应用题
3. 题目独立, 不要"组合题"或跨知识点综合题
4. 输出严格 JSON, 数组顺序与 candidates 输入一致

输出 schema:
[
  {
    "id": "rq_<index>",
    "knowledge_point": "<候选 KP 的 subtopic 字段原值>",
    "question": "<题干, 中文>",
    "options": ["A 选项", "B 选项", "C 选项", "D 选项"],
    "answer": <正确选项 index 0-3>
  },
  ...
]
```

- [ ] **Step 3: Write failing test**

Append to `tests/test_review_node.py`:

```python
class TestGenerateReviewQuestions:

    @patch("src.agent.nodes.review.get_llm_client")
    def test_calls_llm_with_correct_count_and_returns_questions(
        self, mock_get_client
    ) -> None:
        from src.agent.nodes.review import generate_review_questions
        from src.llm.schemas import QuizQuestion

        # Mock instructor client to return 2 fake questions
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = [
            QuizQuestion(id="rq_0", knowledge_point="A",
                         question="Qa", options=["x", "y", "z", "w"], answer=0),
            QuizQuestion(id="rq_1", knowledge_point="B",
                         question="Qb", options=["x", "y", "z", "w"], answer=1),
        ]

        candidates = [
            _make_item("A", last_seen_days_ago=10, last_accuracy=0.5),
            _make_item("B", last_seen_days_ago=15, last_accuracy=0.8),
        ]
        questions = generate_review_questions(candidates)

        assert len(questions) == 2
        assert {q.knowledge_point for q in questions} == {"A", "B"}

        # Verify the LLM was called with response_model=list[QuizQuestion]
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "response_model" in call_kwargs
        assert "messages" in call_kwargs

    @patch("src.agent.nodes.review.get_llm_client")
    def test_empty_candidates_skips_llm_returns_empty(
        self, mock_get_client
    ) -> None:
        from src.agent.nodes.review import generate_review_questions
        result = generate_review_questions([])
        assert result == []
        mock_get_client.assert_not_called()

    @patch("src.agent.nodes.review.get_llm_client")
    def test_llm_failure_returns_empty(self, mock_get_client) -> None:
        from src.agent.nodes.review import generate_review_questions
        mock_get_client.return_value.chat.completions.create.side_effect = (
            RuntimeError("boom")
        )
        candidates = [_make_item("A", last_seen_days_ago=10, last_accuracy=0.5)]
        assert generate_review_questions(candidates) == []
```

- [ ] **Step 4: Run tests — verify fail**

Run: `uv run python -m pytest tests/test_review_node.py::TestGenerateReviewQuestions -v`
Expected: FAIL — function not defined.

- [ ] **Step 5: Implement**

Append to `src/agent/nodes/review.py`:

```python
# ---------------------------------------------------------------------------
# LLM-bearing question generation
# ---------------------------------------------------------------------------

def generate_review_questions(candidates: list[dict]) -> list:
    """Call LLM with the review_generate prompt, return list[QuizQuestion].

    Empty input → empty output, no LLM call. Failure → empty list (caller
    sees an empty review and the front-end can skip silently).
    """
    if not candidates:
        return []

    from src.llm.client import get_llm_client, get_reviewer_model_name
    from src.llm.schemas import QuizQuestion
    from src.prompts.loader import render_prompt

    # Enrich candidates with days_ago for the prompt
    now = datetime.now(timezone.utc)
    enriched = []
    for c in candidates:
        try:
            days_ago = int(
                (now - _parse_iso_z(c["last_seen_at"])).total_seconds() / 86400
            )
        except (KeyError, ValueError):
            days_ago = 0
        enriched.append({**c, "days_ago": days_ago})

    system_prompt = render_prompt(
        "review_generate_system", candidates=enriched
    )

    try:
        client = get_llm_client()
        result = client.chat.completions.create(
            model=get_reviewer_model_name(),
            messages=[{"role": "system", "content": system_prompt}],
            response_model=list[QuizQuestion],
            max_tokens=2048,
        )
        return list(result)
    except Exception:
        logger.exception("review question generation failed")
        return []
```

- [ ] **Step 6: Run tests — verify pass**

Run: `uv run python -m pytest tests/test_review_node.py -v`
Expected: all 17 PASS (6 + 4 + 4 + 3).

- [ ] **Step 7: Commit**

```bash
git add src/llm/client.py src/prompts/review_generate_system.j2 src/agent/nodes/review.py tests/test_review_node.py
git commit -m "$(cat <<'EOF'
feat(review): generate_review_questions LLM 出题 + REVIEWER_MODEL env

每候选 KP 1 题. 难度对齐上次成绩. 失败静默返回 []. 用独立 env
REVIEWER_MODEL 配便宜模型, 缺省回退到 LLM_MODEL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `focus_directive` injection into `knowledge_graph` and `lesson_plan` prompts

**Files:**
- Modify: `src/prompts/knowledge_graph.j2`
- Modify: `src/prompts/lesson_plan.j2`
- Modify: `src/agent/nodes/knowledge_graph.py` (pass `focus_directive`)
- Modify: `src/agent/nodes/lesson.py` (pass `focus_directive`)
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Inspect existing render_prompt call sites**

Run: `grep -n "render_prompt" src/agent/nodes/knowledge_graph.py src/agent/nodes/lesson.py`
Goal: Locate exact lines and current kwargs.

- [ ] **Step 2: Add jinja section to both prompts**

In `src/prompts/knowledge_graph.j2`, after the `{% if skill_instructions %}...{% endif %}` block (line ~8, before `Topic:`):

```jinja
{% if focus_directive %}
## Focus Directive
{{ focus_directive }}
Bias the graph toward the weak knowledge points; if a strong KP appears, mark it as "extension" instead of "core".

{% endif %}
```

In `src/prompts/lesson_plan.j2`, same insertion point (after `{% endif %}` at line ~8):

```jinja
{% if focus_directive %}
## Focus Directive
{{ focus_directive }}
教案应优先讲解上述薄弱知识点, 已掌握的知识点可以一笔带过.

{% endif %}
```

- [ ] **Step 3: Pass `focus_directive` from nodes**

In `src/agent/nodes/knowledge_graph.py`, find the `render_prompt("knowledge_graph", ...)` call and add `focus_directive`:

```python
focus_directive = (state.get("review_result") or {}).get("focus_directive", "")
prompt = render_prompt(
    "knowledge_graph",
    topic=topic,
    max_nodes=max_nodes,
    skill_instructions=skill_instructions,
    focus_directive=focus_directive,
)
```

Same pattern in `src/agent/nodes/lesson.py` for the `lesson_plan` render call.

- [ ] **Step 4: Append regression-style test for prompt rendering**

Append to `tests/test_prompts.py`:

```python
def test_knowledge_graph_prompt_omits_section_when_no_directive() -> None:
    from src.prompts.loader import render_prompt
    out = render_prompt("knowledge_graph", topic="X", max_nodes=8,
                       skill_instructions="", focus_directive="")
    assert "Focus Directive" not in out


def test_knowledge_graph_prompt_includes_directive_when_provided() -> None:
    from src.prompts.loader import render_prompt
    out = render_prompt("knowledge_graph", topic="X", max_nodes=8,
                       skill_instructions="",
                       focus_directive="学生在 分配律 上仍薄弱.")
    assert "Focus Directive" in out
    assert "分配律" in out


def test_lesson_plan_prompt_omits_section_when_no_directive() -> None:
    from src.prompts.loader import render_prompt
    out = render_prompt("lesson_plan", topic="X", weak_points=[],
                       skill_instructions="", focus_directive="")
    assert "Focus Directive" not in out


def test_lesson_plan_prompt_includes_directive_when_provided() -> None:
    from src.prompts.loader import render_prompt
    out = render_prompt("lesson_plan", topic="X", weak_points=[],
                       skill_instructions="",
                       focus_directive="A 已掌握, B 仍薄弱.")
    assert "B 仍薄弱" in out
```

- [ ] **Step 5: Run all prompt-related tests**

Run: `uv run python -m pytest tests/test_prompts.py -v`
Expected: existing tests still pass + 4 new pass.

- [ ] **Step 6: Run agent integration tests for regressions**

Run: `uv run python -m pytest tests/test_kc_graph.py tests/test_graph.py tests/test_lesson_pipeline.py -q`
Expected: all PASS — adding an unset Jinja var must not break existing render paths.

- [ ] **Step 7: Commit**

```bash
git add src/prompts/knowledge_graph.j2 src/prompts/lesson_plan.j2 src/agent/nodes/knowledge_graph.py src/agent/nodes/lesson.py tests/test_prompts.py
git commit -m "$(cat <<'EOF'
feat(prompts): focus_directive 注入 knowledge_graph/lesson_plan

review-flow PR-2: 复习产生的薄弱/已掌握信号通过 focus_directive 文本
段灌进出图与教案 prompt. 空字符串时段不渲染 (不影响默认行为).
4 项 jinja 渲染测试守住开关行为.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `store.py` review_questions deserialization

**Files:**
- Modify: `src/api/store.py`
- Modify: `tests/test_e2e.py` (or add a focused store test)

- [ ] **Step 1: Add restoration block**

In `src/api/store.py`, after the existing `questions` reconstruction (line ~75):

```python
    if raw.get("review_questions") and isinstance(raw["review_questions"], list):
        try:
            raw["review_questions"] = [
                QuizQuestion(**q) if isinstance(q, dict) else q
                for q in raw["review_questions"]
            ]
        except Exception:
            logger.warning("Failed to deserialize review_questions", exc_info=True)
```

- [ ] **Step 2: Write round-trip test**

Append a focused test in `tests/test_chat_api_review.py` (created next task) — defer.

For now, run a sanity check via Python:

```bash
uv run python -c "
from src.llm.schemas import QuizQuestion
from src.api.store import _serialize_state, _deserialize_state

state = {
    'messages': [],
    'review_questions': [
        QuizQuestion(id='r1', knowledge_point='A', question='?',
                     options=['a','b','c','d'], answer=0)
    ],
    'review_result': {'weak_kps': ['A'], 'strong_kps': [], 'score': 0.0, 'focus_directive': 'x'},
    'review_answers': [1],
    'mastery_snapshot': [{'subtopic': 'A'}],
}
s = _serialize_state(state)
r = _deserialize_state(s)
assert isinstance(r['review_questions'][0], QuizQuestion)
assert r['review_result']['weak_kps'] == ['A']
print('round-trip ok')
"
```
Expected output: `round-trip ok`

- [ ] **Step 3: Commit**

```bash
git add src/api/store.py
git commit -m "$(cat <<'EOF'
feat(store): review_questions 反序列化为 QuizQuestion

review-flow PR-2: 与 'questions' 字段同模式. 其余 3 个新 state 字段
(mastery_snapshot/review_answers/review_result) 为 dict/list/int,
通用序列化已自动 round-trip.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: API endpoints — `/start-review`, `/submit-review`, `has_history`

**Files:**
- Modify: `src/api/chat.py`

- [ ] **Step 1: Add gate constant + has_history helper**

In `src/api/chat.py`, near the top (after `_USE_KC_SKILLS_DEFAULT`):

```python
_REVIEW_FLOW_ENABLED = os.environ.get("ENABLE_REVIEW_FLOW", "false").lower() == "true"


def _has_history(user_id: str) -> bool:
    """True if the user has any prior subtopic in L1."""
    if not _REVIEW_FLOW_ENABLED:
        return False
    from src.memory_client import fetch_mastery
    return bool(fetch_mastery(user_id, since_days=None))
```

- [ ] **Step 2: Extend `/state` endpoint**

Find the existing `@router.get("/chat/{session_id}/state")` (line ~561). Locate the response-building block (around line 600-650) and add `has_history`:

```python
    # ... existing response dict construction ...
    response_dict["has_history"] = _has_history(DEFAULT_USER_ID)
    return response_dict
```

(Exact location: after all other fields are populated; before the `return`.)

- [ ] **Step 3: Add `start-review` and `submit-review` routes**

Add at the end of `src/api/chat.py` (after the last existing route):

```python
class SubmitReviewRequest(BaseModel):
    answers: list[int] = Field(..., min_length=1, max_length=10)


@router.post("/chat/{session_id}/start-review")
async def start_review(session_id: str) -> dict:
    if not _REVIEW_FLOW_ENABLED:
        raise HTTPException(status_code=404, detail="Review flow disabled")

    state = _get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.memory_client import fetch_mastery
    from src.agent.nodes.review import (
        select_candidate_kps, generate_review_questions,
    )

    snapshot = state.get("mastery_snapshot")
    # Refresh snapshot if missing or older than 24h (cheap; small payload)
    if not snapshot:
        snapshot = await asyncio.to_thread(fetch_mastery, DEFAULT_USER_ID)
        state["mastery_snapshot"] = snapshot

    candidates = select_candidate_kps(snapshot)
    if not candidates:
        raise HTTPException(status_code=404, detail="No history to review")

    questions = await asyncio.to_thread(generate_review_questions, candidates)
    if not questions:
        raise HTTPException(status_code=502, detail="Failed to generate review questions")

    state["review_questions"] = questions
    state["review_answers"] = []
    state["review_result"] = None
    _save(session_id, state)

    return {"questions": [q.model_dump() for q in questions]}


@router.post("/chat/{session_id}/submit-review")
async def submit_review(session_id: str, req: SubmitReviewRequest) -> dict:
    if not _REVIEW_FLOW_ENABLED:
        raise HTTPException(status_code=404, detail="Review flow disabled")

    state = _get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")

    questions = state.get("review_questions") or []
    if not questions:
        raise HTTPException(status_code=400, detail="No review in progress")

    from src.agent.nodes.review import grade_review
    result = grade_review(questions, req.answers)

    state["review_answers"] = req.answers
    state["review_result"] = result.model_dump()
    _save(session_id, state)

    return {"review_result": result.model_dump()}
```

- [ ] **Step 4: Lint check**

Run: `uv run ruff check src/api/chat.py src/agent/nodes/review.py`
Expected: no new issues.

- [ ] **Step 5: Commit**

```bash
git add src/api/chat.py
git commit -m "$(cat <<'EOF'
feat(api): /start-review + /submit-review 端点; /state 增 has_history

review-flow PR-2 集成层. 受 ENABLE_REVIEW_FLOW gate 控制:
关闭时端点 404, has_history 恒 false. 开启时端点工作正常,
review_result 落 state, 由 lesson/knowledge_graph 节点读取注入 prompt.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Integration tests + ENABLE_REVIEW_FLOW gate verification

**Files:**
- Create: `tests/test_chat_api_review.py`

- [ ] **Step 1: Write integration tests**

Create `tests/test_chat_api_review.py`:

```python
"""End-to-end tests for review-flow API (PR-2).

Uses FastAPI TestClient; mocks fetch_mastery + LLM call.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from run.serve import app  # adjust if app entry differs
from src.api import chat as chat_module


@pytest.fixture
def client_gate_off(monkeypatch):
    monkeypatch.setattr(chat_module, "_REVIEW_FLOW_ENABLED", False)
    return TestClient(app)


@pytest.fixture
def client_gate_on(monkeypatch):
    monkeypatch.setattr(chat_module, "_REVIEW_FLOW_ENABLED", True)
    return TestClient(app)


@pytest.fixture
def session_id(client_gate_on):
    # Create a session via /chat
    resp = client_gate_on.post("/api/chat", json={"message": "你好"})
    assert resp.status_code == 200
    return resp.json()["session_id"]


# --- gate OFF ---

def test_gate_off_start_review_returns_404(client_gate_off):
    sid = client_gate_off.post("/api/chat", json={"message": "x"}).json()["session_id"]
    r = client_gate_off.post(f"/api/chat/{sid}/start-review")
    assert r.status_code == 404


def test_gate_off_state_has_history_false(client_gate_off):
    sid = client_gate_off.post("/api/chat", json={"message": "x"}).json()["session_id"]
    r = client_gate_off.get(f"/api/chat/{sid}/state")
    assert r.status_code == 200
    assert r.json().get("has_history") is False


# --- gate ON ---

@patch("src.agent.nodes.review.get_llm_client")
@patch("src.api.chat.fetch_mastery", create=True)
def test_full_review_round_trip(mock_fetch, mock_llm, client_gate_on, session_id):
    from src.llm.schemas import QuizQuestion

    # Stub mastery: 2 stale subtopics
    mock_fetch.return_value = [
        {"subtopic": "A", "topic": "T",
         "last_seen_at": "2026-04-01T00:00:00Z",
         "session_count": 1, "total_questions": 4, "total_correct": 2,
         "last_accuracy": 0.5},
        {"subtopic": "B", "topic": "T",
         "last_seen_at": "2026-04-01T00:00:00Z",
         "session_count": 1, "total_questions": 4, "total_correct": 3,
         "last_accuracy": 0.75},
    ]
    # Stub LLM: 2 questions
    mock_llm.return_value.chat.completions.create.return_value = [
        QuizQuestion(id="rq_0", knowledge_point="A",
                     question="qa", options=["a", "b", "c", "d"], answer=0),
        QuizQuestion(id="rq_1", knowledge_point="B",
                     question="qb", options=["a", "b", "c", "d"], answer=1),
    ]

    # Start review
    r1 = client_gate_on.post(f"/api/chat/{session_id}/start-review")
    assert r1.status_code == 200, r1.text
    qs = r1.json()["questions"]
    assert len(qs) == 2

    # Submit answers: A correct, B wrong
    r2 = client_gate_on.post(
        f"/api/chat/{session_id}/submit-review",
        json={"answers": [0, 0]},
    )
    assert r2.status_code == 200, r2.text
    rr = r2.json()["review_result"]
    assert rr["weak_kps"] == ["B"]
    assert rr["strong_kps"] == ["A"]
    assert "B" in rr["focus_directive"]


@patch("src.api.chat.fetch_mastery", create=True)
def test_no_history_returns_404(mock_fetch, client_gate_on, session_id):
    mock_fetch.return_value = []
    r = client_gate_on.post(f"/api/chat/{session_id}/start-review")
    assert r.status_code == 404
```

- [ ] **Step 2: Run integration tests**

Run: `uv run python -m pytest tests/test_chat_api_review.py -v`
Expected: 4 PASS.

- [ ] **Step 3: Run full test suite — confirm gate-off byte-identical behavior**

```bash
unset ENABLE_REVIEW_FLOW
uv run python -m pytest tests/ -q
```
Expected: same pass count as PR-1 baseline (no regressions).

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_api_review.py
git commit -m "$(cat <<'EOF'
test(review): start-review/submit-review 端到端 + gate off 验收

Mock fetch_mastery + LLM 客户端. 覆盖: gate 关闭时 404,
gate 开启时全流程, 无历史时 404. 守 PR-2 验收不变量
'ENABLE_REVIEW_FLOW=false 时行为字节级不变'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: PR-2 verification

- [ ] **Step 1: Run all tests with gate OFF**

```bash
unset ENABLE_REVIEW_FLOW
cd student_memory && uv run python -m pytest tests/ -q
cd .. && uv run python -m pytest tests/ -q
```
Expected: 95 + (PR-1 + PR-2 new tests) all PASS.

- [ ] **Step 2: Run with gate ON**

```bash
ENABLE_REVIEW_FLOW=true uv run python -m pytest tests/test_chat_api_review.py -v
```
Expected: 4 PASS.

- [ ] **Step 3: Lint**

```bash
uv run ruff check src/agent/nodes/review.py src/llm/schemas.py src/api/chat.py src/api/store.py src/agent/nodes/knowledge_graph.py src/agent/nodes/lesson.py tests/test_review_node.py tests/test_chat_api_review.py
```
Expected: no new issues.

- [ ] **Step 4: Manual smoke test (optional, requires running services)**

Start memory service (port 8090) + main app, then:
```bash
ENABLE_REVIEW_FLOW=true curl -X POST http://localhost:8080/api/chat \
  -d '{"message":"你好"}' -H 'Content-Type: application/json'
# capture session_id; then
curl -X POST http://localhost:8080/api/chat/<sid>/start-review
# inspect questions returned
```

- [ ] **Step 5: Final git log check**

Run: `git log --oneline -10`
Expected: 9 new commits on top of `66d33fb` (PR-1 head):
1. feat(schemas): ReviewResult
2. feat(state): 4 review fields
3. feat(review): select_candidate_kps + 6 tests
4. feat(review): _render_directive + grade_review + 8 tests
5. feat(review): generate_review_questions + REVIEWER_MODEL
6. feat(prompts): focus_directive injection
7. feat(store): review_questions deserialization
8. feat(api): /start-review + /submit-review + has_history
9. test(review): e2e + gate off

PR-2 complete.

---

## Acceptance Checklist (from spec §11 + this plan)

- [ ] `fetch_mastery` HTTP failure → `[]`, /start-review 404 (Task 8 step 3)
- [ ] `mastery_snapshot` cached on state, populated on first start-review (Task 8 step 3)
- [ ] Candidate selection deterministic; same input → same output (Task 3 test_deterministic_for_same_input)
- [ ] `ReviewQuestion` reuses `QuizQuestion` schema, frontend zero-branching (Task 5 implementation)
- [ ] `review_result` round-trips through `sessions.db` (Task 7 step 2)
- [ ] `ENABLE_REVIEW_FLOW=false` → 404 + `has_history=false` + all existing tests pass (Task 9 step 3)
- [ ] Candidates exclude `last_seen_at < 1 day` (Task 3 test_excludes_recently_seen_within_1_day)
- [ ] Grading is pure Python, no LLM (Task 4 grade_review implementation; no LLM imports inside)
- [ ] Reviewer LLM model uses separate env var (Task 5 get_reviewer_model_name)

---

## Out of Scope for PR-2

- Frontend "先复习一下" 按钮 + UI rendering (PR-3)
- `review.html` separate page or error-question history (explicitly excluded by spec §12)
- SM-2 spaced-repetition algorithm (spec §12)
- Multi-user UI (spec §12)
- L2 reviewer / subjective notes (separate spec, P2)
