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

The L2 agent-memory tables added by PR-1 of the hybrid agent memory
spec (``docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md``):
``notes`` (4-category subjective notes with LRU eviction), ``notes_history``
(audit log of reviewer decisions including NOOP/REJECT), ``progress_snapshots``
(per-topic milestones), ``mastery_curve`` (KP-level time series, ≤10
samples per KP), and ``op_errors`` (operational error tracking with 30-day
retention pruned at startup).
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

-- L2 主观笔记 (4 category) ----------------------------------------------
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('misconception','preference','style','goal')),
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_session_id TEXT,
    source_event TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notes_user_active
    ON notes(user_id, category, created_at DESC) WHERE archived = 0;
CREATE INDEX IF NOT EXISTS idx_notes_user_hash
    ON notes(user_id, content_hash) WHERE archived = 0;
-- Race-free hash dedup: two concurrent POST /notes for the same
-- (user, content) cannot both commit. The application catches the
-- IntegrityError and returns 409 + REJECT history.
CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_user_hash_active_unique
    ON notes(user_id, content_hash) WHERE archived = 0;

-- 审计: 每个 reviewer 决策 (含 NOOP/REJECT) 都留痕 -----------------------
CREATE TABLE IF NOT EXISTS notes_history (
    id INTEGER PRIMARY KEY,
    note_id INTEGER,
    event TEXT NOT NULL CHECK(event IN ('ADD','UPDATE','DELETE','NOOP','REJECT')),
    old_content TEXT,
    new_content TEXT,
    reason TEXT NOT NULL,
    source_session_id TEXT,
    source_event TEXT,
    ts INTEGER NOT NULL,
    FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_history_note
    ON notes_history(note_id, ts);
CREATE INDEX IF NOT EXISTS idx_history_event
    ON notes_history(event, ts);

-- 主题级里程碑 ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS progress_snapshots (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('in_progress','mastered','abandoned')),
    started_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    mastery_overall REAL NOT NULL,
    last_session_id TEXT,
    UNIQUE(user_id, topic)
);
CREATE INDEX IF NOT EXISTS idx_progress_user_recent
    ON progress_snapshots(user_id, last_seen_at DESC);

-- KP 级时间序列 (每 KP 上限 10 采样) ------------------------------------
CREATE TABLE IF NOT EXISTS mastery_curve (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    knowledge_point TEXT NOT NULL,
    score REAL NOT NULL,
    ts INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('quiz','review','lesson_completion'))
);
CREATE INDEX IF NOT EXISTS idx_curve_user_kp
    ON mastery_curve(user_id, topic, knowledge_point, ts);

-- 错误追踪 (30 天 retention, 启动时 prune) ------------------------------
CREATE TABLE IF NOT EXISTS op_errors (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('main_app','memory_service')),
    user_id TEXT,
    session_id TEXT,
    correlation_id TEXT,
    op TEXT NOT NULL,
    status TEXT NOT NULL,
    error_class TEXT,
    error_msg TEXT,
    payload_excerpt TEXT
);
CREATE INDEX IF NOT EXISTS idx_op_errors_user_ts
    ON op_errors(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_op_errors_corr
    ON op_errors(correlation_id);
CREATE INDEX IF NOT EXISTS idx_op_errors_op_ts
    ON op_errors(op, ts DESC);
CREATE INDEX IF NOT EXISTS idx_op_errors_user_op_ts
    ON op_errors(user_id, op, ts DESC);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotently create tables, indexes, and turn on FK enforcement."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
