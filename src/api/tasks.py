"""Background task manager for async LLM generation.

Runs quiz/lesson/slides generation in background asyncio tasks.
Users can leave pages and return — generation continues.
Supports cross-session parallelism and cancellation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BackgroundTask:
    """Metadata for a running background generation task."""

    task_id: str  # "{session_id}:{task_type}"
    session_id: str
    task_type: str  # "quiz" | "lesson" | "slides"
    asyncio_task: asyncio.Task | None = None
    cancelled: bool = False


class TaskManager:
    """Singleton manager for all background generation tasks.

    Tasks are keyed by "{session_id}:{task_type}" so each session can
    have up to 3 concurrent tasks (quiz + lesson + slides).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}

    def _key(self, session_id: str, task_type: str) -> str:
        return f"{session_id}:{task_type}"

    def is_running(self, session_id: str, task_type: str) -> bool:
        """Check if a task is actively running (not just in dict)."""
        key = self._key(session_id, task_type)
        task = self._tasks.get(key)
        if not task or not task.asyncio_task:
            return False
        return not task.asyncio_task.done()

    def start(
        self,
        session_id: str,
        task_type: str,
        coro_factory,
    ) -> str:
        """Start a background task. Returns task_id.

        Args:
            session_id: The session to generate for.
            task_type: "quiz", "lesson", or "slides".
            coro_factory: Callable that returns the coroutine to run.
                         Called as coro_factory() to create the coroutine.

        If a task of the same type is already running for this session,
        returns the existing task_id without starting a new one.
        """
        key = self._key(session_id, task_type)

        # If already running, don't start a new one
        if self.is_running(session_id, task_type):
            logger.info("Task %s already running, skipping", key)
            return key

        # Cancel any stale task object
        if key in self._tasks:
            old = self._tasks[key]
            if old.asyncio_task and not old.asyncio_task.done():
                old.asyncio_task.cancel()

        bt = BackgroundTask(
            task_id=key,
            session_id=session_id,
            task_type=task_type,
        )
        bt.asyncio_task = asyncio.create_task(coro_factory())
        self._tasks[key] = bt
        logger.info("Started background task: %s", key)
        return key

    def cancel(self, session_id: str, task_type: str) -> bool:
        """Cancel a running task and remove from registry.

        Removing from registry ensures start() can create a new task
        immediately, without waiting for the old one to finish dying.
        """
        key = self._key(session_id, task_type)
        task = self._tasks.pop(key, None)
        if not task:
            return False
        task.cancelled = True
        if task.asyncio_task and not task.asyncio_task.done():
            task.asyncio_task.cancel()
        logger.info("Cancelled task: %s", key)
        return True

    def cancel_all(self, session_id: str) -> None:
        """Cancel all tasks for a session (called when session is deleted)."""
        for task_type in ("quiz", "lesson", "slides"):
            self.cancel(session_id, task_type)

    def get_task(self, session_id: str, task_type: str) -> BackgroundTask | None:
        return self._tasks.get(self._key(session_id, task_type))


# Module-level singleton
task_manager = TaskManager()


# ------------------------------------------------------------------
# Background generation coroutines
# ------------------------------------------------------------------


async def run_quiz_generation(
    session_id: str,
    state_snapshot: dict[str, Any],
    get_session,
    save_session,
) -> None:
    """Generate quiz questions in background, one knowledge point at a time."""
    from src.agent.nodes.quiz import quiz_generate_progressive

    task = task_manager.get_task(session_id, "quiz")

    try:
        # Update status
        state = get_session(session_id) or {}
        state["quiz_task_status"] = "running"
        state["quiz_task_progress"] = "0/?"
        save_session(session_id, state)

        all_questions = []
        nodes = state_snapshot.get("knowledge_graph")
        if nodes:
            nodes = nodes.nodes if hasattr(nodes, "nodes") else nodes.get("nodes", [])
        total = len(nodes) if nodes else 0

        for batch in quiz_generate_progressive(state_snapshot):
            if task and task.cancelled:
                logger.info("Quiz generation cancelled for %s", session_id)
                break

            all_questions.extend(batch)
            point_idx = len(all_questions) // 2  # ~2 questions per point

            # Save intermediate results
            state = get_session(session_id) or {}
            state["questions"] = [q.model_dump() for q in all_questions]
            state["quiz_task_progress"] = f"{point_idx}/{total}"
            save_session(session_id, state)

        # Finalize
        state = get_session(session_id) or {}
        state["questions"] = [q.model_dump() for q in all_questions]
        state["current_question_index"] = 0
        state["answers"] = []
        state["mode"] = "quiz"
        state["quiz_task_status"] = "done" if not (task and task.cancelled) else None
        state["quiz_task_progress"] = None
        save_session(session_id, state)

    except Exception:
        logger.exception("Quiz generation failed for %s", session_id)
        state = get_session(session_id) or {}
        state["quiz_task_status"] = "error"
        save_session(session_id, state)


