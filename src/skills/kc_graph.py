"""Topic → KCSkill route resolver, plus subgraph extraction.

See spec: docs/superpowers/specs/2026-04-27-kc-route-driven-knowledge-graph-design.md
"""

from __future__ import annotations

from collections import deque
from typing import Literal

from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode
from src.skills.kc_loader import (
    DEFAULT_ALLOWED_ROOTS,  # noqa: F401  # re-exported for Tasks 3-4
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

    # Siblings (other children of immediate parent)
    parent = parent_of.get(route)
    if parent is not None:
        for sib in children_of.get(parent, []):
            if sib == route:
                continue
            if (
                len(selected) >= max_nodes - 1
            ):  # keep at least 1 slot for descendants if possible
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

    # Leaf-match degradation: pull uncles until ≥ _LEAF_MIN_NODES (capped by max_nodes)
    if not children_of.get(route):  # leaf
        if parent is not None:
            grandparent = parent_of.get(parent)
            if grandparent is not None:
                for uncle in children_of.get(grandparent, []):
                    if uncle == parent:
                        continue
                    if len(selected) >= max_nodes:
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
    selected_ids = set(selected)
    edges = [
        KnowledgeEdge(source=parent_of[c], target=c, relation="prerequisite")
        for c in selected_ids
        if parent_of.get(c) in selected_ids
    ]

    return KnowledgeGraph(
        topic=topic or route.split("----")[-1], nodes=nodes, edges=edges
    )
