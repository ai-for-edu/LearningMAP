"""End-to-end smoke acceptance for PR-1 + PR-2 + PR-3 + PR-4.

Drives the real HTTP API to verify the whole hybrid agent memory pipeline
works end-to-end. Costs ~3-4 LLM calls per run (~30-60s wall time).

Usage:
    1. Start student_memory in one terminal:
        cd student_memory && uv run uvicorn app.main:create_app --factory --port 8090

    2. Start main app in another terminal (with both env vars set):
        ENABLE_MEMORY_REVIEW=true \\
            OPENAI_API_KEY=... OPENAI_BASE_URL=... LLM_MODEL=... \\
            uv run python run/serve.py

    3. Run this script:
        ENABLE_MEMORY_REVIEW=true uv run python -m tests.manual.smoke_e2e

What it verifies:

  PR-1 (server)        : student_memory health + tables exist
  PR-2 (reviewer)      : submit-quiz dispatches reviewer → notes / notes_history grow
  PR-2 (progress)      : submit-quiz dispatches progress → progress_snapshots / mastery_curve grow
  PR-3 (write path)    : 3 BG tasks fire, op_errors stays empty (no silent failures)
  PR-3 (snapshot)      : new session POST /api/chat populates state.memory_snapshot
  PR-4 (read path)     : populated snapshot field contains "## 该学生" Markdown markers

Exit codes:
    0 — all PR-1/2/3/4 acceptance checks passed
    1 — assertion failure (some delta wrong / snapshot missing / op_errors non-empty)
    2 — services not reachable / env not set
    3 — HTTP error during driving phase
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx


_MAIN_APP = "http://localhost:8080"
_STUDENT_MEMORY = "http://localhost:8090"
_DB = Path("student_memory/data/memory.db")
_SESSIONS_DB = Path("sessions.db")
_USER_ID = "default_user"   # main app's DEFAULT_USER_ID
_TOPIC = "勾股定理"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_counts() -> dict[str, int]:
    if not _DB.exists():
        return {}
    conn = sqlite3.connect(str(_DB))
    out: dict[str, int] = {}
    for table in ("notes", "notes_history", "progress_snapshots",
                  "mastery_curve", "op_errors"):
        try:
            out[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = -1
    conn.close()
    return out


def _op_errors_since(since_ts: int) -> list[dict[str, Any]]:
    if not _DB.exists():
        return []
    conn = sqlite3.connect(str(_DB))
    rows = conn.execute(
        "SELECT op, status, error_class, substr(error_msg,1,80), correlation_id "
        "FROM op_errors WHERE ts >= ? ORDER BY ts DESC",
        (since_ts,),
    ).fetchall()
    conn.close()
    return [
        {"op": r[0], "status": r[1], "error_class": r[2],
         "error_msg": r[3], "correlation_id": r[4]}
        for r in rows
    ]


def _check_health(client: httpx.Client) -> None:
    try:
        r1 = client.get(f"{_MAIN_APP}/health", timeout=3)
        r2 = client.get(f"{_STUDENT_MEMORY}/health", timeout=3)
    except Exception as exc:
        print(f"FAIL: health check raised {type(exc).__name__}: {exc}")
        print(f"  expected services at {_MAIN_APP} and {_STUDENT_MEMORY}")
        sys.exit(2)
    if r1.status_code != 200 or r2.status_code != 200:
        print(f"FAIL: main_app={r1.status_code}, student_memory={r2.status_code}")
        sys.exit(2)
    print("OK   health: main_app=200, student_memory=200")


def _drive_quiz_flow(client: httpx.Client) -> tuple[str, dict]:
    """Create session, generate KG via initial chat, generate quiz, submit.

    Returns (session_id, final_state_dict).
    """
    # Step 1: open session with topic message — triggers intent → graph
    print(f"[A] POST /api/chat (creating session, topic='{_TOPIC}')")
    r = client.post(
        f"{_MAIN_APP}/api/chat",
        json={"message": f"我想学{_TOPIC}"},
        timeout=120,
    )
    if r.status_code != 200:
        print(f"FAIL: create session got {r.status_code}: {r.text[:300]}")
        sys.exit(3)
    sid = r.json()["session_id"]
    print(f"     session_id = {sid}")

    # The intent node should have routed to "graph" mode and generated KG.
    # Poll state to confirm.
    state = client.get(f"{_MAIN_APP}/api/chat/{sid}/state", timeout=10).json()
    if not state.get("has_graph"):
        print(f"FAIL: knowledge graph not generated (state.mode={state.get('mode')})")
        sys.exit(3)
    print(f"     KG generated (mode={state['mode']})")

    # Step 2: kick off background quiz generation
    print("[B] POST /api/chat/{sid}/generate/quiz (background)")
    r = client.post(
        f"{_MAIN_APP}/api/chat/{sid}/generate/quiz", timeout=10,
    )
    if r.status_code != 200:
        print(f"FAIL: generate-quiz dispatch got {r.status_code}: {r.text[:300]}")
        sys.exit(3)

    # Poll until done (max 90s)
    for i in range(45):
        time.sleep(2)
        st = client.get(
            f"{_MAIN_APP}/api/chat/{sid}/generate/quiz/status", timeout=5,
        ).json()
        status = st.get("status")
        if status == "done":
            print(f"     quiz ready after ~{(i+1)*2}s")
            break
        if status == "error":
            print(f"FAIL: quiz generation errored: {st}")
            sys.exit(3)
    else:
        print("FAIL: quiz generation timed out after 90s")
        sys.exit(3)

    # Step 3: submit answers (all 0 — we don't care about correctness)
    state = client.get(f"{_MAIN_APP}/api/chat/{sid}/state", timeout=10).json()
    questions = state.get("questions") or []
    if not questions:
        print("FAIL: no questions in state after quiz generation")
        sys.exit(3)
    answers = [0] * len(questions)
    print(f"[C] POST /api/chat/{{sid}}/submit-quiz (answers={answers})")
    r = client.post(
        f"{_MAIN_APP}/api/chat/{sid}/submit-quiz",
        json={"answers": answers},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"FAIL: submit-quiz got {r.status_code}: {r.text[:300]}")
        sys.exit(3)
    diag = r.json().get("diagnosis_result")
    if not diag:
        print("FAIL: no diagnosis_result in submit-quiz response")
        sys.exit(3)
    print(f"     diagnosis: score={diag.get('score')}/{diag.get('total')}")

    return sid, state


def _wait_for_bg(seconds: int = 35) -> None:
    """BG tasks (reviewer + progress + ingest) fire async after submit-quiz.

    Reviewer makes 1 LLM call A + up to 5 LLM calls B; takes 10-30s.
    """
    print(f"[D] sleep {seconds}s for BG tasks (reviewer + progress + ingest)")
    time.sleep(seconds)


def _open_new_session_and_check_snapshot(client: httpx.Client) -> str:
    """Open a fresh session — PR-3 should populate state.memory_snapshot."""
    print("[E] POST /api/chat (NEW session — should fetch snapshot)")
    r = client.post(
        f"{_MAIN_APP}/api/chat",
        json={"message": "你还记得我学过什么吗"},
        timeout=120,
    )
    if r.status_code != 200:
        print(f"FAIL: new session got {r.status_code}: {r.text[:300]}")
        sys.exit(3)
    sid = r.json()["session_id"]

    # Inspect persisted state (memory_snapshot is hidden from /state response
    # by spec, so read the SQLite directly).
    if not _SESSIONS_DB.exists():
        print(f"FAIL: {_SESSIONS_DB} not found — main app didn't persist?")
        sys.exit(3)
    conn = sqlite3.connect(str(_SESSIONS_DB))
    row = conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    conn.close()
    if row is None:
        print(f"FAIL: session {sid} not in sessions.db")
        sys.exit(3)
    state = json.loads(row[0])
    snap = state.get("memory_snapshot")
    print(f"     new session memory_snapshot field: {'<populated>' if snap else '<empty/None>'}")
    if snap:
        print(f"     snapshot first 120 chars: {snap[:120]!r}")
    return snap or ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if os.getenv("ENABLE_MEMORY_REVIEW", "false").lower() != "true":
        print("FAIL: set ENABLE_MEMORY_REVIEW=true on BOTH the script and the main app process")
        return 2

    print("=" * 70)
    print("E2E smoke: PR-1 + PR-2 + PR-3 + PR-4")
    print(f"main_app={_MAIN_APP}  student_memory={_STUDENT_MEMORY}")
    print(f"DB={_DB}")
    print("=" * 70)

    started_at = int(time.time())
    fails: list[str] = []

    with httpx.Client(follow_redirects=True) as client:
        # ---- 0. Health ----
        _check_health(client)

        before = _row_counts()
        print(f"\n[0] DB row counts before: {before}")

        # ---- 1-3. Drive flow ----
        sid, _state = _drive_quiz_flow(client)

        # ---- 4. Wait for BG ----
        _wait_for_bg(35)

        # ---- 5. DB deltas ----
        after = _row_counts()
        print(f"\n[F] DB row counts after:  {after}")
        deltas = {k: after.get(k, 0) - before.get(k, 0) for k in after}
        print(f"    deltas: {deltas}")

        # PR-2 / PR-3 acceptance
        if deltas.get("notes_history", 0) < 1:
            fails.append(
                f"notes_history did not grow (delta={deltas.get('notes_history')}) — "
                "reviewer never wrote any audit row"
            )
        if deltas.get("progress_snapshots", 0) < 1:
            fails.append(
                f"progress_snapshots did not grow (delta={deltas.get('progress_snapshots')}) — "
                "progress aggregator never wrote"
            )
        if deltas.get("mastery_curve", 0) < 1:
            fails.append(
                f"mastery_curve did not grow (delta={deltas.get('mastery_curve')}) — "
                "progress aggregator did not derive KP samples"
            )

        # ---- 6. op_errors ----
        errs = _op_errors_since(started_at)
        if errs:
            fails.append(
                f"op_errors has {len(errs)} new row(s); first: {errs[0]}"
            )
            print(f"\n!!  op_errors since start ({len(errs)} rows):")
            for e in errs[:5]:
                print(f"    - {e}")
        else:
            print("\n[G] op_errors: 0 new rows (no silent failures)")

        # ---- 7. PR-3+4: new session sees snapshot ----
        snap = _open_new_session_and_check_snapshot(client)
        if not snap:
            fails.append("new session has empty memory_snapshot — fetch_snapshot failed")
        elif "## 该学生" not in snap:
            fails.append(
                f"snapshot doesn't contain '## 该学生' marker (got: {snap[:100]!r})"
            )
        else:
            print("[H] PR-3+4: new session correctly populated with snapshot ✓")

    # ---- Summary ----
    print("\n" + "=" * 70)
    if fails:
        print(f"FAIL ({len(fails)} issue(s)):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS: PR-1 + PR-2 + PR-3 + PR-4 all green.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
