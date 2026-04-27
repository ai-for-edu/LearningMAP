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
