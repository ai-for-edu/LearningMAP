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
import os
import re
import time
import uuid
from typing import Any

from pydantic import ValidationError as _PydValidationError

from src import memory_client
from src.llm.client import get_llm_client, get_reviewer_model_name
from src.llm.schemas import ConsolidationDecision, ExtractResult, FactCandidate, NoteCategory
from src.prompts.loader import render_prompt

logger = logging.getLogger(__name__)


_CATEGORIES: tuple[NoteCategory, ...] = ("misconception", "preference", "style", "goal")


def _enabled() -> bool:
    """Read ENABLE_MEMORY_REVIEW env (case-insensitive 'true' check)."""
    return os.getenv("ENABLE_MEMORY_REVIEW", "false").lower() == "true"


def _get_llm_client():
    """Indirection so tests can patch easily without monkey-patching the SDK."""
    return get_llm_client()


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


def _message_row(m: Any) -> dict[str, str]:
    """Normalize a state message to ``{role, content}`` regardless of shape.

    LearningState extends ``langgraph.graph.MessagesState`` whose ``messages``
    are real ``langchain_core.messages.BaseMessage`` subclasses (HumanMessage /
    AIMessage). They expose ``.type`` (``"human"`` / ``"ai"``) and ``.content``
    but **no** ``.role`` attribute, so a naive ``m.role`` would AttributeError
    on the live chat path. Tests use plain ``{"role", "content"}`` dicts. Both
    shapes (plus a dict carrying ``"type"`` from JSON-rebuilt state) are
    handled here.
    """
    if isinstance(m, dict):
        role = m.get("role") or ("assistant" if m.get("type") == "ai" else "user")
        content = m.get("content", "")
    else:
        msg_type = getattr(m, "type", None)
        role = "assistant" if msg_type == "ai" else "user"
        content = getattr(m, "content", "")
    return {"role": role, "content": str(content)[:300]}


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

    For ``source_event="submit-review"`` the wrong-item source is
    ``state["review_questions"]`` / ``state["review_answers"]`` (set by the
    review-flow grading node), NOT the original diagnostic quiz — otherwise
    the reviewer would re-extract from stale quiz data instead of the just-
    graded review attempt.
    """
    msgs = state.get("messages") or []
    dialogue_tail = [_message_row(m) for m in msgs[-10:]]
    wrong_items = _wrong_items_top4(state, source_event=source_event)

    return {
        "source_event": source_event,
        "wrong_items": wrong_items,
        "dialogue_tail": dialogue_tail,
        "existing": existing_notes,
    }


def _wrong_items_top4(
    state: dict[str, Any], *, source_event: str,
) -> list[dict[str, Any]]:
    """Pick at most 4 wrong questions sorted by (last_accuracy ASC, index ASC).

    Tolerates both dict-shape and Pydantic-shape questions.
    Pads options to length 4 so the template can unconditionally index 0..3.
    Unknown KPs default to last_accuracy=1.0 so they sort LAST.
    """
    if source_event == "submit-review":
        questions = state.get("review_questions") or []
        answers = state.get("review_answers") or []
    else:
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
    user_id: str | None = None,
    session_id: str | None = None,
    correlation_id: str | None = None,
) -> ExtractResult:
    """LLM call A. Returns empty ExtractResult on any failure (graceful degradation).

    The instructor-wrapped client must support response_model kwarg; the
    caller (Task 15) passes ``get_llm_client()``. We accept the client by
    parameter so tests can swap in a MagicMock without monkey-patching.

    Failure path writes one op_errors row tagged with ``correlation_id``
    so the silent fallback is observable per spec §12.9.
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
    except Exception as exc:
        logger.exception("LLM call A (extract) failed")
        memory_client.log_error(
            source="main_app",
            user_id=user_id,
            session_id=session_id,
            correlation_id=correlation_id,
            op="extract_candidates",
            status="unexpected",
            error_class=type(exc).__name__,
            error_msg=str(exc)[:500],
            payload_excerpt=f"source_event={extract_input.get('source_event')}",
        )
        return ExtractResult(candidates=[])


