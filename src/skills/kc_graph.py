"""Topic → KCSkill route resolver, plus subgraph extraction.

See spec: docs/superpowers/specs/2026-04-27-kc-route-driven-knowledge-graph-design.md
"""

from __future__ import annotations

from collections import deque
from typing import Literal

from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode
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
    best: tuple[int, int, str] | None = (
        None  # (tier, -depth, route_lex) — smaller is better
    )
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


_ANCESTOR_CAP = 2
_LEAF_MIN_NODES = 3


def _ancestors(
    route: str, parent_of: dict[str, str | None], allowed_roots: frozenset[str]
) -> list[str]:
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


def _bfs_descendants(
    route: str, children_of: dict[str, list[str]], budget: int
) -> list[str]:
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

    # Siblings (other children of immediate parent). Reserve a slot for descendants
    # only when r* actually has children — otherwise leaf graphs get cheated.
    parent = parent_of.get(route)
    has_descendants = bool(children_of.get(route))
    sib_cap = max_nodes - 1 if has_descendants else max_nodes
    if parent is not None:
        for sib in children_of.get(parent, []):
            if sib == route:
                continue
            if len(selected) >= sib_cap:
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

    # Leaf-match degradation: if r* is a leaf, pull in uncles (siblings of parent)
    # until the graph reaches _LEAF_MIN_NODES nodes. This prevents single-point graphs.
    if not children_of.get(route) and parent is not None:
        grandparent = parent_of.get(parent)
        if grandparent is not None:
            for uncle in children_of.get(grandparent, []):
                if uncle == parent:
                    continue
                if len(selected) >= _LEAF_MIN_NODES or len(selected) >= max_nodes:
                    break
                selected.setdefault(uncle, "extension")

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
    edges = [
        KnowledgeEdge(source=parent_of[c], target=c, relation="prerequisite")
        for c in selected
        if parent_of.get(c) in selected
    ]

    return KnowledgeGraph(
        topic=topic or route.split("----")[-1], nodes=nodes, edges=edges
    )


def _llm_match_route(
    topic: str,
    allowed_roots: frozenset[str] | None = None,
) -> str | None:
    """LLM-driven route matching. Reserved seam — raises NotImplementedError today.

    See spec §6 (Future-LLM seam). Will be implemented when an LLM-based topic-to-route
    picker is added; for now this stub guards against accidental ``mode="llm"`` use.
    """
    raise NotImplementedError(
        "LLM-driven route matching is not yet implemented. Future upgrade — see spec §6."
    )


def _match_route(
    topic: str,
    *,
    mode: ResolverMode,
    allowed_roots: frozenset[str] | None,
) -> str | None:
    """Dispatch topic→route matching to the configured mode.

    ``"rule"`` runs the deterministic segment-level matcher. ``"llm"`` raises
    NotImplementedError today. ``"rule_then_llm"`` falls through to the LLM only
    when rule matching misses (and therefore also raises today). Unknown modes raise
    ValueError.
    """
    if mode == "rule":
        return _rule_match_route(topic, allowed_roots)
    if mode == "llm":
        return _llm_match_route(topic, allowed_roots)
    if mode == "rule_then_llm":
        return _rule_match_route(topic, allowed_roots) or _llm_match_route(
            topic, allowed_roots
        )
    raise ValueError(f"Unknown ResolverMode: {mode!r}")


def resolve_kc_graph(
    topic: str,
    *,
    max_nodes: int = 10,
    mode: ResolverMode = "rule",
    allowed_roots: frozenset[str] | None = None,
) -> KnowledgeGraph | None:
    """Resolve a free-text topic against the kc_skills route taxonomy.

    Returns a ``KnowledgeGraph`` whose ``core`` node is the matched route and whose
    ``prerequisite``/``extension`` nodes are drawn from ancestors, siblings, and
    descendants (capped at ``max_nodes``). Returns ``None`` when no route matches —
    callers should fall back to LLM generation.

    The user's raw ``topic`` string is preserved on ``KnowledgeGraph.topic`` rather
    than being replaced with the matched route's last segment.
    """
    route = _match_route(topic, mode=mode, allowed_roots=allowed_roots)
    if route is None:
        return None
    return _extract_subgraph(
        route, max_nodes=max_nodes, topic=topic, allowed_roots=allowed_roots
    )
