"""PR-4 snapshot injection tests.

Covers:
- memory_snapshot_block helper (this task)
- per-template snapshot injection (Tasks 2-4)
- chat node snapshot + retrieve_memory threshold (Task 5)
- review path misconception injection (Task 6)
"""
from __future__ import annotations

from unittest.mock import patch

from langchain_core.messages import HumanMessage

from src.agent.nodes._helpers import memory_snapshot_block


SAMPLE_SNAPSHOT = "## 该学生的学习进程\n- 已开始：勾股定理（3天前）"


# ---------------------------------------------------------------------------
# memory_snapshot_block helper
# ---------------------------------------------------------------------------


def test_helper_returns_empty_for_missing_field():
    """state without memory_snapshot key → empty string."""
    assert memory_snapshot_block({}) == ""


def test_helper_returns_empty_for_none_state():
    """state=None → empty string (defensive for callers passing None)."""
    assert memory_snapshot_block(None) == ""


def test_helper_returns_empty_for_none_value():
    """memory_snapshot=None → empty string (env=off path)."""
    assert memory_snapshot_block({"memory_snapshot": None}) == ""


def test_helper_returns_empty_for_empty_string():
    """memory_snapshot='' → empty string (fetch failure / empty payload)."""
    assert memory_snapshot_block({"memory_snapshot": ""}) == ""


def test_helper_returns_empty_for_whitespace():
    """memory_snapshot='  \\n  ' → empty string after strip."""
    assert memory_snapshot_block({"memory_snapshot": "  \n  "}) == ""


def test_helper_returns_block_with_leading_separator():
    """Non-empty snapshot returns with \\n\\n prefix for direct append."""
    out = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    assert out == f"\n\n{SAMPLE_SNAPSHOT}"


def test_helper_strips_leading_trailing_whitespace_in_content():
    """Content with surrounding whitespace is stripped before block render."""
    out = memory_snapshot_block({"memory_snapshot": "\n\n  ## X  \n\n"})
    assert out == "\n\n## X"


# ---------------------------------------------------------------------------
# Per-template snapshot injection
# ---------------------------------------------------------------------------


def test_knowledge_graph_template_injects_snapshot():
    """When memory_snapshot_block is non-empty, it appears in rendered output."""
    from src.prompts.loader import render_prompt

    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "knowledge_graph",
        topic="X",
        max_nodes=8,
        skill_instructions="",
        focus_directive="",
        review_weak_kps=[],
        review_strong_kps=[],
        existing_mastery=[],
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out
    assert "## 该学生的学习进程" in out


def test_quiz_generate_template_injects_snapshot():
    from src.prompts.loader import render_prompt

    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "quiz_generate",
        skill_instructions="",
        kc_skill_block="",
        knowledge_points=[{"name": "KP1", "description": "desc1"}],
        questions_per_point=2,
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out


def test_lesson_plan_template_injects_snapshot():
    from src.prompts.loader import render_prompt
    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "lesson_plan",
        skill_instructions="",
        focus_directive="",
        topic="X",
        weak_points=[{"name": "WP1", "description": "d1"}],
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out


def test_lesson_point_template_injects_snapshot():
    from src.prompts.loader import render_prompt
    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="KP1",
        knowledge_point_description="d",
        pedagogy_instructions="",
        knowledge_skill="",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="",
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out


def test_lesson_review_template_injects_snapshot():
    from src.prompts.loader import render_prompt
    block = memory_snapshot_block({"memory_snapshot": SAMPLE_SNAPSHOT})
    out = render_prompt(
        "lesson_review",
        topic="X",
        points=[{"name": "P1", "description": "d"}],
        skill_instructions="",
        memory_snapshot_block=block,
    )
    assert SAMPLE_SNAPSHOT in out


# ---------------------------------------------------------------------------
# chat node — snapshot block + retrieve_memory threshold
# ---------------------------------------------------------------------------


