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
        slide = SlideData(**data)
        # Post-process: fix common LLM layout issues
        slide = _fix_element_defaults(slide)
        return slide
    except Exception:
        logger.exception(
            "Slide content generation failed for: %s", outline.title
        )
        return None


CANVAS_W = 1000
CANVAS_H = 562.5
MARGIN = 50


def _fix_element_defaults(slide: SlideData) -> SlideData:
    """Post-process slide elements to fix common layout issues.

    Fixes overflow, missing defaults, and line element width mistakes.
    Adapted from OpenMAIC's fixElementDefaults.
    """
    fixed_elements = []
    for el in slide.elements:
        d = el.model_dump()

        # Ensure within canvas bounds
        if "left" in d and "width" in d:
            if d["left"] < 0:
                d["left"] = MARGIN
            if d["left"] + d["width"] > CANVAS_W:
                d["width"] = CANVAS_W - d["left"] - MARGIN

        if "top" in d and "height" in d:
            if d["top"] < 0:
                d["top"] = MARGIN
            if d["top"] + d["height"] > CANVAS_H:
                d["height"] = CANVAS_H - d["top"] - MARGIN

        # Fix line element: width should be stroke thickness (2-6)
        if d.get("type") == "line" and d.get("width", 0) > 6:
            d["width"] = 3  # Reset to medium stroke

        # Fix text defaults
        if d.get("type") == "text":
            if not d.get("defaultFontName"):
                d["defaultFontName"] = ""
            if not d.get("defaultColor"):
                d["defaultColor"] = "#333333"

        # Fix shape defaults
        if d.get("type") == "shape":
            if not d.get("viewBox"):
                d["viewBox"] = [1, 1]
            if not d.get("path"):
                d["path"] = "M 0 0 L 1 0 L 1 1 L 0 1 Z"

        # Ensure id and rotate
        if not d.get("id"):
            d["id"] = f"{d.get('type', 'el')}_{id(d)}"
        if "rotate" not in d:
            d["rotate"] = 0

        # Reconstruct element from fixed dict
        from src.llm.slide_schemas import (
            ImageElement, LatexElement, LineElement, ShapeElement, TextElement,
        )
        el_type = d.get("type")
        try:
            if el_type == "text":
                fixed_elements.append(TextElement(**d))
            elif el_type == "latex":
                fixed_elements.append(LatexElement(**d))
            elif el_type == "shape":
                fixed_elements.append(ShapeElement(**d))
            elif el_type == "line":
                fixed_elements.append(LineElement(**d))
            elif el_type == "image":
                fixed_elements.append(ImageElement(**d))
        except Exception:
            logger.warning("Skipping invalid element: %s", d.get("id", "?"))

    slide.elements = fixed_elements
    return slide
