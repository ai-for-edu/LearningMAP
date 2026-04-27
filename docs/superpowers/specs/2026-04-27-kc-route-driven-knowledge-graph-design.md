# KC Route-Driven Knowledge Graph — Design Spec

**Date:** 2026-04-27
**Branch:** `demo+memory`
**Status:** Brainstorm complete, awaiting plan

## Background

The `knowledge_graph` node currently asks the LLM to invent a `KnowledgeGraph` for any user-supplied topic, then a downstream `kc_skill_resolver` matches each generated leaf name back to entries in `XES3G5M/metadata/kc_skills.json` (1240 routes, 865 unique leaves) by leaf-name normalization. This loses two things:

1. The **route hierarchy** in `kc_skills.json` — every entry is a `----`-separated path encoding parent→child decomposition (e.g. `拓展思维----计数模块----加乘原理----乘法原理`). The LLM-generated graph is flat and synthesized, ignoring this taxonomy.
2. **Determinism**: identical topics produce different graphs run-to-run; KC skill resolver hit rate is unstable because LLM-generated leaf names rarely match the bank verbatim.

This design replaces the LLM-first path with a **rule-based lookup against the existing kc_skills route tree**, falling back to the current LLM generator only on miss. No new on-disk artifact is introduced; the route tree is built in memory at load time from `kc_skills.json`.

## Goals

- Replace LLM generation with deterministic route-tree lookup when the topic matches the KC bank.
- Preserve current behavior when the topic doesn't match (LLM fallback unchanged).
- Make downstream `kc_skill_resolver_node` near-100% hit rate for route-driven graphs by adding a small id-shape dispatch (see §7); LLM-fallback resolution is unchanged end-to-end.
- Leave a clean seam for a future "all-LLM" upgrade where the LLM picks a route from the taxonomy.
- No new committed data files — kc_skills.json is the single source of truth.

## Non-Goals

- Implementing the LLM-driven route picker (`mode="llm"`). The interface is reserved; the implementation is deferred until rule mode is in production.
- Changing the `KnowledgeGraph` schema. `level ∈ {prerequisite, core, extension}` and `relation = "prerequisite"` are reused as-is.
- Pre-computing or shipping a knowledge-graph JSON file. Discussed and rejected — see Decision Log.

## Scope-affecting interface changes

- `kc_skill_resolver_node` gains a small fast-path: when a node's `id` is itself a registered route in `_KC_SKILLS`, look the KCSkill up by id directly; otherwise fall through to the existing `resolve_by_leaf(name)` path. This lets route-driven graphs hit the bank in O(1) without breaking LLM-fallback graphs whose ids are arbitrary strings. See §7.

## Architecture

### Components

| File | Change |
|---|---|
| `src/skills/kc_loader.py` | **Extend**: keep the existing flat caches (`_KC_SKILLS`, `_LEAF_INDEX`) full-bank; add a per-`allowed_roots` keyed `_TREE_CACHE` of `KCTree(parent_of, children_of, node_desc)`. Existing `get_kc_skills() / leaf_index() / resolve_by_leaf()` API unchanged. |
| `src/skills/kc_graph.py` | **New**: `resolve_kc_graph(topic, *, max_nodes=10, mode="rule", allowed_roots=None) -> KnowledgeGraph \| None`. Pure function over the in-memory tree. |
| `src/agent/nodes/knowledge_graph.py` | **Modify**: try `resolve_kc_graph(topic)` first; on `None`, fall through to the existing LLM generator. Bump `MAX_NODES` from 8 to 10. |
| `src/agent/nodes/kc_skill_resolver.py` | **Modify**: small fast-path — `node.id in _KC_SKILLS` → look up by id; otherwise fall through to current `resolve_by_leaf(name)` behavior. |

Route-driven `KnowledgeNode`s carry their full route in `id`, so the resolver fast-path resolves them in O(1) without leaf-name disambiguation. Synthetic prefix nodes (path prefixes that have no standalone `kc_skills.json` entry) miss the id lookup and **do not fall through to leaf-name search** — see §7 for why.

### Data Flow

```
state.topic (free text)
        │
        ▼
knowledge_graph_node
        │
        ├─► resolve_kc_graph(topic, mode="rule")
        │         │
        │         ├─► _rule_match_route(topic) ─► route | None
        │         │
        │         └─► _extract_subgraph(route, max_nodes=10) ─► KnowledgeGraph
        │
        └─► (on miss) _llm_generate_graph(topic, state)   # current behavior
        │
        ▼
state.knowledge_graph
        │
        ▼
kc_skill_resolver_node  (id-shape dispatch; route-id direct hit, synthetic skipped, non-route id → leaf path)
```

