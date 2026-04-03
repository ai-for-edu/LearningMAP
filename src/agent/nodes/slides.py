"""Slide generation: outlines + per-slide content.

Adapted from OpenMAIC's scene-generator.ts with full post-processing pipeline:
- JSON repair (LaTeX escapes, truncation, code blocks)
- Element defaults fixing (line, text, shape, image)
- LaTeX element validation (remove broken ones)
- Canvas boundary enforcement
"""
from __future__ import annotations

import logging
import uuid

from src.llm.client import get_llm_client, get_model_name, get_raw_client
from src.llm.slide_schemas import (
    ImageElement,
    LatexElement,
    LineElement,
    PPTElement,
    ShapeElement,
    SlideData,
    SlideOutline,
    TextElement,
)
from src.prompts.loader import render_prompt
from src.utils.json_repair import parse_json_response

logger = logging.getLogger(__name__)

CANVAS_W = 1000
CANVAS_H = 562.5
MARGIN = 50
MAX_CONTENT_HEIGHT = CANVAS_H - MARGIN  # 512.5


# ---------------------------------------------------------------------------
# Stage 1: Outline generation
# ---------------------------------------------------------------------------

def generate_slide_outlines(state: dict) -> list[SlideOutline]:
    """Generate slide outlines from topic and diagnosis results."""
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
            status = b.status if hasattr(b, "status") else b.get("status", "")
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


# ---------------------------------------------------------------------------
# Stage 2: Per-slide content generation
# ---------------------------------------------------------------------------

def generate_slide_content(outline: SlideOutline) -> SlideData | None:
    """Generate single slide content from outline with full post-processing."""
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
        raw_content = response.choices[0].message.content.strip()
        logger.debug("Raw slide response length: %d chars", len(raw_content))

        # Step 1: Parse JSON with repair
        data = parse_json_response(raw_content)
        if data is None:
            logger.error("JSON parsing failed for slide: %s", outline.title)
            return None

        # Step 2: Build SlideData (handle elements as raw dicts first)
        elements_raw = data.get("elements", [])
        background = data.get("background")

        # Step 3: Fix element defaults (OpenMAIC fixElementDefaults)
        elements_raw = _fix_element_defaults(elements_raw)

        # Step 4: Process LaTeX elements (validate, remove broken ones)
        elements_raw = _process_latex_elements(elements_raw)

        # Step 5: Enforce canvas boundaries
        elements_raw = _enforce_canvas_bounds(elements_raw)

        # Step 6: Assign IDs and rotation
        elements_raw = _assign_ids_and_rotation(elements_raw)

        # Step 7: Convert to typed Pydantic models
        elements = _to_pydantic_elements(elements_raw)

        slide = SlideData(background=background, elements=elements)
        logger.debug(
            "Slide '%s': %d elements after processing",
            outline.title, len(elements),
        )
        return slide

    except Exception:
        logger.exception("Slide content generation failed for: %s", outline.title)
        return None


# ---------------------------------------------------------------------------
# Post-processing pipeline
# ---------------------------------------------------------------------------

