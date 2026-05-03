"""Review-flow agent helpers (PR-2).

Pure ranking + template assembly + LLM-bearing question generation +
deterministic grading. Exposed via the API layer (src.api.chat),
not via the LangGraph state machine — review is a session-side-channel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.llm.client import get_llm_client, get_reviewer_model_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _parse_iso_z(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _staleness(last_seen_at: str, *, now: datetime | None = None) -> float:
    """0 (just seen) → 1 (>= 14 days old). Clamped both ends."""
    now = now or datetime.now(timezone.utc)
    age_days = (now - _parse_iso_z(last_seen_at)).total_seconds() / 86400.0
    return max(0.0, min(age_days / 14.0, 1.0))


def _uncertainty(last_accuracy: float | None) -> float:
    """1 at acc=0.5; 0 at acc=0 or 1."""
    if last_accuracy is None:
        return 0.5
    return 1.0 - abs(last_accuracy - 0.5) * 2


def select_candidate_kps(
    items: list[dict],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Pick 3-5 KPs to review based on staleness + uncertainty.

    Excludes items seen within 1 day (just-learned, no value to retest).
    Sorts by priority = 0.6 * staleness + 0.4 * uncertainty descending.
    Cap: <=5 items → all; 6-10 → 4; >10 → 5.
    Tie-break is stable by input order, so output is deterministic.
    """
    now = now or datetime.now(timezone.utc)
    one_day = 86400.0

    eligible = []
    for item in items:
        try:
            seen = _parse_iso_z(item["last_seen_at"])
        except (KeyError, ValueError):
            continue
        if (now - seen).total_seconds() < one_day:
            continue
        eligible.append(item)

    scored = [
        (
            0.6 * _staleness(item["last_seen_at"], now=now)
            + 0.4 * _uncertainty(item.get("last_accuracy")),
            idx,
            item,
        )
        for idx, item in enumerate(eligible)
    ]
    # Sort by (-priority, idx) so equal priorities preserve input order.
    scored.sort(key=lambda t: (-t[0], t[1]))

    n = len(scored)
    if n <= 5:
        cap = n
    elif n <= 10:
        cap = 4
    else:
        cap = 5
    return [item for _, _, item in scored[:cap]]


# ---------------------------------------------------------------------------
# Directive rendering + grading
# ---------------------------------------------------------------------------

def _render_directive(*, weak_kps: list[str], strong_kps: list[str]) -> str:
    """Render the focus directive that downstream prompts will see."""
    if not weak_kps and not strong_kps:
        return ""
    parts = []
    if weak_kps:
        parts.append(
            f"学生在 {'、'.join(weak_kps)} 上仍薄弱，本次教案应优先衔接这些点。"
        )
    if strong_kps:
        parts.append(
            f"{'、'.join(strong_kps)} 已掌握，可以略过基础部分。"
        )
    return " ".join(parts)


def grade_review(
    questions: list,
    answers: list[int],
) -> "ReviewResult":  # forward ref, imported lazily  # noqa: F821
    """Pure-Python grading. Unanswered questions count as wrong.

    Note: When the same KP appears in multiple questions, the **first**
    occurrence's correctness wins; later answers for the same KP are
    ignored. In normal use this never triggers because Task 5's prompt
    enforces "1 question per KP".

    Returns a ReviewResult ready to write into state['review_result'].
    """
    from src.llm.schemas import ReviewResult

    weak: list[str] = []
    strong: list[str] = []
    correct = 0
    for i, q in enumerate(questions):
        try:
            kp = q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
            expected = q.answer if hasattr(q, "answer") else q["answer"]
        except (KeyError, AttributeError):
            logger.warning("malformed review question at index %d, skipping", i)
            continue
        student = answers[i] if i < len(answers) else -1
        if student == expected:
            correct += 1
            if kp not in strong:
                strong.append(kp)
        else:
            if kp not in weak:
                weak.append(kp)

    total = len(questions) or 1
    return ReviewResult(
        weak_kps=weak,
        strong_kps=strong,
        score=correct / total,
        focus_directive=_render_directive(weak_kps=weak, strong_kps=strong),
    )


# ---------------------------------------------------------------------------
# LLM-bearing question generation
# ---------------------------------------------------------------------------

def generate_review_questions(candidates: list[dict]) -> list:
    """Call LLM with the review_generate prompt, return list[QuizQuestion].

    Empty input → empty output, no LLM call. Failure → empty list (caller
    sees an empty review and the front-end can skip silently).
    """
    if not candidates:
        return []

    from src.llm.schemas import QuizQuestion
    from src.prompts.loader import render_prompt

    # Enrich candidates with days_ago for the prompt
    now = datetime.now(timezone.utc)
    enriched = []
    for c in candidates:
        try:
            days_ago = int(
                (now - _parse_iso_z(c["last_seen_at"])).total_seconds() / 86400
            )
        except (KeyError, ValueError):
            days_ago = 0
        enriched.append({
            **c,
            "days_ago": days_ago,
            "last_accuracy": c.get("last_accuracy") or 0.0,
        })

    user_prompt = render_prompt(
        "review_generate_system", candidates=enriched
    )

    try:
        client = get_llm_client()
        result = client.chat.completions.create(
            model=get_reviewer_model_name(),
            messages=[{"role": "user", "content": user_prompt}],
            response_model=list[QuizQuestion],
            max_tokens=4096,
        )
        return list(result)
    except Exception:
        logger.exception("review question generation failed")
        return []
