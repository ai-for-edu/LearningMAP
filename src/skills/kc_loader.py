"""Loader for XES3G5M kc_skills.json.

Reads the pre-generated KC skill bank, indexes it by route and by normalized
leaf name, and exposes a lightweight rule-based resolver used by
`kc_skill_resolver_node`.
"""

from __future__ import annotations

import json
import logging
import string
from dataclasses import dataclass, field
from pathlib import Path

from src.llm.schemas import KCSkill

logger = logging.getLogger(__name__)

DEFAULT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "XES3G5M"
    / "metadata"
    / "kc_skills.json"
)

DEFAULT_ALLOWED_ROOTS: frozenset[str] = frozenset({"知识点", "课内知识点", "拓展思维"})


@dataclass
class KCTree:
    parent_of: dict[str, str | None] = field(default_factory=dict)
    children_of: dict[str, list[str]] = field(default_factory=dict)
    node_desc: dict[str, str] = field(default_factory=dict)


_KC_SKILLS: dict[str, KCSkill] | None = None  # route -> KCSkill
_LEAF_INDEX: dict[str, list[str]] | None = None  # normalized leaf -> [routes]
_TREE_CACHE: dict[frozenset[str], KCTree] = {}

# ASCII punctuation + whitespace + common CJK punctuation, as a set for O(1) membership
_STRIP_CHARS = frozenset(
    string.punctuation + string.whitespace + "，。；：、！？「」『』（）【】《》·．"
)


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
        logger.warning(
            "Failed to parse kc_skills.json at %s; KC skills disabled",
            path,
            exc_info=True,
        )
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
