"""Unit tests for src.reviewer (PR-2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


# ----------------------------------------------------------------------
# Schema validation
# ----------------------------------------------------------------------
def test_fact_candidate_accepts_valid_categories() -> None:
    from src.llm.schemas import FactCandidate

    for cat in ("misconception", "preference", "style", "goal"):
        c = FactCandidate(category=cat, content="x")
        assert c.category == cat


def test_fact_candidate_rejects_invalid_category() -> None:
    from src.llm.schemas import FactCandidate

    with pytest.raises(ValidationError):
        FactCandidate(category="bogus", content="x")


def test_fact_candidate_enforces_content_length() -> None:
    from src.llm.schemas import FactCandidate

    with pytest.raises(ValidationError):
        FactCandidate(category="goal", content="")
    with pytest.raises(ValidationError):
        FactCandidate(category="goal", content="x" * 201)


def test_extract_result_caps_at_5_candidates() -> None:
    from src.llm.schemas import ExtractResult, FactCandidate

    cs = [FactCandidate(category="goal", content=str(i)) for i in range(5)]
    ExtractResult(candidates=cs)  # ok
    cs.append(FactCandidate(category="goal", content="6"))
    with pytest.raises(ValidationError):
        ExtractResult(candidates=cs)


def test_consolidation_decision_add_must_have_new_content_no_target() -> None:
    from src.llm.schemas import ConsolidationDecision

    # ADD: target_id None, new_content not None
    ConsolidationDecision(action="ADD", new_content="x", reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="ADD", target_id=1, new_content="x", reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="ADD", reason="r")  # missing new_content
    # new_content="" is treated like missing — Pydantic min_length=1 rejects
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="ADD", new_content="", reason="r")


def test_consolidation_decision_update_needs_target_and_new_content() -> None:
    from src.llm.schemas import ConsolidationDecision

    ConsolidationDecision(action="UPDATE", target_id=7, new_content="x", reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="UPDATE", new_content="x", reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="UPDATE", target_id=7, reason="r")
    # new_content="" is rejected by the field-level min_length=1 constraint
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="UPDATE", target_id=7, new_content="", reason="r")


def test_consolidation_decision_delete_needs_target_only() -> None:
    from src.llm.schemas import ConsolidationDecision

    ConsolidationDecision(action="DELETE", target_id=7, reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="DELETE", reason="r")


def test_consolidation_decision_noop_needs_target() -> None:
    from src.llm.schemas import ConsolidationDecision

    ConsolidationDecision(action="NOOP", target_id=7, reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="NOOP", reason="r")


def test_consolidation_decision_reject_no_target() -> None:
    from src.llm.schemas import ConsolidationDecision

    ConsolidationDecision(action="REJECT", reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="REJECT", target_id=7, reason="r")


def test_mastery_sample_score_range_and_source_literal() -> None:
    from src.llm.schemas import MasterySample

    MasterySample(kp="x", score=0.0, source="quiz")
    MasterySample(kp="x", score=1.0, source="review")
    MasterySample(kp="x", score=0.5, source="lesson_completion")
    with pytest.raises(ValidationError):
        MasterySample(kp="x", score=1.1, source="quiz")
    with pytest.raises(ValidationError):
        MasterySample(kp="x", score=0.5, source="bogus")


# ----------------------------------------------------------------------
# Prompt templates exist and render without errors
# ----------------------------------------------------------------------
def test_extract_system_prompt_renders() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt("memory_extract_system")
    assert "STUDENT" in out
    assert "TUTOR" in out
    assert "candidates" in out


def test_extract_user_prompt_renders_with_empty_inputs() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "memory_extract_user",
        source_event="submit-quiz",
        wrong_items=[],
        dialogue_tail=[],
        existing=[],
    )
    assert "submit-quiz" in out


def test_extract_user_prompt_renders_with_full_inputs() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "memory_extract_user",
        source_event="submit-review",
        wrong_items=[
            {
                "question_text": "x^2-4=0 的根?",
                "options": ["1", "2", "3", "4"],
                "correct_label": "B",
                "student_label": "A",
                "kp": "求根公式",
                "last_accuracy": 0.4,
            }
        ],
        dialogue_tail=[
            {"role": "user", "content": "我不会做"},
            {"role": "assistant", "content": "我们来分步看"},
        ],
        existing=[
            {"category": "misconception", "content": "把判别式当求根公式用"},
        ],
    )
    assert "x^2-4=0" in out
    assert "[USER]" in out or "[STUDENT]" in out  # role rendering
    assert "把判别式当求根公式用" in out


def test_consolidate_system_prompt_renders() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt("memory_consolidate_system")
    for action in ("ADD", "UPDATE", "DELETE", "NOOP", "REJECT"):
        assert action in out


def test_consolidate_user_prompt_with_neighbors() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "memory_consolidate_user",
        candidate={"category": "misconception", "content": "新候选"},
        neighbors=[
            {"id": 1, "content": "旧条目 1", "sim_score": 0.91, "age_days": 3.0},
            {"id": 2, "content": "旧条目 2", "sim_score": 0.85, "age_days": 7.5},
        ],
    )
    assert "新候选" in out
    assert "旧条目 1" in out
    assert "id=1" in out
    assert "0.91" in out


def test_consolidate_user_prompt_empty_neighbors() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "memory_consolidate_user",
        candidate={"category": "goal", "content": "学完二次方程"},
        neighbors=[],
    )
    assert "学完二次方程" in out
    assert "邻居：无" in out
