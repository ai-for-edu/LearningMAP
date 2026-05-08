"""Tests for op_errors repo writes and 30-day prune."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.sqlite_repo import SqliteRepo


@pytest.fixture
def repo(tmp_sqlite_path: Path) -> SqliteRepo:
    r = SqliteRepo(tmp_sqlite_path)
    yield r
    r.close()


def test_log_op_error_inserts_row(repo: SqliteRepo) -> None:
    repo.log_op_error(
        ts=1000,
        source="memory_service",
        user_id="u1",
        session_id="s1",
        correlation_id="corr-x",
        op="fetch_snapshot",
        status="http_5xx",
        error_class="HTTPError",
        error_msg="boom",
        payload_excerpt="user_id=u1",
    )
    row = repo._conn.execute(
        "SELECT * FROM op_errors WHERE correlation_id = 'corr-x'"
    ).fetchone()
    assert row["op"] == "fetch_snapshot"
    assert row["status"] == "http_5xx"
    assert row["error_msg"] == "boom"


def test_log_op_error_truncates_long_fields_to_500(repo: SqliteRepo) -> None:
    long_msg = "x" * 1200
    repo.log_op_error(
        ts=1000,
        source="main_app",
        user_id="u1",
        session_id=None,
        correlation_id=None,
        op="run_memory_review",
        status="unexpected",
        error_class="RuntimeError",
        error_msg=long_msg,
        payload_excerpt=long_msg,
    )
    row = repo._conn.execute(
        "SELECT error_msg, payload_excerpt FROM op_errors LIMIT 1"
    ).fetchone()
    assert len(row["error_msg"]) <= 500
    assert len(row["payload_excerpt"]) <= 500


def test_list_errors_for_user_orders_desc(repo: SqliteRepo) -> None:
    repo.log_op_error(
        ts=1000,
        source="main_app",
        user_id="u1",
        session_id="s",
        correlation_id="c1",
        op="x",
        status="ok",
        error_class="E",
        error_msg="a",
        payload_excerpt="",
    )
    repo.log_op_error(
        ts=2000,
        source="main_app",
        user_id="u1",
        session_id="s",
        correlation_id="c2",
        op="x",
        status="ok",
        error_class="E",
        error_msg="b",
        payload_excerpt="",
    )
    rows = repo.list_errors_for_user("u1")
    assert [r.correlation_id for r in rows] == ["c2", "c1"]


def test_list_errors_for_correlation_groups_chain(repo: SqliteRepo) -> None:
    for op in ("fetch_snapshot", "write_note", "run_memory_review"):
        repo.log_op_error(
            ts=1000,
            source="main_app",
            user_id="u1",
            session_id="s",
            correlation_id="same-corr",
            op=op,
            status="x",
            error_class="E",
            error_msg="m",
            payload_excerpt="",
        )
    rows = repo.list_errors_for_correlation("same-corr")
    assert {r.op for r in rows} == {"fetch_snapshot", "write_note", "run_memory_review"}


def test_prune_op_errors_removes_old_rows(repo: SqliteRepo) -> None:
    """ts 早于 cutoff 的行被删, 之后的保留."""
    repo.log_op_error(
        ts=1000,
        source="main_app",
        user_id="u1",
        session_id="s",
        correlation_id="old",
        op="x",
        status="x",
        error_class="E",
        error_msg="m",
        payload_excerpt="",
    )
    repo.log_op_error(
        ts=5000,
        source="main_app",
        user_id="u1",
        session_id="s",
        correlation_id="new",
        op="x",
        status="x",
        error_class="E",
        error_msg="m",
        payload_excerpt="",
    )
    deleted = repo.prune_op_errors(cutoff_ts=2000)
    assert deleted == 1
    remaining = repo._conn.execute("SELECT correlation_id FROM op_errors").fetchall()
    assert {r["correlation_id"] for r in remaining} == {"new"}
