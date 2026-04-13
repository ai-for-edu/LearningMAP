"""End-to-end ingestion: parse → normalize → embed → SQL + hnsw.

The ``Ingestor`` is the only class that touches the SqliteRepo, the
Embedder, and the HnswIndex together. It owns the transaction boundary:
SQL writes (memory rows + label assignment) commit first, hnsw add
follows, then ``index.save()``. If the hnsw add raises after the SQL
commit, the orphan label rows are detected at startup and the index is
rebuilt from SQLite (the recovery path from ``ARCHITECTURE.md`` §16).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.logging import get_logger
from app.db.sqlite_repo import MemoryRecord, SqliteRepo
from app.embedding.base import Embedder
from app.index.hnsw_index import HnswIndex
from app.memory.normalizer import MemoryUnit, normalize
from app.memory.parser import IngestValidationError, parse_session
from app.memory.retrieval_text import build_retrieval_text

logger = get_logger(__name__)


class DuplicateSessionError(ValueError):
    """Raised when ``ingest`` receives a session_id that already exists.

    Mapped to HTTP 409 at the API layer.
    """


@dataclass(frozen=True)
class IngestResult:
    session_id: str
    memory_count: int


def _unit_to_record(unit: MemoryUnit, retrieval_text: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=unit.memory_id,
        user_id=unit.user_id,
        session_id=unit.session_id,
        memory_type=unit.memory_type,
        topic=unit.topic,
        subtopic=unit.subtopic,
        timestamp=unit.timestamp,
        retrieval_text=retrieval_text,
        raw_json=unit.raw_json,
        question_count=unit.question_count,
        correct_count=unit.correct_count,
        accuracy=unit.accuracy,
        error_types=", ".join(unit.error_types) if unit.error_types else None,
    )


class Ingestor:
    def __init__(
        self,
        repo: SqliteRepo,
        embedder: Embedder,
        index: HnswIndex,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._index = index

    def ingest(self, payload: dict) -> IngestResult:
        session = parse_session(payload)
        units = normalize(session)
        if not units:
            raise IngestValidationError("session normalized to zero memory units")

        texts = [build_retrieval_text(u) for u in units]
        records = [_unit_to_record(u, t) for u, t in zip(units, texts)]
        memory_ids = [r.memory_id for r in records]

        # Single SQL transaction: dedup row + memory rows + label rows.
        # The PK on ingested_sessions gives race-free duplicate detection;
        # there is no check-then-insert window. Atomicity is the entire
        # reason ``hnsw_labels`` lives in SQLite.
        try:
            labels = self._repo.insert_session_atomic(
                user_id=session.user_id,
                session_id=session.session_id,
                records=records,
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateSessionError(
                f"session_id already ingested: {session.session_id}"
            ) from exc

        # Compute embeddings outside the SQL lock — encode() may be slow
        # for the real model but is pure compute, no shared state.
        vectors = self._embedder.encode(texts)
        if vectors.shape[0] != len(records):
            raise RuntimeError(
                f"embedder returned wrong number of rows: "
                f"{vectors.shape[0]} vs {len(records)}"
            )

        # Add to hnsw with the labels we already committed to SQLite.
        self._index.add_batch(memory_ids, vectors, labels=labels)
        try:
            self._index.save()
        except Exception:  # pragma: no cover - best-effort persistence
            logger.exception("failed to persist hnsw index after ingest")

        # Persist the embedding model name so a future model swap forces
        # a rebuild instead of mixing incompatible vectors.
        self._repo.set_meta("embedding_model", self._embedder.model_name)

        logger.info(
            "ingested session %s with %d memory units", session.session_id, len(units)
        )
        return IngestResult(session_id=session.session_id, memory_count=len(units))
