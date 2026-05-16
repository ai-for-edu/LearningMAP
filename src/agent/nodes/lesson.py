from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable
from src.agent.state import LearningState
from src.llm.client import get_raw_client, get_model_name
from src.prompts.loader import render_prompt, render_kc_skill_block
from src.skills.loader import get_skill_section
from src.agent.nodes._helpers import memory_snapshot_block
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


def _resolve_fanout_limit(arg: int | None) -> int:
    """Resolve the lesson fan-out concurrency limit.

    Precedence: explicit `arg` > env var `LESSON_FANOUT_LIMIT` > default 4.
    A non-integer env value silently falls back to the default.
    """
    if arg is not None:
        return arg
    env_val = os.environ.get("LESSON_FANOUT_LIMIT")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return 4


def generate_knowledge_skill(
    topic: str,
    knowledge_point_name: str,
    knowledge_point_description: str,
    pedagogy_method: str,
    pedagogy_instructions: str,
) -> str:
    """Step 1b: Generate a teaching micro-skill for one knowledge point via LLM."""
    prompt = render_prompt(
        "knowledge_skill",
        topic=topic,
        knowledge_point_name=knowledge_point_name,
        knowledge_point_description=knowledge_point_description,
        pedagogy_method=pedagogy_method,
        pedagogy_instructions=pedagogy_instructions,
    )
    client = get_raw_client()
    response = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def _extract_weak_points(state: dict) -> tuple[str, list[dict]]:
    """Extract weak knowledge points from diagnosis + knowledge graph.

    Returns (pedagogy_instructions, weak_points_list).
    """
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")

    if not diagnosis or not graph:
        return get_skill_section(state, "lesson"), []

    breakdown = (
        diagnosis.breakdown
        if hasattr(diagnosis, "breakdown")
        else diagnosis.get("breakdown", [])
    )
    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    node_map = {(n.name if hasattr(n, "name") else n["name"]): n for n in nodes}

    weak_items = [
        b
        for b in breakdown
        if (b.status if hasattr(b, "status") else b["status"]) == "weak"
    ]

    review_strong = set(
        (state.get("review_result") or {}).get("strong_kps") or []
    )

    weak_points = []
    for item in weak_items:
        name = item.name if hasattr(item, "name") else item["name"]
        if not name or not name.strip():
            continue
        if name in review_strong:
            continue
        node = node_map.get(name)
        desc = ""
        if node:
            desc = (
                node.description
                if hasattr(node, "description")
                else node.get("description", "")
            )
        weak_points.append({"name": name, "description": desc})

    return get_skill_section(state, "lesson"), weak_points


def retrieve_skills(state: dict) -> tuple[str, dict[str, str], list[dict]]:
    """Step 1: Retrieve pedagogy skill + optionally generate knowledge point skills.

    Returns:
        (pedagogy_instructions, knowledge_skills, weak_points)
        - pedagogy_instructions: str from active skill's lesson section
        - knowledge_skills: {knowledge_point_name: generated_skill_text}
        - weak_points: list of {"name": ..., "description": ...} for weak points
    """
    topic = state.get("topic", "")
    pedagogy_instructions, weak_points = _extract_weak_points(state)

    if not weak_points:
        return pedagogy_instructions, {}, []

    # Step 1b: Generate knowledge point skills (optional — user can toggle off)
    if not state.get("use_knowledge_skills", True):
        return pedagogy_instructions, {}, weak_points

    active_skill = state.get("active_skill", "")
    skill_meta = None
    if active_skill:
        from src.skills.loader import get_skill

        skill_meta = get_skill(active_skill)

    pedagogy_method = skill_meta.display_name if skill_meta else "Default teaching method"

    knowledge_skills = {}
    for wp in weak_points:
        skill_text = generate_knowledge_skill(
            topic=topic,
            knowledge_point_name=wp["name"],
            knowledge_point_description=wp["description"],
            pedagogy_method=pedagogy_method,
            pedagogy_instructions=pedagogy_instructions or "No specific teaching method",
        )
        knowledge_skills[wp["name"]] = skill_text

    return pedagogy_instructions, knowledge_skills, weak_points