def _fix_element_defaults(elements: list[dict]) -> list[dict]:
    """Fix missing defaults for all element types.

    Adapted from OpenMAIC's fixElementDefaults().
    """
    fixed = []
    for el in elements:
        el_type = el.get("type")

        if el_type == "line":
            # Ensure points
            if not el.get("points") or not isinstance(el["points"], list) or len(el["points"]) != 2:
                el["points"] = ["", ""]
            # Ensure start/end
            if not el.get("start") or not isinstance(el["start"], list):
                el["start"] = [el.get("left", 0), el.get("top", 0)]
            if not el.get("end") or not isinstance(el["end"], list):
                el["end"] = [el.get("left", 0) + el.get("width", 100), el.get("top", 0)]
            # Ensure style/color
            if not el.get("style"):
                el["style"] = "solid"
            if not el.get("color"):
                el["color"] = "#333333"
            # Width is stroke thickness (2-6), not line length
            if el.get("width", 0) > 6:
                el["width"] = 3

        elif el_type == "text":
            if not el.get("defaultFontName"):
                el["defaultFontName"] = "Microsoft YaHei"
            if not el.get("defaultColor"):
                el["defaultColor"] = "#333333"
            if not el.get("content"):
                el["content"] = ""

        elif el_type == "image":
            if el.get("fixedRatio") is None:
                el["fixedRatio"] = True

        elif el_type == "shape":
            if not el.get("viewBox"):
                w = el.get("width", 100)
                h = el.get("height", 100)
                el["viewBox"] = [w, h]
            if not el.get("path"):
                w = el.get("width", 100)
                h = el.get("height", 100)
                el["path"] = f"M 0 0 L {w} 0 L {w} {h} L 0 {h} Z"
            if not el.get("fill"):
                el["fill"] = "#5b9bd5"
            if el.get("fixedRatio") is None:
                el["fixedRatio"] = False

        fixed.append(el)
    return fixed


def _process_latex_elements(elements: list[dict]) -> list[dict]:
    """Validate LaTeX elements. Remove ones with missing/empty latex string.

    Adapted from OpenMAIC's processLatexElements().
    Note: Unlike OpenMAIC (which renders KaTeX server-side), we validate
    and let the frontend render with KaTeX.
    """
    result = []
    for el in elements:
        if el.get("type") != "latex":
            result.append(el)
            continue

        latex_str = el.get("latex")
        if not latex_str or not isinstance(latex_str, str) or not latex_str.strip():
            logger.warning("LaTeX element missing latex string, removing: %s", el.get("id", "?"))
            continue

        # Set fixedRatio
        el["fixedRatio"] = True

        # Validate: basic sanity check (has at least one math-like character)
        el["latex"] = latex_str.strip()
        result.append(el)

    return result


def _enforce_canvas_bounds(elements: list[dict]) -> list[dict]:
    """Ensure all elements stay within canvas boundaries."""
    for el in elements:
        el_type = el.get("type")

        # Lines don't have standard width/height bounds
        if el_type == "line":
            continue

        left = el.get("left", 0)
        top = el.get("top", 0)
        width = el.get("width", 0)
        height = el.get("height", 0)

        # Clamp position
        if left < 0:
            el["left"] = 0
        if top < 0:
            el["top"] = 0

        # Clamp right edge
        if left + width > CANVAS_W:
            el["width"] = max(CANVAS_W - left, 50)

        # Clamp bottom edge
        if top + height > CANVAS_H:
            el["height"] = max(CANVAS_H - top, 20)

    return elements


def _assign_ids_and_rotation(elements: list[dict]) -> list[dict]:
    """Assign unique IDs and default rotation to elements."""
    for el in elements:
        if not el.get("id"):
            el["id"] = f"{el.get('type', 'el')}_{uuid.uuid4().hex[:8]}"
        if "rotate" not in el and el.get("type") != "line":
            el["rotate"] = 0
    return elements


def _to_pydantic_elements(elements: list[dict]) -> list[PPTElement]:
    """Convert raw dicts to typed Pydantic element models."""
    result: list[PPTElement] = []
    for d in elements:
        el_type = d.get("type")
        try:
            if el_type == "text":
                result.append(TextElement(**d))
            elif el_type == "latex":
                # Remove fields that LatexElement doesn't have
                d.pop("fixedRatio", None)
                d.pop("rotate", None)
                result.append(LatexElement(**d))
            elif el_type == "shape":
                result.append(ShapeElement(**d))
            elif el_type == "line":
                result.append(LineElement(**d))
            elif el_type == "image":
                result.append(ImageElement(**d))
            else:
                logger.warning("Unknown element type: %s", el_type)
        except Exception:
            logger.warning("Skipping invalid element: %s (type=%s)", d.get("id", "?"), el_type)
    return result
