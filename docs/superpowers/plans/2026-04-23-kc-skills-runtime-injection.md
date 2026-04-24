# KC Skills Runtime Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject per-knowledge-point "KC skills" (pre-generated metadata from `XES3G5M/metadata/kc_skills.json`) into the LangGraph agent's quiz, lesson, and slide pipelines; surface the student-facing fields (考察重点 / 易错点 / 难度提示) as a collapsible card on the lesson page.

**Architecture:** A new synchronous LangGraph node `kc_skill_resolver` runs after `knowledge_graph` and populates `state["kc_skills"]` (a dict keyed by figure-node name, values = `KCSkill.model_dump()`). Resolution is two-step: leaf-name rule match first (zero token cost), then one batched LLM fallback call for unmatched nodes. Downstream quiz / lesson / slide-outline prompts read the block through a shared `render_kc_skill_block()` helper and a `{{ kc_skill_block }}` template variable. The lesson page renders a second (independent) `<details>` card next to the existing pedagogy-skill card.

**Tech Stack:** Python 3.11 (uv), FastAPI, LangGraph, Instructor + OpenAI SDK, Jinja2, pytest, vanilla JS + Tailwind.

---

## Spec Reference

Design spec: `docs/superpowers/specs/2026-04-23-kc-skills-runtime-injection-design.md`. The plan below is a direct translation of that spec into tasks. Read the spec first if architectural context is needed.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/agent/state.py` | Modify | Add `kc_skills: dict \| None` and `use_kc_skills: bool` fields |
| `src/api/chat.py` | Modify | Add the two new keys to `_DEFAULT_STATE`, read `USE_KC_SKILLS` env var |
| `src/skills/kc_loader.py` | Create | Load `kc_skills.json` into memory; build leaf→routes index; `resolve_by_leaf()` |
| `src/prompts/kc_skill_block.j2` | Create | Render a single KCSkill dict into a markdown snippet (empty if falsy) |
| `src/prompts/kc_resolver.j2` | Create | LLM fallback prompt: map unresolved node names to candidate routes |
| `src/prompts/loader.py` | Modify | Add `render_kc_skill_block()` helper |
| `src/llm/schemas.py` | Modify | Add `ResolverMapping` Pydantic model (for instructor call) |
| `src/agent/nodes/kc_skill_resolver.py` | Create | The new LangGraph node |
| `src/agent/nodes/__init__.py` | Modify | Export the new node |
| `src/agent/graph.py` | Modify | Wire `knowledge_graph → kc_skill_resolver → END` |
| `src/agent/nodes/quiz.py` | Modify | Read `state["kc_skills"]`, inject into `quiz_generate` prompt (2 call sites) |
| `src/agent/nodes/lesson.py` | Modify | Inject into `lesson_point` prompt (2 call sites: batch + stream) |
| `src/agent/nodes/slides.py` | Modify | Inject aggregated block into `slide_outline` prompt |
| `src/prompts/quiz_generate.j2` | Modify | Add `{{ kc_skill_block }}` placeholder |
| `src/prompts/lesson_point.j2` | Modify | Add `{{ kc_skill_block }}` placeholder |
| `src/prompts/slide_outline.j2` | Modify | Add `{{ kc_skill_block }}` placeholder |
| `static/lesson.html` | Modify | Render `.kc-skill-card` alongside existing `.inline-ks-card` |
| `tests/test_kc_loader.py` | Create | Loader + normalize + resolver rule tests |
| `tests/test_kc_skill_resolver.py` | Create | Node behavior (rule / LLM / fail / disabled) |
| `tests/test_prompts.py` | Modify | Add `kc_skill_block` render tests |
| `tests/test_graph.py` | Modify | Assert `kc_skill_resolver` node is present |

Three PRs follow the spec's release plan:
- **PR 1 (Foundation)**: Tasks 1-7 — state, loader, resolver, graph wiring. No consumers, no frontend.
- **PR 2 (Consumers)**: Tasks 8-10 — quiz / lesson / slides injection.
- **PR 3 (Frontend)**: Task 11 — lesson.html card.

Each PR is independently shippable: after PR 1 the system runs exactly as before but with `state["kc_skills"]` populated silently; PR 2 lights it up in prompts; PR 3 surfaces to the student.

Throughout: every task uses TDD (test first, run to see red, implement, run to see green, commit). `uv run python -m pytest` is the command; single test: `uv run python -m pytest tests/<file>::<test_name> -v`. Lint after each task: `uv run ruff check src/ run/ tests/`.

---

# PR 1 — Foundation

## Task 1: Add KC skill state fields

**Files:**
- Modify: `src/agent/state.py`
- Modify: `src/api/chat.py` (`_DEFAULT_STATE` around line 34-57)

- [ ] **Step 1: Add fields to `LearningState`**

Open `src/agent/state.py`. After the existing `use_knowledge_skills` block (around line 34-36), append new fields:

```python
    # KC skills (knowledge-component metadata loaded from XES3G5M/metadata/kc_skills.json)
    # Orthogonal to the existing knowledge_skills field (which holds pedagogy-derived text).
    kc_skills: dict | None  # {kp_name: KCSkill.model_dump()}
    use_kc_skills: bool     # whether kc_skill_resolver should run + prompts inject
```

Keep the existing `pending_topic` field below it untouched.

- [ ] **Step 2: Add to `_DEFAULT_STATE` with env-driven default**

Open `src/api/chat.py`. At the very top of the file (after existing imports, before `_DEFAULT_STATE`), add:

```python
_USE_KC_SKILLS_DEFAULT = os.environ.get("USE_KC_SKILLS", "true").lower() == "true"
```

(If `os` is not already imported at the top of the file, add `import os`.)

Then in the `_DEFAULT_STATE` dict, add two entries right after the existing `"use_knowledge_skills": True,` line:

```python
    "kc_skills": None,
    "use_kc_skills": _USE_KC_SKILLS_DEFAULT,
```

- [ ] **Step 3: Run existing tests to confirm no regressions**

Run: `uv run python -m pytest tests/test_graph.py tests/test_api.py -v`
Expected: PASS (we added fields without breaking existing behavior).

- [ ] **Step 4: Commit**

```bash
git add src/agent/state.py src/api/chat.py
git commit -m "feat(kc-skills): add kc_skills/use_kc_skills state fields