def _capture_chat_system_prompt(state, *, retrieve_return=""):
    """Run chat_node with all LLM/IO mocked; return the captured system prompt."""
    from src.agent.nodes import chat as chat_mod

    captured = {}

    class _FakeChoice:
        message = type("M", (), {"content": "ok"})()

    class _FakeResp:
        choices = [_FakeChoice()]

    def _fake_create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _FakeResp()

    with patch.object(chat_mod, "retrieve_memory", return_value=retrieve_return) as mock_retrieve, \
         patch.object(chat_mod, "get_raw_client") as mock_client:
        mock_client.return_value.chat.completions.create.side_effect = _fake_create
        chat_mod.chat_node(state)

    return captured["messages"][0]["content"], mock_retrieve


def test_chat_node_appends_snapshot_when_present():
    """state has memory_snapshot → system prompt contains snapshot text."""
    state = {
        "messages": [HumanMessage(content="解释勾股定理是什么意思")],  # ≥10 chars
        "memory_snapshot": SAMPLE_SNAPSHOT,
        "memory_snapshot_loaded_at": 1000,
    }
    sys_prompt, _ = _capture_chat_system_prompt(state)
    assert SAMPLE_SNAPSHOT in sys_prompt


def test_chat_node_no_snapshot_when_field_is_none():
    """state has memory_snapshot=None → system prompt does NOT contain snapshot."""
    state = {
        "messages": [HumanMessage(content="解释勾股定理是什么意思")],
        "memory_snapshot": None,
    }
    sys_prompt, _ = _capture_chat_system_prompt(state)
    assert "## 该学生" not in sys_prompt


def test_chat_node_skips_retrieve_for_short_message():
    """User msg <10 chars → retrieve_memory NOT called."""
    state = {
        "messages": [HumanMessage(content="嗯")],   # 1 char
        "memory_snapshot": None,
    }
    _, mock_retrieve = _capture_chat_system_prompt(state)
    mock_retrieve.assert_not_called()


def test_chat_node_calls_retrieve_for_long_message():
    """User msg ≥10 chars → retrieve_memory called once."""
    state = {
        "messages": [HumanMessage(content="解释一下勾股定理的证明")],  # ≥10 chars
        "memory_snapshot": None,
    }
    _, mock_retrieve = _capture_chat_system_prompt(state)
    mock_retrieve.assert_called_once()


def _capture_chat_stream_system_prompt(state, *, retrieve_return=""):
    """Run chat_node_stream (a generator); return the captured system prompt.

    Mocks the streaming LLM client; consumes one yielded token to ensure
    the request was made.
    """
    from src.agent.nodes import chat as chat_mod

    captured = {}

    class _FakeDelta:
        content = "ok"

    class _FakeChunkChoice:
        delta = _FakeDelta()

    class _FakeChunk:
        choices = [_FakeChunkChoice()]

    def _fake_stream_create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return iter([_FakeChunk()])

    with patch.object(chat_mod, "retrieve_memory", return_value=retrieve_return) as mock_retrieve, \
         patch.object(chat_mod, "get_raw_client") as mock_client:
        mock_client.return_value.chat.completions.create.side_effect = _fake_stream_create
        # Drive the generator to make the LLM call
        list(chat_mod.chat_node_stream(state))

    return captured["messages"][0]["content"], mock_retrieve


def test_chat_node_stream_appends_snapshot_when_present():
    """chat_node_stream: state has memory_snapshot → system prompt contains snapshot."""
    state = {
        "messages": [HumanMessage(content="解释勾股定理是什么意思")],  # ≥10 chars
        "memory_snapshot": SAMPLE_SNAPSHOT,
        "memory_snapshot_loaded_at": 1000,
    }
    sys_prompt, _ = _capture_chat_stream_system_prompt(state)
    assert SAMPLE_SNAPSHOT in sys_prompt


def test_chat_node_stream_skips_retrieve_for_short_message():
    """chat_node_stream: user msg <10 chars → retrieve_memory NOT called."""
    state = {
        "messages": [HumanMessage(content="嗯")],  # 1 char
        "memory_snapshot": None,
    }
    _, mock_retrieve = _capture_chat_stream_system_prompt(state)
    mock_retrieve.assert_not_called()


