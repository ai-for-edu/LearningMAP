from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_raw_client, get_model_name
from src.prompts.loader import render_prompt
from src.skills.loader import get_skill_section
from langchain_core.messages import AIMessage

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

    # Build points info
    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    node_map = {(n.name if hasattr(n, "name") else n["name"]): n for n in nodes}

    skill_instructions = get_skill_section(state, "lesson")

    if not weak_items:
        # All mastered — generate review/consolidation lesson
        all_points = [{"name": n.name if hasattr(n, "name") else n["name"],
                       "description": n.description if hasattr(n, "description") else n.get("description", "")}
                      for n in nodes]
        prompt = render_prompt("lesson_review", topic=topic, points=all_points, skill_instructions=skill_instructions)
    else:
        # Has weak points — generate targeted lesson
        weak_points = []
        for item in weak_items:
            name = item.name if hasattr(item, "name") else item["name"]
            node = node_map.get(name)
            if node:
                desc = node.description if hasattr(node, "description") else node.get("description", "")
                weak_points.append({"name": name, "description": desc})
            else:
                weak_points.append({"name": name, "description": ""})
        prompt = render_prompt("lesson_plan", topic=topic, weak_points=weak_points, skill_instructions=skill_instructions)

    client = get_raw_client()
    response = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    lesson_text = response.choices[0].message.content
    return {
        "lesson_content": lesson_text,
        "mode": "lesson",
        "messages": [AIMessage(content=lesson_text)],
    }


def _build_lesson_prompt(state: dict) -> str | None:
    """Build the lesson prompt from state. Returns None if diagnosis missing."""
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")
    topic = state.get("topic", "")

    if not diagnosis:
        return None

    breakdown = diagnosis.breakdown if hasattr(diagnosis, "breakdown") else diagnosis.get("breakdown", [])
    weak_items = [b for b in breakdown if (b.status if hasattr(b, "status") else b["status"]) == "weak"]

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    node_map = {(n.name if hasattr(n, "name") else n["name"]): n for n in nodes}

    skill_instructions = get_skill_section(state, "lesson")

    if not weak_items:
        all_points = [{"name": n.name if hasattr(n, "name") else n["name"],
                       "description": n.description if hasattr(n, "description") else n.get("description", "")}
                      for n in nodes]
        return render_prompt("lesson_review", topic=topic, points=all_points, skill_instructions=skill_instructions)

    weak_points = []
    for item in weak_items:
        name = item.name if hasattr(item, "name") else item["name"]
        node = node_map.get(name)
        desc = node.description if node and hasattr(node, "description") else (node.get("description", "") if node else "")
        weak_points.append({"name": name, "description": desc})
    return render_prompt("lesson_plan", topic=topic, weak_points=weak_points, skill_instructions=skill_instructions)


def lesson_node_stream(state: dict):
    """Streaming version of lesson_node. Yields token strings."""
    prompt = _build_lesson_prompt(state)
    if not prompt:
        yield "请先完成诊断。"
        return

    client = get_raw_client()
    stream = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content
