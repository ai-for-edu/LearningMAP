# Knowledge Graph Mastery Annotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 KG 节点上叠加掌握度/遗忘度标注，让前端可视化、让 review/quiz 出题按遗忘程度加权。

**Architecture:** 在 `knowledge_graph_node` 内统一做 "fetch mastery → 注入 prompt → 生成 → 注解"。所有 KG（无论来自 LLM 还是 kc_skills 规则路径）走同一个后置注解函数。遗忘曲线封装为 `ForgettingModel` Protocol，默认实现 `EbbinghausV1`，便于未来替换。Quiz 节点读取节点上的 `mastery.retention` 做加权出题。

**Tech Stack:** Python 3.11, Pydantic v2, Jinja2, pytest, existing FastAPI + LangGraph stack。

---

## File Structure

**New files:**
- `src/forgetting/__init__.py` — package marker, exports `ForgettingModel`, `get_forgetting_model`
- `src/forgetting/base.py` — `ForgettingModel` Protocol
- `src/forgetting/ebbinghaus.py` — `EbbinghausV1` 默认实现
- `src/forgetting/registry.py` — `get_forgetting_model(name)` 工厂
- `src/agent/mastery_annotator.py` — `annotate_graph_with_mastery()` 主入口 + 名字归一化 + status 阈值
- `tests/test_forgetting.py`
- `tests/test_mastery_annotator.py`
- `tests/test_knowledge_graph_node_mastery.py`

**Modified files:**
- `src/llm/schemas.py` — 新增 `MasteryMeta`，给 `KnowledgeNode` 加 `mastery: MasteryMeta | None = None`
- `src/prompts/knowledge_graph.j2` — 新增 `existing_mastery` 块
- `src/agent/nodes/knowledge_graph.py` — `_llm_generate_graph` 接收 mastery 列表参数；`knowledge_graph_node` 调 `fetch_mastery` + 调注解
- `src/agent/nodes/quiz.py` — 按 `1 - retention` 加权出题数
- `tests/test_prompts.py` — 验证 prompt 渲染
- `tests/test_schemas.py` — 验证新 schema

---

## Task 1: ForgettingModel Protocol + EbbinghausV1

**Files:**
- Create: `src/forgetting/__init__.py`
- Create: `src/forgetting/base.py`
- Create: `src/forgetting/ebbinghaus.py`
- Create: `src/forgetting/registry.py`
- Test: `tests/test_forgetting.py`

- [ ] **Step 1: Write the failing test for the Protocol shape and Ebbinghaus math**

Create `tests/test_forgetting.py`:

```python
"""Forgetting model contract + EbbinghausV1 default."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.forgetting import ForgettingModel, get_forgetting_model
from src.forgetting.ebbinghaus import EbbinghausV1


UTC = timezone.utc


class TestEbbinghausV1:
    def setup_method(self) -> None:
        self.model = EbbinghausV1()
        self.now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)

    def test_unseen_topic_returns_zero(self) -> None:
        # last_accuracy is None → never tested → no retention to speak of
        r = self.model.retention(
            last_seen=self.now - timedelta(days=1),
            last_accuracy=None,
            session_count=0,
            now=self.now,
        )
        assert r == 0.0

    def test_just_seen_perfect_score_high_retention(self) -> None:
        r = self.model.retention(
            last_seen=self.now - timedelta(hours=1),
            last_accuracy=1.0,
            session_count=1,
            now=self.now,
        )
        # ~1 hour after a perfect first study: should be near 1
        assert r > 0.95

    def test_decay_over_time(self) -> None:
        r1 = self.model.retention(
            last_seen=self.now - timedelta(days=1),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        r7 = self.model.retention(
            last_seen=self.now - timedelta(days=7),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        r30 = self.model.retention(
            last_seen=self.now - timedelta(days=30),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        assert r1 > r7 > r30

    def test_better_accuracy_better_retention(self) -> None:
        kw = dict(last_seen=self.now - timedelta(days=3), session_count=1, now=self.now)
        r_low = self.model.retention(last_accuracy=0.2, **kw)
        r_high = self.model.retention(last_accuracy=1.0, **kw)
        assert r_high > r_low

    def test_more_repetitions_better_retention(self) -> None:
        kw = dict(
            last_seen=self.now - timedelta(days=3),
            last_accuracy=0.8, now=self.now,
        )
        r_once = self.model.retention(session_count=1, **kw)
        r_many = self.model.retention(session_count=5, **kw)
        assert r_many > r_once

    def test_retention_clamped_to_unit_interval(self) -> None:
        # Future last_seen (clock skew) — must not produce >1
        r = self.model.retention(
            last_seen=self.now + timedelta(days=1),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        assert 0.0 <= r <= 1.0

    def test_name_attribute(self) -> None:
        assert self.model.name == "ebbinghaus_v1"

    def test_formula_exact(self) -> None:
        # Pin one concrete value so a refactor that changes the formula
        # is caught immediately.
        # accuracy=0.5, session_count=1, days=2:
        #   S = 1.0 + 4.0*0.5 + 1.0*(1-1) = 3.0
        #   R = exp(-2/3) ≈ 0.5134
        r = self.model.retention(
            last_seen=self.now - timedelta(days=2),
            last_accuracy=0.5, session_count=1, now=self.now,
        )
        assert math.isclose(r, math.exp(-2.0 / 3.0), rel_tol=1e-6)


class TestRegistry:
    def test_default_returns_ebbinghaus(self) -> None:
        m = get_forgetting_model()
        assert m.name == "ebbinghaus_v1"

    def test_named_lookup(self) -> None:
        m = get_forgetting_model("ebbinghaus_v1")
        assert isinstance(m, EbbinghausV1)

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown forgetting model"):
            get_forgetting_model("does_not_exist")


class TestProtocolConformance:
    def test_ebbinghaus_satisfies_protocol(self) -> None:
        m: ForgettingModel = EbbinghausV1()  # static type check by assignment
        assert hasattr(m, "name")
        assert callable(m.retention)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_forgetting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.forgetting'`

- [ ] **Step 3: Implement `src/forgetting/base.py`**

