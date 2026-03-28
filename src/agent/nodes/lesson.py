from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_model_name
from src.prompts.loader import render_prompt
from langchain_core.messages import AIMessage
import anthropic

logger = logging.getLogger(__name__)


def lesson_node(state: LearningState) -> dict:
    """Generate personalized lesson for weak points."""
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")
    topic = state.get("topic", "")

    if not diagnosis:
        return {"mode": "chat", "messages": [AIMessage(content="请先完成诊断。")]}

    # Get weak points
    breakdown = diagnosis.breakdown if hasattr(diagnosis, "breakdown") else diagnosis.get("breakdown", [])
    weak_items = [b for b in breakdown if (b.status if hasattr(b, "status") else b["status"]) == "weak"]

    if not weak_items:
        return {
            "mode": "chat",
            "lesson_content": "",
            "messages": [AIMessage(content="你已经掌握了所有知识点，太棒了！有其他想学的吗？")],
        }

    # Build weak points info
    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    node_map = {(n.name if hasattr(n, "name") else n["name"]): n for n in nodes}
    weak_points = []
    for item in weak_items:
        name = item.name if hasattr(item, "name") else item["name"]
        node = node_map.get(name)
        if node:
            desc = node.description if hasattr(node, "description") else node.get("description", "")
            weak_points.append({"name": name, "description": desc})
        else:
            weak_points.append({"name": name, "description": ""})

    prompt = render_prompt("lesson_plan", topic=topic, weak_points=weak_points)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=get_model_name(),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    lesson_text = response.content[0].text
    return {
        "lesson_content": lesson_text,
        "mode": "lesson",
        "messages": [AIMessage(content=lesson_text)],
    }
