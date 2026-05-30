"""End-to-end smoke test for run_quiz_generation with parallel fan-out."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch


def _make_kg_state(num_nodes: int) -> dict:
    """Build a minimal state dict that run_quiz_generation can consume.

    NOTE: KnowledgeNode requires id + level (see src/llm/schemas.py:20-25).
    """
    from src.llm.schemas import KnowledgeNode
    nodes = [
        KnowledgeNode(
            id=f"kc-{i}",
            name=f"kc-{i}",
            description=f"desc-{i}",
            level="core",
        )
        for i in range(num_nodes)
    ]
    graph = MagicMock()
    graph.nodes = nodes
    return {"knowledge_graph": graph, "kc_skills": {}}


def _make_question(kc_name: str, text: str):
    """NOTE: QuizQuestion has fields {id, knowledge_point, question, options,
    answer: int}. There is no `correct_answer` or `explanation` field
    (see src/llm/schemas.py:40-45).
    """
    from src.llm.schemas import QuizQuestion
    return QuizQuestion(
        id=f"q-{text}",
        knowledge_point=kc_name,
        question=text,
        options=["A", "B", "C", "D"],
        answer=0,
    )


def test_run_quiz_generation_final_state_is_kc_ordered_even_when_kc0_slowest():
    """run_quiz_generation must persist state['questions'] in KC declaration order.

    KC-0 completes last; intermediate writes will show completion-order
    questions (which is fine for polling latency), but the FINAL state
    must be re-sorted to KC order.
    """
    from src.api.tasks import run_quiz_generation

    delays = [0.03, 0.0, 0.0]  # KC-0 slowest, KC-1 + KC-2 instant

    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        # Use the call index as the question text so we can tell which
        # call produced which question.
        return [_make_question(f"will-be-pinned-to-kc-{i}", f"Q-from-call-{i}")]

    session_id = "test-quiz-bg-1"
    state_snapshot = _make_kg_state(3)

    # In-memory session store.
    store: dict[str, dict] = {session_id: dict(state_snapshot)}
    writes: list[dict] = []

    def _get_session(sid):
        return dict(store.get(sid, {}))

    def _save_session(sid, s):
        store[sid] = dict(s)
        writes.append(dict(s))

    # run_quiz_generation handles task_manager.get_task() returning None
    # via `if task and task.cancelled:` — no registration needed.

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = _create

    with patch("src.agent.nodes.quiz.get_llm_client", return_value=mock_client):
        asyncio.run(
            run_quiz_generation(
                session_id=session_id,
                state_snapshot=state_snapshot,
                get_session=_get_session,
                save_session=_save_session,
            )
        )

    final = store[session_id]
    assert final["quiz_task_status"] == "done"
    assert final["quiz_task_progress"] is None
    assert final["mode"] == "quiz"
    assert final["current_question_index"] == 0
    assert final["answers"] == []

    # Crucial: final questions are in KC declaration order. The async core
    # pins q.knowledge_point to the KG node name (kc-0, kc-1, kc-2), so we
    # assert on knowledge_point, not on question text.
    assert [q["knowledge_point"] for q in final["questions"]] == [
        "kc-0", "kc-1", "kc-2",
    ]

    # Progress was non-None at least once during the run.
    intermediate_progress = [w.get("quiz_task_progress") for w in writes]
    assert any(p is not None for p in intermediate_progress)


def test_run_quiz_generation_cancel_writes_clean_terminal_state():
    """On task_manager.cancel(), persisted state must reach a clean terminal:
    quiz_task_status=None, quiz_task_progress=None, questions sorted by KC.
    CancelledError must propagate to the awaiter."""
    from src.api.tasks import run_quiz_generation, task_manager
    import pytest

    delays = [0.3, 0.3, 0.3, 0.3]  # all slow so cancel lands mid-flight

    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        time.sleep(delays[i])
        return [_make_question(f"raw-kc-{i}", f"Q-{i}")]

    session_id = "test-quiz-cancel-1"
    state_snapshot = _make_kg_state(4)

    store: dict[str, dict] = {session_id: dict(state_snapshot)}

    def _get_session(sid):
        return dict(store.get(sid, {}))

    def _save_session(sid, s):
        store[sid] = dict(s)

    # Register the task in task_manager so the cancel flag flips.
    async def _runner_with_cancel():
        # Start the generation as a real asyncio task.
        gen_task = asyncio.create_task(
            run_quiz_generation(
                session_id=session_id,
                state_snapshot=state_snapshot,
                get_session=_get_session,
                save_session=_save_session,
            )
        )
        # Register with task_manager so the cancelled-flag check sees it.
        from src.api.tasks import BackgroundTask
        bt = BackgroundTask(
            task_id=f"{session_id}:quiz",
            session_id=session_id,
            task_type="quiz",
        )
        bt.asyncio_task = gen_task
        task_manager._tasks[f"{session_id}:quiz"] = bt

        await asyncio.sleep(0.05)  # let the fan-out start
        task_manager.cancel(session_id, "quiz")
        with pytest.raises(asyncio.CancelledError):
            await gen_task

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = _create

    with patch("src.agent.nodes.quiz.get_llm_client", return_value=mock_client):
        asyncio.run(_runner_with_cancel())

    final = store[session_id]
    # Terminal status must be None (matches the happy-path cancel ternary).
    assert final.get("quiz_task_status") is None
    assert final.get("quiz_task_progress") is None
    # Questions (whatever made it through) must be in KC order — assert
    # knowledge_points list is monotonically increasing in kc index.
    qs = final.get("questions") or []
    if qs:
        kc_ids = [q["knowledge_point"] for q in qs]
        # kc_ids should be a prefix of ["kc-0", "kc-1", "kc-2", "kc-3"] OR
        # if cancel landed before any completed, kc_ids is []. Either way,
        # whatever's there must be in strictly increasing order by index.
        indices = [int(k.split("-")[1]) for k in kc_ids]
        assert indices == sorted(indices)
