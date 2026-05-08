"""Tests for progress_snapshots UPSERT and mastery_curve append+cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.sqlite_repo import SqliteRepo


@pytest.fixture
def repo(tmp_sqlite_path: Path) -> SqliteRepo:
    r = SqliteRepo(tmp_sqlite_path)
    yield r
    r.close()


def test_upsert_progress_inserts_new(repo: SqliteRepo) -> None:
    repo.upsert_progress(
        user_id="u1",
        topic="勾股定理",
        status="in_progress",
        started_at=1000,
        last_seen_at=1000,
        mastery_overall=0.6,
        last_session_id="s1",
    )
    row = repo._conn.execute(
        "SELECT * FROM progress_snapshots WHERE user_id='u1' AND topic='勾股定理'"
    ).fetchone()
    assert row["status"] == "in_progress"
    assert row["mastery_overall"] == pytest.approx(0.6)


def test_upsert_progress_updates_existing(repo: SqliteRepo) -> None:
    repo.upsert_progress(
        user_id="u1",
        topic="勾股定理",
        status="in_progress",
        started_at=1000,
        last_seen_at=1000,
        mastery_overall=0.6,
        last_session_id="s1",
    )
    repo.upsert_progress(
        user_id="u1",
        topic="勾股定理",
        status="mastered",
        started_at=1000,
        last_seen_at=2000,
        mastery_overall=0.9,
        last_session_id="s2",
    )

    rows = repo._conn.execute(
        "SELECT * FROM progress_snapshots WHERE user_id='u1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "mastered"
    assert rows[0]["mastery_overall"] == pytest.approx(0.9)
    assert rows[0]["started_at"] == 1000  # 不被覆盖


def test_get_progress_returns_none_for_missing(repo: SqliteRepo) -> None:
    assert repo.get_progress("u1", "missing") is None


def test_list_progress_for_user_orders_by_recency(repo: SqliteRepo) -> None:
    repo.upsert_progress(
        user_id="u1",
        topic="A",
        status="in_progress",
        started_at=1000,
        last_seen_at=1000,
        mastery_overall=0.5,
        last_session_id="s1",
    )
    repo.upsert_progress(
        user_id="u1",
        topic="B",
        status="in_progress",
        started_at=2000,
        last_seen_at=2000,
        mastery_overall=0.5,
        last_session_id="s2",
    )

    items = repo.list_progress_for_user("u1")
    assert [i.topic for i in items] == ["B", "A"]
