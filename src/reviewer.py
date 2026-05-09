"""LLM-driven memory reviewer.

Spec §6 (`docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`).

Entry point ``run_memory_review`` is added in a later task. This module
keeps every helper pure and unit-testable in isolation: ``_hash``,
``_is_injection``, ``_collect_extract_input`` have no I/O.

LLM calls (``_extract_candidates``, ``_consolidate``) are isolated to two
functions that take an instructor-wrapped client as an argument so tests
can swap in a MagicMock without monkey-patching globals.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from src.llm.schemas import ExtractResult
from src.prompts.loader import render_prompt

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def _hash(text: str) -> str:
    """16-hex-char SHA-256 prefix of normalized content.

    Normalization mirrors the server's ``_content_hash``: lowercase + strip.
    The two MUST stay in sync — the UNIQUE partial index added in PR-1
    fix #1 uses the server-side normalization, and a divergent client
    hash would let dedup misses sneak past the in-Python set only to
    fail at the database layer.
    """
    return hashlib.sha256(text.lower().strip().encode("utf-8")).hexdigest()[:16]


_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)ignore (previous|all|the above) instructions"),
    re.compile(r"(?i)disregard (the )?(system|prior) (prompt|message)"),
    re.compile(r"(?i)you are now"),
    re.compile(r"<\|.*?\|>"),
    re.compile(r"(?i)system\s*:"),
]


def _is_injection(content: str) -> bool:
    return any(p.search(content) for p in _INJECTION_PATTERNS)


def _collect_extract_input(
    state: dict[str, Any],
    *,
    source_event: str,
    existing_notes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the dict that ``memory_extract_user.j2`` consumes.

    Slicing rules from spec §6.2:
      - dialogue_tail: last 10 messages, content truncated to 300 chars
      - wrong_items: top 4 wrong questions sorted by (last_accuracy ASC,
        index ASC); each question_text truncated to 400 chars
      - existing: passed through (server already caps at ~120 rows)
    """
    msgs = state.get("messages") or []
    dialogue_tail = [
        {"role": (m["role"] if isinstance(m, dict) else m.role),
         "content": (m["content"] if isinstance(m, dict) else m.content)[:300]}
        for m in msgs[-10:]
    ]

    wrong_items = _wrong_items_top4(state)

    return {
        "source_event": source_event,
        "wrong_items": wrong_items,
        "dialogue_tail": dialogue_tail,
        "existing": existing_notes,
    }


def _wrong_items_top4(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Pick at most 4 wrong questions sorted by (last_accuracy ASC, index ASC).

    Tolerates both dict-shape and Pydantic-shape questions.
    Pads options to length 4 so the template can unconditionally index 0..3.
    Unknown KPs default to last_accuracy=1.0 so they sort LAST.
    """
    questions = state.get("questions") or []
    answers = state.get("answers") or []
    last_acc_by_kp = state.get("wrong_item_last_accuracy") or {}

    wrongs: list[tuple[float, int, dict[str, Any]]] = []
    for i, q in enumerate(questions):
        if i >= len(answers):
            break
        correct = q.answer if hasattr(q, "answer") else q.get("answer", -1)
        student = answers[i]
        if student == correct:
            continue
        kp = q.knowledge_point if hasattr(q, "knowledge_point") else q.get("knowledge_point", "")
        question_text = (q.question if hasattr(q, "question") else q.get("question", ""))[:400]
        options = q.options if hasattr(q, "options") else q.get("options", ["", "", "", ""])
        # Pad to 4 options so the template can index 0..3 unconditionally
        options = (list(options) + ["", "", "", ""])[:4]
        last_acc = float(last_acc_by_kp.get(kp, 1.0))
        item = {
            "question_text": question_text,
            "options": options,
            "correct_label": "ABCD"[correct] if 0 <= correct < 4 else "?",
            "student_label": "ABCD"[student] if 0 <= student < 4 else "?",
            "kp": kp,
            "last_accuracy": last_acc,
        }
        wrongs.append((last_acc, i, item))

    wrongs.sort(key=lambda t: (t[0], t[1]))
    return [w[2] for w in wrongs[:4]]


# ----------------------------------------------------------------------
# LLM call A: _extract_candidates
# ----------------------------------------------------------------------
def _extract_candidates(
    client: Any,
    extract_input: dict[str, Any],
    *,
    model: str,
) -> ExtractResult:
    """LLM call A. Returns empty ExtractResult on any failure (graceful degradation).

    The instructor-wrapped client must support response_model kwarg; the
    caller (Task 15) passes `get_llm_client()`. We accept the client by
    parameter so tests can swap in a MagicMock without monkey-patching.
    """
    system = render_prompt("memory_extract_system")
    user = render_prompt("memory_extract_user", **extract_input)
    try:
        return client.chat.completions.create(
            model=model,
            response_model=ExtractResult,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception:
        logger.exception("LLM call A (extract) failed")
        return ExtractResult(candidates=[])
