"""SQLite repository — the source of truth for memory metadata.

Single ``threading.Lock`` guards all writes (single-user, coarse lock is
fine). Reads are lock-free; SQLite handles concurrent reads natively.

``insert_session_atomic`` is the only entry point ingestion uses for
writes: it commits the session dedup row, the memory rows, and the
``hnsw_labels`` rows inside one ``BEGIN``/``COMMIT`` block. The session
dedup PK gives race-free duplicate detection; combined with the
single-transaction guarantee this realizes the atomicity contract from
``CLAUDE.md`` §11 ("hnswlib is not canonical").
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.db.migrations import init_db
from app.utils.time import format_timestamp, utcnow


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    user_id: str
    session_id: str
    memory_type: str
    topic: str | None
    subtopic: str | None
    timestamp: str  # ISO-8601 UTC string
    retrieval_text: str
    raw_json: str
    question_count: int | None
    correct_count: int | None
    accuracy: float | None
    error_types: str | None  # comma-joined


_COLUMNS = (
    "memory_id",
    "user_id",
    "session_id",
    "memory_type",
    "topic",
    "subtopic",
    "timestamp",
    "retrieval_text",
    "raw_json",
    "question_count",
    "correct_count",
    "accuracy",
    "error_types",
)


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=row["memory_id"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        memory_type=row["memory_type"],
        topic=row["topic"],
        subtopic=row["subtopic"],
        timestamp=row["timestamp"],
        retrieval_text=row["retrieval_text"],
        raw_json=row["raw_json"],
        question_count=row["question_count"],
        correct_count=row["correct_count"],
        accuracy=row["accuracy"],
        error_types=row["error_types"],
    )


class SqliteRepo:
    """Thin wrapper around a single ``sqlite3.Connection``."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage txns explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        init_db(self._conn)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover - defensive
            pass

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Single explicit BEGIN/COMMIT block, lock-protected."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    _INSERT_MEMORY_SQL = (
        "INSERT INTO memories ("
        + ", ".join(_COLUMNS)
        + ") VALUES ("
        + ", ".join(["?"] * len(_COLUMNS))
        + ")"
    )

    @staticmethod
    def _record_to_row(r: MemoryRecord) -> tuple:
        return (
            r.memory_id,
            r.user_id,
            r.session_id,
            r.memory_type,
            r.topic,
            r.subtopic,
            r.timestamp,
            r.retrieval_text,
            r.raw_json,
            r.question_count,
            r.correct_count,
            r.accuracy,
            r.error_types,
        )

    def _insert_memory_rows(
        self, cur: sqlite3.Cursor, recs: Sequence[MemoryRecord]
    ) -> None:
        cur.executemany(self._INSERT_MEMORY_SQL, [self._record_to_row(r) for r in recs])

    def _assign_label_rows(
        self, cur: sqlite3.Cursor, memory_ids: Sequence[str]
    ) -> list[int]:
        labels: list[int] = []
        for mid in memory_ids:
            cur.execute(
                "INSERT INTO hnsw_labels(memory_id) VALUES (?)",
                (mid,),
            )
            labels.append(int(cur.lastrowid))
        return labels

    def insert_memory(self, rec: MemoryRecord) -> None:
        self.insert_memories([rec])

    def insert_memories(self, recs: Sequence[MemoryRecord]) -> None:
        if not recs:
            return
        with self.transaction() as cur:
            self._insert_memory_rows(cur, recs)

    def assign_labels(self, memory_ids: Sequence[str]) -> list[int]:
        """Insert into ``hnsw_labels`` and return the assigned labels in order.

        Uses AUTOINCREMENT so labels are monotonic and unique even after
        deletes. Hnswlib accepts any int label, contiguous or not.
        """
        if not memory_ids:
            return []
        with self.transaction() as cur:
            return self._assign_label_rows(cur, memory_ids)

    def insert_session_atomic(
        self,
        *,
        user_id: str,
        session_id: str,
        records: Sequence[MemoryRecord],
    ) -> list[int]:
        """Insert a session dedup row, the memory rows, and the hnsw labels
        in a single transaction. Returns the assigned labels in order.

        Raises ``sqlite3.IntegrityError`` if ``(user_id, session_id)`` is
        already present in ``ingested_sessions``. The ingestor catches that
        and re-raises as ``DuplicateSessionError``. This replaces the old
        check-then-insert dedup, which had a race window.
        """
        if not records:
            raise ValueError("records must be non-empty")
        ingested_at = format_timestamp(utcnow())
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO ingested_sessions(user_id, session_id, ingested_at) "
                "VALUES (?, ?, ?)",
                (user_id, session_id, ingested_at),
            )
            self._insert_memory_rows(cur, records)
            return self._assign_label_rows(cur, [r.memory_id for r in records])

    def clear_labels(self) -> None:
        """Wipe the label mapping table. Used by index rebuild."""
        with self.transaction() as cur:
            cur.execute("DELETE FROM hnsw_labels")
            cur.execute("DELETE FROM sqlite_sequence WHERE name='hnsw_labels'")

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO index_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete_memory(self, memory_id: str) -> None:
        with self.transaction() as cur:
            cur.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        cur = self._conn.execute(
            "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
        )
        row = cur.fetchone()
        return _row_to_record(row) if row else None

    def get_memories(self, memory_ids: Sequence[str]) -> list[MemoryRecord]:
        if not memory_ids:
            return []
        placeholders = ", ".join(["?"] * len(memory_ids))
        cur = self._conn.execute(
            f"SELECT * FROM memories WHERE memory_id IN ({placeholders})",
            tuple(memory_ids),
        )
        rows = cur.fetchall()
        # Preserve caller order rather than SQL's arbitrary order.
        by_id = {r["memory_id"]: r for r in rows}
        return [_row_to_record(by_id[mid]) for mid in memory_ids if mid in by_id]

    def list_by_user(
        self, user_id: str, limit: int | None = None
    ) -> list[MemoryRecord]:
        sql = (
            "SELECT * FROM memories WHERE user_id = ? "
            "ORDER BY timestamp DESC"
        )
        params: tuple = (user_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (user_id, limit)
        cur = self._conn.execute(sql, params)
        return [_row_to_record(r) for r in cur.fetchall()]

    def aggregate_mastery(
        self,
        user_id: str,
        *,
        topic: str | None = None,
        since_days: int | None = None,
        min_seen: int = 1,
    ) -> list[dict]:
        """Aggregate mastery per subtopic for review-flow.

        Returns one row per (subtopic, topic) with:
          - last_seen_at: max timestamp
          - session_count: distinct sessions touched
          - total_questions / total_correct: across all sessions
          - last_accuracy: accuracy of the most recent session

        Sorted by last_seen_at DESC. Rows with NULL subtopic are excluded.
        Fails closed: returns [] for unknown user_id (no exception).
        """
        from datetime import timedelta

        from app.utils.time import format_timestamp, utcnow

        sql_parts = [
            """
            SELECT
                subtopic, topic,
                MAX(timestamp) AS last_seen_at,
                COUNT(DISTINCT session_id) AS session_count,
                COALESCE(SUM(question_count), 0) AS total_questions,
                COALESCE(SUM(correct_count), 0) AS total_correct,
                (SELECT accuracy FROM memories m2
                   WHERE m2.user_id = m.user_id
                     AND m2.subtopic = m.subtopic
                   ORDER BY timestamp DESC LIMIT 1) AS last_accuracy
            FROM memories m
            WHERE user_id = ?
              AND subtopic IS NOT NULL
            """
        ]
        params: list = [user_id]

        if topic is not None:
            sql_parts.append("AND topic = ?")
            params.append(topic)

        if since_days is not None:
            cutoff_iso = format_timestamp(utcnow() - timedelta(days=since_days))
            sql_parts.append("AND timestamp >= ?")
            params.append(cutoff_iso)

        sql_parts.append("GROUP BY subtopic, topic")
        sql_parts.append("HAVING session_count >= ?")
        params.append(min_seen)
        sql_parts.append("ORDER BY last_seen_at DESC")

        sql = "\n".join(sql_parts)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "subtopic": row["subtopic"],
                "topic": row["topic"],
                "last_seen_at": row["last_seen_at"],
                "session_count": row["session_count"],
                "total_questions": row["total_questions"],
                "total_correct": row["total_correct"],
                "last_accuracy": row["last_accuracy"],
            }
            for row in rows
        ]

    def iter_all(self) -> Iterator[MemoryRecord]:
        cur = self._conn.execute("SELECT * FROM memories ORDER BY rowid")
        for row in cur:
            yield _row_to_record(row)

    def count_memories(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM memories")
        return int(cur.fetchone()[0])

    def session_exists(self, session_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM memories WHERE session_id = ? LIMIT 1", (session_id,)
        )
        return cur.fetchone() is not None

    # label mapping reads
    def get_label(self, memory_id: str) -> int | None:
        cur = self._conn.execute(
            "SELECT label FROM hnsw_labels WHERE memory_id = ?", (memory_id,)
        )
        row = cur.fetchone()
        return int(row["label"]) if row else None

    def get_memory_id(self, label: int) -> str | None:
        cur = self._conn.execute(
            "SELECT memory_id FROM hnsw_labels WHERE label = ?", (label,)
        )
        row = cur.fetchone()
        return row["memory_id"] if row else None

    def all_labels(self) -> list[tuple[int, str]]:
        cur = self._conn.execute(
            "SELECT label, memory_id FROM hnsw_labels ORDER BY label"
        )
        return [(int(r["label"]), r["memory_id"]) for r in cur.fetchall()]

    def get_meta(self, key: str) -> str | None:
        cur = self._conn.execute(
            "SELECT value FROM index_meta WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return row["value"] if row else None

    # convenience for tests / rebuild
    def all_records(self) -> list[MemoryRecord]:
        return list(self.iter_all())

    def insert_records_iter(self, recs: Iterable[MemoryRecord]) -> int:
        batch = list(recs)
        self.insert_memories(batch)
        return len(batch)
