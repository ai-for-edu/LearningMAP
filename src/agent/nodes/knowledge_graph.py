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
        out.append(
            {
                "subtopic": sub,
                "last_accuracy": row.get("last_accuracy"),
                "last_seen_at": row.get("last_seen_at"),
                # Coarse status hint for the LLM — annotator will recompute
                # the authoritative one. We only need a string here.
                "status": _coarse_status_for_prompt(row.get("last_accuracy")),
            }
        )
    return out


def _coarse_status_for_prompt(acc: Any) -> str:
    # Thresholds mirror mastery_annotator._MASTERED_MIN / _WEAK_MIN.
    # These apply to raw last_accuracy; the annotator applies the same
    # thresholds to forgetting-adjusted retention. Keep in sync.
    if acc is None:
        return "unseen"
    try:
        a = float(acc)
    except (TypeError, ValueError):
        return "unseen"
    if a >= 0.70:
        return "mastered"
    if a >= 0.30:
        return "weak"
    return "forgotten"


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

    if topic:
        try:
            mastery_rows = fetch_mastery(DEFAULT_USER_ID, topic=topic)
        except Exception:
            logger.warning(
                "fetch_mastery raised unexpectedly; degrading to no-mastery",
                exc_info=True,
            )
            mastery_rows = []
    else:
        mastery_rows = []

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
