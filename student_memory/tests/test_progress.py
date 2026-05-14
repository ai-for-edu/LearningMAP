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
        topic="Pythagorean Theorem",
        status="in_progress",
        started_at=1000,
        last_seen_at=1000,
        mastery_overall=0.6,
        last_session_id="s1",
    )
    row = repo._conn.execute(
        "SELECT * FROM progress_snapshots WHERE user_id='u1' AND topic='Pythagorean Theorem'"
    ).fetchone()
    assert row["status"] == "in_progress"
    assert row["mastery_overall"] == pytest.approx(0.6)


def test_upsert_progress_updates_existing(repo: SqliteRepo) -> None:
    repo.upsert_progress(
        user_id="u1",
        topic="Pythagorean Theorem",
        status="in_progress",
        started_at=1000,
        last_seen_at=1000,
        mastery_overall=0.6,
        last_session_id="s1",
    )
    repo.upsert_progress(
        user_id="u1",
        topic="Pythagorean Theorem",
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
    assert rows[0]["started_at"] == 1000  # must not be overwritten


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


def test_add_mastery_sample_inserts_row(repo: SqliteRepo) -> None:
    repo.add_mastery_sample(
        user_id="u1",
        topic="A",
        knowledge_point="kp1",
        score=0.5,
        ts=1000,
        source="quiz",
    )
    row = repo._conn.execute(
        "SELECT * FROM mastery_curve WHERE user_id='u1' AND knowledge_point='kp1'"
    ).fetchone()
    assert row["score"] == pytest.approx(0.5)
    assert row["source"] == "quiz"


def test_mastery_curve_caps_at_10_samples_per_kp(repo: SqliteRepo) -> None:
    """Same (user, topic, kp) keeps at most 10 rows; oldest are evicted when over the cap."""
    for i in range(15):
        repo.add_mastery_sample(
            user_id="u1",
            topic="A",
            knowledge_point="kp1",
            score=0.1 * i,
            ts=1000 + i,
            source="quiz",
        )

    rows = repo._conn.execute(
        "SELECT score, ts FROM mastery_curve "
        "WHERE user_id='u1' AND knowledge_point='kp1' ORDER BY ts ASC"
    ).fetchall()
    assert len(rows) == 10
    # Oldest 5 rows (i=0..4) are deleted, leaving i=5..14
    assert rows[0]["ts"] == 1005
    assert rows[-1]["ts"] == 1014


def test_list_mastery_for_kp_returns_chronological(repo: SqliteRepo) -> None:
    for i, score in enumerate([0.1, 0.4, 0.7]):
        repo.add_mastery_sample(
            user_id="u1",
            topic="A",
            knowledge_point="kp1",
            score=score,
            ts=1000 + i,
            source="quiz",
        )

    samples = repo.list_mastery_for_kp("u1", "A", "kp1")
    assert [s.score for s in samples] == pytest.approx([0.1, 0.4, 0.7])


def test_list_weak_mastery_filters_below_threshold(repo: SqliteRepo) -> None:
    """list_weak_mastery returns only KPs whose latest sample score is < threshold."""
    repo.add_mastery_sample(
        user_id="u1",
        topic="A",
        knowledge_point="weak_kp",
        score=0.3,
        ts=1000,
        source="quiz",
    )
    repo.add_mastery_sample(
        user_id="u1",
        topic="A",
        knowledge_point="strong_kp",
        score=0.9,
        ts=1000,
        source="quiz",
    )

    weak = repo.list_weak_mastery(user_id="u1", threshold=0.7)
    kps = {w.knowledge_point for w in weak}
    assert "weak_kp" in kps
    assert "strong_kp" not in kps