async def generate_lesson_per_point_async(
    topic: str,
    weak_points: list[dict],
    pedagogy_instructions: str,
    knowledge_skills: dict[str, str],
    kc_skills: dict | None = None,
    focus_directive: str = "",
    state: dict | None = None,
    limit: int | None = None,
    on_section_done: Callable[[int], None] | None = None,
) -> str:
    """Async fan-out version of generate_lesson_per_point.

    Generates per-KC sections concurrently, joins them in the original
    weak_points order. Output is byte-identical to the serial version
    for identical inputs.
    """
    if not weak_points:
        return ""

    client = get_raw_client()
    total = len(weak_points)
    sem = asyncio.Semaphore(_resolve_fanout_limit(limit))

    async def _one_section(i: int, wp: dict) -> tuple[int, str]:
        kc_skill_block = render_kc_skill_block((kc_skills or {}).get(wp["name"]))
        prompt = render_prompt(
            "lesson_point",
            topic=topic,
            knowledge_point_name=wp["name"],
            knowledge_point_description=wp["description"],
            pedagogy_instructions=pedagogy_instructions or "No specific teaching method",
            knowledge_skill=knowledge_skills.get(wp["name"], "no additional teaching strategy"),
            point_index=i + 1,
            total_points=total,
            kc_skill_block=kc_skill_block,
            focus_directive=focus_directive,
            memory_snapshot_block=memory_snapshot_block(state),
        )
        async with sem:
            response = await asyncio.to_thread(
                lambda p=prompt: client.chat.completions.create(
                    model=get_model_name(),
                    max_tokens=4096,
                    messages=[{"role": "user", "content": p}],
                )
            )
        return i, response.choices[0].message.content.strip()

    tasks = [_one_section(i, wp) for i, wp in enumerate(weak_points)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0])
    return "\n\n---\n\n".join(text for _, text in results)


def generate_lesson_per_point(
    topic: str,
    weak_points: list[dict],
    pedagogy_instructions: str,
    knowledge_skills: dict[str, str],
    kc_skills: dict | None = None,
    focus_directive: str = "",
    state: dict | None = None,
) -> str:
    """Step 2: Generate lesson content per knowledge point, then concatenate."""
    client = get_raw_client()
    sections = []
    total = len(weak_points)

    for i, wp in enumerate(weak_points):
        kc_skill_block = render_kc_skill_block((kc_skills or {}).get(wp["name"]))
        prompt = render_prompt(
            "lesson_point",
            topic=topic,
            knowledge_point_name=wp["name"],
            knowledge_point_description=wp["description"],
            pedagogy_instructions=pedagogy_instructions or "No specific teaching method",
            knowledge_skill=knowledge_skills.get(wp["name"], "no additional teaching strategy"),
            point_index=i + 1,
            total_points=total,
            kc_skill_block=kc_skill_block,
            focus_directive=focus_directive,
            memory_snapshot_block=memory_snapshot_block(state),
        )
        response = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        sections.append(response.choices[0].message.content.strip())

    return "\n\n---\n\n".join(sections)


def _generate_review_lesson(
    topic: str,
    nodes: list,
    skill_instructions: str,
    state: dict | None = None,
) -> str:
    """Generate a review/consolidation lesson when all points are mastered."""
    all_points = [
        {
            "name": n.name if hasattr(n, "name") else n["name"],
            "description": n.description
            if hasattr(n, "description")
            else n.get("description", ""),
        }
        for n in nodes
    ]
    prompt = render_prompt(
        "lesson_review",
        topic=topic,
        points=all_points,
        skill_instructions=skill_instructions,
        memory_snapshot_block=memory_snapshot_block(state),
    )
    client = get_raw_client()
    response = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def lesson_node(state: LearningState) -> dict:
    """Generate personalized lesson using two-step pipeline."""
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")
    topic = state.get("topic", "")

    if not diagnosis:
        return {"mode": "chat", "messages": [AIMessage(content="Please complete the diagnosis first.")]}

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])

    # Step 1: Retrieve skills
    pedagogy_instructions, knowledge_skills, weak_points = retrieve_skills(state)

    if not weak_points:
        lesson_text = _generate_review_lesson(topic, nodes, pedagogy_instructions, state=state)
        return {
            "lesson_content": lesson_text,
            "knowledge_skills": None,
            "mode": "lesson",
            "messages": [AIMessage(content=lesson_text)],
        }

    # Step 2: Generate per-point content
    focus_directive = (state.get("review_result") or {}).get("focus_directive", "")
    lesson_text = generate_lesson_per_point(
        topic, weak_points, pedagogy_instructions, knowledge_skills,
        kc_skills=state.get("kc_skills"),
        focus_directive=focus_directive,
        state=state,
    )

    return {
        "lesson_content": lesson_text,
        "knowledge_skills": knowledge_skills or None,
        "mode": "lesson",
        "messages": [AIMessage(content=lesson_text)],
    }


