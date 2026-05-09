"""Unit tests for src.progress_aggregator (PR-2)."""

from __future__ import annotations

from src.llm.schemas import DiagnosisBreakdown, DiagnosisResult, ReviewResult


# ----------------------------------------------------------------------
# _status_to_score (pure)
# ----------------------------------------------------------------------
def test_status_to_score_known_values() -> None:
    from src.progress_aggregator import _status_to_score

    assert _status_to_score("mastered") == 1.0
    assert _status_to_score("partial") == 0.6
    assert _status_to_score("weak") == 0.2


def test_status_to_score_unknown_falls_back_to_zero() -> None:
    from src.progress_aggregator import _status_to_score

    # Unknown status (e.g. 'skipped') becomes 0.0 — conservative
    assert _status_to_score("skipped") == 0.0
    assert _status_to_score("") == 0.0


# ----------------------------------------------------------------------
# _extract_kp_samples (pure, exhaustive)
# ----------------------------------------------------------------------
def _diag(breakdown_pairs: list[tuple[str, str]]) -> DiagnosisResult:
    return DiagnosisResult(
        score=0, total=10, correct=5, incorrect=5,
        breakdown=[
            DiagnosisBreakdown(name=name, status=status, detail="x")
            for name, status in breakdown_pairs
        ],
    )


def test_extract_kp_samples_from_diagnosis_only() -> None:
    from src.progress_aggregator import _extract_kp_samples

    state = {"diagnosis_result": _diag([("公式", "mastered"), ("距离", "weak")])}
    samples = _extract_kp_samples(state)
    by_kp = {s.kp: s for s in samples}
    assert by_kp["公式"].score == 1.0
    assert by_kp["公式"].source == "quiz"
    assert by_kp["距离"].score == 0.2
    assert by_kp["距离"].source == "quiz"


def test_extract_kp_samples_combines_diagnosis_and_review() -> None:
    from src.progress_aggregator import _extract_kp_samples

    # Inject a kp_results-shaped object on the side (per spec §7),
    # using SimpleNamespace since we don't extend ReviewResult here.
    from types import SimpleNamespace
    rr2 = SimpleNamespace(kp_results=[
        SimpleNamespace(kp="公式", accuracy=0.9),
        SimpleNamespace(kp="距离", accuracy=0.3),
    ])
    state = {
        "diagnosis_result": _diag([("公式", "weak")]),
        "review_result": rr2,
    }
    samples = _extract_kp_samples(state)
    sources = {(s.kp, s.source) for s in samples}
    # one quiz sample for 公式, one review sample for 公式 + 距离
    assert ("公式", "quiz") in sources
    assert ("公式", "review") in sources
    assert ("距离", "review") in sources


def test_extract_kp_samples_review_fallback_uses_weak_strong_kps() -> None:
    """When review_result has no kp_results field, fall back to deriving
    from weak_kps (score=0.2) and strong_kps (score=1.0)."""
    from src.progress_aggregator import _extract_kp_samples

    rr = ReviewResult(
        weak_kps=["距离"], strong_kps=["公式"],
        score=0.6, focus_directive="...",
    )
    state = {"review_result": rr}
    samples = _extract_kp_samples(state)
    by_kp_source = {(s.kp, s.source): s for s in samples}
    assert ("公式", "review") in by_kp_source
    assert by_kp_source[("公式", "review")].score == 1.0
    assert ("距离", "review") in by_kp_source
    assert by_kp_source[("距离", "review")].score == 0.2


def test_extract_kp_samples_empty_state_returns_empty() -> None:
    from src.progress_aggregator import _extract_kp_samples

    assert _extract_kp_samples({}) == []
    assert _extract_kp_samples({"topic": "x"}) == []


def test_extract_kp_samples_handles_non_pydantic_dicts() -> None:
    """state may be reconstructed from JSON; breakdown could be plain dicts."""
    from src.progress_aggregator import _extract_kp_samples

    state = {
        "diagnosis_result": {
            "score": 0, "total": 1, "correct": 0, "incorrect": 1,
            "breakdown": [{"name": "公式", "status": "weak", "detail": "x"}],
        },
    }
    samples = _extract_kp_samples(state)
    assert len(samples) == 1
    assert samples[0].kp == "公式"
    assert samples[0].score == 0.2
