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


class TestAggregateMastery:
    """Aggregation queries for review-flow."""

    def test_empty_db_returns_empty_list(self, repo: SqliteRepo) -> None:
        assert repo.aggregate_mastery("u_001") == []

    def test_groups_by_subtopic_with_session_count(self, repo: SqliteRepo) -> None:
        # Two sessions on Discriminant, one on Factoring
        repo.insert_memories(
            [
                _make_record("m1", session_id="s1", subtopic="Discriminant",
                             timestamp="2026-04-01T10:00:00Z"),
                _make_record("m2", session_id="s2", subtopic="Discriminant",
                             timestamp="2026-04-15T10:00:00Z"),
                _make_record("m3", session_id="s3", subtopic="Factoring",
                             timestamp="2026-04-10T10:00:00Z"),
            ]
        )
        items = repo.aggregate_mastery("u_001")

        # Sorted by last_seen_at desc → Discriminant (Apr 15) first
        assert [i["subtopic"] for i in items] == ["Discriminant", "Factoring"]

        disc = items[0]
        assert disc["session_count"] == 2
        assert disc["last_seen_at"] == "2026-04-15T10:00:00Z"
        assert disc["total_questions"] == 4   # 2 questions per record
        assert disc["total_correct"] == 2
        assert disc["last_accuracy"] == 0.5   # from _make_record default

    def test_filters_other_users(self, repo: SqliteRepo) -> None:
        repo.insert_memories([
            _make_record("m1", user_id="u_001", subtopic="A"),
            _make_record("m2", user_id="u_002", subtopic="A"),
        ])
        items = repo.aggregate_mastery("u_001")
        assert len(items) == 1

    def test_excludes_null_subtopic_rows(self, repo: SqliteRepo) -> None:
        repo.insert_memories([
            _make_record("m1", subtopic="A"),
            _make_record("m2", subtopic=None),  # goal-level row, must skip
        ])
        items = repo.aggregate_mastery("u_001")
        assert {i["subtopic"] for i in items} == {"A"}

    def test_topic_filter(self, repo: SqliteRepo) -> None:
        repo.insert_memories([
            _make_record("m1", topic="Algebra", subtopic="A"),
            _make_record("m2", topic="Geometry", subtopic="B"),
        ])
        items = repo.aggregate_mastery("u_001", topic="Algebra")
        assert {i["subtopic"] for i in items} == {"A"}

    def test_min_seen_filter(self, repo: SqliteRepo) -> None:
        # Subtopic A: 1 session; B: 2 sessions
        repo.insert_memories([
            _make_record("m1", session_id="s1", subtopic="A"),
            _make_record("m2", session_id="s1", subtopic="B"),
            _make_record("m3", session_id="s2", subtopic="B"),
        ])
        items = repo.aggregate_mastery("u_001", min_seen=2)
        assert {i["subtopic"] for i in items} == {"B"}

    def test_since_days_filter(self, repo: SqliteRepo) -> None:
        # 100 days old → excluded; 1 day old → included
        from datetime import timedelta
        from app.utils.time import format_timestamp, utcnow
        old_ts = format_timestamp(utcnow() - timedelta(days=100))
        new_ts = format_timestamp(utcnow() - timedelta(days=1))
        repo.insert_memories([
            _make_record("m1", subtopic="Old", timestamp=old_ts),
            _make_record("m2", subtopic="New", timestamp=new_ts),
        ])
        items = repo.aggregate_mastery("u_001", since_days=30)
        assert {i["subtopic"] for i in items} == {"New"}

    def test_last_accuracy_uses_most_recent_session(self, repo: SqliteRepo) -> None:
        # Insert two records same subtopic; later one should win
        rec_old = MemoryRecord(
            memory_id="m1", user_id="u_001", session_id="s1",
            memory_type="subtopic", topic="T", subtopic="A",
            timestamp="2026-04-01T10:00:00Z",
            retrieval_text="x", raw_json="{}",
            question_count=2, correct_count=1, accuracy=0.5,
            error_types=None,
        )
        rec_new = MemoryRecord(
            memory_id="m2", user_id="u_001", session_id="s2",
            memory_type="subtopic", topic="T", subtopic="A",
            timestamp="2026-04-20T10:00:00Z",
            retrieval_text="y", raw_json="{}",
            question_count=2, correct_count=2, accuracy=1.0,
            error_types=None,
        )
        repo.insert_memories([rec_old, rec_new])
        items = repo.aggregate_mastery("u_001")
        assert items[0]["last_accuracy"] == 1.0

    def test_weighted_accuracy_recent_wins_over_old(
        self, repo: SqliteRepo
    ) -> None:
        # 100% three months ago + 0% today (with the same question_count).
        # half_life=30d → old session weight = 2^(-90/30) = 0.125;
        # new session weight = 1.0. Weighted: 0.125*1 / (0.125+1) ≈ 0.111.
        from datetime import timedelta

        from app.utils.time import format_timestamp, utcnow

        now = utcnow()
        old_ts = format_timestamp(now - timedelta(days=90))
        new_ts = format_timestamp(now - timedelta(seconds=10))
        rec_old = MemoryRecord(
            memory_id="m1", user_id="u_001", session_id="s1",
            memory_type="subtopic", topic="T", subtopic="A",
            timestamp=old_ts, retrieval_text="x", raw_json="{}",
            question_count=2, correct_count=2, accuracy=1.0,
            error_types=None,
        )
        rec_new = MemoryRecord(
            memory_id="m2", user_id="u_001", session_id="s2",
            memory_type="subtopic", topic="T", subtopic="A",
            timestamp=new_ts, retrieval_text="y", raw_json="{}",
            question_count=2, correct_count=0, accuracy=0.0,
            error_types=None,
        )
        repo.insert_memories([rec_old, rec_new])
        items = repo.aggregate_mastery("u_001", half_life_days=30.0)
        assert items[0]["last_accuracy"] == 0.0  # most recent
        # weighted should be much closer to the new (low) value than to old
        wa = items[0]["weighted_accuracy"]
        assert wa is not None
        assert 0.05 < wa < 0.15

    def test_weighted_accuracy_blends_close_in_time(
        self, repo: SqliteRepo
    ) -> None:
        # Two sessions same day → roughly equal weights → near average
        from datetime import timedelta

        from app.utils.time import format_timestamp, utcnow

        now = utcnow()
        ts1 = format_timestamp(now - timedelta(hours=2))
        ts2 = format_timestamp(now - timedelta(hours=1))
        repo.insert_memories([
            MemoryRecord(
                memory_id="m1", user_id="u_001", session_id="s1",
                memory_type="subtopic", topic="T", subtopic="A",
                timestamp=ts1, retrieval_text="x", raw_json="{}",
                question_count=2, correct_count=1, accuracy=0.5,
                error_types=None,
            ),
            MemoryRecord(
                memory_id="m2", user_id="u_001", session_id="s2",
                memory_type="subtopic", topic="T", subtopic="A",
                timestamp=ts2, retrieval_text="y", raw_json="{}",
                question_count=2, correct_count=2, accuracy=1.0,
                error_types=None,
            ),
        ])
        items = repo.aggregate_mastery("u_001", half_life_days=30.0)
        wa = items[0]["weighted_accuracy"]
        # Both sessions have nearly equal weight, average ≈ 0.75
        assert wa is not None
        assert 0.74 < wa < 0.76

    def test_weighted_accuracy_none_when_all_accuracies_null(
        self, repo: SqliteRepo
    ) -> None:
        rec = MemoryRecord(
            memory_id="m1", user_id="u_001", session_id="s1",
            memory_type="subtopic", topic="T", subtopic="A",
            timestamp="2026-04-01T10:00:00Z",
            retrieval_text="x", raw_json="{}",
            question_count=0, correct_count=0, accuracy=None,
            error_types=None,
        )
        repo.insert_memories([rec])
        items = repo.aggregate_mastery("u_001")
        assert items[0]["weighted_accuracy"] is None