def _consolidate(
    client: Any,
    candidate: FactCandidate,
    *,
    neighbors: list[dict[str, Any]],
    model: str,
    user_id: str | None = None,
    session_id: str | None = None,
    correlation_id: str | None = None,
) -> ConsolidationDecision:
    """LLM call B. Hard upper bound = once per candidate.

    Failure modes mapped to REJECT (never raise):
      - LLM raised pydantic ValidationError (Decision failed @model_validator)
        → REJECT('invalid decision schema'), op_error status='schema_invalid'
      - Any other exception → REJECT('llm call B failed'),
        op_error status='unexpected'
    Both write a single op_errors row per spec §12.9.
    """
    system = render_prompt("memory_consolidate_system")
    user = render_prompt(
        "memory_consolidate_user",
        candidate={"category": candidate.category, "content": candidate.content},
        neighbors=neighbors,
    )
    try:
        return client.chat.completions.create(
            model=model,
            response_model=ConsolidationDecision,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except _PydValidationError as exc:
        logger.warning("LLM call B returned invalid decision schema; rejecting")
        memory_client.log_error(
            source="main_app",
            user_id=user_id,
            session_id=session_id,
            correlation_id=correlation_id,
            op="consolidate",
            status="schema_invalid",
            error_class=type(exc).__name__,
            error_msg=str(exc)[:500],
            payload_excerpt=f"category={candidate.category}",
        )
        return ConsolidationDecision(
            action="REJECT", reason="invalid decision schema",
        )
    except Exception as exc:
        logger.exception("LLM call B (consolidate) failed")
        memory_client.log_error(
            source="main_app",
            user_id=user_id,
            session_id=session_id,
            correlation_id=correlation_id,
            op="consolidate",
            status="unexpected",
            error_class=type(exc).__name__,
            error_msg=str(exc)[:500],
            payload_excerpt=f"category={candidate.category}",
        )
        return ConsolidationDecision(
            action="REJECT", reason="llm call B failed",
        )


def _apply_decision(
    decision: ConsolidationDecision,
    candidate: FactCandidate,
    *,
    user_id: str,
    session_id: str,
    source_event: str,
    correlation_id: str,
) -> str:
    """Dispatch a decision to memory_client; return the action name actually
    persisted (or 'SKIP' for the one server-audited race case).

    Per docs/reviews/2026-05-09-hybrid-memory-pr2.md fixes #5b/#5c/#5d, we
    align with spec §12.4 ("5 actions all in notes_history") rather than
    §6.6's per-branch sample (which silently dropped failed UPDATEs).
    Concretely:

    - ADD / write_note returns ("race_audited", None) → SKIP (server's
      UNIQUE-index IntegrityError already wrote a REJECT row, no
      double-audit).
    - ADD / write_note returns ("failed", None) → write client-side REJECT
      history (422 / 5xx / network — server has nothing).
    - UPDATE / update_note returns False → write client-side REJECT history.
    - DELETE → ALWAYS write history regardless of archive_note's return
      (404 still records "intent to delete" for audit; spec §6.6's DELETE
      sample has no if-gate).
    """
    if decision.action == "ADD":
        status, note_id = memory_client.write_note(
            user_id, candidate.category, decision.new_content or "",
            session_id, source_event, correlation_id=correlation_id,
        )
        if status == "race_audited":
            return "SKIP"
        if status == "failed":
            memory_client.write_history(
                note_id=None, event="REJECT",
                old_content=None, new_content=candidate.content,
                reason=f"ADD write failed: {decision.reason}",
                source_session_id=session_id, source_event=source_event,
                correlation_id=correlation_id,
            )
            return "REJECT"
        # status == "ok"
        memory_client.write_history(
            note_id=note_id, event="ADD",
            new_content=decision.new_content, old_content=None,
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
            correlation_id=correlation_id,
        )
        return "ADD"

    if decision.action == "UPDATE":
        old = memory_client.get_note(decision.target_id, correlation_id=correlation_id) or {}
        ok = memory_client.update_note(
            decision.target_id, decision.new_content or "",
            correlation_id=correlation_id,
        )
        if not ok:
            # Spec §12.4 wins over §6.6's "if ok": every action audited.
            memory_client.write_history(
                note_id=None, event="REJECT",
                old_content=old.get("content"), new_content=candidate.content,
                reason=f"UPDATE write failed: {decision.reason}",
                source_session_id=session_id, source_event=source_event,
                correlation_id=correlation_id,
            )
            return "REJECT"
        memory_client.write_history(
            note_id=decision.target_id, event="UPDATE",
            old_content=old.get("content"), new_content=decision.new_content,
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
            correlation_id=correlation_id,
        )
        return "UPDATE"

    if decision.action == "DELETE":
        # Spec §6.6: DELETE always writes history, even when archive_note
        # returns False (note already archived). The audit row records
        # the reviewer's intent to delete regardless.
        old = memory_client.get_note(decision.target_id, correlation_id=correlation_id) or {}
        memory_client.archive_note(decision.target_id, correlation_id=correlation_id)
        memory_client.write_history(
            note_id=decision.target_id, event="DELETE",
            old_content=old.get("content"), new_content=None,
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
            correlation_id=correlation_id,
        )
        return "DELETE"

    if decision.action == "NOOP":
        memory_client.write_history(
            note_id=decision.target_id, event="NOOP",
            old_content=None, new_content=None,
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
            correlation_id=correlation_id,
        )
        return "NOOP"

    # REJECT
    memory_client.write_history(
        note_id=None, event="REJECT",
        old_content=None, new_content=candidate.content,
        reason=decision.reason,
        source_session_id=session_id, source_event=source_event,
        correlation_id=correlation_id,
    )
    return "REJECT"


def run_memory_review(
    user_id: str,
    session_id: str,
    state: dict[str, Any],
    source_event: str,
) -> None:
    """Side-band reviewer entry point.

    Caller (PR-3 chat.py) wraps this in `asyncio.to_thread`. Top-level
    try/except writes ONE op_errors row tagged with ``correlation_id`` so
    the bug is greppable end-to-end.
    """
    if not _enabled():
        return

    correlation_id = uuid.uuid4().hex
    started = time.time()

    try:
        _do_review(user_id, session_id, state, source_event, correlation_id, started)
    except Exception as exc:
        logger.exception("memory_review failed")
        memory_client.log_error(
            source="main_app", user_id=user_id, session_id=session_id,
            correlation_id=correlation_id,
            op="run_memory_review",
            status="unexpected",
            error_class=type(exc).__name__,
            error_msg=str(exc)[:500],
            payload_excerpt=f"source_event={source_event}",
        )


def _do_review(
    user_id: str,
    session_id: str,
    state: dict[str, Any],
    source_event: str,
    correlation_id: str,
    started_at: float,
) -> None:
    counters = {
        "hash_reject": 0,
        "inj_reject": 0,
        "ADD": 0,
        "UPDATE": 0,
        "DELETE": 0,
        "NOOP": 0,
        "REJECT": 0,
        "SKIP": 0,
    }
    candidates_seen = 0

    try:
        client = _get_llm_client()
        model = get_reviewer_model_name()

        # Seed hash-dedup set: pull all 4 categories' active notes (≤ 120 rows total)
        existing_by_cat: dict[NoteCategory, list[dict[str, Any]]] = {}
        existing_hashes: set[str] = set()
        for cat in _CATEGORIES:
            rows = memory_client.fetch_active_notes(
                user_id, cat, correlation_id=correlation_id,
            )
            existing_by_cat[cat] = rows
            for r in rows:
                h = r.get("content_hash")
                if h:
                    existing_hashes.add(h)

        flat_existing = [
            {"category": cat, "content": r["content"]}
            for cat, rows in existing_by_cat.items()
            for r in rows
        ]
        extract_input = _collect_extract_input(
            state, source_event=source_event, existing_notes=flat_existing,
        )

        extract = _extract_candidates(
            client, extract_input, model=model,
            user_id=user_id, session_id=session_id, correlation_id=correlation_id,
        )
        candidates_seen = len(extract.candidates)

        for candidate in extract.candidates:
            h = _hash(candidate.content)
            if h in existing_hashes:
                memory_client.write_history(
                    note_id=None, event="REJECT",
                    new_content=candidate.content, old_content=None,
                    reason="hash duplicate",
                    source_session_id=session_id, source_event=source_event,
                    correlation_id=correlation_id,
                )
                counters["hash_reject"] += 1
                continue

            if _is_injection(candidate.content):
                memory_client.write_history(
                    note_id=None, event="REJECT",
                    new_content=candidate.content, old_content=None,
                    reason="injection pattern",
                    source_session_id=session_id, source_event=source_event,
                    correlation_id=correlation_id,
                )
                counters["inj_reject"] += 1
                continue

            neighbors = memory_client.fetch_neighbors(
                user_id, candidate.category, candidate.content,
                top_k=5, correlation_id=correlation_id,
            )
            decision = _consolidate(
                client, candidate, neighbors=neighbors, model=model,
                user_id=user_id, session_id=session_id, correlation_id=correlation_id,
            )
            action = _apply_decision(
                decision, candidate,
                user_id=user_id, session_id=session_id,
                source_event=source_event, correlation_id=correlation_id,
            )
            counters[action] = counters.get(action, 0) + 1

            # Within-batch dedup: a successful ADD seeds the next iteration
            if action == "ADD":
                existing_hashes.add(h)
    finally:
        duration_ms = int((time.time() - started_at) * 1000)
        logger.info(
            "memory_review corr=%s session=%s event=%s extract=%d hash_reject=%d "
            "inj_reject=%d add=%d update=%d delete=%d noop=%d reject=%d skip=%d "
            "duration_ms=%d",
            correlation_id, session_id, source_event,
            candidates_seen, counters["hash_reject"], counters["inj_reject"],
            counters["ADD"], counters["UPDATE"], counters["DELETE"],
            counters["NOOP"], counters["REJECT"], counters["SKIP"], duration_ms,
        )
