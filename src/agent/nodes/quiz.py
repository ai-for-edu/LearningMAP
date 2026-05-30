from __future__ import annotations

import asyncio
import logging
import os
from typing import Sequence

from langchain_core.messages import AIMessage

from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import KnowledgeNode, QuizQuestion
from src.agent.nodes._helpers import memory_snapshot_block
from src.prompts.loader import render_kc_skill_block, render_prompt
from src.skills.loader import get_skill_section

logger = logging.getLogger(__name__)


def _resolve_quiz_fanout_limit(arg: int | None) -> int:
    """Resolve the quiz fan-out concurrency limit.

    Precedence: explicit `arg` > env var `QUIZ_FANOUT_LIMIT` > default 4.
    A non-integer env value silently falls back to the default.
    Values < 1 are clamped to 1 to prevent asyncio.Semaphore(0) deadlock.
    """
    if arg is not None:
        return max(1, arg)
    env_val = os.environ.get("QUIZ_FANOUT_LIMIT")
    if env_val:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    return 4


QUESTIONS_PER_POINT = 2
# UNSEEN_PRIORITY = priority for nodes with NO memory record at all
# (mastery is None). Set below 1.0 because we deliberately rank these
# below "matched-but-forgotten" nodes (mastery exists, retention low →
# priority ~1.0). Rationale: a student we've never seen on a topic gets
# fewer questions than one we've seen but who has clearly forgotten.
UNSEEN_PRIORITY = 0.7
MIN_PER_NODE = 1


def _priority(node: KnowledgeNode) -> float:
    """Higher priority → more questions. Mastery-aware when possible."""
    if node.mastery is None:
        return UNSEEN_PRIORITY
    return max(0.0, 1.0 - float(node.mastery.retention))


def _allocate_questions_per_node(
    nodes: Sequence[KnowledgeNode],
    *,
    base_per_node: int = QUESTIONS_PER_POINT,
) -> dict[str, int]:
    """Distribute base_per_node × N questions across nodes by priority.

    Every node gets at least MIN_PER_NODE. Remaining budget is split in
    proportion to priority. Total is preserved (modulo rounding ±len(nodes)).
    """
    n = len(nodes)
    if n == 0:
        return {}

    # If no mastery info anywhere → uniform (preserves legacy behavior)
    if all(node.mastery is None for node in nodes):
        return {node.name: base_per_node for node in nodes}

    total_budget = base_per_node * n
    weights = [_priority(node) for node in nodes]
    total_w = sum(weights)
    if total_w <= 0:
        return {node.name: base_per_node for node in nodes}

    # Step 1: floor of weighted share, with a min of MIN_PER_NODE
    raw = [max(MIN_PER_NODE, int((w / total_w) * total_budget)) for w in weights]
    # Step 2: distribute leftover by priority (highest weight gets extras)
    used = sum(raw)
    leftover = total_budget - used
    if leftover > 0:
        order = sorted(range(n), key=lambda i: -weights[i])
        for i in order[:leftover]:
            raw[i] += 1
    elif leftover < 0:
        # Trim greedily from the highest-raw node above MIN until budget
        # is satisfied. Single-pass trimming wasn't enough when one node
        # dominated the raw allocation (e.g., one forgotten kp + many
        # mastered kps pinned at MIN).
        while leftover < 0:
            best = -1
            best_raw = MIN_PER_NODE
            for i in range(n):
                if raw[i] > best_raw:
                    best = i
                    best_raw = raw[i]
            if best < 0:
                break  # everyone at MIN; give up
            raw[best] -= 1
            leftover += 1

    return {node.name: raw[i] for i, node in enumerate(nodes)}


def _coerce_node(item) -> KnowledgeNode:
    """Accept either a KnowledgeNode or a dict (after JSON round-trip)."""
    if isinstance(item, KnowledgeNode):
        return item
    return KnowledgeNode.model_validate(item)


def quiz_node(state: LearningState) -> dict:
    """Generate quiz questions or serve the next question."""
    questions = state.get("questions", [])
    current_idx = state.get("current_question_index", 0)

    if not questions:
        graph = state.get("knowledge_graph")
        if not graph:
            return {
                "mode": "chat",
                "messages": [AIMessage(content="Please select a learning topic first.")],
            }

        raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        nodes = [_coerce_node(n) for n in raw_nodes]
        per_node = _allocate_questions_per_node(nodes)

        skill_instructions = get_skill_section(state, "quiz")
        kc_skills_state = state.get("kc_skills") or {}

        all_generated: list[QuizQuestion] = []
        client = get_llm_client()
        for node in nodes:
            count = per_node.get(node.name, QUESTIONS_PER_POINT)
            if count <= 0:
                continue
            point = {"name": node.name, "description": node.description}
            block = render_kc_skill_block(kc_skills_state.get(node.name))
            kc_skill_block = block or ""
            prompt = render_prompt(
                "quiz_generate",
                knowledge_points=[point],
                questions_per_point=count,
                skill_instructions=skill_instructions,
                kc_skill_block=kc_skill_block,
                memory_snapshot_block=memory_snapshot_block(state),
            )
            batch = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                response_model=list[QuizQuestion],
            )
            # Pin knowledge_point to the KG node name. The LLM has been
            # observed to paraphrase or append the description into this
            # field (e.g. "multiplication principle: explanation of step-by-step counting..."),
            # which then becomes the subtopic key in memory and breaks cross-session matching.
            for q in batch:
                q.knowledge_point = node.name
            all_generated.extend(batch)

        return {
            "questions": all_generated,
            "current_question_index": 0,
            "answers": [],
            "mode": "quiz",
            "messages": [
                AIMessage(content=f"{len(all_generated)} questions ready. Let's begin!")
            ],
        }

    if current_idx < len(questions):
        q = questions[current_idx]
        q_name = (
            q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
        )
        q_text = q.question if hasattr(q, "question") else q["question"]
        opts = q.options if hasattr(q, "options") else q["options"]
        options_text = "\n".join(
            f"  {chr(65 + i)}) {opt}" for i, opt in enumerate(opts)
        )
        reply = f"[{q_name}] Question {current_idx + 1}/{len(questions)}\n\n{q_text}\n\n{options_text}"
        return {"mode": "quiz", "messages": [AIMessage(content=reply)]}

    return {"mode": "result"}


