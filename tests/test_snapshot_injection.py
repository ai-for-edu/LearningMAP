"""PR-4 snapshot injection tests.

Covers:
- memory_snapshot_block helper (this task)
- per-template snapshot injection (Tasks 2-4)
- chat node snapshot + retrieve_memory threshold (Task 5)
- review path misconception injection (Task 6)
"""
from __future__ import annotations

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