```python
"""Forgetting model contract.

A ForgettingModel takes a (timestamp, accuracy, repetition count) tuple
and returns a retention score in [0, 1]. Implementations are pure functions
on their input — no I/O, no caching — so they're safe to call per-node.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ForgettingModel(Protocol):
    name: str

    def retention(
        self,
        *,
        last_seen: datetime,
        last_accuracy: float | None,
        session_count: int,
        now: datetime,
    ) -> float:
        """Return retention probability in [0, 1].

        last_accuracy is None means the subtopic was never tested,
        which the implementation should treat as zero retention.
        """
        ...
```

- [ ] **Step 4: Implement `src/forgetting/ebbinghaus.py`**

```python
"""Default Ebbinghaus retention curve.

R = exp(-t / S)   where t is days since last_seen and S is "stability"
in days. Stability grows with last_accuracy (you remember mastered
material longer) and with session_count (spaced repetition compounds).
"""
from __future__ import annotations

import math
from datetime import datetime


class EbbinghausV1:
    name = "ebbinghaus_v1"

    def __init__(
        self,
        *,
        base_stability_days: float = 1.0,
        accuracy_boost: float = 4.0,
        repetition_boost: float = 1.0,
    ) -> None:
        self.S0 = base_stability_days
        self.alpha = accuracy_boost
        self.beta = repetition_boost

    def retention(
        self,
        *,
        last_seen: datetime,
        last_accuracy: float | None,
        session_count: int,
        now: datetime,
    ) -> float:
        if last_accuracy is None:
            return 0.0
        days = max((now - last_seen).total_seconds() / 86400.0, 0.0)
        stability = (
            self.S0
            + self.alpha * last_accuracy
            + self.beta * max(session_count - 1, 0)
        )
        if stability <= 0:
            return 0.0
        r = math.exp(-days / stability)
        return max(0.0, min(1.0, r))
```

- [ ] **Step 5: Implement `src/forgetting/registry.py`**

```python
"""Lookup table for ForgettingModel implementations."""
from __future__ import annotations

from src.forgetting.base import ForgettingModel
from src.forgetting.ebbinghaus import EbbinghausV1

_REGISTRY: dict[str, type] = {
    "ebbinghaus_v1": EbbinghausV1,
}

DEFAULT_MODEL_NAME = "ebbinghaus_v1"


def get_forgetting_model(name: str | None = None) -> ForgettingModel:
    key = name or DEFAULT_MODEL_NAME
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(f"unknown forgetting model: {key}")
    return cls()
```

- [ ] **Step 6: Implement `src/forgetting/__init__.py`**

```python
"""Forgetting curve subsystem.

Public surface kept tiny on purpose — callers use the Protocol type
and the factory; implementations are private.
"""
from src.forgetting.base import ForgettingModel
from src.forgetting.registry import DEFAULT_MODEL_NAME, get_forgetting_model

__all__ = ["ForgettingModel", "get_forgetting_model", "DEFAULT_MODEL_NAME"]
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_forgetting.py -v`
Expected: 10 PASS

- [ ] **Step 8: Commit**

```bash
git add src/forgetting/ tests/test_forgetting.py
git commit -m "feat(forgetting): add ForgettingModel protocol + Ebbinghaus default"
```

---

## Task 2: Schema — MasteryMeta + KnowledgeNode.mastery

**Files:**
- Modify: `src/llm/schemas.py:4-9`
- Test: `tests/test_schemas.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schemas.py` (create if missing). If file already exists, add this class at the bottom:

```python
# ---- MasteryMeta + KnowledgeNode.mastery ----

from src.llm.schemas import KnowledgeNode, MasteryMeta


class TestMasteryMeta:
    def test_required_fields(self) -> None:
        m = MasteryMeta(
            last_accuracy=0.8,
            last_seen_at="2026-04-30T10:00:00Z",
            session_count=2,
            retention=0.62,
            status="partial",
            forgetting_model="ebbinghaus_v1",
        )
        assert m.status == "partial"
        assert m.retention == 0.62

    def test_last_accuracy_optional(self) -> None:
        # accuracy can be None for never-tested rows
        m = MasteryMeta(
            last_accuracy=None,
            last_seen_at="2026-04-30T10:00:00Z",
            session_count=1,
            retention=0.0,
            status="unseen",
            forgetting_model="ebbinghaus_v1",
        )
        assert m.last_accuracy is None


class TestKnowledgeNodeMastery:
    def test_default_mastery_none(self) -> None:
        n = KnowledgeNode(id="n1", name="判别式", description="...", level="core")
        assert n.mastery is None

    def test_with_mastery(self) -> None:
        n = KnowledgeNode(
            id="n1", name="判别式", description="...", level="core",
            mastery=MasteryMeta(
                last_accuracy=0.5, last_seen_at="2026-04-30T10:00:00Z",
                session_count=1, retention=0.4, status="weak",
                forgetting_model="ebbinghaus_v1",
            ),
        )
        assert n.mastery.status == "weak"

    def test_round_trip_dict(self) -> None:
        n = KnowledgeNode(
            id="n1", name="x", description="d", level="core",
            mastery=MasteryMeta(
                last_accuracy=0.9, last_seen_at="2026-04-30T10:00:00Z",
                session_count=2, retention=0.88, status="mastered",
                forgetting_model="ebbinghaus_v1",
            ),
        )
        dumped = n.model_dump()
        assert dumped["mastery"]["status"] == "mastered"
        rebuilt = KnowledgeNode.model_validate(dumped)
        assert rebuilt.mastery.status == "mastered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'MasteryMeta'` or attribute error on `KnowledgeNode.mastery`.

- [ ] **Step 3: Modify `src/llm/schemas.py`**

Replace the file head (lines 1-9) with:

```python
from typing import Literal

from pydantic import BaseModel, Field


MasteryStatus = Literal["mastered", "partial", "weak", "forgotten", "unseen"]


class MasteryMeta(BaseModel):
    """Per-node mastery snapshot — what the student knows about this node."""

    last_accuracy: float | None = None
    last_seen_at: str
    session_count: int
    retention: float  # output of the active ForgettingModel, in [0, 1]
    status: MasteryStatus
    forgetting_model: str  # e.g. "ebbinghaus_v1" — for future A/B tracking


class KnowledgeNode(BaseModel):
    id: str
    name: str
    description: str
    level: str  # "prerequisite" | "core" | "extension"
    mastery: MasteryMeta | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_schemas.py -v`
