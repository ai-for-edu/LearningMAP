"""Manual smoke acceptance for PR-2.

Usage:
    1. Start student_memory in a separate terminal:
        cd student_memory && uv run uvicorn app.main:create_app --factory --port 8090
    2. Run this script:
        ENABLE_MEMORY_REVIEW=true LLM_MODEL=... OPENAI_API_KEY=... \\
            uv run python -m tests.manual.smoke_pr2

Verifies spec §11 PR-2 acceptance:
  > fixture state: manually run run_memory_review + refresh_progress, check SQLite row count increases.

Prints SQLite row deltas + the correlation_id so the operator can manually
grep notes_history / op_errors. NOT included in pytest collection — the file
lives under tests/manual/ which is excluded via tool.pytest.ini_options
norecursedirs in pyproject.toml.

Exit codes:
    0 — all expected SQLite deltas observed (acceptance passed)
    1 — assertion failure (some delta was wrong / missing)
    2 — missing required env (ENABLE_MEMORY_REVIEW not 'true')
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

from src.llm.schemas import DiagnosisBreakdown, DiagnosisResult


_DB = Path("student_memory/data/memory.db")


def _row_counts() -> dict[str, int]:
    """Count rows in each PR-1 table; return {} if DB is absent."""
    if not _DB.exists():
        return {}
    conn = sqlite3.connect(str(_DB))
    out: dict[str, int] = {}
    for table in (
        "notes",
        "notes_history",
        "progress_snapshots",
        "mastery_curve",
        "op_errors",
    ):
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = -1
    conn.close()
    return out


def _recent_correlation_ids(session_id: str, *, since_ts: int) -> dict[str, list[str]]:
    """Pull correlation_ids from history.reason and op_errors for the given
    session_id since `since_ts`. Useful for ops grep without prior knowledge.
    """
    if not _DB.exists():
        return {"history": [], "op_errors": []}
    conn = sqlite3.connect(str(_DB))
    history_corrs: list[str] = []
    op_error_corrs: list[str] = []
    try:
        rows = conn.execute(
            "SELECT reason FROM notes_history "
            "WHERE source_session_id = ? AND ts >= ? "
            "ORDER BY id DESC LIMIT 20",
            (session_id, since_ts),
        ).fetchall()
        for (reason,) in rows:
            if reason and reason.startswith("[corr="):
                end = reason.find("]")
                if end > 6:
                    history_corrs.append(reason[6:end])
        rows = conn.execute(
            "SELECT correlation_id FROM op_errors "
            "WHERE session_id = ? AND ts >= ? AND correlation_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 20",
            (session_id, since_ts),
        ).fetchall()
        op_error_corrs = [r[0] for r in rows]
    except sqlite3.OperationalError:
        pass
    conn.close()
    return {
        "history": sorted(set(history_corrs)),
        "op_errors": sorted(set(op_error_corrs)),
    }


def main() -> int:
    if os.getenv("ENABLE_MEMORY_REVIEW", "false").lower() != "true":
        print("FAIL: set ENABLE_MEMORY_REVIEW=true before running")
        return 2

    user_id = "smoke_pr2_user"
    session_id = f"smoke_{int(time.time())}"

    state = {
        "topic": "Pythagorean theorem",
        "messages": [
            {"role": "user", "content": "I confused 'distance between two coordinate points' with 'difference of coordinates', forgetting to square then take the square root"},
            {"role": "assistant", "content": "Right, you need to square first and then take the square root."},
        ],
        "questions": [],
        "answers": [],
        "wrong_item_last_accuracy": {},
        "diagnosis_result": DiagnosisResult(
            score=2,
            total=4,
            correct=2,
            incorrect=2,
            breakdown=[
                DiagnosisBreakdown(name="Pythagorean formula", status="mastered", detail="ok"),
                DiagnosisBreakdown(
                    name="coordinate distance",
                    status="weak",
                    detail="distance error",
                ),
            ],
        ),
    }

    print("=" * 60)
    print(f"smoke_pr2 user={user_id} session={session_id}")
    print(f"ENABLE_MEMORY_REVIEW={os.getenv('ENABLE_MEMORY_REVIEW')}")
    print(f"REVIEWER_MODEL={os.getenv('REVIEWER_MODEL', '(default)')}")
    print("=" * 60)

    before = _row_counts()
    print(f"before: {before}")

    # Imports here so failure to start the service doesn't crash module load
    from src import progress_aggregator, reviewer

    reviewer.run_memory_review(user_id, session_id, state, "submit-quiz")
    progress_aggregator.refresh_progress(user_id, session_id, state)

    # Reviewer + aggregator are synchronous in this thread; no asyncio.to_thread
    # in the smoke script. Brief pause anyway to let any in-flight HTTP settle.
    time.sleep(0.5)

    after = _row_counts()
    print(f"after:  {after}")

    deltas = {k: after.get(k, 0) - before.get(k, 0) for k in after}
    print(f"deltas: {deltas}")

    corrs = _recent_correlation_ids(session_id, since_ts=int(time.time()) - 60)
    print(f"correlation_ids (notes_history): {corrs['history']}")
    print(f"correlation_ids (op_errors):     {corrs['op_errors']}")

    fails: list[str] = []
    if deltas.get("progress_snapshots", 0) < 1:
        fails.append("progress_snapshots: expected at least 1 new row")
    if deltas.get("mastery_curve", 0) < 2:
        fails.append(
            "mastery_curve: expected at least 2 new rows (one per KP sample)"
        )
    # notes/notes_history may be 0 if the LLM extracted nothing; we don't
    # hard-assert. But if they're 0 we print a soft note so the operator
    # knows the LLM didn't produce candidates.
    if deltas.get("notes_history", 0) < 1:
        print("(soft) notes_history delta is 0 — LLM extracted no candidates")

    # op_errors should ideally be 0; if non-zero, the operator should grep
    # by correlation_id to see what failed silently.
    if deltas.get("op_errors", 0) > 0:
        print(
            f"(soft) op_errors delta is {deltas['op_errors']} — "
            f"some silent fallback fired; grep notes_history/op_errors by corr"
        )

    if fails:
        print("FAILURES:")
        for f in fails:
            print(f"  - {f}")
        return 1

    print("OK — PR-2 smoke acceptance passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