def _build_lesson_prompt(state: dict) -> str | None:
    """Build the lesson prompt from state. Kept for backward compatibility."""
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")
    topic = state.get("topic", "")

    if not diagnosis:
        return None

    breakdown = (
        diagnosis.breakdown
        if hasattr(diagnosis, "breakdown")
        else diagnosis.get("breakdown", [])
    )
    weak_items = [
        b
        for b in breakdown
        if (b.status if hasattr(b, "status") else b["status"]) == "weak"
    ]

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    node_map = {(n.name if hasattr(n, "name") else n["name"]): n for n in nodes}

    skill_instructions = get_skill_section(state, "lesson")
    focus_directive = (state.get("review_result") or {}).get("focus_directive", "")

    if not weak_items:
        all_points = [
            {
                "name": n.name if hasattr(n, "name") else n["name"],
                "description": n.description
                if hasattr(n, "description")
                else n.get("description", ""),
            }
            for n in nodes
        ]
        return render_prompt(
            "lesson_review",
            topic=topic,
            points=all_points,
            skill_instructions=skill_instructions,
            memory_snapshot_block=memory_snapshot_block(state),
        )

    weak_points = []
    for item in weak_items:
        name = item.name if hasattr(item, "name") else item["name"]
        node = node_map.get(name)
        desc = (
            node.description
            if node and hasattr(node, "description")
            else (node.get("description", "") if node else "")
        )
        weak_points.append({"name": name, "description": desc})
    return render_prompt(
        "lesson_plan",
        topic=topic,
        weak_points=weak_points,
        skill_instructions=skill_instructions,
        focus_directive=focus_directive,
        memory_snapshot_block=memory_snapshot_block(state),
    )


def lesson_node_stream(state: dict):
    """Streaming version — generates per-point with section markers."""
    diagnosis = state.get("diagnosis_result")
    if not diagnosis:
        yield "Please complete the diagnosis first."
        return

    graph = state.get("knowledge_graph")
    topic = state.get("topic", "")
    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])

    pedagogy_instructions, knowledge_skills, weak_points = retrieve_skills(state)
    focus_directive = (state.get("review_result") or {}).get("focus_directive", "")

    if not weak_points:
        skill_instructions = get_skill_section(state, "lesson")
        all_points = [
            {
                "name": n.name if hasattr(n, "name") else n["name"],
                "description": n.description
                if hasattr(n, "description")
                else n.get("description", ""),
            }
            for n in nodes
        ]
        prompt = render_prompt(
            "lesson_review",
            topic=topic,
            points=all_points,
            skill_instructions=skill_instructions,
            memory_snapshot_block=memory_snapshot_block(state),
        )
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
        return

    client = get_raw_client()
    total = len(weak_points)

    for i, wp in enumerate(weak_points):
        if i > 0:
            yield "\n\n---\n\n"

        kc_skill_block = render_kc_skill_block((state.get("kc_skills") or {}).get(wp["name"]))
        prompt = render_prompt(
            "lesson_point",
            topic=topic,
            knowledge_point_name=wp["name"],
            knowledge_point_description=wp["description"],
            pedagogy_instructions=pedagogy_instructions or "No specific teaching method",
            knowledge_skill=knowledge_skills.get(wp["name"], "no additional teaching strategy"),
            point_index=i + 1,
            total_points=total,
            kc_skill_block=kc_skill_block,
            focus_directive=focus_directive,
            memory_snapshot_block=memory_snapshot_block(state),
        )
        stream = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