# ---------------------------------------------------------------------------
# review path — fetch_active_notes('misconception') + soft-guidance template
# ---------------------------------------------------------------------------


def _make_review_module_with_env(monkeypatch, enabled: bool):
    """Reload review module with monkeypatched env constant.

    `_MEMORY_REVIEW_ENABLED` is computed at import time, so we set both
    the env var (for completeness) and force the module-level constant.
    """
    from src.agent.nodes import review as review_mod
    monkeypatch.setenv("ENABLE_MEMORY_REVIEW", "true" if enabled else "false")
    monkeypatch.setattr(review_mod, "_MEMORY_REVIEW_ENABLED", enabled)
    return review_mod


def test_review_injects_misconceptions_env_on(monkeypatch):
    """env=on: fetch_active_notes called once, misconceptions appear in prompt."""
    review_mod = _make_review_module_with_env(monkeypatch, enabled=True)

    fake_notes = [
        {"content": "把两点距离误用为坐标差不平方"},
        {"content": "勾股定理只在直角三角形成立但学生在锐角也用"},
    ]
    candidates = [
        {"subtopic": "勾股定理", "last_seen_at": "2026-04-01T00:00:00Z",
         "last_accuracy": 0.5, "topic": "几何"}
    ]

    captured = {}

    def _fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return []

    with patch("src.memory_client.fetch_active_notes", return_value=fake_notes) as mock_fetch, \
         patch("src.llm.client.get_llm_client") as mock_llm:
        mock_llm.return_value.chat.completions.create.side_effect = _fake_create
        review_mod.generate_review_questions(candidates)

    mock_fetch.assert_called_once_with(
        "default_user", "misconception"
    )
    assert "把两点距离误用为坐标差不平方" in captured["prompt"]
    assert "易混点" in captured["prompt"]


def test_review_does_not_fetch_env_off(monkeypatch):
    """env=off: fetch_active_notes never called, misconceptions absent from prompt."""
    review_mod = _make_review_module_with_env(monkeypatch, enabled=False)

    candidates = [
        {"subtopic": "X", "last_seen_at": "2026-04-01T00:00:00Z",
         "last_accuracy": 0.5, "topic": "T"}
    ]

    captured = {}
    def _fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return []

    with patch("src.memory_client.fetch_active_notes") as mock_fetch, \
         patch("src.llm.client.get_llm_client") as mock_llm:
        mock_llm.return_value.chat.completions.create.side_effect = _fake_create
        review_mod.generate_review_questions(candidates)

    mock_fetch.assert_not_called()
    assert "易混点" not in captured["prompt"]


def test_review_filters_empty_content_misconceptions(monkeypatch):
    """env=on, fetch returns notes with empty/missing content → those filtered out.

    Spec §6.3: 'env=on, 有 notes 但 content 空字符串 → 过滤掉 (if n.get("content")), 不进 prompt'.
    """
    review_mod = _make_review_module_with_env(monkeypatch, enabled=True)

    fake_notes = [
        {"content": "valid misconception"},
        {"content": ""},                  # filtered (empty string)
        {"content": None},                # filtered (None)
        {},                               # filtered (missing key)
        {"content": "another valid one"},
    ]
    candidates = [
        {"subtopic": "X", "last_seen_at": "2026-04-01T00:00:00Z",
         "last_accuracy": 0.5, "topic": "T"}
    ]

    captured = {}

    def _fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return []

    with patch("src.memory_client.fetch_active_notes", return_value=fake_notes), \
         patch("src.llm.client.get_llm_client") as mock_llm:
        mock_llm.return_value.chat.completions.create.side_effect = _fake_create
        review_mod.generate_review_questions(candidates)

    # Two valid items appear, three falsy items don't
    assert "valid misconception" in captured["prompt"]
    assert "another valid one" in captured["prompt"]
    # The empty/None/missing-content items should NOT appear as bullet lines
    # (they would render as bare "- " with no text — assert there's no such empty bullet)
    assert "\n- \n" not in captured["prompt"]
