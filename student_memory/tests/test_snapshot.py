"""Tests for snapshot rendering: priority truncation, formatting, edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.sqlite_repo import SqliteRepo
from app.memory.snapshot import _elapsed_label, _trend_label, render_snapshot


@pytest.fixture
def repo(tmp_sqlite_path: Path) -> SqliteRepo:
    r = SqliteRepo(tmp_sqlite_path)
    yield r
    r.close()


@pytest.fixture
def now_ts() -> int:
    return 1715000000  # 2024-05-06


def _seed_minimal(repo: SqliteRepo, now_ts: int) -> None:
    repo.upsert_progress(
        user_id="u1",
        topic="Pythagorean Theorem",
        status="in_progress",
        started_at=now_ts - 3 * 86400,
        last_seen_at=now_ts,
        mastery_overall=0.6,
        last_session_id="s1",
    )
    repo.add_mastery_sample(
        user_id="u1",
        topic="Pythagorean Theorem",
        knowledge_point="distance formula",
        score=0.0,
        ts=now_ts,
        source="quiz",
    )
    repo.add_note(
        user_id="u1",
        category="misconception",
        content="confuses 'distance between two points' with 'difference of coordinates'",
        content_hash="h1",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=now_ts,
    )


def test_render_returns_progress_section_with_topic(
    repo: SqliteRepo, now_ts: int
) -> None:
    _seed_minimal(repo, now_ts)
    resp = render_snapshot(repo, user_id="u1", now_ts=now_ts)
    assert "## Student Learning Progress" in resp.snapshot_md
    assert "Pythagorean Theorem" in resp.snapshot_md
    assert "60%" in resp.snapshot_md
    assert "3d ago" in resp.snapshot_md


def test_render_returns_misconception_section(repo: SqliteRepo, now_ts: int) -> None:
    _seed_minimal(repo, now_ts)
    resp = render_snapshot(repo, user_id="u1", now_ts=now_ts)
    assert "## Student Misconception History" in resp.snapshot_md
    assert "distance between two points" in resp.snapshot_md


def test_render_skips_empty_sections(repo: SqliteRepo, now_ts: int) -> None:
    """No progress, no notes → empty string, char_count=0."""
    resp = render_snapshot(repo, user_id="u_unknown", now_ts=now_ts)
    assert resp.snapshot_md == ""
    assert resp.char_count == 0
    assert resp.truncated is False
    assert resp.sections == {}


def test_render_priority_truncates_low_priority_when_over_budget(
    repo: SqliteRepo, now_ts: int
) -> None:
    """Goal section (lowest priority) gets dropped when budget tight."""
    _seed_minimal(repo, now_ts)
    for i in range(30):
        repo.add_note(
            user_id="u1",
            category="misconception",
            content="X" * 150,
            content_hash=f"hm{i}",
            source_session_id="s1",
            source_event="submit-quiz",
            now_ts=now_ts + i,
        )
        repo.add_note(
            user_id="u1",
            category="goal",
            content="Y" * 150,
            content_hash=f"hg{i}",
            source_session_id="s1",
            source_event="submit-quiz",
            now_ts=now_ts + i,
        )

    resp = render_snapshot(repo, user_id="u1", now_ts=now_ts, budget=2000)
    assert resp.truncated is True
    assert "## Student Misconception History" in resp.snapshot_md
    assert "## Student Long-Term Goals" not in resp.snapshot_md


def test_render_respects_3500_default_budget(repo: SqliteRepo, now_ts: int) -> None:
    _seed_minimal(repo, now_ts)
    resp = render_snapshot(repo, user_id="u1", now_ts=now_ts)
    assert len(resp.snapshot_md) <= 3500


def test_elapsed_label_buckets() -> None:
    """elapsed: <24h→today, <7d→Nd ago, <30d→Nw ago, else→Nmo ago."""
    now = 1715000000
    assert _elapsed_label(now - 3600, now) == "today"
    assert _elapsed_label(now - 3 * 86400, now) == "3d ago"
    assert _elapsed_label(now - 14 * 86400, now) == "2w ago"
    assert _elapsed_label(now - 90 * 86400, now) == "3mo ago"


def test_trend_monotonic_up() -> None:
    samples = [
        type("S", (), {"score": 0.3})(),
        type("S", (), {"score": 0.5})(),
        type("S", (), {"score": 0.7})(),
    ]
    assert _trend_label(samples) == "improving"


def test_trend_monotonic_down() -> None:
    samples = [
        type("S", (), {"score": 0.7})(),
        type("S", (), {"score": 0.5})(),
        type("S", (), {"score": 0.3})(),
    ]
    assert _trend_label(samples) == "declining"


def test_trend_returns_none_when_fewer_than_3_samples() -> None:
    assert _trend_label([]) is None
    assert _trend_label([type("S", (), {"score": 0.5})()]) is None
