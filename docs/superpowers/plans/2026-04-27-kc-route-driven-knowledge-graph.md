# KC Route-Driven Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LLM-first knowledge-graph generation with a deterministic lookup against the kc_skills route taxonomy; fall back to the existing LLM path on miss.

**Architecture:** Build an in-memory parent→child tree from `XES3G5M/metadata/kc_skills.json` route strings, scoped to `{知识点, 课内知识点, 拓展思维}`. A new `kc_graph.resolve_kc_graph(topic)` matches the topic against route segments, picks the matched-segment prefix as the focal route, then extracts a `≤10`-node subgraph (ancestors ≤2 + match `core` + siblings + descendants BFS, leaf-degraded by adding uncles). The downstream `kc_skill_resolver_node` gains a small id-shape dispatch so route-id nodes hit `_KC_SKILLS` directly while LLM-fallback graphs keep their existing leaf-name path.

**Tech Stack:** Python 3.11, Pydantic, LangGraph, pytest, ruff. uv for env management.

**Spec:** [`docs/superpowers/specs/2026-04-27-kc-route-driven-knowledge-graph-design.md`](../specs/2026-04-27-kc-route-driven-knowledge-graph-design.md)

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/skills/kc_loader.py` | Modify | Existing flat caches (`_KC_SKILLS`, `_LEAF_INDEX`) stay full-bank. Add `KCTree` dataclass, `DEFAULT_ALLOWED_ROOTS`, `_TREE_CACHE`, `_get_tree()`. Tree caches are scoped per `frozenset(allowed_roots)`. |
| `src/skills/kc_graph.py` | Create | `resolve_kc_graph(topic, *, max_nodes, mode, allowed_roots) -> KnowledgeGraph \| None`. Internals: `_rule_match_route` (segment-level normalize+containment, prefix-as-candidate, ≥2-char Tier 2 guard), `_extract_subgraph` (ancestors+core+siblings+descendants budget allocation), `_llm_match_route` (NotImplementedError stub), `_match_route` dispatch. |
| `src/agent/nodes/knowledge_graph.py` | Modify | Try `resolve_kc_graph` first under `use_kc_skills`, fall back to existing LLM path. Bump `MAX_NODES` to 10. Extract LLM body into `_llm_generate_graph()`. |
| `src/agent/nodes/kc_skill_resolver.py` | Modify | Add id-shape dispatch fast-path: `node.id in _KC_SKILLS` → direct hit; `"----" in node.id` but not in `_KC_SKILLS` → silent skip; else legacy leaf-name path. |
| `tests/test_kc_loader.py` | Extend | KCTree construction, allowed_roots filter, synthetic prefixes, parent-child invariants, cache keying, flat caches stay full-bank. |
| `tests/test_kc_graph.py` | Create | All resolver behaviors: prefix-as-candidate, short-segment guard, subgraph budget, leaf degradation, edges parent→child only, descriptions, LLM mode raises. |
| `tests/test_graph.py` | Extend | `knowledge_graph_node` dual path: kc hit, LLM fallback, flag-off. |
| `tests/test_kc_skill_resolver.py` | Extend | Fast-path on route id, synthetic-prefix silent skip, legacy non-route id behavior. |

---

## Task 1: Add `KCTree` + `_get_tree` to `kc_loader.py`

**Files:**
- Modify: `src/skills/kc_loader.py`
- Test: `tests/test_kc_loader.py`

- [ ] **Step 1.1: Write failing tests in `tests/test_kc_loader.py`**

Append to the file:

```python
def test_flat_caches_are_full_bank(tmp_path):
    """allowed_roots must NOT filter the flat caches."""
    sample = {
        "知识点----A": {
            "route": "知识点----A", "leaf": "A",
            "skill": {"description": "d", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        "课内题型----B": {
            "route": "课内题型----B", "leaf": "B",
            "skill": {"description": "d", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    # Even though 课内题型 is excluded from the default tree, flat caches contain everything.
    assert "知识点----A" in kc_loader.get_kc_skills()
    assert "课内题型----B" in kc_loader.get_kc_skills()
    assert kc_loader.normalize("A") in kc_loader.leaf_index()
    assert kc_loader.normalize("B") in kc_loader.leaf_index()


def test_default_tree_built_for_allowed_roots(tmp_path):
    sample = {
        "知识点----A----X": {
            "route": "知识点----A----X", "leaf": "X",
            "skill": {"description": "dx", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        "课内题型----B": {
            "route": "课内题型----B", "leaf": "B",
            "skill": {"description": "db", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    tree = kc_loader._get_tree()  # default allowed_roots
    # 课内题型 root excluded
    assert "课内题型----B" not in tree.parent_of
    # 知识点 leaf and synthetic prefix present
    assert "知识点----A----X" in tree.parent_of
    assert "知识点----A" in tree.parent_of  # synthetic
    assert "知识点" in tree.parent_of  # root
    assert tree.parent_of["知识点"] is None
    assert tree.parent_of["知识点----A"] == "知识点"
    assert tree.parent_of["知识点----A----X"] == "知识点----A"


def test_synthetic_prefix_node_desc_falls_back_to_leaf_segment(tmp_path):
    sample = {
        "知识点----A----X": {
            "route": "知识点----A----X", "leaf": "X",
            "skill": {"description": "dx", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    tree = kc_loader._get_tree()
    assert tree.node_desc["知识点----A----X"] == "dx"  # registered
    assert tree.node_desc["知识点----A"] == "A"  # synthetic, last segment
    assert tree.node_desc["知识点"] == "知识点"  # synthetic root


def test_tree_cache_keyed_by_allowed_roots(tmp_path):
    sample = {
        "知识点----A": {
            "route": "知识点----A", "leaf": "A",
            "skill": {"description": "d", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        "拓展思维----C": {
            "route": "拓展思维----C", "leaf": "C",
            "skill": {"description": "d", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    only_kp = kc_loader._get_tree(frozenset({"知识点"}))
    default = kc_loader._get_tree()
    assert "拓展思维----C" not in only_kp.parent_of
    assert "拓展思维----C" in default.parent_of
    # Same scope returns same instance (cached)
    again = kc_loader._get_tree(frozenset({"知识点"}))
    assert again is only_kp


def test_parent_child_invariants(tmp_path):
    sample = {
        "知识点----A----X": {
            "route": "知识点----A----X", "leaf": "X",
            "skill": {"description": "dx", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        "知识点----A----Y": {
            "route": "知识点----A----Y", "leaf": "Y",
            "skill": {"description": "dy", "考察重点": "y", "考察形式": "y", "易错点": "y", "难度提示": "y"},
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    tree = kc_loader._get_tree()
    # children_of[知识点----A] contains both X and Y
    assert set(tree.children_of["知识点----A"]) == {"知识点----A----X", "知识点----A----Y"}
    # Each child has 知识点----A as parent
    assert tree.parent_of["知识点----A----X"] == "知识点----A"
    assert tree.parent_of["知识点----A----Y"] == "知识点----A"
```

Also extend the autouse fixture to reset `_TREE_CACHE`:

```python
@pytest.fixture(autouse=True)
def reset_kc_loader_state():
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_kc_loader.py -v
```
Expected: AttributeError on `kc_loader._get_tree` / `kc_loader._TREE_CACHE` / `KCTree`.

- [ ] **Step 1.3: Implement `KCTree` + tree builder in `src/skills/kc_loader.py`**

At the top of the file (after existing imports), add:

```python
from dataclasses import dataclass, field


DEFAULT_ALLOWED_ROOTS: frozenset[str] = frozenset({"知识点", "课内知识点", "拓展思维"})


@dataclass
class KCTree:
    parent_of: dict[str, str | None] = field(default_factory=dict)
    children_of: dict[str, list[str]] = field(default_factory=dict)
    node_desc: dict[str, str] = field(default_factory=dict)


_TREE_CACHE: dict[frozenset[str], KCTree] = {}
```

Then add the builder + accessor at the bottom of the file:

```python
def _build_tree(allowed_roots: frozenset[str]) -> KCTree:
    skills = get_kc_skills()
    tree = KCTree()

    for route, skill in skills.items():
        parts = route.split("----")
        if parts[0] not in allowed_roots:
            continue
        # Walk every prefix so synthetic ancestors get registered too.
        for i in range(1, len(parts) + 1):
            prefix = "----".join(parts[:i])
            if prefix in tree.parent_of:
                # Existing entry — only ensure description is registered version if we now know it.
                if i == len(parts) and skill.description:
                    tree.node_desc[prefix] = skill.description
                continue
            parent = "----".join(parts[: i - 1]) if i > 1 else None
            tree.parent_of[prefix] = parent
            tree.children_of.setdefault(prefix, [])
            if parent is not None:
                tree.children_of.setdefault(parent, []).append(prefix)
            # Description: registered route gets skill description; synthetic prefix gets last segment.
            if i == len(parts) and skill.description:
                tree.node_desc[prefix] = skill.description
            else:
                tree.node_desc[prefix] = parts[i - 1]

    return tree


def _get_tree(allowed_roots: frozenset[str] | None = None) -> KCTree:
    """Return the tree scoped to allowed_roots, building+caching on first request."""
    key = allowed_roots if allowed_roots is not None else DEFAULT_ALLOWED_ROOTS
    if key not in _TREE_CACHE:
        _TREE_CACHE[key] = _build_tree(key)
    return _TREE_CACHE[key]
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_kc_loader.py -v
```
Expected: all tests in the file pass, including the 5 new ones.

- [ ] **Step 1.5: Lint**

```bash
uv run ruff check src/skills/kc_loader.py tests/test_kc_loader.py
uv run ruff format src/skills/kc_loader.py tests/test_kc_loader.py
```

- [ ] **Step 1.6: Commit**

```bash
git add src/skills/kc_loader.py tests/test_kc_loader.py
git commit -m "feat(kc-graph): add KCTree + _get_tree scoped per allowed_roots"
```

---

## Task 2: Add `_rule_match_route` to `kc_graph.py`

**Files:**
- Create: `src/skills/kc_graph.py`
- Test: `tests/test_kc_graph.py`

- [ ] **Step 2.1: Write failing tests in `tests/test_kc_graph.py`**

Create the file:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.skills import kc_loader
from src.skills.kc_graph import _rule_match_route


@pytest.fixture(autouse=True)
def reset_kc_state(tmp_path):
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    sample = {
        # Two routes share segment "乘法原理": one as terminal, one as ancestor of deeper descendant.
        "拓展思维----计数模块----加乘原理----乘法原理": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理", "leaf": "乘法原理",
            "skill": {"description": "乘法原理 desc", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型", "leaf": "乘法原理里的其他类型",
            "skill": {"description": "其他类型 desc", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        # One-char segment to test Tier 2 guard
        "知识点----几何----线": {
            "route": "知识点----几何----线", "leaf": "线",
            "skill": {"description": "线 desc", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        # Containment match candidate
        "拓展思维----组合----抽屉原理": {
            "route": "拓展思维----组合----抽屉原理", "leaf": "抽屉原理",
            "skill": {"description": "抽屉 desc", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}


def test_rule_match_returns_prefix_not_descendant():
    """topic exactly equals an intermediate segment → return prefix, not deeper descendant."""
    route = _rule_match_route("乘法原理")
    assert route == "拓展思维----计数模块----加乘原理----乘法原理"


def test_rule_match_containment():
    route = _rule_match_route("抽屉原理")
    assert route == "拓展思维----组合----抽屉原理"


def test_rule_match_short_segment_exact_only():
    # topic="线性方程" must NOT match the geometry segment "线"
    assert _rule_match_route("线性方程") is None
    # but exact "线" still matches
    assert _rule_match_route("线") == "知识点----几何----线"


def test_rule_match_returns_none_on_unrelated():
    assert _rule_match_route("完全不相关的词") is None


def test_rule_match_empty_topic():
    assert _rule_match_route("") is None
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_kc_graph.py -v
```
Expected: ImportError on `kc_graph` / `_rule_match_route`.

- [ ] **Step 2.3: Implement `_rule_match_route` in `src/skills/kc_graph.py`**

Create the file:

```python
"""Topic → KCSkill route resolver, plus subgraph extraction.

See spec: docs/superpowers/specs/2026-04-27-kc-route-driven-knowledge-graph-design.md
"""

from __future__ import annotations

from typing import Literal

from src.skills.kc_loader import (
    DEFAULT_ALLOWED_ROOTS,
    _get_tree,
    normalize,
)


ResolverMode = Literal["rule", "llm", "rule_then_llm"]


def _rule_match_route(
    topic: str,
    allowed_roots: frozenset[str] | None = None,
) -> str | None:
    """Match topic to a route prefix via segment-level normalize + bounded containment."""
    t = normalize(topic)
    if not t:
        return None

    tree = _get_tree(allowed_roots)
    best: tuple[int, int, str] | None = None  # (tier, -depth, route_lex) — smaller is better
    best_route: str | None = None

    for route in tree.parent_of:
        parts = route.split("----")
        for i, seg in enumerate(parts):
            s = normalize(seg)
            if not s:
                continue
            tier: int | None = None
            if s == t:
                tier = 1
            elif len(s) >= 2 and len(t) >= 2 and (t in s or s in t):
                tier = 2
            if tier is None:
                continue
            candidate = "----".join(parts[: i + 1])
            depth = i + 1
            key = (tier, -depth, candidate)
            if best is None or key < best:
                best = key
                best_route = candidate

    return best_route
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_kc_graph.py -v
```
Expected: 5 tests pass.

- [ ] **Step 2.5: Lint + commit**

```bash
uv run ruff check src/skills/kc_graph.py tests/test_kc_graph.py
uv run ruff format src/skills/kc_graph.py tests/test_kc_graph.py
git add src/skills/kc_graph.py tests/test_kc_graph.py
git commit -m "feat(kc-graph): rule-based topic→route matcher (prefix-as-candidate)"
```

---

## Task 3: Add `_extract_subgraph` to `kc_graph.py`

**Files:**
- Modify: `src/skills/kc_graph.py`
- Test: `tests/test_kc_graph.py`

- [ ] **Step 3.1: Write failing tests in `tests/test_kc_graph.py`**

Append to the file:

```python
from src.skills.kc_graph import _extract_subgraph


def test_subgraph_includes_match_as_core():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    cores = [n for n in g.nodes if n.level == "core"]
    assert len(cores) == 1
    assert cores[0].id == "拓展思维----计数模块----加乘原理----乘法原理"
    assert cores[0].name == "乘法原理"


def test_subgraph_size_within_budget():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    assert 1 <= len(g.nodes) <= 10


def test_subgraph_ancestors_capped_at_2():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型", max_nodes=10)
    prereqs = [n for n in g.nodes if n.level == "prerequisite"]
    assert len(prereqs) <= 2


def test_subgraph_includes_descendants_when_present():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    ext_ids = {n.id for n in g.nodes if n.level == "extension"}
    assert "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型" in ext_ids


def test_leaf_match_falls_back_to_uncle_nodes(tmp_path):
    """Leaf with no descendants and no siblings — uncles are pulled in to reach ≥3 nodes."""
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    sample = {
        "知识点----A----只有一个孩子----叶子": {
            "route": "知识点----A----只有一个孩子----叶子", "leaf": "叶子",
            "skill": {"description": "d", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
        "知识点----A----另一个分支": {
            "route": "知识点----A----另一个分支", "leaf": "另一个分支",
            "skill": {"description": "d", "考察重点": "x", "考察形式": "x", "易错点": "x", "难度提示": "x"},
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    g = _extract_subgraph("知识点----A----只有一个孩子----叶子", max_nodes=10)
    assert len(g.nodes) >= 3
    ext_ids = {n.id for n in g.nodes if n.level == "extension"}
    # Uncle "另一个分支" reached
    assert "知识点----A----另一个分支" in ext_ids


def test_edges_only_for_parent_child():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    node_ids = {n.id for n in g.nodes}
    tree = kc_loader._get_tree()
    for edge in g.edges:
        assert edge.source in node_ids
        assert edge.target in node_ids
        assert tree.parent_of[edge.target] == edge.source
        assert edge.relation == "prerequisite"


def test_node_id_is_route_and_name_is_last_segment():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    for n in g.nodes:
        assert n.name == n.id.split("----")[-1]


def test_node_description_uses_skill_when_registered():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    core = [n for n in g.nodes if n.level == "core"][0]
    assert core.description == "乘法原理 desc"


def test_node_description_falls_back_for_synthetic():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    # 计数模块 is synthetic (no standalone entry in the test data)
    synthetic = [n for n in g.nodes if n.id == "拓展思维----计数模块"]
    if synthetic:
        assert synthetic[0].description == "计数模块"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_kc_graph.py -v
```
Expected: ImportError on `_extract_subgraph`.

- [ ] **Step 3.3: Implement `_extract_subgraph` in `src/skills/kc_graph.py`**

Append to the file:

```python
from collections import deque

from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode


_ANCESTOR_CAP = 2
_LEAF_MIN_NODES = 3


def _ancestors(route: str, parent_of: dict[str, str | None], allowed_roots: frozenset[str]) -> list[str]:
    """Return up to _ANCESTOR_CAP ancestors of `route`, walking up. Skip the absolute root segment."""
    out: list[str] = []
    cur = parent_of.get(route)
    while cur is not None and len(out) < _ANCESTOR_CAP:
        # Skip the absolute root segment (e.g. "拓展思维"): too generic to use as prerequisite.
        if cur in allowed_roots:
            break
        out.append(cur)
        cur = parent_of.get(cur)
    return out


def _bfs_descendants(route: str, children_of: dict[str, list[str]], budget: int) -> list[str]:
    """BFS descendants of `route`, returning up to `budget` ids in shallow-first order."""
    out: list[str] = []
    queue: deque[str] = deque(children_of.get(route, []))
    while queue and len(out) < budget:
        nxt = queue.popleft()
        out.append(nxt)
        for child in children_of.get(nxt, []):
            queue.append(child)
    return out


def _extract_subgraph(
    route: str,
    *,
    max_nodes: int = 10,
    topic: str | None = None,
    allowed_roots: frozenset[str] | None = None,
) -> KnowledgeGraph:
    roots = allowed_roots if allowed_roots is not None else DEFAULT_ALLOWED_ROOTS
    tree = _get_tree(roots)
    parent_of = tree.parent_of
    children_of = tree.children_of
    node_desc = tree.node_desc

    selected: dict[str, str] = {}  # id → level
    selected[route] = "core"

    # Ancestors
    for anc in _ancestors(route, parent_of, roots):
        if len(selected) >= max_nodes:
            break
        selected[anc] = "prerequisite"

    # Siblings (other children of immediate parent)
    parent = parent_of.get(route)
    if parent is not None:
        for sib in children_of.get(parent, []):
            if sib == route:
                continue
            if len(selected) >= max_nodes - 1:  # keep at least 1 slot for descendants if possible
                break
            selected.setdefault(sib, "extension")

    # Descendants BFS
    remaining = max_nodes - len(selected)
    if remaining > 0:
        for desc in _bfs_descendants(route, children_of, remaining):
            if desc in selected:
                continue
            selected[desc] = "extension"
            if len(selected) >= max_nodes:
                break

    # Leaf-match degradation: pull uncles until ≥ _LEAF_MIN_NODES
    if not children_of.get(route):  # leaf
        if parent is not None:
            grandparent = parent_of.get(parent)
            if grandparent is not None:
                for uncle in children_of.get(grandparent, []):
                    if uncle == parent:
                        continue
                    if len(selected) >= max_nodes:
                        break
                    if len(selected) >= _LEAF_MIN_NODES and len(selected) >= max_nodes:
                        break
                    selected.setdefault(uncle, "extension")
                    if len(selected) >= _LEAF_MIN_NODES:
                        # keep going only until we've filled at least the minimum
                        if len(selected) >= max_nodes:
                            break

    # Build nodes
    nodes = [
        KnowledgeNode(
            id=rid,
            name=rid.split("----")[-1],
            description=node_desc.get(rid, rid.split("----")[-1]),
            level=lvl,
        )
        for rid, lvl in selected.items()
    ]

    # Build edges: parent→child within selection
    selected_ids = set(selected)
    edges = [
        KnowledgeEdge(source=parent_of[c], target=c, relation="prerequisite")
        for c in selected_ids
        if parent_of.get(c) in selected_ids
    ]

    return KnowledgeGraph(topic=topic or route.split("----")[-1], nodes=nodes, edges=edges)
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_kc_graph.py -v
```
Expected: all tests including the 9 new ones pass.

- [ ] **Step 3.5: Lint + commit**

```bash
uv run ruff check src/skills/kc_graph.py tests/test_kc_graph.py
uv run ruff format src/skills/kc_graph.py tests/test_kc_graph.py
git add src/skills/kc_graph.py tests/test_kc_graph.py
git commit -m "feat(kc-graph): subgraph extraction with ancestor/sibling/descendant budget"
```

---

## Task 4: Add `resolve_kc_graph` + LLM stub

**Files:**
- Modify: `src/skills/kc_graph.py`
- Test: `tests/test_kc_graph.py`

- [ ] **Step 4.1: Write failing tests**

Append to `tests/test_kc_graph.py`:

```python
from src.skills.kc_graph import resolve_kc_graph


def test_resolve_returns_graph_on_match():
    g = resolve_kc_graph("乘法原理", max_nodes=10)
    assert g is not None
    assert g.topic == "乘法原理"
    cores = [n for n in g.nodes if n.level == "core"]
    assert cores[0].name == "乘法原理"


def test_resolve_returns_none_on_miss():
    assert resolve_kc_graph("完全无关的话题") is None


def test_resolve_llm_mode_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        resolve_kc_graph("乘法原理", mode="llm")


def test_resolve_unknown_mode_raises_value_error():
    with pytest.raises(ValueError):
        resolve_kc_graph("乘法原理", mode="garbage")  # type: ignore[arg-type]
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_kc_graph.py -v
```
Expected: ImportError on `resolve_kc_graph`.

- [ ] **Step 4.3: Implement `resolve_kc_graph` + dispatch**

Append to `src/skills/kc_graph.py`:

```python
def _llm_match_route(
    topic: str,
    allowed_roots: frozenset[str] | None = None,
) -> str | None:
    raise NotImplementedError(
        "LLM-driven route matching is not yet implemented. Future upgrade — see spec §6."
    )


def _match_route(
    topic: str,
    *,
    mode: ResolverMode,
    allowed_roots: frozenset[str] | None,
) -> str | None:
    if mode == "rule":
        return _rule_match_route(topic, allowed_roots)
    if mode == "llm":
        return _llm_match_route(topic, allowed_roots)
    if mode == "rule_then_llm":
        return _rule_match_route(topic, allowed_roots) or _llm_match_route(topic, allowed_roots)
    raise ValueError(f"Unknown ResolverMode: {mode!r}")


def resolve_kc_graph(
    topic: str,
    *,
    max_nodes: int = 10,
    mode: ResolverMode = "rule",
    allowed_roots: frozenset[str] | None = None,
) -> KnowledgeGraph | None:
    route = _match_route(topic, mode=mode, allowed_roots=allowed_roots)
    if route is None:
        return None
    return _extract_subgraph(route, max_nodes=max_nodes, topic=topic, allowed_roots=allowed_roots)
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_kc_graph.py -v
```
Expected: all tests pass.

- [ ] **Step 4.5: Lint + commit**

```bash
uv run ruff check src/skills/kc_graph.py tests/test_kc_graph.py
uv run ruff format src/skills/kc_graph.py tests/test_kc_graph.py
git add src/skills/kc_graph.py tests/test_kc_graph.py
git commit -m "feat(kc-graph): resolve_kc_graph public API with rule/llm/rule_then_llm dispatch"
```

---

## Task 5: Add id-shape fast-path to `kc_skill_resolver_node`

**Files:**
- Modify: `src/agent/nodes/kc_skill_resolver.py`
- Test: `tests/test_kc_skill_resolver.py`

- [ ] **Step 5.1: Write failing tests**

Append to `tests/test_kc_skill_resolver.py`:

```python
def test_resolver_id_fast_path_for_route_id():
    """Route-id node hits _KC_SKILLS directly; resolve_by_leaf is NOT called."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    kc_loader._KC_SKILLS = {"知识点----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["知识点----乘法原理"]}

    nodes = [KnowledgeNode(id="知识点----乘法原理", name="乘法原理", description="d", level="core")]
    graph = KnowledgeGraph(topic="t", nodes=nodes, edges=[])
    state = {"use_kc_skills": True, "knowledge_graph": graph}

    with patch("src.agent.nodes.kc_skill_resolver.resolve_by_leaf") as mock_leaf:
        result = kc_skill_resolver_node(state)
        mock_leaf.assert_not_called()

    assert "乘法原理" in result["kc_skills"]
    assert result["kc_skills"]["乘法原理"]["考察重点"] == "A-重点"


def test_resolver_skips_synthetic_prefix_silently():
    """Synthetic-prefix node (id has '----' but isn't in _KC_SKILLS) → skipped, no leaf fallback."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    # Real route lives at a different leaf path; synthetic-prefix node has the same tail name but no entry
    kc_loader._KC_SKILLS = {"知识点----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["知识点----乘法原理"]}

    nodes = [KnowledgeNode(id="知识点----加乘原理----乘法原理", name="乘法原理", description="d", level="core")]
    graph = KnowledgeGraph(topic="t", nodes=nodes, edges=[])
    state = {"use_kc_skills": True, "knowledge_graph": graph}

    with patch("src.agent.nodes.kc_skill_resolver.resolve_by_leaf") as mock_leaf:
        result = kc_skill_resolver_node(state)
        mock_leaf.assert_not_called()

    # No false positive — the synthetic node is silently skipped.
    assert result.get("kc_skills", {}) == {}


def test_resolver_legacy_path_for_non_route_ids():
    """Node id without '----' falls through to existing leaf-name resolver."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    kc_loader._KC_SKILLS = {"root----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["root----乘法原理"]}

    nodes = [KnowledgeNode(id="n1", name="乘法原理", description="d", level="core")]
    graph = KnowledgeGraph(topic="t", nodes=nodes, edges=[])
    state = {"use_kc_skills": True, "knowledge_graph": graph}

    result = kc_skill_resolver_node(state)
    assert "乘法原理" in result["kc_skills"]
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_kc_skill_resolver.py -v
```
Expected: the new tests fail because the resolver still uses leaf-name path for all nodes.

- [ ] **Step 5.3: Implement id-shape dispatch in `src/agent/nodes/kc_skill_resolver.py`**

Replace the body of `kc_skill_resolver_node` with:

```python
def kc_skill_resolver_node(state: LearningState) -> dict:
    if not state.get("use_kc_skills"):
        return {}
    graph = state.get("knowledge_graph")
    if not graph:
        return {}

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    kc_skills_map = get_kc_skills()  # full-bank flat cache

    resolved: dict[str, dict] = {}
    unresolved: list[str] = []

    for node in nodes:
        name = node.name if hasattr(node, "name") else node["name"]
        nid = node.id if hasattr(node, "id") else node["id"]
        if not name:
            continue

        # Fast-path: id is a registered route → direct hit
        if nid in kc_skills_map:
            resolved[name] = kc_skills_map[nid].model_dump()
            continue

        # id looks like a route (has '----') but isn't registered → synthetic prefix, skip silently
        if "----" in nid:
            continue

        # Legacy path: LLM-fallback graphs whose ids aren't routes
        hit = resolve_by_leaf(name)
        if hit is not None:
            resolved[name] = hit.model_dump()
        else:
            unresolved.append(name)

    if unresolved and leaf_index():
        try:
            mapping = _llm_resolve(unresolved, list(leaf_index().keys()))
            for name, route in mapping.items():
                if route and route in kc_skills_map:
                    resolved[name] = kc_skills_map[route].model_dump()
        except Exception:
            logger.warning("KC skill LLM resolver failed; keeping rule-matched results only", exc_info=True)

    return {"kc_skills": resolved}
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_kc_skill_resolver.py -v
```
Expected: all tests pass, including the 3 new ones.

- [ ] **Step 5.5: Lint + commit**

```bash
uv run ruff check src/agent/nodes/kc_skill_resolver.py tests/test_kc_skill_resolver.py
uv run ruff format src/agent/nodes/kc_skill_resolver.py tests/test_kc_skill_resolver.py
git add src/agent/nodes/kc_skill_resolver.py tests/test_kc_skill_resolver.py
git commit -m "feat(kc-graph): add id-shape fast-path to kc_skill_resolver_node"
```

---

## Task 6: Wire `resolve_kc_graph` into `knowledge_graph_node`

**Files:**
- Modify: `src/agent/nodes/knowledge_graph.py`
- Test: `tests/test_graph.py`

- [ ] **Step 6.1: Write failing tests**

Append to `tests/test_graph.py`:

```python
from unittest.mock import patch

from src.llm.schemas import KnowledgeGraph, KnowledgeNode


def _fake_llm_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        topic="from-llm",
        nodes=[KnowledgeNode(id="x", name="x", description="d", level="core")],
        edges=[],
    )


def test_knowledge_graph_node_uses_kc_when_topic_matches(monkeypatch):
    """Topic matches kc bank → resolve_kc_graph returns a graph; LLM is NOT called."""
    from src.agent.nodes import knowledge_graph as node_mod

    fake_graph = KnowledgeGraph(
        topic="乘法原理",
        nodes=[KnowledgeNode(id="r", name="乘法原理", description="d", level="core")],
        edges=[],
    )

    state = {"topic": "乘法原理", "use_kc_skills": True}
    with (
        patch.object(node_mod, "resolve_kc_graph", return_value=fake_graph) as mock_resolve,
        patch.object(node_mod, "_llm_generate_graph") as mock_llm,
    ):
        result = node_mod.knowledge_graph_node(state)
        mock_resolve.assert_called_once_with("乘法原理", max_nodes=node_mod.MAX_NODES)
        mock_llm.assert_not_called()

    assert result["knowledge_graph"] is fake_graph
    assert result["mode"] == "graph"


def test_knowledge_graph_node_falls_back_to_llm_on_miss():
    from src.agent.nodes import knowledge_graph as node_mod

    state = {"topic": "完全无关的话题", "use_kc_skills": True}
    with (
        patch.object(node_mod, "resolve_kc_graph", return_value=None),
        patch.object(node_mod, "_llm_generate_graph", return_value=_fake_llm_graph()) as mock_llm,
    ):
        result = node_mod.knowledge_graph_node(state)
        mock_llm.assert_called_once()

    assert result["knowledge_graph"].topic == "from-llm"


def test_knowledge_graph_node_skips_kc_when_flag_off():
    from src.agent.nodes import knowledge_graph as node_mod

    state = {"topic": "乘法原理", "use_kc_skills": False}
    with (
        patch.object(node_mod, "resolve_kc_graph") as mock_resolve,
        patch.object(node_mod, "_llm_generate_graph", return_value=_fake_llm_graph()) as mock_llm,
    ):
        node_mod.knowledge_graph_node(state)
        mock_resolve.assert_not_called()
        mock_llm.assert_called_once()
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_graph.py -v
```
Expected: AttributeError on `_llm_generate_graph` / `resolve_kc_graph` not present in `knowledge_graph` module.

- [ ] **Step 6.3: Modify `src/agent/nodes/knowledge_graph.py`**

Replace the file body with:

```python
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import KnowledgeGraph
from src.prompts.loader import render_prompt
from src.skills.kc_graph import resolve_kc_graph
from src.skills.loader import get_skill_section

logger = logging.getLogger(__name__)

MAX_NODES = 10


def _llm_generate_graph(topic: str, state: LearningState) -> KnowledgeGraph | None:
    skill_instructions = get_skill_section(state, "knowledge_graph")
    prompt = render_prompt(
        "knowledge_graph",
        topic=topic,
        max_nodes=MAX_NODES,
        skill_instructions=skill_instructions,
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
    """Generate knowledge graph for the given topic.

    Try the kc_skills route lookup first; fall back to LLM generation on miss
    (or when use_kc_skills is False).
    """
    topic = state.get("topic", "")

    graph: KnowledgeGraph | None = None
    if state.get("use_kc_skills", True):
        graph = resolve_kc_graph(topic, max_nodes=MAX_NODES)

    if graph is None:
        graph = _llm_generate_graph(topic, state)

    if graph is None:
        return {
            "messages": [AIMessage(content="知识图谱生成失败，请稍后再试。")],
            "mode": "chat",
        }

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

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_graph.py -v
```
Expected: all tests pass, including the 3 new ones.

- [ ] **Step 6.5: Run the full suite as a regression check**

```bash
uv run python -m pytest tests/ -v
```
Expected: all green.

- [ ] **Step 6.6: Lint + commit**

```bash
uv run ruff check src/agent/nodes/knowledge_graph.py tests/test_graph.py
uv run ruff format src/agent/nodes/knowledge_graph.py tests/test_graph.py
git add src/agent/nodes/knowledge_graph.py tests/test_graph.py
git commit -m "feat(kc-graph): wire resolve_kc_graph into knowledge_graph_node with LLM fallback"
```

---

## Task 7: Manual smoke test

**Files:** none

- [ ] **Step 7.1: Start the server**

```bash
uv run python run/serve.py
```

- [ ] **Step 7.2: Trigger a topic that should hit the kc bank**

Open a new session in the browser at `http://127.0.0.1:8080/`, send a topic like "乘法原理" or "抽屉原理".

Verify in the response:
- The knowledge graph appears with the matched topic as the central node.
- 2 ancestors at most are shown as prerequisites.
- Sibling and descendant routes appear as extensions.
- Node count is ≤ 10.

- [ ] **Step 7.3: Trigger a topic that should miss the kc bank**

Send a free-form topic like "我的小学三年级期末复习计划".

Verify:
- LLM fallback path is taken (latency similar to current main).
- A graph is returned (not "生成失败" unless LLM itself fails).

- [ ] **Step 7.4: Document smoke-test result inline**

If both behaviors are correct, no further action. If unexpected, file a follow-up issue and update the plan with the regression.

---

## Self-Review Notes

Reviewed against spec §1–§7 and Decision Log:

- **Spec coverage:** §1 (Task 1), §2 (Task 2), §3 (Task 3), §4 schema mapping (Tasks 2/3 — node id/name/description directly enforced via test cases), §5 node integration (Task 6), §6 LLM seam (Task 4), §7 resolver fast-path (Task 5).
- **Type consistency:** `KCTree` fields (`parent_of`, `children_of`, `node_desc`) are referenced consistently across Tasks 1, 3, and the resolver call sites. `_get_tree(allowed_roots)` signature is stable.
- **No placeholders:** every step has concrete code or commands; commit messages are spelled out.
- **Edge cases:** leaf degradation (Task 3.5 test), short segment guard (Task 2.3 implementation + test), unknown ResolverMode (Task 4 ValueError test) are all covered.

One acknowledged limitation: Task 7 manual smoke-test depends on the dev environment having a working `OPENAI_API_KEY` for the LLM-fallback case. If unavailable, skip Step 7.3 and rely on Task 6's mocked test.
