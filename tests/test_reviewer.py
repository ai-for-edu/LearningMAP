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


def test_consolidation_decision_update_needs_target_and_new_content() -> None:
    from src.llm.schemas import ConsolidationDecision

    ConsolidationDecision(action="UPDATE", target_id=7, new_content="x", reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="UPDATE", new_content="x", reason="r")
    with pytest.raises(ValidationError):
        ConsolidationDecision(action="UPDATE", target_id=7, reason="r")


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
