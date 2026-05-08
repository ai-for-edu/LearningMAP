"""Tests for notes/history/progress/mastery repo methods."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.sqlite_repo import SqliteRepo


@pytest.fixture
def repo(tmp_sqlite_path: Path):
    r = SqliteRepo(tmp_sqlite_path)
    yield r
    r.close()


def test_add_note_returns_id_and_inserts_row(repo):
    note_id = repo.add_note(
        user_id="u1",
        category="misconception",
        content="confuses A with B",
        content_hash="abc123",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=1000,
    )
    assert note_id > 0

    row = repo._conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    assert row["user_id"] == "u1"
    assert row["category"] == "misconception"
    assert row["content"] == "confuses A with B"
    assert row["content_hash"] == "abc123"
    assert row["created_at"] == 1000
    assert row["updated_at"] == 1000
    assert row["archived"] == 0


def test_list_active_notes_filters_by_category_and_archived(repo):
    repo.add_note(
        user_id="u1",
        category="misconception",
        content="m1",
        content_hash="h1",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=1000,
    )
    repo.add_note(
        user_id="u1",
        category="preference",
        content="p1",
        content_hash="h2",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=2000,
    )
    archived_id = repo.add_note(
        user_id="u1",
        category="misconception",
        content="m2",
        content_hash="h3",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=3000,
    )
    repo.archive_note(archived_id)

    notes = repo.list_active_notes(user_id="u1", category="misconception")
    assert len(notes) == 1
    assert notes[0].content == "m1"

    prefs = repo.list_active_notes(user_id="u1", category="preference")
    assert len(prefs) == 1
    assert prefs[0].content == "p1"


def test_lru_eviction_archives_oldest_when_over_30(repo):
    """超过 30 条 active 时, 最老的被 archived=1, 不物理删。"""
    for i in range(31):
        repo.add_note(
            user_id="u1",
            category="misconception",
            content=f"m{i}",
            content_hash=f"h{i}",
            source_session_id="s1",
            source_event="submit-quiz",
            now_ts=1000 + i,
        )

    # 总行数 31, active 30
    total = repo._conn.execute(
        "SELECT COUNT(*) FROM notes WHERE user_id='u1'"
    ).fetchone()[0]
    assert total == 31

    active = repo.list_active_notes(user_id="u1", category="misconception")
    assert len(active) == 30
    contents = {n.content for n in active}
    assert "m0" not in contents  # 最老的被 archived
    assert "m30" in contents


def test_get_note_by_id_returns_full_record(repo):
    note_id = repo.add_note(
        user_id="u1",
        category="goal",
        content="learn calc",
        content_hash="hh",
        source_session_id="s1",
        source_event="generate-lesson",
        now_ts=5000,
    )
    note = repo.get_note(note_id)
    assert note is not None
    assert note.id == note_id
    assert note.content == "learn calc"
    assert note.category == "goal"


def test_get_note_returns_none_for_missing_id(repo):
    assert repo.get_note(99999) is None


def test_update_note_changes_content_and_hash_and_timestamp(repo):
    note_id = repo.add_note(
        user_id="u1",
        category="style",
        content="old",
        content_hash="oldhash",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=1000,
    )
    ok = repo.update_note(
        note_id=note_id, content="new content", content_hash="newhash", now_ts=2000
    )
    assert ok is True

    note = repo.get_note(note_id)
    assert note.content == "new content"
    assert note.content_hash == "newhash"
    assert note.updated_at == 2000
    assert note.created_at == 1000  # 不动


def test_archive_note_sets_flag_softdel(repo):
    note_id = repo.add_note(
        user_id="u1",
        category="misconception",
        content="m",
        content_hash="h",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=1000,
    )
    assert repo.archive_note(note_id) is True

    note = repo.get_note(note_id)
    assert note is not None  # 不物理删
    assert note.archived == 1


def test_hash_exists_active_only(repo):
    note_id = repo.add_note(
        user_id="u1",
        category="misconception",
        content="m",
        content_hash="duphash",
        source_session_id="s1",
        source_event="submit-quiz",
        now_ts=1000,
    )
    assert repo.hash_exists_active("u1", "duphash") is True

    # archived 后应不再算 dup
    repo.archive_note(note_id)
    assert repo.hash_exists_active("u1", "duphash") is False