## Component Detail

### 1. Tree building (`kc_loader.py`)

Two layers of caching, with different lifecycles and scopes:

**Flat caches (single-shot, full-bank, no filter):**

`load_kc_skills()` keeps its current behavior: parse `kc_skills.json` once and populate
- `_KC_SKILLS: route → KCSkill`
- `_LEAF_INDEX: leaf → [routes]`

over **all 1240 routes regardless of `allowed_roots`**. These power the unchanged `resolve_by_leaf()` and the new resolver fast-path (§7), and must stay full-bank so LLM-fallback graphs can still resolve any leaf in the bank.

**Tree caches (per-`allowed_roots`, keyed):**

A new helper `_get_tree(allowed_roots: frozenset[str]) -> KCTree` builds and caches a scoped tree on demand:

```python
@dataclass
class KCTree:
    parent_of: dict[str, str | None]
    children_of: dict[str, list[str]]
    node_desc: dict[str, str]

_TREE_CACHE: dict[frozenset[str], KCTree] = {}
DEFAULT_ALLOWED_ROOTS: frozenset[str] = frozenset({"知识点", "课内知识点", "拓展思维"})
```

For a given `allowed_roots`:
1. Filter `_KC_SKILLS.keys()` to routes whose first segment ∈ `allowed_roots`.
2. **Register synthetic prefixes**: for each surviving route `A----B----C----D`, ensure `A`, `A----B`, `A----B----C` exist as tree nodes even if not standalone entries in `kc_skills.json`. This connects the tree and lets BFS/ancestor walks reach the root without gaps.
3. Populate the `KCTree` fields:
   - `node_desc[route]` = `_KC_SKILLS[route].description` if registered, else `route.split("----")[-1]` (synthetic-prefix fallback).
   - `parent_of[route]` = the immediately enclosing prefix, or `None` for the root segment.
   - `children_of[route]` = list of direct children in deterministic order (kc_skills.json insertion order, with synthetic prefixes appended).
4. Store under `_TREE_CACHE[allowed_roots]`.

Different `allowed_roots` calls don't trample each other; calling with the default is the common path and pays the build cost exactly once per process.

`load_kc_skills(allowed_roots=...)` is a back-compat shim: it still loads the flat caches (full-bank, ignoring its param) and additionally warms the tree cache for the requested scope. New code should call `_get_tree(allowed_roots)` directly.

### 2. Rule-based topic matching (`kc_graph._rule_match_route`)

```
def _rule_match_route(topic: str, allowed_roots: frozenset[str] | None = None) -> str | None
```

1. `t = normalize(topic)`; empty → `None`.
2. For every route in the scoped tree, iterate its segments. For each segment at index `i`, compute `s = normalize(seg)`. The **candidate route** is the prefix that ends at this segment:
   ```
   candidate = "----".join(parts[: i + 1])
   ```
   Note this is the prefix, **not** the full route — see rationale below.

   Assign a tier:
   - **Tier 1**: `s == t`
   - **Tier 2**: `t in s` or `s in t`, **with the additional guard `len(s) >= 2 and len(t) >= 2`**. Single-character segments (e.g. `线`, `角`) only participate in Tier 1; otherwise topics like `线性方程` would falsely match the geometry segment `线`.

3. Score each candidate by `(tier_asc, depth_desc, route_lex_asc)`:
   - lower tier wins (exact > containment)
   - deeper prefix wins (more specific segment match)
   - lexicographic route as tiebreak

4. Return the best candidate prefix, or `None` when no candidates.

**Rationale for prefix-as-candidate.** If `topic="乘法原理"` exactly equals an intermediate segment, multiple routes will hit Tier 1: the route ending at `乘法原理` (depth 4) and any descendant route like `…乘法原理----乘法原理里的其他类型` (depth 5+). Scoring the **full** descendant would let depth-priority crown the deeper route, making the `core` node "乘法原理里的其他类型" instead of "乘法原理" — wrong. Scoring the **matched-segment prefix** keeps `r*` at the conceptual layer the user actually named; descendants enter the graph naturally via §3 BFS extraction.

**Rationale for segment-level matching.** User topics are bare concept names without `----`; per-segment scan makes intermediate-level hits first-class without privileging leaves.

### 3. Subgraph extraction (`kc_graph._extract_subgraph`)

Input: matched route `r*`, `max_nodes=10`. Output: `KnowledgeGraph(topic, nodes, edges)`.