New fields keyed independently from the existing knowledge_skills slot
(which stores pedagogy-derived per-KP text). Default toggle honors the
USE_KC_SKILLS env var."
```

---

## Task 2: Build `kc_loader` with tests (TDD)

**Files:**
- Create: `src/skills/kc_loader.py`
- Create: `tests/test_kc_loader.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_kc_loader.py`:

```python
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from src.llm.schemas import KCSkill
from src.skills import kc_loader


@pytest.fixture(autouse=True)
def reset_kc_loader_state():
    """Reset module-level caches before each test."""
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None


def _sample_json(tmp_path: Path) -> Path:
    data = {
        "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
            "leaf": "乘法原理里的其他类型",
            "skill": {
                "description": "desc A",
                "考察重点": "重点 A",
                "考察形式": "形式 A",
                "易错点": "易错 A",
                "难度提示": "难度 A",
            },
        },
        "拓展思维----计数模块----加乘原理----乘法原理": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "desc B",
                "考察重点": "重点 B",
                "考察形式": "形式 B",
                "易错点": "易错 B",
                "难度提示": "难度 B",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_kc_skills_parses_every_entry(tmp_path: Path):
    path = _sample_json(tmp_path)
    skills = kc_loader.load_kc_skills(path)
    assert len(skills) == 2
    short_route = "拓展思维----计数模块----加乘原理----乘法原理"
    assert short_route in skills
    assert isinstance(skills[short_route], KCSkill)
    assert skills[short_route].考察重点 == "重点 B"


def test_load_missing_file_returns_empty_no_raise(tmp_path: Path):
    skills = kc_loader.load_kc_skills(tmp_path / "does-not-exist.json")
    assert skills == {}