Expected: PASS for the three new test methods, plus any pre-existing schema tests still passing.

Also run the wider suite to catch fallout from the new optional field:

Run: `uv run python -m pytest tests/ -v -k "schema or graph or kc_graph"`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/llm/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): add MasteryMeta and KnowledgeNode.mastery field"
```

---

## Task 3: Mastery Annotator (name match + status thresholds + node merge)

**Files:**
- Create: `src/agent/mastery_annotator.py`
- Test: `tests/test_mastery_annotator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mastery_annotator.py`:

```python
"""Tests for graph-level mastery annotation.

Covers:
- Status thresholds at exact boundaries
- Name normalization match (exact + normalized fallback)
- Unmatched nodes get mastery=None (sentinel for "unseen" without metadata)
- forgetting_model name is propagated
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.agent.mastery_annotator import (
    _compute_status,
    _normalize_name,
    annotate_graph_with_mastery,
)
from src.forgetting.ebbinghaus import EbbinghausV1
from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode


UTC = timezone.utc


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        topic="二次方程",
        nodes=[
            KnowledgeNode(id="n1", name="判别式", description="d1", level="core"),
            KnowledgeNode(id="n2", name="求根公式", description="d2", level="core"),
            KnowledgeNode(id="n3", name="配方法", description="d3", level="extension"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )


class TestNormalizeName:
    def test_strips_whitespace(self) -> None:
        assert _normalize_name("  判别式 ") == _normalize_name("判别式")

    def test_lowercases_ascii(self) -> None:
        assert _normalize_name("Quadratic") == _normalize_name("quadratic")

    def test_strips_common_punct(self) -> None:
        assert _normalize_name("判别式（Δ）") == _normalize_name("判别式 Δ")
        assert _normalize_name("a, b, c") == _normalize_name("abc")


class TestComputeStatus:
    @pytest.mark.parametrize("retention,expected", [
        (1.00, "mastered"),
        (0.90, "mastered"),
        (0.85, "mastered"),       # boundary
        (0.84999, "partial"),
        (0.70, "partial"),
        (0.60, "partial"),        # boundary
        (0.59999, "weak"),
        (0.45, "weak"),
        (0.30, "weak"),           # boundary
        (0.29999, "forgotten"),
        (0.10, "forgotten"),
        (0.0, "forgotten"),
    ])
    def test_thresholds(self, retention: float, expected: str) -> None:
        assert _compute_status(retention) == expected


class TestAnnotateGraphWithMastery:
    def test_empty_mastery_leaves_all_nodes_unseen(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        out = annotate_graph_with_mastery(
            graph, mastery_rows=[], model=EbbinghausV1(), now=now,
        )
        for node in out.nodes:
            assert node.mastery is None  # unseen sentinel

    def test_exact_name_match_attaches_mastery(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [{
            "subtopic": "判别式",
            "topic": "二次方程",
            "last_accuracy": 1.0,
            "last_seen_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_count": 1,
        }]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is not None
        assert n1.mastery.status == "mastered"
        assert n1.mastery.forgetting_model == "ebbinghaus_v1"
        # other nodes still unseen
        n3 = next(n for n in out.nodes if n.name == "配方法")
        assert n3.mastery is None

    def test_normalized_match(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [{
            "subtopic": "判别式（Δ）",  # KG has "判别式" → match via normalization
            "topic": "二次方程",
            "last_accuracy": 0.5,
            "last_seen_at": (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_count": 1,
        }]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is not None

    def test_unmatched_row_does_not_crash(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [{
            "subtopic": "完全无关的主题",
            "topic": "其他",
            "last_accuracy": 0.9,
            "last_seen_at": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_count": 1,
        }]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        # All graph nodes still unseen
        for node in out.nodes:
            assert node.mastery is None

    def test_malformed_row_skipped(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [
            {"subtopic": "判别式"},  # missing required keys
            {
                "subtopic": "求根公式",
                "topic": "二次方程",
                "last_accuracy": 0.95,
                "last_seen_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "session_count": 1,
            },
        ]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        # 判别式 row malformed → unseen
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is None
        # 求根公式 row valid → annotated
        n2 = next(n for n in out.nodes if n.name == "求根公式")
        assert n2.mastery is not None

    def test_returns_new_graph_does_not_mutate_input(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [{
            "subtopic": "判别式", "topic": "二次方程",
            "last_accuracy": 0.9,
            "last_seen_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_count": 1,
        }]
        original_n1 = next(n for n in graph.nodes if n.name == "判别式")
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        assert original_n1.mastery is None  # original unchanged
        out_n1 = next(n for n in out.nodes if n.name == "判别式")
        assert out_n1.mastery is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_mastery_annotator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.mastery_annotator'`.

- [ ] **Step 3: Implement `src/agent/mastery_annotator.py`**

```python
"""Annotate a KnowledgeGraph with per-node mastery metadata.

Two-stage match between KG nodes and memory mastery rows:
1. exact name match (case- and whitespace-sensitive)
2. normalized name match — strip whitespace, lowercase ASCII, drop
   common punctuation. Catches LLM drift like "判别式 (Δ)" vs "判别式".

Anything still unmatched stays as `mastery=None`, which the frontend
renders as "unseen". Malformed rows are skipped (graceful degradation).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from src.forgetting.base import ForgettingModel
from src.llm.schemas import KnowledgeGraph, KnowledgeNode, MasteryMeta

logger = logging.getLogger(__name__)


# ---- thresholds (tunable; pinned by tests) ------------------------

_MASTERED_MIN = 0.85
_PARTIAL_MIN = 0.60
_WEAK_MIN = 0.30


def _compute_status(retention: float) -> str:
    if retention >= _MASTERED_MIN:
        return "mastered"
    if retention >= _PARTIAL_MIN:
        return "partial"
    if retention >= _WEAK_MIN:
        return "weak"
    return "forgotten"


