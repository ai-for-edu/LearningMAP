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

    state = {"diagnosis_result": _diag([("formula", "mastered"), ("distance", "weak")])}
    samples = _extract_kp_samples(state)
    by_kp = {s.kp: s for s in samples}
    assert by_kp["formula"].score == 1.0
    assert by_kp["formula"].source == "quiz"
    assert by_kp["distance"].score == 0.2
    assert by_kp["distance"].source == "quiz"


def test_extract_kp_samples_combines_diagnosis_and_review() -> None:
    from src.progress_aggregator import _extract_kp_samples

    # Inject a kp_results-shaped object on the side (per spec §7),
    # using SimpleNamespace since we don't extend ReviewResult here.
    from types import SimpleNamespace
    rr2 = SimpleNamespace(kp_results=[
        SimpleNamespace(kp="formula", accuracy=0.9),
        SimpleNamespace(kp="distance", accuracy=0.3),
    ])
    state = {
        "diagnosis_result": _diag([("formula", "weak")]),
        "review_result": rr2,
    }
    samples = _extract_kp_samples(state)
    sources = {(s.kp, s.source) for s in samples}
    # one quiz sample for formula, one review sample for formula + distance
    assert ("formula", "quiz") in sources
    assert ("formula", "review") in sources
    assert ("distance", "review") in sources


def test_extract_kp_samples_review_fallback_uses_weak_strong_kps() -> None:
    """When review_result has no kp_results field, fall back to deriving
    from weak_kps (score=0.2) and strong_kps (score=1.0)."""
    from src.progress_aggregator import _extract_kp_samples

    rr = ReviewResult(
        weak_kps=["distance"], strong_kps=["formula"],
        score=0.6, focus_directive="...",
    )
    state = {"review_result": rr}
    samples = _extract_kp_samples(state)
    by_kp_source = {(s.kp, s.source): s for s in samples}
    assert ("formula", "review") in by_kp_source
    assert by_kp_source[("formula", "review")].score == 1.0
    assert ("distance", "review") in by_kp_source
    assert by_kp_source[("distance", "review")].score == 0.2


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
            "breakdown": [{"name": "formula", "status": "weak", "detail": "x"}],
        },
    }
    samples = _extract_kp_samples(state)
    assert len(samples) == 1
    assert samples[0].kp == "formula"
    assert samples[0].score == 0.2


# ----------------------------------------------------------------------
# _iter_kp_results (pure, precedence)
# ----------------------------------------------------------------------
def test_iter_kp_results_prefers_kp_results_over_weak_strong() -> None:
    """When both kp_results and weak_kps/strong_kps are present, kp_results wins.

    Pins the precedence contract so a future refactor that flips the order
    (e.g., merging both sources) is caught by the test suite.
    """
    from types import SimpleNamespace

    from src.progress_aggregator import _iter_kp_results

    review = SimpleNamespace(
        kp_results=[SimpleNamespace(kp="A", accuracy=0.9)],
        weak_kps=["B"],
        strong_kps=["C"],
    )
    results = _iter_kp_results(review)
    # Only A from kp_results — B and C must NOT appear in the fallback shape.
    assert results == [("A", 0.9)]


# ----------------------------------------------------------------------
# refresh_progress entry point
# ----------------------------------------------------------------------
def test_refresh_progress_calls_upsert_with_aggregate_score() -> None:
    """mastery_overall = arithmetic mean of kp scores."""
    from unittest.mock import patch

    from src import progress_aggregator

    state = {
        "topic": "Pythagorean theorem",
        "diagnosis_result": _diag([("formula", "mastered"), ("distance", "weak")]),
    }
    with patch("src.progress_aggregator.memory_client") as mc, \
         patch.dict("os.environ", {"ENABLE_MEMORY_REVIEW": "true"}):
        progress_aggregator.refresh_progress("u1", "s1", state)
    mc.upsert_progress.assert_called_once()
    kwargs = mc.upsert_progress.call_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["topic"] == "Pythagorean theorem"
    # (1.0 + 0.2) / 2 = 0.6
    assert abs(kwargs["mastery_overall"] - 0.6) < 1e-9
    assert kwargs["last_session_id"] == "s1"
    assert len(kwargs["kp_samples"]) == 2
    assert kwargs["correlation_id"]  # non-empty


def test_refresh_progress_kp_samples_are_dicts_not_pydantic() -> None:
    """memory_client.upsert_progress expects plain dicts on the wire."""
    from unittest.mock import patch

    from src import progress_aggregator

    state = {
        "topic": "x",
        "diagnosis_result": _diag([("kp1", "weak")]),
    }
    with patch("src.progress_aggregator.memory_client") as mc, \
         patch.dict("os.environ", {"ENABLE_MEMORY_REVIEW": "true"}):
        progress_aggregator.refresh_progress("u1", "s1", state)
    samples = mc.upsert_progress.call_args.kwargs["kp_samples"]
    assert isinstance(samples, list)
    assert isinstance(samples[0], dict)
    assert samples[0]["kp"] == "kp1"
    assert samples[0]["score"] == 0.2
    assert samples[0]["source"] == "quiz"


def test_refresh_progress_noop_when_env_disabled() -> None:
    from unittest.mock import patch

    from src import progress_aggregator

    state = {
        "topic": "x",
        "diagnosis_result": _diag([("a", "weak")]),
    }
    with patch("src.progress_aggregator.memory_client") as mc, \
         patch.dict("os.environ", {"ENABLE_MEMORY_REVIEW": "false"}):
        progress_aggregator.refresh_progress("u1", "s1", state)
    mc.upsert_progress.assert_not_called()
    mc.log_error.assert_not_called()


def test_refresh_progress_noop_when_no_topic() -> None:
    from unittest.mock import patch

    from src import progress_aggregator

    with patch("src.progress_aggregator.memory_client") as mc, \
         patch.dict("os.environ", {"ENABLE_MEMORY_REVIEW": "true"}):
        progress_aggregator.refresh_progress("u1", "s1", {})
    mc.upsert_progress.assert_not_called()
    mc.log_error.assert_not_called()


def test_refresh_progress_noop_when_no_kp_samples() -> None:
    from unittest.mock import patch

    from src import progress_aggregator

    state = {"topic": "x"}  # no diagnosis_result, no review_result
    with patch("src.progress_aggregator.memory_client") as mc, \
         patch.dict("os.environ", {"ENABLE_MEMORY_REVIEW": "true"}):
        progress_aggregator.refresh_progress("u1", "s1", state)
    mc.upsert_progress.assert_not_called()
    mc.log_error.assert_not_called()


def test_refresh_progress_swallows_exceptions_and_logs_op_error() -> None:
    """Top-level try/except: any failure inside must NOT propagate."""
    from unittest.mock import patch

    from src import progress_aggregator

    state = {
        "topic": "x",
        "diagnosis_result": _diag([("a", "weak")]),
    }
    with patch("src.progress_aggregator.memory_client") as mc, \
         patch.dict("os.environ", {"ENABLE_MEMORY_REVIEW": "true"}):
        mc.upsert_progress.side_effect = RuntimeError("boom")
        # Must not raise
        progress_aggregator.refresh_progress("u1", "s1", state)
    # And must record an op_error via log_error
    mc.log_error.assert_called_once()
    kwargs = mc.log_error.call_args.kwargs
    assert kwargs["op"] == "refresh_progress"
    assert kwargs["error_class"] == "RuntimeError"
    assert kwargs["correlation_id"]
