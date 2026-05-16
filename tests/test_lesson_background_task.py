"""End-to-end test for run_lesson_generation parallel path."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _make_diagnosis_with_weak_points(names):
    diag = MagicMock()
    diag.breakdown = [
        MagicMock(name_val=n, status="weak") for n in names
    ]
    # MagicMock's `.name` attr is special; use a side-channel that the production
    # code reads via the getattr fallback `b.name`.
    for item, n in zip(diag.breakdown, names):
        item.name = n
        item.status = "weak"
    return diag


def _make_graph_with_nodes(names):
    g = MagicMock()
    nodes = []
    for n in names:
        node = MagicMock()
        node.name = n
        node.description = f"desc-{n}"
        nodes.append(node)
    g.nodes = nodes
    return g


def test_run_lesson_generation_end_to_end_uses_parallel_fanout(monkeypatch):
    """Drive run_lesson_generation against an in-memory session store.

    Asserts the terminal session state:
    - lesson_content joins sections in original KC order
    - lesson_task_status == "done"
    - lesson_task_progress is None at terminal state
    - lesson_task_progress was non-None at least once during execution
    """
    from src.api.tasks import run_lesson_generation, task_manager

    # In-memory session store
    store: dict[str, dict] = {}
    progress_observations: list = []

    def get_session(sid):
        return store.get(sid)

    def save_session(sid, st):
        # Snapshot the progress value at each write so we can prove it was non-None
        progress_observations.append(st.get("lesson_task_progress"))
        store[sid] = dict(st)

    names = ["alpha", "beta", "gamma"]
    state_snapshot = {
        "topic": "Pythagorean theorem",
        "knowledge_graph": _make_graph_with_nodes(names),
        "diagnosis_result": _make_diagnosis_with_weak_points(names),
        "active_skill": "feynman",
        "active_skill_content": {"lesson": "Explain with analogies"},
        "use_knowledge_skills": False,  # skip the first-round LLM calls
    }
    store["sess-1"] = dict(state_snapshot)

    # Mock the OpenAI client: serve three lesson sections.
    call_idx = {"i": 0}

    def _create(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = f"SECTION-{i}"
        return resp

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = _create

    # Disable memory write fan-out side effects.
    monkeypatch.setattr(
        "src.api.chat._dispatch_memory_writes",
        lambda *a, **kw: None,
        raising=False,
    )

    # Register a fake background task so task_manager.get_task() returns something.
    bt_key = "sess-1:lesson"
    from src.api.tasks import BackgroundTask
    task_manager._tasks[bt_key] = BackgroundTask(
        task_id=bt_key, session_id="sess-1", task_type="lesson"
    )

    try:
        with patch("src.agent.nodes.lesson.get_raw_client", return_value=mock_client):
            asyncio.run(
                run_lesson_generation(
                    session_id="sess-1",
                    state_snapshot=state_snapshot,
                    get_session=get_session,
                    save_session=save_session,
                )
            )
    finally:
        task_manager._tasks.pop(bt_key, None)

    final = store["sess-1"]
    assert final["lesson_task_status"] == "done"
    assert final["lesson_task_progress"] is None
    # Sections joined in original weak-point order:
    assert final["lesson_content"] == "SECTION-0\n\n---\n\nSECTION-1\n\n---\n\nSECTION-2"
    # Progress should have transitioned through a "k/3" string at least once.
    assert any(p and "/3" in p for p in progress_observations), progress_observations
