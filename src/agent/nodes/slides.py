"""Slide generation: outlines + per-slide content."""
from __future__ import annotations

import json
import logging

from src.llm.client import get_llm_client, get_model_name, get_raw_client
from src.llm.slide_schemas import SlideData, SlideOutline
from src.prompts.loader import render_prompt

logger = logging.getLogger(__name__)


def generate_slide_outlines(state: dict) -> list[SlideOutline]:
    """Generate slide outlines from topic and diagnosis results.

    Args:
        state: Current learning session state dict.

    Returns:
        List of slide outlines, empty list on failure.
    """
    topic = state.get("topic", "")
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")

    weak_points: list[dict] = []
    if diagnosis:
        breakdown = (
            diagnosis.breakdown
            if hasattr(diagnosis, "breakdown")
            else diagnosis.get("breakdown", [])
        )
        nodes = (
            graph.nodes
            if graph and hasattr(graph, "nodes")
            else (graph.get("nodes", []) if graph else [])
        )
        node_map = {
            (n.name if hasattr(n, "name") else n["name"]): n for n in nodes
        }
        for b in breakdown:
            status = (
                b.status if hasattr(b, "status") else b.get("status", "")
            )
            name = b.name if hasattr(b, "name") else b.get("name", "")
            if status == "weak":
                node = node_map.get(name)
                desc = ""
                if node:
                    desc = (
                        node.description
                        if hasattr(node, "description")
                        else node.get("description", "")
                    )
                weak_points.append({"name": name, "description": desc})

    prompt = render_prompt("slide_outline", topic=topic, weak_points=weak_points)

    try:
        client = get_llm_client()
        outlines = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            response_model=list[SlideOutline],
        )
        return outlines
    except Exception:
        logger.exception("Slide outline generation failed")
        return []


def generate_slide_content(outline: SlideOutline) -> SlideData | None:
    """Generate single slide content from outline.

    Args:
        outline: Slide outline with title, description, and key points.

    Returns:
        SlideData instance or None on failure.
    """
    system_prompt = render_prompt("slide_content_system")
    user_prompt = render_prompt(
        "slide_content_user",
        title=outline.title,
        description=outline.description,
        key_points=outline.keyPoints,
    )

    try:
        client = get_raw_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content.strip()
        # Strip code block wrapper if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(content)
        return SlideData(**data)
    except Exception:
        logger.exception(
            "Slide content generation failed for: %s", outline.title
        )
        return None
