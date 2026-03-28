from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import KnowledgeGraph
from src.prompts.loader import render_prompt
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

MAX_NODES = 8


def knowledge_graph_node(state: LearningState) -> dict:
    """Generate knowledge graph for the given topic."""
    topic = state.get("topic", "")
    prompt = render_prompt("knowledge_graph", topic=topic, max_nodes=MAX_NODES)

    try:
        client = get_llm_client()
        graph = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            response_model=KnowledgeGraph,
        )
    except Exception:
        logger.exception("Knowledge graph generation failed")
        return {
            "messages": [AIMessage(content="知识图谱生成失败，请稍后再试。")],
            "mode": "chat",
        }

    reply = f"我已经为你梳理了「{topic}」的知识图谱，包含 {len(graph.nodes)} 个知识点。准备好了就点击开始诊断！"
    return {
        "messages": [AIMessage(content=reply)],
        "knowledge_graph": graph,
        "mode": "graph",
    }
