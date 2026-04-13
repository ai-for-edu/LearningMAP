"""Phase 2: SQLite repository unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.sqlite_repo import MemoryRecord, SqliteRepo


def _make_record(
    memory_id: str,
    *,
    user_id: str = "u_001",
    session_id: str = "s_001",
    timestamp: str = "2026-03-28T10:30:00Z",
    topic: str | None = "Quadratic Equations",
    subtopic: str | None = "Discriminant",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        session_id=session_id,
        memory_type="subtopic",
        topic=topic,
        subtopic=subtopic,
        timestamp=timestamp,
        retrieval_text=f"text-for-{memory_id}",
        raw_json='{"x": 1}',
        question_count=2,
        correct_count=1,
        accuracy=0.5,
        error_types="confusion",
    )


@pytest.fixture
def repo(tmp_sqlite_path: Path) -> SqliteRepo:
    r = SqliteRepo(tmp_sqlite_path)
    yield r
    r.close()


def test_init_db_is_idempotent(tmp_sqlite_path: Path) -> None:
    r1 = SqliteRepo(tmp_sqlite_path)
    r1.close()
    # Re-opening the same file must not raise.
    r2 = SqliteRepo(tmp_sqlite_path)
    r2.close()


def test_insert_and_get_memory(repo: SqliteRepo) -> None:
    rec = _make_record("m_001")
    repo.insert_memory(rec)
    fetched = repo.get_memory("m_001")
    assert fetched == rec
    assert repo.count_memories() == 1


def test_insert_memories_batch_and_list_by_user_orders_by_timestamp(
    repo: SqliteRepo,
) -> None:
    repo.insert_memories(
        [
            _make_record("m_001", timestamp="2026-03-01T00:00:00Z"),
            _make_record("m_002", timestamp="2026-03-15T00:00:00Z"),
            _make_record("m_003", timestamp="2026-03-10T00:00:00Z"),
        ]
    )
    rows = repo.list_by_user("u_001")
    assert [r.memory_id for r in rows] == ["m_002", "m_003", "m_001"]


def test_get_memories_preserves_request_order(repo: SqliteRepo) -> None:
    repo.insert_memories([_make_record("m_a"), _make_record("m_b"), _make_record("m_c")])
    out = repo.get_memories(["m_c", "m_a"])
    assert [r.memory_id for r in out] == ["m_c", "m_a"]


def test_get_memories_empty_input_returns_empty(repo: SqliteRepo) -> None:
    assert repo.get_memories([]) == []


def test_get_memory_missing_returns_none(repo: SqliteRepo) -> None:
    assert repo.get_memory("nope") is None


def test_assign_labels_returns_monotonic_ints(repo: SqliteRepo) -> None:
    repo.insert_memories([_make_record("m_a"), _make_record("m_b")])
    labels = repo.assign_labels(["m_a", "m_b"])
    assert labels == [1, 2]
    assert repo.get_label("m_a") == 1
    assert repo.get_label("m_b") == 2
    assert repo.get_memory_id(1) == "m_a"
    assert repo.get_memory_id(2) == "m_b"


def test_clear_labels_resets_autoincrement(repo: SqliteRepo) -> None:
    repo.insert_memory(_make_record("m_a"))
    repo.assign_labels(["m_a"])
    repo.clear_labels()
    repo.insert_memory(_make_record("m_b"))
    labels = repo.assign_labels(["m_b"])
    assert labels == [1]


def test_session_exists(repo: SqliteRepo) -> None:
    assert not repo.session_exists("s_001")
    repo.insert_memory(_make_record("m_a", session_id="s_001"))
    assert repo.session_exists("s_001")


def test_iter_all_yields_in_insertion_order(repo: SqliteRepo) -> None:
    repo.insert_memories(
        [_make_record("m_a"), _make_record("m_b"), _make_record("m_c")]
    )
    ids = [r.memory_id for r in repo.iter_all()]
    assert ids == ["m_a", "m_b", "m_c"]


def test_set_and_get_meta(repo: SqliteRepo) -> None:
    assert repo.get_meta("embedding_model") is None
    repo.set_meta("embedding_model", "BAAI/bge-m3")
    assert repo.get_meta("embedding_model") == "BAAI/bge-m3"
    repo.set_meta("embedding_model", "BAAI/bge-small-zh-v1.5")
    assert repo.get_meta("embedding_model") == "BAAI/bge-small-zh-v1.5"


def test_transaction_rolls_back_on_exception(repo: SqliteRepo, tmp_sqlite_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with repo.transaction() as cur:
            cur.execute(
                "INSERT INTO memories (memory_id, user_id, session_id, memory_type, "
                "timestamp, retrieval_text, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("m_x", "u", "s", "subtopic", "2026-01-01T00:00:00Z", "t", "{}"),
            )
            raise RuntimeError("boom")
    assert repo.get_memory("m_x") is None


def test_delete_memory_cascades_to_labels(repo: SqliteRepo) -> None:
    repo.insert_memory(_make_record("m_a"))
    repo.assign_labels(["m_a"])
    assert repo.get_label("m_a") == 1
    repo.delete_memory("m_a")
    assert repo.get_memory("m_a") is None
    assert repo.get_label("m_a") is None