async def run_lesson_generation(
    session_id: str,
    state_snapshot: dict[str, Any],
    get_session,
    save_session,
) -> None:
    """Generate lesson content in background using two-step pipeline."""
    from src.agent.nodes.lesson import retrieve_skills, _generate_review_lesson
    from src.prompts.loader import render_prompt
    from src.llm.client import get_raw_client, get_model_name
    from src.memory_client import ingest_session, DEFAULT_USER_ID

    try:
        state = get_session(session_id) or {}
        state["lesson_task_status"] = "running"
        state["lesson_task_progress"] = "retrieving skills"
        save_session(session_id, state)

        task = task_manager.get_task(session_id, "lesson")

        # Step 1: Retrieve skills
        pedagogy_instructions, knowledge_skills, weak_points = await asyncio.to_thread(
            retrieve_skills, state_snapshot
        )

        if task and task.cancelled:
            return

        # Save knowledge skills to state
        state = get_session(session_id) or {}
        state["knowledge_skills"] = knowledge_skills if knowledge_skills else None
        state["lesson_task_progress"] = (
            f"0/{len(weak_points)}" if weak_points else "generating"
        )
        save_session(session_id, state)

        if not weak_points:
            # All mastered — review lesson
            graph = state_snapshot.get("knowledge_graph")
            nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
            lesson_text = await asyncio.to_thread(
                _generate_review_lesson,
                state_snapshot.get("topic", ""),
                nodes,
                pedagogy_instructions,
            )
        else:
            # Step 2: Generate per-point content (with progress updates)
            focus_directive = (state_snapshot.get("review_result") or {}).get("focus_directive", "")
            client = get_raw_client()
            sections = []
            total = len(weak_points)

            for i, wp in enumerate(weak_points):
                if task and task.cancelled:
                    logger.info("Lesson generation cancelled for %s", session_id)
                    return

                prompt = render_prompt(
                    "lesson_point",
                    topic=state_snapshot.get("topic", ""),
                    knowledge_point_name=wp["name"],
                    knowledge_point_description=wp["description"],
                    pedagogy_instructions=pedagogy_instructions or "无特定教学法要求",
                    knowledge_skill=knowledge_skills.get(wp["name"], "无额外教学技能"),
                    point_index=i + 1,
                    total_points=total,
                    focus_directive=focus_directive,
                )
                response = await asyncio.to_thread(
                    lambda p=prompt: client.chat.completions.create(
                        model=get_model_name(),
                        max_tokens=4096,
                        messages=[{"role": "user", "content": p}],
                    )
                )
                sections.append(response.choices[0].message.content.strip())

                # Update progress
                state = get_session(session_id) or {}
                state["lesson_task_progress"] = f"{i + 1}/{total}"
                save_session(session_id, state)

            lesson_text = "\n\n---\n\n".join(sections)

        if task and task.cancelled:
            return

        state = get_session(session_id) or {}
        state["lesson_content"] = lesson_text
        state["lesson_task_status"] = "done"
        state["lesson_task_progress"] = None
        state["lesson_format"] = "markdown"
        if knowledge_skills:
            state["knowledge_skills"] = knowledge_skills
        save_session(session_id, state)

        # Memory ingest
        asyncio.get_event_loop().run_in_executor(
            None,
            ingest_session,
            DEFAULT_USER_ID,
            f"{session_id}_lesson",
            state_snapshot,
        )

    except Exception:
        logger.exception("Lesson generation failed for %s", session_id)
        state = get_session(session_id) or {}
        state["lesson_task_status"] = "error"
        save_session(session_id, state)


async def run_slides_generation(
    session_id: str,
    state_snapshot: dict[str, Any],
    get_session,
    save_session,
) -> None:
    """Generate slides in background, one slide at a time."""
    from src.agent.nodes.slides import generate_slide_content, generate_slide_outlines
    from src.memory_client import ingest_session, DEFAULT_USER_ID

    try:
        state = get_session(session_id) or {}
        state["slides_task_status"] = "running"
        state["slides_task_progress"] = "outlines"
        save_session(session_id, state)

        task = task_manager.get_task(session_id, "slides")

        # Stage 1: outlines
        outlines = await asyncio.to_thread(generate_slide_outlines, state_snapshot)
        if not outlines:
            state = get_session(session_id) or {}
            state["slides_task_status"] = "error"
            save_session(session_id, state)
            return

        outlines_data = [o.model_dump() for o in outlines]
        total = len(outlines)

        state = get_session(session_id) or {}
        state["slide_outlines"] = outlines_data
        state["slide_contents"] = [None] * total
        state["slides_task_progress"] = f"0/{total}"
        save_session(session_id, state)

        # Stage 2: per-slide content
        for i, outline in enumerate(outlines):
            if task and task.cancelled:
                logger.info("Slides generation cancelled for %s", session_id)
                break

            slide = await asyncio.to_thread(generate_slide_content, outline)
            slide_data = slide.model_dump() if slide else None

            state = get_session(session_id) or {}
            contents = state.get("slide_contents") or [None] * total
            contents[i] = slide_data
            state["slide_contents"] = contents
            state["slides_task_progress"] = f"{i + 1}/{total}"
            save_session(session_id, state)

        # Finalize
        state = get_session(session_id) or {}
        state["slides_task_status"] = "done" if not (task and task.cancelled) else None
        state["slides_task_progress"] = None
        state["lesson_format"] = "slides"
        save_session(session_id, state)

        # Memory ingest
        if not (task and task.cancelled):
            asyncio.get_event_loop().run_in_executor(
                None,
                ingest_session,
                DEFAULT_USER_ID,
                f"{session_id}_slides",
                state_snapshot,
            )

    except Exception:
        logger.exception("Slides generation failed for %s", session_id)
        state = get_session(session_id) or {}
        state["slides_task_status"] = "error"
        save_session(session_id, state)
