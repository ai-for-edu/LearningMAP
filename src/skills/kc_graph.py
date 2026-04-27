"""Topic → KCSkill route resolver, plus subgraph extraction.

See spec: docs/superpowers/specs/2026-04-27-kc-route-driven-knowledge-graph-design.md
"""

from __future__ import annotations

from typing import Literal

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