# ---- name normalization -------------------------------------------

# Strip Unicode-category P (punctuation) + S (symbol) + Z (separator).
_PUNCT_RE = re.compile(
    r"[" + "".join(
        chr(c) for c in range(0x0021, 0x007F)
        if unicodedata.category(chr(c)).startswith(("P", "S"))
    ) + r"\s　（）、。，：；]+",
    re.UNICODE,
)


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).strip().lower()
    s = _PUNCT_RE.sub("", s)
    return s


# ---- timestamp parsing --------------------------------------------

def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    # Memory service emits "YYYY-MM-DDTHH:MM:SSZ"; tolerate "+00:00" too.
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1] + "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ---- main ----------------------------------------------------------

def annotate_graph_with_mastery(
    graph: KnowledgeGraph,
    *,
    mastery_rows: list[dict[str, Any]],
    model: ForgettingModel,
    now: datetime,
) -> KnowledgeGraph:
    """Return a new KnowledgeGraph with mastery attached to matching nodes."""
    by_exact: dict[str, dict[str, Any]] = {}
    by_norm: dict[str, dict[str, Any]] = {}
    for row in mastery_rows:
        sub = row.get("subtopic")
        if not isinstance(sub, str) or not sub:
            continue
        by_exact.setdefault(sub, row)
        by_norm.setdefault(_normalize_name(sub), row)

    new_nodes: list[KnowledgeNode] = []
    for node in graph.nodes:
        row = by_exact.get(node.name) or by_norm.get(_normalize_name(node.name))
        meta: MasteryMeta | None = None
        if row is not None:
            meta = _row_to_meta(row, model=model, now=now)
        new_nodes.append(node.model_copy(update={"mastery": meta}))

    return graph.model_copy(update={"nodes": new_nodes})