def quiz_generate_progressive(state: dict):
    """Generate quiz questions one knowledge point at a time, weighted by mastery.

    Yields list[QuizQuestion] batches.
    """
    graph = state.get("knowledge_graph")
    if not graph:
        return

    raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    nodes = [_coerce_node(n) for n in raw_nodes]
    per_node = _allocate_questions_per_node(nodes)

    skill_instructions = get_skill_section(state, "quiz")
    client = get_llm_client()

    for node in nodes:
        count = per_node.get(node.name, QUESTIONS_PER_POINT)
        if count <= 0:
            continue
        point = {"name": node.name, "description": node.description}
        kc_skill_block = render_kc_skill_block(
            (state.get("kc_skills") or {}).get(node.name)
        )
        prompt = render_prompt(
            "quiz_generate",
            knowledge_points=[point],
            questions_per_point=count,
            skill_instructions=skill_instructions,
            kc_skill_block=kc_skill_block,
            memory_snapshot_block=memory_snapshot_block(state),
        )
        try:
            batch = client.chat.completions.create(
                model=get_model_name(),
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                response_model=list[QuizQuestion],
            )
            # Pin knowledge_point to the KG node name — same reason as in
            # quiz_node: prevent LLM-paraphrase pollution from breaking memory matching.
            for q in batch:
                q.knowledge_point = node.name
            yield batch
        except Exception:
            logger.exception(
                "Failed to generate quiz for knowledge point: %s", node.name
            )


async def generate_quiz_per_point_async(
    state: dict,
    *,
    limit: int | None = None,
    on_point_done=None,
) -> list:
    """Generate quiz batches concurrently across knowledge points.

    Returns the full list of QuizQuestion in KC declaration order
    (i.e. as nodes appear in state['knowledge_graph'].nodes). Per-KC
    failures are logged and contribute an empty batch — the function
    never raises for an LLM error.

    on_point_done(done_count, kc_index, batch) fires once per completed
    KC, with done_count monotonically increasing 1..N in completion
    order (not KC order). kc_index is the original index into nodes.
    """
    graph = state.get("knowledge_graph")
    if not graph:
        return []
    raw_nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    if not raw_nodes:
        return []

    nodes = [_coerce_node(n) for n in raw_nodes]
    per_node = _allocate_questions_per_node(nodes)
    skill_instructions = get_skill_section(state, "quiz")
    kc_skills_state = state.get("kc_skills") or {}
    client = get_llm_client()
    sem = asyncio.Semaphore(_resolve_quiz_fanout_limit(limit))
    done_counter = {"n": 0}

    async def _one_point(i: int, node: KnowledgeNode):
        count = per_node.get(node.name, QUESTIONS_PER_POINT)
        if count <= 0:
            batch: list[QuizQuestion] = []
        else:
            # try wraps prompt assembly AS WELL — render_prompt,
            # render_kc_skill_block, and memory_snapshot_block(state) can
            # all raise (template lookup, malformed kc_skills, memory
            # service down). Keeping them outside the try would let
            # asyncio.gather raise and abort all sibling KCs, breaking
            # the best-effort contract that matches the serial path.
            try:
                point = {"name": node.name, "description": node.description}
                kc_skill_block = render_kc_skill_block(
                    kc_skills_state.get(node.name)
                ) or ""
                prompt = render_prompt(
                    "quiz_generate",
                    knowledge_points=[point],
                    questions_per_point=count,
                    skill_instructions=skill_instructions,
                    kc_skill_block=kc_skill_block,
                    memory_snapshot_block=memory_snapshot_block(state),
                )
                async with sem:
                    batch = await asyncio.to_thread(
                        lambda p=prompt: client.chat.completions.create(
                            model=get_model_name(),
                            max_tokens=4096,
                            messages=[{"role": "user", "content": p}],
                            response_model=list[QuizQuestion],
                        )
                    )
                # Pin knowledge_point to the KG node name. The LLM has been
                # observed to paraphrase or append the description into this
                # field, which then becomes the subtopic key in memory and
                # breaks cross-session matching.
                for q in batch:
                    q.knowledge_point = node.name
            except Exception:
                logger.exception(
                    "Failed to generate quiz for knowledge point: %s", node.name
                )
                batch = []

        done_counter["n"] += 1
        if on_point_done is not None:
            on_point_done(done_counter["n"], i, batch)
        return i, batch

    tasks = [asyncio.create_task(_one_point(i, n)) for i, n in enumerate(nodes)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda pair: pair[0])
    return [q for _, batch in results for q in batch]