**Node selection (priority order, fills the budget):**

| Order | Source | Count | level | Notes |
|---|---|---|---|---|
| 1 | `r*` itself | 1 | `core` | required |
| 2 | Ancestors of `r*` | ≤2 | `prerequisite` | walk up; stop after 2 levels or before reaching the root segment (e.g. `拓展思维`) |
| 3 | Siblings (other children of immediate parent) | within budget | `extension` | sort by route lex; reserve at least 1 slot for descendants |
| 4 | Descendants (BFS from `r*`) | fill to 10 | `extension` | shallow first; ties broken by route lex |

**Leaf-match degradation:** if `r*` has no descendants, also pull in **uncles** (siblings of `r*`'s parent) at `extension` level until the graph has at least 3 nodes. This prevents single-point graphs.

**Edge selection:** for the chosen node set, emit `KnowledgeEdge(source=p, target=c, relation="prerequisite")` for every `(p, c)` where `tree.parent_of[c] == p` and both are in the set. Sibling↔sibling edges are not drawn — the schema has no parallel-relation, and forging prerequisite edges between siblings would mislead downstream prompts.

### 4. Schema mapping

| Field | Source |
|---|---|
| `KnowledgeGraph.topic` | original user topic string (unchanged) |
| `KnowledgeNode.id` | full route (e.g. `拓展思维----计数模块----加乘原理----乘法原理`) — stable, unique, debuggable |
| `KnowledgeNode.name` | last segment of route (the leaf-tail name shown to the user / consumed by `kc_skill_resolver`) |
| `KnowledgeNode.description` | `tree.node_desc[route]` |
| `KnowledgeNode.level` | per the table in §3 |
| `KnowledgeEdge.source` / `target` | route ids |
| `KnowledgeEdge.relation` | `"prerequisite"` (only enum value used) |

### 5. Node integration (`knowledge_graph_node`)

```python
MAX_NODES = 10  # was 8; aligned with kc_graph budget

def knowledge_graph_node(state: LearningState) -> dict:
    topic = state.get("topic", "")
    use_kc = state.get("use_kc_skills", True)

    graph: KnowledgeGraph | None = None
    if use_kc:
        graph = resolve_kc_graph(topic, max_nodes=MAX_NODES)  # rule mode

    if graph is None:
        graph = _llm_generate_graph(topic, state)  # extracted from current body, behavior unchanged

    if graph is None:
        return {"messages": [AIMessage(content="知识图谱生成失败，请稍后再试。")], "mode": "chat"}

    reply = f"我已经为你梳理了「{topic}」的知识图谱，包含 {len(graph.nodes)} 个知识点。准备好了就点击开始诊断！"
    return {"messages": [AIMessage(content=reply)], "knowledge_graph": graph, "mode": "graph"}
```

Notes:
- `use_kc_skills` is the existing state flag, reused as the master switch. Disabling reverts to fully-LLM behavior.
- `_llm_generate_graph` is the current LLM body extracted to a private function with no behavioral change.
- `MAX_NODES` increases from 8 to 10. The LLM fallback path now also produces up to 10 nodes; this is the intended uplift.

### 6. Future-LLM seam

```python
# src/skills/kc_graph.py
ResolverMode = Literal["rule", "llm", "rule_then_llm"]

def resolve_kc_graph(topic, *, max_nodes=10, mode: ResolverMode = "rule",
                     allowed_roots: set[str] | None = None) -> KnowledgeGraph | None:
    route = _match_route(topic, mode=mode, allowed_roots=allowed_roots)
    if route is None:
        return None
    return _extract_subgraph(route, max_nodes=max_nodes)

def _match_route(topic, *, mode, allowed_roots) -> str | None:
    if mode == "rule":
        return _rule_match_route(topic, allowed_roots)
    if mode == "llm":
        return _llm_match_route(topic, allowed_roots)         # raises NotImplementedError today
    if mode == "rule_then_llm":
        return _rule_match_route(topic, allowed_roots) or _llm_match_route(topic, allowed_roots)
    raise ValueError(f"Unknown mode: {mode}")
```

The LLM upgrade is confined to `_match_route`. Subgraph extraction and schema mapping are mode-agnostic — they're deterministic pure functions over the in-memory tree. When `_llm_match_route` is implemented, it will feed the filtered tree's route list (≤1131 strings) plus the topic to the LLM and ask it to return one route; nothing else changes.

The `mode` parameter is **not exposed via state or env in this iteration** — `knowledge_graph_node` calls with the default `mode="rule"`. A switch will be added when `_llm_match_route` actually exists.

### 7. `kc_skill_resolver_node` fast-path

The current resolver maps `node.name` → `KCSkill` via `resolve_by_leaf(name)`. For route-driven nodes this works but is wasteful (we already know the exact route in `node.id`); for synthetic-prefix nodes it's actively dangerous, because a synthetic prefix `id="A----B"` has no kc_skills entry, yet `resolve_by_leaf(name)` could match a real route ending in the same leaf segment elsewhere in the bank — a silent false hit.

The patch is ~5 lines:

```python
def kc_skill_resolver_node(state: LearningState) -> dict:
    if not state.get("use_kc_skills"):
        return {}
    graph = state.get("knowledge_graph")
    if not graph:
        return {}

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    kc_skills_map = get_kc_skills()  # full-bank flat cache (§1)

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

        # If id looks like a route (contains '----') but isn't registered,
        # it's a synthetic prefix from kc_graph. Don't fall through to
        # leaf-name search — that would risk a silent false hit.
        if "----" in nid:
            continue

        # Legacy path: LLM-fallback graphs whose ids aren't routes
        hit = resolve_by_leaf(name)
        if hit is not None:
            resolved[name] = hit.model_dump()
        else:
            unresolved.append(name)

    if unresolved and leaf_index():
        # ...existing LLM tier (unchanged)...

    return {"kc_skills": resolved}
```

This makes the resolver dispatch on id shape:
- **id ∈ `_KC_SKILLS`** → direct hit (route-driven real-route nodes; covers nearly all kc_graph output)
- **id contains `----` but ∉ `_KC_SKILLS`** → synthetic prefix; skip silently (no KCSkill exists for it)
- **id has no `----`** → LLM-fallback graph; existing leaf-name + LLM-resolver pipeline runs unchanged

The full-bank `_KC_SKILLS` is essential here: `id` lookup must hit any registered route, including ones outside the current `allowed_roots` (LLM fallbacks may produce them).

## Testing

All tests are pure unit tests; no network.

### `tests/test_kc_loader.py` — extended

| Test | Assertion |
|---|---|
| `test_flat_caches_are_full_bank` | `_KC_SKILLS` and `_LEAF_INDEX` contain entries from every root, including excluded ones (`课内题型` etc.), regardless of `allowed_roots` |
| `test_default_tree_built_for_allowed_roots` | every key in `_get_tree(DEFAULT_ALLOWED_ROOTS).parent_of` has a root segment in the default allow-list |
| `test_tree_filters_excluded_roots` | "课内题型 / 能力 / 数学思想" etc. absent from the default tree |
| `test_synthetic_prefix_nodes_present` | path prefixes that aren't standalone routes still appear in `children_of` and connect to the root |
| `test_parent_child_invariants` | exactly one parent per non-root node; `children_of[parent]` contains all children |
| `test_tree_cache_keyed_by_allowed_roots` | `_get_tree({"知识点"})` and `_get_tree(DEFAULT_ALLOWED_ROOTS)` return distinct cached trees; calling with the same scope twice returns the same instance |

### `tests/test_kc_graph.py` — new

| Test | Assertion |
|---|---|
| `test_rule_match_returns_prefix_not_descendant` | topic="乘法原理" returns the prefix route ending at that segment, NOT the deeper "…乘法原理----乘法原理里的其他类型" descendant |
| `test_rule_match_containment` | topic="抽屉原理" matches via 2-char-or-longer containment |
| `test_rule_match_short_segment_exact_only` | topic="线性方程" does NOT match the geometry segment "线"; topic="线" itself does match it (Tier 1 exact still permitted) |
| `test_rule_match_returns_none` | unrelated topic returns `None` |
| `test_subgraph_size_within_budget` | `1 <= len(graph.nodes) <= max_nodes` for any matched topic |
| `test_subgraph_includes_match_as_core` | exactly one node has `level == "core"`; its id is the matched route |
| `test_subgraph_ancestors_capped_at_2` | even at depth 6+, prerequisite count ≤ 2 |
| `test_subgraph_includes_siblings` | when match has siblings, ≥ 1 appears with `level="extension"` |
| `test_leaf_match_falls_back_to_uncle_nodes` | leaf match yields ≥ 3 nodes via ancestors + siblings + uncles |
| `test_edges_only_for_parent_child` | every edge satisfies `tree.parent_of[target] == source`; relation always `"prerequisite"` |
| `test_node_id_is_route` | every `node.id` is a registered route or synthetic path prefix |
| `test_node_description_uses_skill_when_registered` | description matches `_KC_SKILLS[route].description` when route is in the bank |
| `test_node_description_falls_back_for_synthetic` | synthetic-prefix description equals the last segment |
| `test_llm_mode_raises_not_implemented` | calling `resolve_kc_graph(topic, mode="llm")` raises `NotImplementedError` |

### `tests/test_kc_skill_resolver.py` — extended

| Test | Assertion |
|---|---|
| `test_resolver_id_fast_path_for_route_id` | a `KnowledgeNode` whose `id` is a registered route resolves directly via `_KC_SKILLS` (mock `resolve_by_leaf` to assert it isn't called) |
| `test_resolver_skips_synthetic_prefix_silently` | a node with id containing `----` but not in `_KC_SKILLS` is skipped — neither resolved nor passed to leaf-name search (no false positives even when the leaf segment exists in another real route) |
| `test_resolver_legacy_path_for_non_route_ids` | a node with id without `----` falls through to the existing `resolve_by_leaf` + LLM tier, matching today's behavior |

### `tests/test_graph.py` — extended

| Test | Assertion |
|---|---|
| `test_knowledge_graph_node_uses_kc_when_topic_matches` | mocked LLM client; topic="乘法原理"; LLM not called; `state.knowledge_graph` is from kc_graph |
| `test_knowledge_graph_node_falls_back_to_llm_on_miss` | unrelated topic; LLM called once; result follows old path |
| `test_knowledge_graph_node_skips_kc_when_flag_off` | `state["use_kc_skills"] = False`; LLM called even when topic would match |

### Out of scope

- LLM mode behavior — write tests when `_llm_match_route` is implemented.
- Performance / cache hit rate — 1240-entry tree, millisecond build, not worth measuring.
- End-to-end quiz/lesson/slides — `test_kc_skill_resolver.py` already covers state-field consumers; this design only changes how `state.knowledge_graph` is populated, not how it's consumed.

## Decision Log

| Decision | Rationale |
|---|---|
| Default `allowed_roots = {知识点, 课内知识点, 拓展思维}` | Excludes meta-classification roots ("课内题型", "能力", "数学思想", etc.). Keeps content-substantive trees. Configurable for future tuning. |
| `allowed_roots` filters tree caches only, NOT flat caches | `_KC_SKILLS` and `_LEAF_INDEX` stay full-bank so the resolver fast-path and LLM-fallback leaf lookup keep working over all 1240 routes. Confining the filter to tree caches isolates the new feature from existing resolution behavior. |
| Tree cache keyed by `frozenset(allowed_roots)` | Avoids cache poisoning when callers (e.g. tests) pass a non-default scope. Different scopes coexist; same scope is built exactly once. |
| In-memory tree, no committed JSON file | kc_skills.json already contains all route info; an extra file would be a true subset, redundant, and risks drift. Build is millisecond-scale at startup. |
| Match per-segment, candidate is the matched-segment **prefix** | User topics are bare concept names without `----`; per-segment match makes intermediate-level hits first-class. Scoring the prefix (not the deepest descendant) keeps `core` at the layer the user actually named — descendants enter via §3 BFS. |
| Tier 2 containment requires `len ≥ 2` on both sides | Prevents one-character segments like `线`, `角` from matching unrelated topics via substring containment. Tier 1 still permits exact one-char matches. |
| `kc_skill_resolver_node` gains an id-shape dispatch | Route-driven node ids (full route strings) hit `_KC_SKILLS` directly in O(1). Synthetic prefixes are skipped silently rather than risking a false-positive leaf-name match against an unrelated registered route. LLM-fallback graphs (no `----` in id) keep their existing behavior end-to-end. |
| Ancestor cap of 2 | Avoids forcing top-level roots ("拓展思维") into every graph as prerequisite; they're too generic to be pedagogically useful. |
| `MAX_NODES = 10` (was 8) | User explicitly requested. Allows ancestors + siblings + descendants to coexist comfortably. |
| Edges only for parent→child, none for siblings | The `KnowledgeEdge.relation` enum has only `"prerequisite"`; faking sibling links as prerequisites would mislead downstream prompts. |
| `node.id = route`, `node.name = last segment` | route as id is stable/unique/debuggable and powers the resolver fast-path. Last segment as name preserves the surface text users / downstream prompts already expect. |
| `mode` parameter present but no runtime switch | YAGNI: `_llm_match_route` doesn't exist yet, exposing the switch now would be dead config. |
| Reuse `use_kc_skills` state flag | Existing master switch; turning it off cleanly reverts to current LLM-only behavior. |