def test_load_invalid_json_returns_empty(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json {", encoding="utf-8")
    skills = kc_loader.load_kc_skills(bad)
    assert skills == {}


def test_leaf_index_is_inverted_and_normalized(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    index = kc_loader.leaf_index()
    # leaf names are normalized (lowercased + punctuation/whitespace stripped)
    assert kc_loader.normalize("乘法原理") in index
    assert kc_loader.normalize("乘法原理里的其他类型") in index


def test_resolve_by_leaf_exact_match(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    hit = kc_loader.resolve_by_leaf("乘法原理里的其他类型")
    assert hit is not None
    assert hit.考察重点 == "重点 A"


def test_resolve_by_leaf_containment(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    # figure-node name is shorter and contained in leaf name → leaf side wins
    hit = kc_loader.resolve_by_leaf("乘法原理")
    assert hit is not None
    assert hit.考察重点 == "重点 B"  # exact match to 乘法原理 leaf, deeper route wins


def test_resolve_by_leaf_prefers_longer_route_on_containment(tmp_path: Path):
    # Both leaves contain "乘法原理"; longer route (more segments) should win
    data = {
        "根----乘法原理": {
            "route": "根----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "d", "考察重点": "短 route",
                "考察形式": "f", "易错点": "e", "难度提示": "l",
            },
        },
        "根----分支----乘法原理": {
            "route": "根----分支----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "d", "考察重点": "长 route",
                "考察形式": "f", "易错点": "e", "难度提示": "l",
            },
        },
    }
    p = tmp_path / "dup.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    hit = kc_loader.resolve_by_leaf("乘法原理")
    assert hit is not None
    assert hit.考察重点 == "长 route"


def test_resolve_by_leaf_no_match_returns_none(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    assert kc_loader.resolve_by_leaf("完全无关的知识点") is None


def test_normalize_strips_whitespace_and_punctuation():
    assert kc_loader.normalize("  乘法 原理 ") == kc_loader.normalize("乘法原理")
    assert kc_loader.normalize("ABC") == "abc"
    assert kc_loader.normalize("A,B.C!") == "abc"
```

- [ ] **Step 2: Run tests to see them fail**

Run: `uv run python -m pytest tests/test_kc_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.skills.kc_loader'`

- [ ] **Step 3: Implement `kc_loader.py`**

Create `src/skills/kc_loader.py`:

```python
"""Loader for XES3G5M kc_skills.json.

Reads the pre-generated KC skill bank, indexes it by route and by normalized
leaf name, and exposes a lightweight rule-based resolver used by
`kc_skill_resolver_node`.
"""

from __future__ import annotations

import json
import logging
import string
from pathlib import Path

from src.llm.schemas import KCSkill

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "XES3G5M" / "metadata" / "kc_skills.json"

_KC_SKILLS: dict[str, KCSkill] | None = None  # route -> KCSkill
_LEAF_INDEX: dict[str, list[str]] | None = None  # normalized leaf -> [routes]

# ASCII punctuation + whitespace + common CJK punctuation, as a set for O(1) membership
_STRIP_CHARS = frozenset(string.punctuation + string.whitespace + "，。；：、！？「」『』（）【】《》·．")


def normalize(s: str) -> str:
    """Lowercase + strip whitespace and punctuation. Deterministic."""
    if not s:
        return ""
    return "".join(ch for ch in s if ch not in _STRIP_CHARS).lower()


def load_kc_skills(path: Path = DEFAULT_PATH) -> dict[str, KCSkill]:
    """Parse the JSON once and populate module-level caches.

    Returns {} (with a warning log) if the file is missing or malformed.
    """
    global _KC_SKILLS, _LEAF_INDEX

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("kc_skills.json not found at %s; KC skills disabled", path)
        _KC_SKILLS = {}
        _LEAF_INDEX = {}
        return _KC_SKILLS
    except Exception:
        logger.warning("Failed to parse kc_skills.json at %s; KC skills disabled", path, exc_info=True)
        _KC_SKILLS = {}
        _LEAF_INDEX = {}
        return _KC_SKILLS

    skills: dict[str, KCSkill] = {}
    index: dict[str, list[str]] = {}

    for route, entry in raw.items():
        skill_data = entry.get("skill")
        if not skill_data:
            continue
        try:
            skills[route] = KCSkill(**skill_data)
        except Exception:
            logger.warning("Skipping malformed skill at route %s", route, exc_info=True)
            continue
        leaf = entry.get("leaf") or route.split("----")[-1]
        key = normalize(leaf)
        if key:
            index.setdefault(key, []).append(route)

    _KC_SKILLS = skills
    _LEAF_INDEX = index
    logger.info("Loaded %d KC skills from %s", len(skills), path)
    return skills


def get_kc_skills() -> dict[str, KCSkill]:
    """Return cached map, loading lazily on first access."""
    if _KC_SKILLS is None:
        load_kc_skills()
    return _KC_SKILLS or {}


def leaf_index() -> dict[str, list[str]]:
    """Return cached leaf→routes index, loading lazily on first access."""
    if _LEAF_INDEX is None:
        load_kc_skills()
    return _LEAF_INDEX or {}


def _route_depth(route: str) -> int:
    return route.count("----") + 1


def resolve_by_leaf(name: str) -> KCSkill | None:
    """Return the best-matching KCSkill for a figure-node name, or None.

    Matching tiers:
      1. exact normalized equality between `name` and any leaf
      2. containment in either direction
    On multiple candidates, prefer the deepest route (most `----` segments);
    ties are broken by lexicographic route order for stability.
    """
    if not name:
        return None
    target = normalize(name)
    if not target:
        return None

    index = leaf_index()
    skills = get_kc_skills()

    # Tier 1: exact match
    exact = index.get(target, [])
    candidates = list(exact)

    # Tier 2: containment (run only if no exact match, to keep precise hits clean)
    if not candidates:
        for leaf_key, routes in index.items():
            if leaf_key in target or target in leaf_key:
                candidates.extend(routes)

    if not candidates:
        return None

    # Most specific route wins; lexicographic tie-break
    best = sorted(candidates, key=lambda r: (-_route_depth(r), r))[0]
    return skills.get(best)
```

- [ ] **Step 4: Run tests to see them pass**

Run: `uv run python -m pytest tests/test_kc_loader.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/skills/kc_loader.py tests/test_kc_loader.py`
Expected: no errors. Fix any issues before committing.

- [ ] **Step 6: Commit**

```bash
git add src/skills/kc_loader.py tests/test_kc_loader.py
git commit -m "feat(kc-skills): add kc_loader with leaf-index resolver

Parses XES3G5M/metadata/kc_skills.json, builds a normalized
leaf-name → routes inverted index, and exposes resolve_by_leaf()
which returns the deepest matching KCSkill or None. Graceful
fallback to {} on missing / malformed JSON."
```

---

## Task 3: Add `kc_skill_block` template + helper (TDD)

**Files:**
- Create: `src/prompts/kc_skill_block.j2`
- Modify: `src/prompts/loader.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_prompts.py` (end of file):

```python
def test_render_kc_skill_block_with_content():
    from src.prompts.loader import render_kc_skill_block
    block = render_kc_skill_block({
        "description": "desc",
        "考察重点": "重点内容",
        "考察形式": "形式内容",
        "易错点": "易错内容",
        "难度提示": "难度内容",
    })
    assert "考察重点" in block
    assert "重点内容" in block
    assert "易错内容" in block
    assert "难度内容" in block


def test_render_kc_skill_block_empty_returns_empty_string():
    from src.prompts.loader import render_kc_skill_block
    assert render_kc_skill_block(None) == ""
    assert render_kc_skill_block({}) == ""
```

- [ ] **Step 2: Run tests to see them fail**

Run: `uv run python -m pytest tests/test_prompts.py::test_render_kc_skill_block_with_content tests/test_prompts.py::test_render_kc_skill_block_empty_returns_empty_string -v`
Expected: FAIL — `ImportError: cannot import name 'render_kc_skill_block'`.

- [ ] **Step 3: Create the template**

Create `src/prompts/kc_skill_block.j2`:

```jinja
{% if kc_skill %}
## 当前知识点的出题/教学上下文（来源：题库聚合）
- 考察重点：{{ kc_skill["考察重点"] }}
- 考察形式：{{ kc_skill["考察形式"] }}
- 常见易错点：{{ kc_skill["易错点"] }}
- 难度提示：{{ kc_skill["难度提示"] }}
{% endif %}
```

- [ ] **Step 4: Add the helper**

Open `src/prompts/loader.py`. After the existing `render_prompt` function, append:

```python
def render_kc_skill_block(kc_skill: dict | None) -> str:
    """Render a single KCSkill dict (from state['kc_skills']) into a markdown block.

    Returns '' for falsy input so callers can unconditionally pass the result
    as a template variable.
    """
    if not kc_skill:
        return ""
    return render_prompt("kc_skill_block", kc_skill=kc_skill)
```

- [ ] **Step 5: Run tests to see them pass**

Run: `uv run python -m pytest tests/test_prompts.py::test_render_kc_skill_block_with_content tests/test_prompts.py::test_render_kc_skill_block_empty_returns_empty_string -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/prompts/kc_skill_block.j2 src/prompts/loader.py tests/test_prompts.py
git commit -m "feat(kc-skills): add kc_skill_block template and helper

render_kc_skill_block(None|{}) returns '' so callers can pass the
result unconditionally. Template renders 4 lines when kc_skill is
a dict, nothing otherwise."
```

---

## Task 4: Add `ResolverMapping` schema + `kc_resolver` prompt

**Files:**
- Modify: `src/llm/schemas.py`
- Create: `src/prompts/kc_resolver.j2`
- Modify: `tests/test_schemas.py` (if it exists; we only add a small check)

- [ ] **Step 1: Add the Pydantic model**

Open `src/llm/schemas.py`. After the existing `KCSkill` class, append:

```python


class ResolverMapping(BaseModel):
    """LLM output for the kc_skill_resolver fallback call.

    Maps figure-node names to the best-matching kc_skills.json route,
    or None if no reasonable match exists.
    """

    mapping: dict[str, str | None] = Field(
        ...,
        description="{node_name: full_route | null} — one entry per unresolved node.",
    )
```

- [ ] **Step 2: Create the LLM-fallback prompt**

Create `src/prompts/kc_resolver.j2`:

```jinja
你是一个知识点路径匹配器。下面给出 N 个自由生成的知识点名称，以及 M 个来自题库的候选 leaf 路径。对每个知识点名称，从候选 leaf 中选择**最匹配**的一个，返回它对应的完整 route 字符串；如果没有合理匹配，返回 null。

## 判断标准
- 完整语义对应（不是字面包含即视为匹配）
- 细分程度优先：候选里既有"乘法原理"又有"乘法原理里的其他类型"时，若节点语义更接近具体子类，选子类；若只是泛指，选泛化版
- 不确定时宁可返回 null，也不要强行匹配

## 待匹配的图节点
{% for name in unresolved_nodes %}
- {{ name }}
{% endfor %}

## 候选 leaf → route 映射
{% for leaf, routes in candidates.items() %}
- leaf "{{ leaf }}":
  {% for r in routes %}
  - {{ r }}
  {% endfor %}
{% endfor %}

## 输出要求
严格返回一个 `ResolverMapping` 对象，字段 `mapping` 是 `{node_name: route | null}`。每个输入 node_name 都必须出现在 `mapping` 里。
```

- [ ] **Step 3: Verify template renders without error**

Run: `uv run python -c "from src.prompts.loader import render_prompt; print(render_prompt('kc_resolver', unresolved_nodes=['乘法原理'], candidates={'乘法原理': ['root----乘法原理']}))"`
Expected: the template renders (Chinese text is printed without exception).

- [ ] **Step 4: Commit**

```bash
git add src/llm/schemas.py src/prompts/kc_resolver.j2
git commit -m "feat(kc-skills): add ResolverMapping schema + kc_resolver prompt

Used by kc_skill_resolver_node's LLM fallback path to batch-match
unresolved figure-node names against candidate leaf→route pairs."
```

---

## Task 5: Implement `kc_skill_resolver_node` (TDD)

**Files:**
- Create: `src/agent/nodes/kc_skill_resolver.py`
- Create: `tests/test_kc_skill_resolver.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_kc_skill_resolver.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.llm.schemas import (
    KCSkill,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
    ResolverMapping,
)
from src.skills import kc_loader


def _skill(mark: str = "重点") -> KCSkill:
    return KCSkill(
        description="d",
        考察重点=f"{mark}-重点",
        考察形式="形式",
        易错点="易错",
        难度提示="难度",
    )


def _graph(names: list[str]) -> KnowledgeGraph:
    nodes = [KnowledgeNode(id=str(i), name=n, description="", level="core") for i, n in enumerate(names)]
    return KnowledgeGraph(topic="t", nodes=nodes, edges=[])


@pytest.fixture(autouse=True)
def reset_kc_loader():
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None


def test_resolver_returns_empty_patch_when_disabled():
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    state = {"use_kc_skills": False, "knowledge_graph": _graph(["乘法原理"])}
    patch_dict = kc_skill_resolver_node(state)
    assert patch_dict == {}


def test_resolver_returns_empty_patch_when_graph_missing():
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    state = {"use_kc_skills": True, "knowledge_graph": None}
    patch_dict = kc_skill_resolver_node(state)
    assert patch_dict == {}


def test_resolver_rule_match_only_fills_kc_skills():
    """All nodes match via rule; LLM fallback never invoked."""
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {"root----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["root----乘法原理"]}

    with patch.object(node_mod, "_llm_resolve") as mock_llm:
        state = {"use_kc_skills": True, "knowledge_graph": _graph(["乘法原理"])}
        patch_dict = node_mod.kc_skill_resolver_node(state)

    mock_llm.assert_not_called()
    assert "kc_skills" in patch_dict
    assert "乘法原理" in patch_dict["kc_skills"]
    assert patch_dict["kc_skills"]["乘法原理"]["考察重点"] == "A-重点"
    # stored as plain dict, not KCSkill instance
    assert isinstance(patch_dict["kc_skills"]["乘法原理"], dict)


def test_resolver_falls_back_to_llm_for_unmatched_nodes():
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {
        "root----乘法原理": _skill("A"),
        "root----染色计数问题": _skill("B"),
    }
    kc_loader._LEAF_INDEX = {
        kc_loader.normalize("乘法原理"): ["root----乘法原理"],
        kc_loader.normalize("染色计数问题"): ["root----染色计数问题"],
    }

    def fake_llm(unresolved, _candidates):
        # Only "给地图涂色" should be in unresolved — map it to the 染色 route
        return {"给地图涂色": "root----染色计数问题"}

    with patch.object(node_mod, "_llm_resolve", side_effect=fake_llm) as mock_llm:
        state = {
            "use_kc_skills": True,
            "knowledge_graph": _graph(["乘法原理", "给地图涂色"]),
        }
        patch_dict = node_mod.kc_skill_resolver_node(state)

    mock_llm.assert_called_once()
    called_args = mock_llm.call_args
    assert called_args.args[0] == ["给地图涂色"]  # only unresolved node passed
    assert patch_dict["kc_skills"]["乘法原理"]["考察重点"] == "A-重点"
    assert patch_dict["kc_skills"]["给地图涂色"]["考察重点"] == "B-重点"


def test_resolver_llm_error_preserves_rule_results():
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {"root----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["root----乘法原理"]}

    with patch.object(node_mod, "_llm_resolve", side_effect=RuntimeError("llm down")):
        state = {
            "use_kc_skills": True,
            "knowledge_graph": _graph(["乘法原理", "未知节点"]),
        }
        patch_dict = node_mod.kc_skill_resolver_node(state)

    # Rule-matched survives
    assert "乘法原理" in patch_dict["kc_skills"]
    # Unmatched dropped silently
    assert "未知节点" not in patch_dict["kc_skills"]


def test_resolver_skips_llm_when_leaf_index_empty():
    """JSON missing/empty → leaf_index is {} → no LLM call even if unresolved."""
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {}
    kc_loader._LEAF_INDEX = {}

    with patch.object(node_mod, "_llm_resolve") as mock_llm:
        state = {"use_kc_skills": True, "knowledge_graph": _graph(["任何节点"])}
        patch_dict = node_mod.kc_skill_resolver_node(state)

    mock_llm.assert_not_called()
    assert patch_dict == {"kc_skills": {}}
```

- [ ] **Step 2: Run tests to see them fail**

Run: `uv run python -m pytest tests/test_kc_skill_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.agent.nodes.kc_skill_resolver'`.

- [ ] **Step 3: Implement the node**

Create `src/agent/nodes/kc_skill_resolver.py`:

```python
"""LangGraph node that maps knowledge-graph nodes to KC skills.

Runs after `knowledge_graph`. Fills `state["kc_skills"]` with
`{kp_name: KCSkill.model_dump()}` for every figure-node that can be
matched against the kc_skills.json bank.
"""

from __future__ import annotations

import logging

from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import ResolverMapping
from src.prompts.loader import render_prompt
from src.skills.kc_loader import get_kc_skills, leaf_index, resolve_by_leaf

logger = logging.getLogger(__name__)


def _llm_resolve(unresolved: list[str], candidate_leaves: list[str]) -> dict[str, str | None]:
    """Ask the LLM to map each unresolved node name to a route (or null).

    Returns a plain dict so the caller can stay agnostic of Pydantic.
    """
    skills = get_kc_skills()
    # Build leaf → list of routes for the prompt; the LLM picks a route, not a leaf
    candidates: dict[str, list[str]] = {}
    for leaf in candidate_leaves:
        # reuse the inverted index, but translate normalized key back via skills
        # easier: iterate routes and group by the original leaf string from kc_skills.json
        pass
    # Simpler: give the LLM the full (leaf, route) pairs derived directly from get_kc_skills
    pairs: dict[str, list[str]] = {}
    for route in skills.keys():
        leaf = route.split("----")[-1]
        pairs.setdefault(leaf, []).append(route)

    prompt = render_prompt(
        "kc_resolver",
        unresolved_nodes=unresolved,
        candidates=pairs,
    )
    client = get_llm_client()
    result = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        response_model=ResolverMapping,
    )
    return result.mapping


def kc_skill_resolver_node(state: LearningState) -> dict:
    if not state.get("use_kc_skills"):
        return {}
    graph = state.get("knowledge_graph")
    if not graph:
        return {}

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])

    resolved: dict[str, dict] = {}
    unresolved: list[str] = []

    for node in nodes:
        name = node.name if hasattr(node, "name") else node["name"]
        if not name:
            continue
        hit = resolve_by_leaf(name)
        if hit is not None:
            resolved[name] = hit.model_dump()
        else:
            unresolved.append(name)

    if unresolved and leaf_index():
        try:
            mapping = _llm_resolve(unresolved, list(leaf_index().keys()))
            kc = get_kc_skills()
            for name, route in mapping.items():
                if route and route in kc:
                    resolved[name] = kc[route].model_dump()
        except Exception:
            logger.warning("KC skill LLM resolver failed; keeping rule-matched results only", exc_info=True)

    return {"kc_skills": resolved}
```

Clean up the unused scaffold loop inside `_llm_resolve` before saving:

Replace the body of `_llm_resolve` with the simpler version (drop the unused `candidates` dict and the `for leaf in candidate_leaves` loop):

```python
def _llm_resolve(unresolved: list[str], candidate_leaves: list[str]) -> dict[str, str | None]:
    """Ask the LLM to map each unresolved node name to a route (or null).

    `candidate_leaves` is accepted for symmetry with the caller but the prompt
    is built from `get_kc_skills()` directly, so it already reflects the full
    route/leaf catalogue.
    """
    del candidate_leaves  # intentional: caller passes it to share a guard

    pairs: dict[str, list[str]] = {}
    for route in get_kc_skills().keys():
        leaf = route.split("----")[-1]
        pairs.setdefault(leaf, []).append(route)

    prompt = render_prompt("kc_resolver", unresolved_nodes=unresolved, candidates=pairs)
    client = get_llm_client()
    result = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        response_model=ResolverMapping,
    )
    return result.mapping
```

- [ ] **Step 4: Run tests to see them pass**

Run: `uv run python -m pytest tests/test_kc_skill_resolver.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/agent/nodes/kc_skill_resolver.py tests/test_kc_skill_resolver.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/agent/nodes/kc_skill_resolver.py tests/test_kc_skill_resolver.py
git commit -m "feat(kc-skills): add kc_skill_resolver_node

Two-step resolver: rule match via resolve_by_leaf() first, then a
single batched LLM fallback for unresolved names. Stores results
as dict (KCSkill.model_dump()) in state['kc_skills']. LLM failure
preserves rule-matched results; disabled flag short-circuits."
```

---

## Task 6: Export the new node and wire it into LangGraph

**Files:**
- Modify: `src/agent/nodes/__init__.py`
- Modify: `src/agent/graph.py`
- Modify: `tests/test_graph.py`

- [ ] **Step 1: Extend graph test first**

Open `tests/test_graph.py` and replace the `test_graph_has_expected_nodes` function with:

```python
def test_graph_has_expected_nodes():
    graph = build_graph()
    node_names = set(graph.nodes.keys())
    expected = {
        "intent", "chat", "knowledge_graph", "kc_skill_resolver",
        "quiz", "grading", "diagnosis", "lesson",
    }
    assert expected.issubset(node_names)
```

Run: `uv run python -m pytest tests/test_graph.py -v`
Expected: FAIL — `kc_skill_resolver` is not a registered node.

- [ ] **Step 2: Export the node**

Open `src/agent/nodes/__init__.py`. Check whether a `kc_skill_resolver_node` import already exists — if not, add it, matching the existing style. Read the current file first, then append:

```python
from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node
```

and add `"kc_skill_resolver_node"` to the `__all__` list.

- [ ] **Step 3: Wire the node into the graph**

Open `src/agent/graph.py`. Update the imports block at the top:

```python
from src.agent.nodes import (
    intent_node,
    chat_node,
    knowledge_graph_node,
    kc_skill_resolver_node,
    quiz_node,
    grading_node,
    diagnosis_node,
    lesson_node,
)
```

Inside `build_graph()`, add the node registration right after `builder.add_node("knowledge_graph", knowledge_graph_node)`:

```python
    builder.add_node("kc_skill_resolver", kc_skill_resolver_node)
```

Then replace the existing `builder.add_edge("knowledge_graph", END)` line with:

```python
    builder.add_edge("knowledge_graph", "kc_skill_resolver")
    builder.add_edge("kc_skill_resolver", END)
```

- [ ] **Step 4: Run tests to see them pass**

Run: `uv run python -m pytest tests/test_graph.py tests/test_kc_skill_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite sanity check**

Run: `uv run python -m pytest tests/ -v`
Expected: all PASS. If any previously-passing test now fails, read the traceback — most likely a session test needs the new default-state keys; add them to fixture dicts if so.

- [ ] **Step 6: Commit**

```bash
git add src/agent/nodes/__init__.py src/agent/graph.py tests/test_graph.py
git commit -m "feat(kc-skills): wire kc_skill_resolver between knowledge_graph and END

knowledge_graph → kc_skill_resolver → END. The resolver internally
short-circuits when use_kc_skills is False, so existing sessions
behave identically to pre-change."
```

**PR 1 exit criteria:** `uv run python -m pytest tests/` passes. The app starts (`uv run python run/serve.py` briefly to confirm no import errors), nothing broken end-to-end. Open the PR; merge before starting PR 2.

---

# PR 2 — Consumers

Each of the three consumer tasks follows the same pattern: modify the `.j2` to accept `{{ kc_skill_block }}`, modify the corresponding node to read from state and pass the block, run the existing test suite to prove no regression.

## Task 7: Inject into quiz pipeline

**Files:**
- Modify: `src/prompts/quiz_generate.j2`
- Modify: `src/agent/nodes/quiz.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Write a failing prompt test**

Append to `tests/test_prompts.py`:

```python
def test_quiz_generate_renders_kc_skill_block():
    block = "## KC 测试块\n- 易错点：示例"
    result = render_prompt(
        "quiz_generate",
        knowledge_points=[{"name": "乘法原理", "description": "desc"}],
        questions_per_point=2,
        skill_instructions="",
        kc_skill_block=block,
    )
    assert "KC 测试块" in result
    assert "易错点：示例" in result


def test_quiz_generate_omits_kc_skill_block_when_empty():
    result = render_prompt(
        "quiz_generate",
        knowledge_points=[{"name": "乘法原理", "description": "desc"}],
        questions_per_point=2,
        skill_instructions="",
        kc_skill_block="",
    )
    # The block would have a distinctive heading — verify it's absent
    assert "当前知识点的出题" not in result
    assert "KC 测试块" not in result
```

Run: `uv run python -m pytest tests/test_prompts.py::test_quiz_generate_renders_kc_skill_block tests/test_prompts.py::test_quiz_generate_omits_kc_skill_block_when_empty -v`
Expected: the first test FAIL (template has no `{{ kc_skill_block }}`); the second will PASS trivially.

- [ ] **Step 2: Update the prompt template**

Open `src/prompts/quiz_generate.j2`. Insert the `kc_skill_block` placeholder on its own line between the `## Teaching Method` block and the `Knowledge points to test:` line, like this:

```jinja
You are a quiz generator for adaptive learning. Generate multiple-choice questions to diagnose student mastery.

{% if skill_instructions %}
## Teaching Method
{{ skill_instructions }}
Apply the above method when designing questions.

{% endif %}
{{ kc_skill_block }}
Knowledge points to test:
...
```

(Leave everything below `Knowledge points to test:` untouched.)

- [ ] **Step 3: Run prompt tests**

Run: `uv run python -m pytest tests/test_prompts.py -v`
Expected: both new tests PASS; existing `test_quiz_template_with_skill` / `test_quiz_template_without_skill` still PASS (those don't pass `kc_skill_block`, so it defaults to undefined — Jinja2 treats undefined as empty when rendering, but to be safe let's check).

If the existing tests now fail because `kc_skill_block` is undefined, update them to pass `kc_skill_block=""`. Read the current version of those tests at `tests/test_skills.py::TestPromptInjection::test_quiz_template_with_skill` (lines ~242-261 from earlier exploration) and add `kc_skill_block=""` to each `render_prompt("quiz_generate", ...)` call.

- [ ] **Step 4: Update `quiz_node` to pass the block**

Open `src/agent/nodes/quiz.py`. In `quiz_node` (the first function), locate the `skill_instructions = get_skill_section(state, "quiz")` line and insert two lines right after it:

```python
        skill_instructions = get_skill_section(state, "quiz")
        kc_skills_state = state.get("kc_skills") or {}
        # Aggregate: kc_skill_block per knowledge point, joined
        blocks = []
        for p in points:
            from src.prompts.loader import render_kc_skill_block  # local import to keep top clean
            b = render_kc_skill_block(kc_skills_state.get(p["name"]))
            if b:
                blocks.append(b)
        kc_skill_block = "\n\n".join(blocks)
```

(Move the `from src.prompts.loader import render_kc_skill_block` to the module's top-level import block: `from src.prompts.loader import render_prompt, render_kc_skill_block` — keeps lint clean.)

Then update the `render_prompt("quiz_generate", ...)` call to include `kc_skill_block=kc_skill_block`:

```python
        prompt = render_prompt(
            "quiz_generate",
            knowledge_points=points,
            questions_per_point=QUESTIONS_PER_POINT,
            skill_instructions=skill_instructions,
            kc_skill_block=kc_skill_block,
        )
```

- [ ] **Step 5: Update `quiz_generate_progressive` similarly**

In the same file, `quiz_generate_progressive` iterates one KP at a time. Inside the `for node in nodes:` loop, after `point = {"name": name, "description": desc}` and before the `render_prompt` call, insert:

```python
        kc_skill_block = render_kc_skill_block((state.get("kc_skills") or {}).get(name))
```

Then add `kc_skill_block=kc_skill_block` to the `render_prompt` call in that function.

- [ ] **Step 6: Run the full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/prompts/ src/agent/nodes/quiz.py tests/test_prompts.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/prompts/quiz_generate.j2 src/agent/nodes/quiz.py tests/test_prompts.py
git commit -m "feat(kc-skills): inject kc_skill_block into quiz_generate prompt

quiz_node aggregates per-KP blocks for the batch path;
quiz_generate_progressive uses the per-KP block for each point."
```

---

## Task 8: Inject into lesson pipeline

**Files:**
- Modify: `src/prompts/lesson_point.j2`
- Modify: `src/agent/nodes/lesson.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Write a failing prompt test**

Append to `tests/test_prompts.py`:

```python
def test_lesson_point_renders_kc_skill_block():
    block = "## KC 课件块\n- 难度提示：示例"
    result = render_prompt(
        "lesson_point",
        topic="勾股定理",
        knowledge_point_name="直角三角形",
        knowledge_point_description="desc",
        pedagogy_instructions="无特定教学法要求",
        knowledge_skill="无额外教学技能",
        point_index=1,
        total_points=1,
        kc_skill_block=block,
    )
    assert "KC 课件块" in result
    assert "难度提示：示例" in result
```

Run: `uv run python -m pytest tests/test_prompts.py::test_lesson_point_renders_kc_skill_block -v`
Expected: FAIL (template has no `{{ kc_skill_block }}`).

- [ ] **Step 2: Update the prompt template**

Open `src/prompts/lesson_point.j2`. Insert `{{ kc_skill_block }}` after the existing `{% if knowledge_skill ... %}` block and before `## 知识点信息`:

```jinja
{% if knowledge_skill and knowledge_skill != "无额外教学技能" %}
## 知识点教学技能
以下是针对该知识点的教学策略，请严格遵循：
{{ knowledge_skill }}
{% endif %}

{{ kc_skill_block }}
## 知识点信息
...
```

- [ ] **Step 3: Run prompt test**

Run: `uv run python -m pytest tests/test_prompts.py::test_lesson_point_renders_kc_skill_block -v`
Expected: PASS.

- [ ] **Step 4: Update `generate_lesson_per_point` to pass the block**

Open `src/agent/nodes/lesson.py`. At the top of the file, extend the loader import:

```python
from src.prompts.loader import render_prompt, render_kc_skill_block
```

In `generate_lesson_per_point`, the function currently takes `(topic, weak_points, pedagogy_instructions, knowledge_skills)`. Add a fifth parameter `kc_skills: dict | None = None`:

```python
def generate_lesson_per_point(
    topic: str,
    weak_points: list[dict],
    pedagogy_instructions: str,
    knowledge_skills: dict[str, str],
    kc_skills: dict | None = None,
) -> str:
```

Inside the for loop, before the `render_prompt("lesson_point", ...)` call, add:

```python
        kc_skill_block = render_kc_skill_block((kc_skills or {}).get(wp["name"]))
```

Then add `kc_skill_block=kc_skill_block` to the `render_prompt` call's kwargs.

- [ ] **Step 5: Update `lesson_node` to pass `state["kc_skills"]`**

In `lesson_node`, find the `lesson_text = generate_lesson_per_point(...)` call and add the new argument:

```python
    lesson_text = generate_lesson_per_point(
        topic, weak_points, pedagogy_instructions, knowledge_skills,
        kc_skills=state.get("kc_skills"),
    )
```

- [ ] **Step 6: Update `lesson_node_stream`**

In `lesson_node_stream`, inside the `for i, wp in enumerate(weak_points):` loop, before the `render_prompt("lesson_point", ...)` call, insert:

```python
        kc_skill_block = render_kc_skill_block((state.get("kc_skills") or {}).get(wp["name"]))
```

and add `kc_skill_block=kc_skill_block` to the `render_prompt` kwargs.

- [ ] **Step 7: Run the full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: all PASS. If `tests/test_lesson_pipeline.py` is affected, read the failure and update mocks to include the new argument (look for any calls to `generate_lesson_per_point` in tests).

- [ ] **Step 8: Commit**

```bash
git add src/prompts/lesson_point.j2 src/agent/nodes/lesson.py tests/test_prompts.py
git commit -m "feat(kc-skills): inject kc_skill_block into lesson_point prompt

Both the batch path (generate_lesson_per_point) and the streaming
path (lesson_node_stream) read state['kc_skills'] and pass per-KP
blocks to the template."
```

---

## Task 9: Inject into slides outline pipeline

**Files:**
- Modify: `src/prompts/slide_outline.j2`
- Modify: `src/agent/nodes/slides.py`
- Modify: `tests/test_prompts.py`

- [ ] **Step 1: Write a failing prompt test**

Append to `tests/test_prompts.py`:

```python
def test_slide_outline_renders_kc_skill_block():
    block = "## KC 课件块\n- 难度提示：slide 测试"
    result = render_prompt(
        "slide_outline",
        topic="计数",
        weak_points=[{"name": "乘法原理", "description": "desc"}],
        kc_skill_block=block,
    )
    assert "KC 课件块" in result
    assert "slide 测试" in result
```

Run: `uv run python -m pytest tests/test_prompts.py::test_slide_outline_renders_kc_skill_block -v`
Expected: FAIL.

- [ ] **Step 2: Update the prompt template**

Open `src/prompts/slide_outline.j2`. Locate the `## Input` section (around line 73-85). Insert `{{ kc_skill_block }}` on its own line immediately after `Topic: {{ topic }}` and before `{% if weak_points %}`:

```jinja
## Input

Topic: {{ topic }}

{{ kc_skill_block }}

{% if weak_points %}
Weak areas to focus on:
...
```

- [ ] **Step 3: Run prompt test**

Run: `uv run python -m pytest tests/test_prompts.py::test_slide_outline_renders_kc_skill_block -v`
Expected: PASS.

- [ ] **Step 4: Update `generate_slide_outlines` to aggregate and pass**

Open `src/agent/nodes/slides.py`. Update the top import:

```python
from src.prompts.loader import render_prompt, render_kc_skill_block
```

In `generate_slide_outlines`, locate the `prompt = render_prompt("slide_outline", topic=topic, weak_points=weak_points)` call (around line 78). Immediately before it, insert:

```python
    kc_skills = state.get("kc_skills") or {}
    blocks = [render_kc_skill_block(kc_skills.get(wp["name"])) for wp in weak_points]
    kc_skill_block = "\n\n".join(b for b in blocks if b)
```

Then change the `render_prompt` call to:

```python
    prompt = render_prompt(
        "slide_outline",
        topic=topic,
        weak_points=weak_points,
        kc_skill_block=kc_skill_block,
    )
```

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/prompts/slide_outline.j2 src/agent/nodes/slides.py tests/test_prompts.py
git commit -m "feat(kc-skills): inject aggregated kc_skill_block into slide outline

Concatenates per-weak-point KC skill blocks once for the outline
prompt — gives the LLM full KP-level context when structuring
the lesson into slides. content_draft and layout stages are
intentionally not touched (no clean KP mapping)."
```

## Task 10: Manual smoke test the full pipeline

- [ ] **Step 1: Confirm `XES3G5M/metadata/kc_skills.json` exists and is readable**

Run: `uv run python -c "from src.skills.kc_loader import load_kc_skills, get_kc_skills; print(len(load_kc_skills()))"`
Expected: prints a positive integer (number of loaded skills).

- [ ] **Step 2: Run the end-to-end session test**

Run: `uv run python -m pytest tests/test_e2e.py -v`
Expected: PASS (or: read any failure; it should NOT be caused by our changes — if it is, fix).

- [ ] **Step 3: Start the server and observe logs**

Run (foreground): `uv run python run/serve.py`
Send `Ctrl-C` after server is up. In the startup logs confirm: `Loaded N KC skills from ...`. If instead you see `kc_skills.json not found`, check the path in `DEFAULT_PATH`.

- [ ] **Step 4: Commit any log/cleanup fixes**

If any adjustments were needed (imports, defensive guards), commit them separately with a short message.

**PR 2 exit criteria:** all tests PASS; `uv run python run/serve.py` starts and logs the KC skill count. Open PR 2, merge before starting PR 3.

---

# PR 3 — Frontend

## Task 11: Render KC skill card on lesson page

**Files:**
- Modify: `static/lesson.html`

This task modifies one HTML file. No Python tests apply; verification is visual.

- [ ] **Step 1: Locate the existing inline-ks-card injection block**

Read `static/lesson.html` lines 540-575 for context. The existing block injects pedagogy-skill cards after each `h2`. We will add a parallel block that injects KC skill cards next to them.

- [ ] **Step 2: Add CSS for the new card**

In the `<style>` block near the top of the file (look for the `.inline-ks-card` CSS if it exists; otherwise place at the end of the main stylesheet), add:

```css
.kc-skill-card {
  margin: 12px 0;
  border: 1px solid #C7D4E8;
  border-radius: 8px;
  background: #F1F5FB;
  overflow: hidden;
}
.kc-skill-card summary {
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 600;
  color: #3B5A8A;
  cursor: pointer;
  list-style: none;
}
.kc-skill-card summary::-webkit-details-marker { display: none; }
.kc-skill-card summary::before {
  content: '▸';
  display: inline-block;
  margin-right: 6px;
  transition: transform 0.15s;
}
.kc-skill-card[open] summary::before { transform: rotate(90deg); }
.kc-skill-card-body {
  padding: 10px 14px 14px 26px;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.6;
  background: white;
}
.kc-skill-card-body p { margin: 4px 0; }
.kc-skill-card-body strong { color: #3B5A8A; }
```

- [ ] **Step 3: Inject KC cards inside the same `h2s.forEach` loop**

Find the existing loop (around line 557):

```javascript
  h2s.forEach(function(h2) {
    const headingText = h2.textContent.trim();
    // Match heading to knowledge point name
    const matchedKey = Object.keys(ks).find(function(k) {
      return headingText.indexOf(k) !== -1 || k.indexOf(headingText) !== -1;
    });
    if (matchedKey) {
      ...existing inline-ks-card insertion...
    }
  });
```

Immediately after the closing `});` of this forEach, add a **second** forEach block that walks the same `h2s` and injects KC skill cards (reading `state.kc_skills`):

```javascript
  // --- KC skill cards (from state.kc_skills, independent of pedagogy knowledge_skills) ---
  const kcSkills = state.kc_skills || {};
  if (Object.keys(kcSkills).length > 0) {
    const sectionElKc = document.getElementById('section-1');
    const h2sKc = sectionElKc.querySelectorAll('h2');
    h2sKc.forEach(function(h2) {
      const headingText = h2.textContent.trim();
      const matchedKey = Object.keys(kcSkills).find(function(k) {
        return headingText.indexOf(k) !== -1 || k.indexOf(headingText) !== -1;
      });
      if (!matchedKey) return;
      const kc = kcSkills[matchedKey] || {};
      const card = document.createElement('div');
      card.innerHTML =
        '<details class="kc-skill-card">' +
          '<summary>📘 出题考察提示：' + escapeHtml(matchedKey) + '</summary>' +
          '<div class="kc-skill-card-body">' +
            '<p><strong>考察重点：</strong>' + escapeHtml(kc['考察重点'] || '') + '</p>' +
            '<p><strong>常见易错点：</strong>' + escapeHtml(kc['易错点'] || '') + '</p>' +
            '<p><strong>难度提示：</strong>' + escapeHtml(kc['难度提示'] || '') + '</p>' +
          '</div>' +
        '</details>';
      // Insert after any existing inline-ks-card that's already a sibling
      let insertAfter = h2;
      let next = h2.nextSibling;
      while (next && next.nodeType === 1 && next.querySelector && next.querySelector('.inline-ks-card')) {
        insertAfter = next;
        next = next.nextSibling;
      }
      insertAfter.parentNode.insertBefore(card, insertAfter.nextSibling);
    });
  }
```

- [ ] **Step 4: Manual verification**

1. Start server: `uv run python run/serve.py`
2. Open `http://localhost:8080/static/index.html`, pick a topic relevant to XES3G5M (e.g., "乘法原理里的其他类型" or any Chinese math topic that would route to a leaf in the bank).
3. Complete the quiz quickly (pick any answers), let diagnosis + lesson run.
4. On the lesson page, verify: a collapsible card with 📘 icon appears below each h2 knowledge-point heading whose name matches a KC skill entry. Clicking expands it and shows the three fields.
5. Confirm the existing 💡 pedagogy card still renders separately (they should not overlap or duplicate).
6. Pick a topic with NO XES3G5M match (e.g., "光合作用"). Verify: no KC cards appear, and the page still renders normally.

- [ ] **Step 5: Commit**

```bash
git add static/lesson.html
git commit -m "feat(kc-skills): render KC skill card on lesson page

Collapsible blue-themed <details> card injected after each h2
whose text matches a state.kc_skills entry. Independent of the
existing orange pedagogy card (both can coexist)."
```

**PR 3 exit criteria:** manual smoke test passes — student-facing UX shows the card correctly for matched KPs and silently omits for unmatched. Open PR 3.

---

# Post-landing sanity

After all three PRs merge:

- [ ] **Step 1: Run full test suite on `main`-merged branch**

Run: `uv run python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 2: Run lint + format**

Run: `uv run ruff check src/ run/ tests/ && uv run ruff format --check src/ run/ tests/`
Expected: clean.

- [ ] **Step 3: Quick E2E dry-run**

Start server, run through one full session with a KC-matching topic and one non-matching topic. Verify: logs contain `Loaded N KC skills`, no warnings about JSON, lesson page shows cards appropriately.

---

## Rollback

If anything lands wrong in production:
- Setting `USE_KC_SKILLS=false` in `.env` and restarting makes every new session behave exactly as pre-change (resolver short-circuits; `state["kc_skills"]` stays `None`; prompts render with empty block; frontend renders no card). Existing sessions that already stored `kc_skills` continue to show them on reload — harmless.
- A fuller revert is a plain `git revert` of the three merged PRs, in reverse order.

## Future work (tracked separately, not in this plan)

- Expose KC skill on the diagnosis page (currently "easier-to-misunderstand" concerns blocked this — see spec).
- Route-tree-based topic picker (second entry path).
- Embedding-based resolver once the codebase grows embedding infrastructure.
