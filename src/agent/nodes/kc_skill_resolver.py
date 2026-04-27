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


def _llm_resolve(
    unresolved: list[str], candidate_leaves: list[str]
) -> dict[str, str | None]:
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
            logger.warning(
                "KC skill LLM resolver failed; keeping rule-matched results only",
                exc_info=True,
            )

    return {"kc_skills": resolved}