def _row_to_meta(
    row: dict[str, Any], *, model: ForgettingModel, now: datetime
) -> MasteryMeta | None:
    last_seen_str = row.get("last_seen_at")
    last_seen = _parse_ts(last_seen_str) if isinstance(last_seen_str, str) else None
    if last_seen is None:
        logger.debug("dropping mastery row with bad last_seen_at: %r", row)
        return None
    try:
        session_count = int(row.get("session_count") or 0)
    except (TypeError, ValueError):
        return None
    last_accuracy = row.get("last_accuracy")
    if last_accuracy is not None:
        try:
            last_accuracy = float(last_accuracy)
        except (TypeError, ValueError):
            last_accuracy = None

    retention = model.retention(
        last_seen=last_seen,
        last_accuracy=last_accuracy,
        session_count=session_count,
        now=now,
    )
    status = "unseen" if last_accuracy is None else _compute_status(retention)
    return MasteryMeta(
        last_accuracy=last_accuracy,
        last_seen_at=last_seen_str,
        session_count=session_count,
        retention=retention,
        status=status,
        forgetting_model=model.name,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_mastery_annotator.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/mastery_annotator.py tests/test_mastery_annotator.py
git commit -m "feat(agent): annotate KG nodes with mastery via forgetting model"
```

---

## Task 4: KG Prompt — `existing_mastery` block

**Files:**
- Modify: `src/prompts/knowledge_graph.j2`
- Test: `tests/test_prompts.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompts.py` (or create if missing — use existing `render_prompt` import):

```python
# ---- KG prompt: existing_mastery block ----

from src.prompts.loader import render_prompt


class TestKnowledgeGraphMasteryBlock:
    def test_no_mastery_no_block(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="二次方程",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[],
        )
        assert "已有学习记录" not in text
        assert "Prior Mastery Hints" not in text

    def test_mastery_block_lists_subtopics(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="二次方程",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[
                {
                    "subtopic": "判别式",
                    "last_accuracy": 0.5,
                    "last_seen_at": "2026-04-30T10:00:00Z",
                    "status": "weak",
                },
                {
                    "subtopic": "求根公式",
                    "last_accuracy": 1.0,
                    "last_seen_at": "2026-04-30T11:00:00Z",
                    "status": "mastered",
                },
            ],
        )
        assert "判别式" in text
        assert "求根公式" in text
        # The block must instruct the LLM to reuse subtopic names as node names
        assert "复用" in text or "reuse" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_prompts.py::TestKnowledgeGraphMasteryBlock -v`
Expected: FAIL — second test fails because new block isn't in the template yet.

- [ ] **Step 3: Modify `src/prompts/knowledge_graph.j2`**

Replace the file with:

```jinja
You are an educational knowledge graph builder. Given a learning topic, generate a knowledge graph of related concepts.

{% if skill_instructions %}
## Teaching Method
{{ skill_instructions }}
Organize the knowledge graph according to the above teaching method.

{% endif %}
{% if focus_directive %}
## Focus Directive
{{ focus_directive }}

{% endif %}
{% if review_strong_kps or review_weak_kps %}
## Mastery Constraints (HARD RULES — must follow)
{% if review_strong_kps %}
- The student has DEMONSTRATED MASTERY of: {{ review_strong_kps | join(', ') }}.
  - DO NOT include these as `core` or `prerequisite` nodes.
  - Either OMIT them entirely, or include only as `extension` nodes if they are genuinely advanced.
  - Treat the student as already competent — do not re-teach them.
{% endif %}
{% if review_weak_kps %}
- The student is STILL WEAK on: {{ review_weak_kps | join(', ') }}.
  - These MUST appear as `core` nodes.
  - Add prerequisite nodes that scaffold understanding of these weak points.
{% endif %}

{% endif %}
{% if existing_mastery %}
## 已有学习记录（Prior Mastery Hints）
该学生在此主题下的历史学习子主题如下：
{% for m in existing_mastery %}
- {{ m.subtopic }}（上次正确率 {{ m.last_accuracy }}，{{ m.last_seen_at }}，状态 {{ m.status }}）
{% endfor %}

生成 KG 时，若你打算包含这些已有子主题，请**直接复用上述名字作为节点 `name`**（一字不差），以便系统跨会话关联记忆。新增的概念可自由命名。

{% endif %}
Topic: {{ topic }}
Maximum nodes: {{ max_nodes }}

Generate a knowledge graph with:
1. The core topic and its prerequisite concepts
2. Each node has: id, name, description, level (prerequisite/core/extension)
3. Edges show prerequisite relationships (source → target means source is prerequisite of target)
4. Include 4-{{ max_nodes }} nodes covering prerequisites and core concepts

Return a structured knowledge graph.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_prompts.py::TestKnowledgeGraphMasteryBlock -v`
Expected: PASS.

Also re-run the existing prompt tests to make sure the new template still renders for old call sites:

Run: `uv run python -m pytest tests/test_prompts.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/prompts/knowledge_graph.j2 tests/test_prompts.py
git commit -m "feat(prompts): add existing_mastery block to KG generation prompt"
```

---

## Task 5: knowledge_graph_node — fetch mastery, inject prompt, annotate

**Files:**
- Modify: `src/agent/nodes/knowledge_graph.py` (entire file rewritten)
- Test: `tests/test_knowledge_graph_node_mastery.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_graph_node_mastery.py`:

```python
"""Integration test for knowledge_graph_node with mastery annotation.

Verifies:
- fetch_mastery is called with the topic
- existing_mastery is passed into the prompt
- annotate_graph_with_mastery is applied regardless of generation path
- empty mastery degrades gracefully (no annotation, no prompt block)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agent.nodes.knowledge_graph import knowledge_graph_node
from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode

UTC = timezone.utc


def _fake_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        topic="二次方程",
        nodes=[
            KnowledgeNode(id="n1", name="判别式", description="d", level="core"),
            KnowledgeNode(id="n2", name="求根公式", description="d", level="core"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_kc_path_still_annotated(
    mock_resolve: MagicMock, mock_fetch: MagicMock,
) -> None:
    """When kc_skills resolver hits, the result must still be annotated."""
    mock_resolve.return_value = _fake_graph()
    now = datetime(2026, 5, 7, 12, tzinfo=UTC)
    mock_fetch.return_value = [{
        "subtopic": "判别式",
        "topic": "二次方程",
        "last_accuracy": 0.9,
        "last_seen_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_count": 1,
    }]

    state = {"topic": "二次方程", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    graph = out["knowledge_graph"]
    n1 = next(n for n in graph.nodes if n.name == "判别式")
    assert n1.mastery is not None
    assert n1.mastery.status in {"mastered", "partial"}


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph._llm_generate_graph")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_llm_path_receives_mastery_in_prompt(
    mock_resolve: MagicMock,
    mock_llm: MagicMock,
    mock_fetch: MagicMock,
) -> None:
    """LLM generator must be called with existing_mastery list."""
    mock_resolve.return_value = None  # force LLM fallback
    mock_llm.return_value = _fake_graph()
    mock_fetch.return_value = [{
        "subtopic": "判别式", "topic": "二次方程",
        "last_accuracy": 0.9, "last_seen_at": "2026-05-05T10:00:00Z",
        "session_count": 1,
    }]

    state = {"topic": "二次方程", "use_kc_skills": False}
    knowledge_graph_node(state)

    args, kwargs = mock_llm.call_args
    # _llm_generate_graph must have received the mastery list
    assert "existing_mastery" in kwargs or len(args) >= 3
    mastery_arg = kwargs.get("existing_mastery") or args[2]
    assert any(m.get("subtopic") == "判别式" for m in mastery_arg)


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_no_mastery_means_unseen_nodes(
    mock_resolve: MagicMock, mock_fetch: MagicMock,
) -> None:
    mock_resolve.return_value = _fake_graph()
    mock_fetch.return_value = []
    state = {"topic": "二次方程", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    graph = out["knowledge_graph"]
    for node in graph.nodes:
        assert node.mastery is None


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_fetch_mastery_failure_does_not_break_graph(
    mock_resolve: MagicMock, mock_fetch: MagicMock,
) -> None:
    """fetch_mastery already returns [] on error; verify node still works."""
    mock_resolve.return_value = _fake_graph()
    mock_fetch.return_value = []  # graceful-degradation contract
    state = {"topic": "二次方程", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    assert "knowledge_graph" in out
    assert len(out["knowledge_graph"].nodes) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_knowledge_graph_node_mastery.py -v`
Expected: FAIL — `fetch_mastery` is not yet imported in `knowledge_graph.py`, mocks won't bind.

- [ ] **Step 3: Rewrite `src/agent/nodes/knowledge_graph.py`**

Replace the entire file with:

```python
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage

from src.agent.mastery_annotator import annotate_graph_with_mastery
from src.agent.state import LearningState
from src.forgetting import get_forgetting_model
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import KnowledgeGraph
from src.memory_client import DEFAULT_USER_ID, fetch_mastery
from src.prompts.loader import render_prompt
from src.skills.kc_graph import resolve_kc_graph
from src.skills.loader import get_skill_section

logger = logging.getLogger(__name__)

MAX_NODES = 10


def _build_prompt_mastery(
    mastery_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project raw /mastery rows into the smaller shape used by the prompt."""
    out: list[dict[str, Any]] = []
    for row in mastery_rows:
        sub = row.get("subtopic")
        if not isinstance(sub, str) or not sub:
            continue
        out.append({
            "subtopic": sub,
            "last_accuracy": row.get("last_accuracy"),
            "last_seen_at": row.get("last_seen_at"),
            # Coarse status hint for the LLM — annotator will recompute
            # the authoritative one. We only need a string here.
            "status": _coarse_status_for_prompt(row.get("last_accuracy")),
        })
    return out


def _coarse_status_for_prompt(acc: Any) -> str:
    if acc is None:
        return "unseen"
    try:
        a = float(acc)
    except (TypeError, ValueError):
        return "unseen"
    if a >= 0.85:
        return "mastered"
    if a >= 0.6:
        return "partial"
    return "weak"


def _llm_generate_graph(
    topic: str,
    state: LearningState,
    existing_mastery: list[dict[str, Any]] | None = None,
) -> KnowledgeGraph | None:
    """Render the knowledge_graph prompt and call the LLM; return None on failure."""
    skill_instructions = get_skill_section(state, "knowledge_graph")
    review_result = state.get("review_result") or {}
    focus_directive = review_result.get("focus_directive", "")
    review_weak_kps = list(review_result.get("weak_kps") or [])
    review_strong_kps = list(review_result.get("strong_kps") or [])
    prompt = render_prompt(
        "knowledge_graph",
        topic=topic,
        max_nodes=MAX_NODES,
        skill_instructions=skill_instructions,
        focus_directive=focus_directive,
        review_weak_kps=review_weak_kps,
        review_strong_kps=review_strong_kps,
        existing_mastery=existing_mastery or [],
    )
    try:
        client = get_llm_client()
        return client.chat.completions.create(
            model=get_model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            response_model=KnowledgeGraph,
        )
    except Exception:
        logger.exception("Knowledge graph generation failed")
        return None


def knowledge_graph_node(state: LearningState) -> dict:
    """Generate knowledge graph for the given topic, annotated with mastery.

    Steps:
    1. Pull mastery rows for (user, topic) from the memory service.
    2. Generate the KG via kc_skills resolver, falling back to LLM. The LLM
       is given the mastery list so it can reuse subtopic names as node names.
    3. Annotate the resulting KG with per-node MasteryMeta via the
       configured ForgettingModel.
    """
    topic = state.get("topic", "")

    mastery_rows = fetch_mastery(DEFAULT_USER_ID, topic=topic) if topic else []

    graph: KnowledgeGraph | None = None
    if state.get("use_kc_skills", True):
        graph = resolve_kc_graph(topic, max_nodes=MAX_NODES)

    if graph is None:
        prompt_mastery = _build_prompt_mastery(mastery_rows)
        graph = _llm_generate_graph(topic, state, existing_mastery=prompt_mastery)

    if graph is None:
        return {
            "messages": [AIMessage(content="知识图谱生成失败，请稍后再试。")],
            "mode": "chat",
        }

    # Post-process: annotate every node, regardless of which generator made it.
    model = get_forgetting_model()
    graph = annotate_graph_with_mastery(
        graph,
        mastery_rows=mastery_rows,
        model=model,
        now=datetime.now(timezone.utc),
    )

    reply = (
        f"我已经为你梳理了「{topic}」的知识图谱，"
        f"包含 {len(graph.nodes)} 个知识点。准备好了就点击开始诊断！"
    )
    return {
        "messages": [AIMessage(content=reply)],
        "knowledge_graph": graph,
        "mode": "graph",
    }
```

- [ ] **Step 4: Run new test to verify it passes**

Run: `uv run python -m pytest tests/test_knowledge_graph_node_mastery.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Run wider regression to make sure existing flows still work**

Run: `uv run python -m pytest tests/ -v --ignore=tests/test_e2e.py`
Expected: All previously-passing tests still pass. (e2e excluded because it may need a running memory service.)

If `test_graph.py` or `test_review_node.py` failed because they didn't expect a `fetch_mastery` HTTP call, patch them by mocking `src.agent.nodes.knowledge_graph.fetch_mastery` to return `[]` in their setup. Add such patches as needed — same idea as the new tests above. Do not weaken the production code to satisfy old tests.

- [ ] **Step 6: Commit**

```bash
git add src/agent/nodes/knowledge_graph.py tests/test_knowledge_graph_node_mastery.py tests/
git commit -m "feat(graph-node): wire fetch_mastery + annotate into knowledge_graph_node"
```

---

## Task 6: Quiz node — weight question count by `1 − retention`

**Files:**
- Modify: `src/agent/nodes/quiz.py:16-61` (the `quiz_node` blocking generator path) and the SSE generator path `quiz_generate_progressive`
- Test: `tests/test_quiz_node_weighting.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_quiz_node_weighting.py`:

```python
"""Quiz weighting: forgotten/weak nodes get more questions, mastered get fewer."""
from __future__ import annotations

from src.agent.nodes.quiz import _allocate_questions_per_node
from src.llm.schemas import KnowledgeNode, MasteryMeta


def _node(name: str, retention: float | None = None, status: str = "unseen") -> KnowledgeNode:
    if retention is None:
        return KnowledgeNode(id=name, name=name, description="d", level="core")
    return KnowledgeNode(
        id=name, name=name, description="d", level="core",
        mastery=MasteryMeta(
            last_accuracy=0.5, last_seen_at="2026-05-01T10:00:00Z",
            session_count=1, retention=retention, status=status,
            forgetting_model="ebbinghaus_v1",
        ),
    )


class TestAllocateQuestionsPerNode:
    def test_no_mastery_uniform(self) -> None:
        nodes = [_node("a"), _node("b"), _node("c")]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        # Without mastery info, every node gets the base count
        assert alloc == {"a": 2, "b": 2, "c": 2}

    def test_forgotten_gets_more_than_mastered(self) -> None:
        nodes = [
            _node("forgot", retention=0.05, status="forgotten"),
            _node("solid", retention=0.95, status="mastered"),
        ]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        assert alloc["forgot"] > alloc["solid"]

    def test_min_one_per_node(self) -> None:
        # Even a fully-mastered node should still get at least 1 question
        # (acts as a spot-check)
        nodes = [_node("m", retention=1.0, status="mastered")]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        assert alloc["m"] >= 1

    def test_total_count_close_to_base_times_n(self) -> None:
        nodes = [
            _node("a", retention=0.1, status="forgotten"),
            _node("b", retention=0.5, status="weak"),
            _node("c", retention=0.9, status="mastered"),
        ]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        # Total budget is preserved (within ±N rounding)
        assert sum(alloc.values()) == 2 * 3 or abs(sum(alloc.values()) - 6) <= 1

    def test_unseen_node_gets_baseline_weight(self) -> None:
        # Unseen nodes get a moderate priority — between forgotten and mastered
        nodes = [
            _node("unseen"),
            _node("mastered_one", retention=0.95, status="mastered"),
        ]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        assert alloc["unseen"] >= alloc["mastered_one"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_quiz_node_weighting.py -v`
Expected: FAIL — `_allocate_questions_per_node` doesn't exist yet.

- [ ] **Step 3: Modify `src/agent/nodes/quiz.py`**

Replace the file with:

```python
from __future__ import annotations

import logging
from typing import Sequence

from langchain_core.messages import AIMessage

from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import KnowledgeNode, QuizQuestion
from src.prompts.loader import render_kc_skill_block, render_prompt
from src.skills.loader import get_skill_section

logger = logging.getLogger(__name__)

QUESTIONS_PER_POINT = 2
UNSEEN_PRIORITY = 0.7  # treat unseen nodes as moderately needing attention
MIN_PER_NODE = 1


def _priority(node: KnowledgeNode) -> float:
    """Higher priority → more questions. Mastery-aware when possible."""
    if node.mastery is None:
        return UNSEEN_PRIORITY
    return max(0.0, 1.0 - float(node.mastery.retention))


def _allocate_questions_per_node(
    nodes: Sequence[KnowledgeNode],
    *,
    base_per_node: int = QUESTIONS_PER_POINT,
) -> dict[str, int]:
    """Distribute base_per_node × N questions across nodes by priority.

    Every node gets at least MIN_PER_NODE. Remaining budget is split in
    proportion to priority. Total is preserved (modulo rounding ±len(nodes)).
    """
    n = len(nodes)
    if n == 0:
        return {}

    # If no mastery info anywhere → uniform (preserves legacy behavior)
    if all(node.mastery is None for node in nodes):
        return {node.name: base_per_node for node in nodes}

    total_budget = base_per_node * n
    weights = [_priority(node) for node in nodes]
    total_w = sum(weights)
    if total_w <= 0:
        return {node.name: base_per_node for node in nodes}

    # Step 1: floor of weighted share, with a min of MIN_PER_NODE
    raw = [max(MIN_PER_NODE, int((w / total_w) * total_budget)) for w in weights]
    # Step 2: distribute leftover by largest fractional remainder
    used = sum(raw)
    leftover = total_budget - used
    if leftover > 0:
        # Give extras to nodes with highest priority first
        order = sorted(range(n), key=lambda i: -weights[i])
        for i in order[:leftover]:
            raw[i] += 1
    elif leftover < 0:
        # Trim from lowest-priority nodes (but never below MIN_PER_NODE)
        order = sorted(range(n), key=lambda i: weights[i])
        for i in order:
            if leftover == 0:
                break
            if raw[i] > MIN_PER_NODE:
                raw[i] -= 1
                leftover += 1

    return {node.name: raw[i] for i, node in enumerate(nodes)}


def _coerce_node(item) -> KnowledgeNode:
    """Accept either a KnowledgeNode or a dict (after JSON round-trip)."""
    if isinstance(item, KnowledgeNode):
        return item
    return KnowledgeNode.model_validate(item)


def quiz_node(state: LearningState) -> dict:
    """Generate quiz questions or serve the next question."""
    questions = state.get("questions", [])
    current_idx = state.get("current_question_index", 0)

    if not questions:
        graph = state.get("knowledge_graph")
        if not graph:
            return {"mode": "chat", "messages": [AIMessage(content="请先选择一个学习主题。")]}

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
            )
            batch = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                response_model=list[QuizQuestion],
            )
            all_generated.extend(batch)

        return {
            "questions": all_generated,
            "current_question_index": 0,
            "answers": [],
            "mode": "quiz",
            "messages": [AIMessage(content=f"共 {len(all_generated)} 道题，开始作答！")],
        }

    if current_idx < len(questions):
        q = questions[current_idx]
        q_name = q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
        q_text = q.question if hasattr(q, "question") else q["question"]
        opts = q.options if hasattr(q, "options") else q["options"]
        options_text = "\n".join(f"  {chr(65+i)}) {opt}" for i, opt in enumerate(opts))
        reply = f"[{q_name}] 第 {current_idx+1}/{len(questions)} 题\n\n{q_text}\n\n{options_text}"
        return {"mode": "quiz", "messages": [AIMessage(content=reply)]}

    return {"mode": "result"}


def quiz_generate_progressive(state: dict):
    """Generate quiz questions one knowledge point at a time, weighted by mastery.

    Yields list[QuizQuestion] batches.
    """
    graph = state.get("knowledge_graph")
    if not graph:
        return

    raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    nodes = [_coerce_node(n) for n in raw_nodes]
    per_node = _allocate_questions_per_node(nodes)

    skill_instructions = get_skill_section(state, "quiz")
    client = get_llm_client()

    for node in nodes:
        count = per_node.get(node.name, QUESTIONS_PER_POINT)
        if count <= 0:
            continue
        point = {"name": node.name, "description": node.description}
        kc_skill_block = render_kc_skill_block((state.get("kc_skills") or {}).get(node.name))
        prompt = render_prompt(
            "quiz_generate",
            knowledge_points=[point],
            questions_per_point=count,
            skill_instructions=skill_instructions,
            kc_skill_block=kc_skill_block,
        )
        try:
            batch = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
                response_model=list[QuizQuestion],
            )
            yield batch
        except Exception:
            logger.exception("Failed to generate quiz for knowledge point: %s", node.name)
```

- [ ] **Step 4: Run new test to verify it passes**

Run: `uv run python -m pytest tests/test_quiz_node_weighting.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Run wider regression**

Run: `uv run python -m pytest tests/ -v --ignore=tests/test_e2e.py`
Expected: All passing tests still pass. If existing tests assumed exactly `2 × len(nodes)` questions and now hit weighting, they should mock `state["knowledge_graph"]` with `mastery=None` on every node — which already produces uniform allocation per Task 3 design. Inspect any failure carefully — DO NOT weaken weighting logic to satisfy a stale test; instead pass mastery-free fixtures.

- [ ] **Step 6: Commit**

```bash
git add src/agent/nodes/quiz.py tests/test_quiz_node_weighting.py
git commit -m "feat(quiz): allocate question count per node by 1 - retention"
```

---

## Task 7: End-to-end smoke test

**Files:**
- Create: `tests/test_kg_mastery_e2e.py`

This is a single in-process integration test that exercises the whole new path
without hitting the network: mock memory_client, mock LLM client, but use
real schemas / real annotator / real prompt / real forgetting model.

- [ ] **Step 1: Write the test**

Create `tests/test_kg_mastery_e2e.py`:

```python
"""End-to-end smoke for KG mastery annotation.

Real: schemas, annotator, prompt rendering, forgetting model, quiz allocation.
Mocked: memory HTTP client, LLM HTTP client.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agent.nodes.knowledge_graph import knowledge_graph_node
from src.agent.nodes.quiz import _allocate_questions_per_node
from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode

UTC = timezone.utc


@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
def test_full_pipeline_kc_path(
    mock_fetch: MagicMock, mock_resolve: MagicMock,
) -> None:
    mock_resolve.return_value = KnowledgeGraph(
        topic="二次方程",
        nodes=[
            KnowledgeNode(id="n1", name="判别式", description="d", level="core"),
            KnowledgeNode(id="n2", name="求根公式", description="d", level="core"),
            KnowledgeNode(id="n3", name="韦达定理", description="d", level="extension"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )
    now_iso_recent = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_iso_old = (datetime.now(UTC) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mock_fetch.return_value = [
        {
            "subtopic": "判别式", "topic": "二次方程",
            "last_accuracy": 1.0, "last_seen_at": now_iso_recent,
            "session_count": 2,
        },
        {
            "subtopic": "求根公式", "topic": "二次方程",
            "last_accuracy": 0.5, "last_seen_at": now_iso_old,
            "session_count": 1,
        },
    ]

    out = knowledge_graph_node({"topic": "二次方程", "use_kc_skills": True})
    graph = out["knowledge_graph"]

    by_name = {n.name: n for n in graph.nodes}
    # Recently-mastered: status mastered or partial
    assert by_name["判别式"].mastery is not None
    assert by_name["判别式"].mastery.status in {"mastered", "partial"}
    # Long-ago weak: status forgotten or weak
    assert by_name["求根公式"].mastery is not None
    assert by_name["求根公式"].mastery.status in {"weak", "forgotten"}
    # Never seen: mastery is None
    assert by_name["韦达定理"].mastery is None

    # Quiz allocation should give the forgotten node more questions
    alloc = _allocate_questions_per_node(graph.nodes)
    assert alloc["求根公式"] >= alloc["判别式"]
```

- [ ] **Step 2: Run it**

Run: `uv run python -m pytest tests/test_kg_mastery_e2e.py -v`
Expected: 1 PASS.

- [ ] **Step 3: Run full suite one more time**

Run: `uv run python -m pytest tests/ -v --ignore=tests/test_e2e.py`
Expected: ALL PASS.

- [ ] **Step 4: Lint + format**

Run:
```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```
Expected: No errors. If `ruff format` rewrites files, stage them.

- [ ] **Step 5: Commit**

```bash
git add tests/test_kg_mastery_e2e.py
git commit -m "test(integration): e2e smoke for KG mastery annotation + quiz weighting"
```

---

## Task 8: Manual verification checklist

This is **not** automated — run through it before declaring the feature done.

- [ ] **Step 1: Start both services**

Terminal 1:
```bash
cd student_memory && SM_USE_FAKE_EMBEDDER=1 SM_EMBEDDING_DIM=16 uv run uvicorn app.main:app --port 8090
```
Terminal 2:
```bash
uv run python run/serve.py
```

Both should reach `Application startup complete.` without error.

- [ ] **Step 2: Seed mastery for a topic**

Open the demo at `http://localhost:8080`, complete a full quiz on topic "勾股定理" (or any topic). After diagnosis, the lesson task fires `ingest_session` automatically — check terminal 2 for `Ingested session ... _lesson: N memory units`.

- [ ] **Step 3: Re-enter the same topic**

Start a new chat, ask for the same topic again. Expected behaviors:
- Backend logs show `fetch_mastery` returned non-empty rows for that topic.
- `GET /api/chat/{id}/state` response includes `knowledge_graph.nodes[*].mastery` with non-null entries on the previously-tested subtopics.
- LLM-generated KG (when kc_skills doesn't hit) has node names that match the mastery subtopic names.

- [ ] **Step 4: Verify quiz weighting visible in DOM/state**

After "开始诊断", check that the generated `state.questions` has more entries on weak/forgotten subtopics than on mastered ones. This should be visible by counting question entries per `knowledge_point`.

- [ ] **Step 5: Clean up the test cache**

The manual seed will have written rows to `student_memory/data/memory.db`. If they're noise, delete:
```bash
rm student_memory/data/memory.db student_memory/data/hnsw.bin
```

- [ ] **Step 6: Final commit (only if any docs/comments tweaks emerged from manual testing)**

```bash
git status        # should be clean
```
If anything changed during verification (only typos/comments allowed at this point), commit with `chore: post-verification cleanup`.

---

## Self-Review Notes

**Spec coverage:**
- ✅ Decision 1 (a) "替换现有 KG 生成逻辑" — Task 5 wires every KG path through `annotate_graph_with_mastery`.
- ✅ Decision 2 (a)+(b) "字面精确 + 归一化兜底" — Task 3's `annotate_graph_with_mastery` first tries `by_exact`, falls back to `by_norm`.
- ✅ Decision 3 "默认阈值 + 留接口" — Task 1 ships `ForgettingModel` Protocol + registry; Task 3 pins thresholds at 0.85/0.6/0.3 with the parametrized test guarding boundaries.
- ✅ "驱动 review/quiz" — Task 6 reads `node.mastery.retention` for question weighting.
- ✅ "后端给全量、前端决定呈现" — `MasteryMeta` includes `last_accuracy / last_seen_at / session_count / retention / status / forgetting_model`. Frontend can read any subset.

**Type consistency:** `MasteryMeta`, `KnowledgeNode.mastery`, `_compute_status`, `_normalize_name`, `_allocate_questions_per_node`, `annotate_graph_with_mastery`, `EbbinghausV1.retention`, `get_forgetting_model` — all referenced names defined in this plan, signatures consistent across tasks.

**No placeholders:** every step shows real code or a real command. No "TBD"/"similar to"/"add error handling" hand-waves.
