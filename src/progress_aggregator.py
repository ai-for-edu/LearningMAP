"""Progress aggregator — pure structured derivation from session state.

Spec §7 (`docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`).

Public entry point ``refresh_progress`` is added in the next task.
This module is purely structural — no LLM calls — so any LLM-related
import would be a smell. Keep it that way.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm.schemas import MasterySample

logger = logging.getLogger(__name__)


# Mastery status → score mapping. Mirrors the diagnosis status values
# emitted by the grading node:
#   mastered = 1.0, partial = 0.6, weak = 0.2; anything else (e.g.
#   'skipped' for an unattempted KP) → 0.
_STATUS_SCORE: dict[str, float] = {
    "mastered": 1.0,
    "partial": 0.6,
    "weak": 0.2,
}


def _status_to_score(status: str) -> float:
    return _STATUS_SCORE.get(status, 0.0)


def _iter_breakdown(diag: Any) -> list[tuple[str, str]]:
    """Yield (name, status) tuples whether ``diag`` is a Pydantic
    DiagnosisResult or a plain dict (post-JSON state reconstruction).
    """
    if diag is None:
        return []
    bd = diag.breakdown if hasattr(diag, "breakdown") else diag.get("breakdown", [])
    out: list[tuple[str, str]] = []
    for b in bd:
        name = b.name if hasattr(b, "name") else b.get("name", "")
        status = b.status if hasattr(b, "status") else b.get("status", "")
        if name and status:
            out.append((name, status))
    return out


def _iter_kp_results(review: Any) -> list[tuple[str, float]]:
    """Yield (kp, accuracy) tuples; tolerates dict / SimpleNamespace shapes.

    The shape ``kp_results: [{kp, accuracy}]`` is what the spec assumes
    review_result will eventually carry. Today's ReviewResult only has
    weak_kps / strong_kps, so this falls back to deriving samples from
    those: weak ⇒ 0.2, strong ⇒ 1.0.
    """
    if review is None:
        return []
    out: list[tuple[str, float]] = []
    kp_results = getattr(review, "kp_results", None)
    if kp_results is None and isinstance(review, dict):
        kp_results = review.get("kp_results")
    if kp_results:
        for r in kp_results:
            kp = r.kp if hasattr(r, "kp") else r.get("kp", "")
            acc = r.accuracy if hasattr(r, "accuracy") else r.get("accuracy", 0.0)
            if kp:
                out.append((kp, float(acc)))
        return out
    # Fallback: derive from weak_kps / strong_kps shape used today
    weak = getattr(review, "weak_kps", None)
    strong = getattr(review, "strong_kps", None)
    if weak is None and isinstance(review, dict):
        weak = review.get("weak_kps") or []
        strong = review.get("strong_kps") or []
    for kp in weak or []:
        out.append((kp, 0.2))
    for kp in strong or []:
        out.append((kp, 1.0))
    return out


def _extract_kp_samples(state: dict[str, Any]) -> list[MasterySample]:
    """Build MasterySample list from session state. Pure, no I/O."""
    samples: list[MasterySample] = []

    for kp, status in _iter_breakdown(state.get("diagnosis_result")):
        samples.append(
            MasterySample(kp=kp, score=_status_to_score(status), source="quiz")
        )

    for kp, acc in _iter_kp_results(state.get("review_result")):
        samples.append(MasterySample(kp=kp, score=acc, source="review"))

    return samples
