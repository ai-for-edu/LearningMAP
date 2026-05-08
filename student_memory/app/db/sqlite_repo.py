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


@dataclass(frozen=True)
class NoteRecord:
    id: int
    user_id: str
    category: str
    content: str
    content_hash: str
    source_session_id: str | None
    source_event: str
    created_at: int
    updated_at: int
    archived: int


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


def _row_to_note(row: sqlite3.Row) -> NoteRecord:
    return NoteRecord(
        id=row["id"],
        user_id=row["user_id"],
        category=row["category"],
        content=row["content"],
        content_hash=row["content_hash"],
        source_session_id=row["source_session_id"],
        source_event=row["source_event"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived=row["archived"],
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
    # notes (L2 主观笔记)
    # ------------------------------------------------------------------
    NOTES_ACTIVE_CAP = 30

    def add_note(
        self,
        *,
        user_id: str,
        category: str,
        content: str,
        content_hash: str,
        source_session_id: str | None,
        source_event: str,
        now_ts: int,
    ) -> int:
        """插入一条 active note，触发 LRU 淘汰，返回新 note id。

        category 必须是 'misconception'/'preference'/'style'/'goal' 之一
        (SQL CHECK 强制). content/content_hash 由调用方校验长度与脱敏.
        """
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO notes (user_id, category, content, content_hash, "
                "source_session_id, source_event, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    category,
                    content,
                    content_hash,
                    source_session_id,
                    source_event,
                    now_ts,
                    now_ts,
                ),
            )
            new_id = int(cur.lastrowid)
            # LRU: active 超过上限时 archived=1 所有最老的超额行
            cur.execute(
                "SELECT id FROM notes "
                "WHERE user_id = ? AND category = ? AND archived = 0 "
                "ORDER BY created_at ASC, id ASC",
                (user_id, category),
            )
            ids = [r["id"] for r in cur.fetchall()]
            if len(ids) > self.NOTES_ACTIVE_CAP:
                to_archive = ids[: len(ids) - self.NOTES_ACTIVE_CAP]
                cur.executemany(
                    "UPDATE notes SET archived = 1 WHERE id = ?",
                    [(nid,) for nid in to_archive],
                )
            return new_id

    def get_note(self, note_id: int) -> NoteRecord | None:
        cur = self._conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        return _row_to_note(row) if row else None

    def list_active_notes(
        self,
        *,
        user_id: str,
        category: str,
        limit: int = 30,
    ) -> list[NoteRecord]:
        cur = self._conn.execute(
            "SELECT * FROM notes "
            "WHERE user_id = ? AND category = ? AND archived = 0 "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, category, limit),
        )
        return [_row_to_note(r) for r in cur.fetchall()]

    def update_note(
        self,
        *,
        note_id: int,
        content: str,
        content_hash: str,
        now_ts: int,
    ) -> bool:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE notes SET content = ?, content_hash = ?, updated_at = ? "
                "WHERE id = ? AND archived = 0",
                (content, content_hash, now_ts, note_id),
            )
            return cur.rowcount == 1

    def archive_note(self, note_id: int) -> bool:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE notes SET archived = 1 WHERE id = ? AND archived = 0",
                (note_id,),
            )
            return cur.rowcount == 1

    def hash_exists_active(self, user_id: str, content_hash: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM notes "
            "WHERE user_id = ? AND content_hash = ? AND archived = 0 LIMIT 1",
            (user_id, content_hash),
        )
        return cur.fetchone() is not None

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
        sql = "SELECT * FROM memories WHERE user_id = ? ORDER BY timestamp DESC"
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
        half_life_days: float = 30.0,
    ) -> list[dict]:
        """Aggregate mastery per subtopic for review-flow.

        Returns one row per (subtopic, topic) with:
          - last_seen_at: max timestamp
          - session_count: distinct sessions touched
          - total_questions / total_correct: across all sessions
          - last_accuracy: accuracy of the most recent session
          - weighted_accuracy: time-decay-weighted average across all
            sessions for this subtopic. Each session's accuracy is
            weighted by question_count * exp(-age_days * ln2 / half_life).
            None if no session had a non-null accuracy.

        Sorted by last_seen_at DESC. Rows with NULL subtopic are excluded.
        Fails closed: returns [] for unknown user_id (no exception).
        """
        import math
        from datetime import timedelta

        from app.utils.time import format_timestamp, parse_timestamp, utcnow

        # Fetch the full row set (no GROUP BY) so we can do per-session
        # time-weighting in Python. SQLite has no native exp().
        sql_parts = [
            """
            SELECT
                subtopic, topic, timestamp, session_id,
                question_count, correct_count, accuracy
            FROM memories
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

        sql = "\n".join(sql_parts)
        rows = self._conn.execute(sql, params).fetchall()

        # Group in Python by (subtopic, topic).
        groups: dict[tuple[str, str | None], list] = {}
        for r in rows:
            key = (r["subtopic"], r["topic"])
            groups.setdefault(key, []).append(r)

        now = utcnow()
        ln2 = math.log(2.0)
        out: list[dict] = []
        for (subtopic, t), grp in groups.items():
            sessions = {r["session_id"] for r in grp}
            if len(sessions) < min_seen:
                continue
            # Sort newest-first for last_accuracy and last_seen_at picks.
            grp_sorted = sorted(grp, key=lambda r: r["timestamp"], reverse=True)
            last_seen_at = grp_sorted[0]["timestamp"]
            last_accuracy = grp_sorted[0]["accuracy"]
            total_q = sum((r["question_count"] or 0) for r in grp)
            total_c = sum((r["correct_count"] or 0) for r in grp)

            # Weighted accuracy across all rows with non-null accuracy.
            wsum = 0.0
            wnum = 0.0
            for r in grp:
                acc = r["accuracy"]
                if acc is None:
                    continue
                ts = parse_timestamp(r["timestamp"])
                if ts is None:
                    continue
                age_days = max((now - ts).total_seconds() / 86400.0, 0.0)
                qc = r["question_count"] or 1
                w = qc * math.exp(-age_days * ln2 / max(half_life_days, 1e-6))
                wnum += float(acc) * w
                wsum += w
            weighted_accuracy = (wnum / wsum) if wsum > 0 else None

            out.append(
                {
                    "subtopic": subtopic,
                    "topic": t,
                    "last_seen_at": last_seen_at,
                    "session_count": len(sessions),
                    "total_questions": total_q,
                    "total_correct": total_c,
                    "last_accuracy": last_accuracy,
                    "weighted_accuracy": weighted_accuracy,
                }
            )

        out.sort(key=lambda d: d["last_seen_at"], reverse=True)
        return out

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
        cur = self._conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    # convenience for tests / rebuild
    def all_records(self) -> list[MemoryRecord]:
        return list(self.iter_all())

    def insert_records_iter(self, recs: Iterable[MemoryRecord]) -> int:
        batch = list(recs)
        self.insert_memories(batch)
        return len(batch)
