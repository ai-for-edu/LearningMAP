"""SQLite schema and idempotent migration.

Schema follows ``todo.md`` §6 plus a ``hnsw_labels`` table that stores the
bidirectional ``label ↔ memory_id`` mapping (see ``CLAUDE.md`` §11). Both
tables live in the same SQLite file so ingestion can wrap the memory
insert and the label assignment in one transaction.

There is also a tiny ``index_meta`` key/value table for one piece of
information: the embedding model name that was used the last time the
hnswlib index was populated. On startup we compare it against the
configured model name and force a full rebuild if they disagree —
embeddings from different models are not interchangeable.
"""

from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    topic TEXT,
    subtopic TEXT,
    timestamp TEXT NOT NULL,
    retrieval_text TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    question_count INTEGER,
    correct_count INTEGER,
    accuracy REAL,
    error_types TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_user_time
    ON memories(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_memories_user_topic
    ON memories(user_id, topic);
CREATE INDEX IF NOT EXISTS idx_memories_user_subtopic
    ON memories(user_id, subtopic);
CREATE INDEX IF NOT EXISTS idx_memories_session
    ON memories(session_id);

CREATE TABLE IF NOT EXISTS hnsw_labels (
    label INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT UNIQUE NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hnsw_labels_memory_id
    ON hnsw_labels(memory_id);

CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Race-free session dedup. PRIMARY KEY (user_id, session_id) lets the
-- ingestor INSERT into this table inside the same transaction as the
-- memory rows; a duplicate request fails with sqlite3.IntegrityError
-- instead of slipping through a check-then-insert window.
CREATE TABLE IF NOT EXISTS ingested_sessions (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (user_id, session_id)
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotently create tables, indexes, and turn on FK enforcement."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
