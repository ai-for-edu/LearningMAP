"""Notes-only hnswlib index wrapper.

Stores embeddings of L2 notes in a separate index file (``notes_hnsw.bin``)
so the existing ``memories`` index is untouched. Index labels are exactly
the SQLite ``notes.id`` values (no separate label table needed since note
ids are already monotonic integers managed by SQLite).

The index is treated as a derived cache: SQLite is the source of truth.
``load()`` swallows corruption / missing-file errors and falls back to an
empty index; the caller (``wire_app``) is then responsible for noticing
``count == 0`` while SQLite has rows and triggering ``rebuild_from``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import hnswlib
import numpy as np

from app.core.logging import get_logger
from app.embedding.base import Embedder

logger = get_logger(__name__)


@dataclass(frozen=True)
class NoteHit:
    note_id: int
    sim_score: float


class NotesIndex:
    """Thin wrapper around a hnswlib index keyed by note_id.

    Designed to be (re)built from SQLite — losing the on-disk file is
    not catastrophic, just slow.
    """

    def __init__(
        self,
        *,
        index_path: Path,
        embedder: Embedder,
        dimension: int,
        max_elements: int = 10000,
        ef_construction: int = 200,
        M: int = 16,
    ) -> None:
        self._path = index_path
        self._embedder = embedder
        self._dim = dimension
        self._lock = threading.Lock()
        self._index = hnswlib.Index(space="cosine", dim=dimension)
        self._index.init_index(
            max_elements=max_elements,
            ef_construction=ef_construction,
            M=M,
        )
        self._index.set_ef(50)
        self._loaded_max = max_elements

    # ----- IO --------------------------------------------------------
    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._index.save_index(str(self._path))

    def load(self) -> bool:
        """Load from disk if possible; on any failure, leave the index in
        a fresh empty state and return False. Callers (``wire_app``)
        should treat False as "rebuild from SQLite if rows exist".
        """
        if not self._path.exists():
            return False
        try:
            with self._lock:
                self._index.load_index(str(self._path), max_elements=self._loaded_max)
                self._index.set_ef(50)
            return True
        except (RuntimeError, OSError) as exc:
            logger.warning(
                "failed to load notes hnsw index from %s (%s); "
                "reinitializing empty — caller should rebuild from SQLite",
                self._path,
                exc,
            )
            with self._lock:
                self._index = hnswlib.Index(space="cosine", dim=self._dim)
                self._index.init_index(
                    max_elements=self._loaded_max,
                    ef_construction=200,
                    M=16,
                )
                self._index.set_ef(50)
            return False

    @property
    def count(self) -> int:
        return int(self._index.get_current_count())

    # ----- mutate ----------------------------------------------------
    def add(self, *, note_id: int, text: str) -> None:
        vec = self._embed(text)
        with self._lock:
            self._ensure_capacity_for(1)
            self._index.add_items(vec.reshape(1, -1), np.array([note_id]))

    def _ensure_capacity_for(self, n: int) -> None:
        """Grow the index when current+n would exceed capacity.

        Mirrors ``HnswIndex.add_batch`` so long-running services don't
        500 once they hit the initial ``max_elements`` cap. hnswlib does
        not currently reuse slots from ``mark_deleted`` calls, so the
        effective capacity also has to absorb deletes/updates.
        """
        current = self._index.get_current_count()
        cap = self._index.get_max_elements()
        if current + n > cap:
            new_cap = max(current + n, cap * 2)
            self._index.resize_index(new_cap)
            self._loaded_max = new_cap

    def update(self, *, note_id: int, text: str) -> None:
        """hnswlib does not support in-place update; mark deleted then re-add."""
        with self._lock:
            try:
                self._index.mark_deleted(note_id)
            except RuntimeError:
                pass
        self.add(note_id=note_id, text=text)

    def remove(self, *, note_id: int) -> None:
        with self._lock:
            try:
                self._index.mark_deleted(note_id)
            except RuntimeError:
                pass

    # ----- recovery --------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._index = hnswlib.Index(space="cosine", dim=self._dim)
            self._index.init_index(
                max_elements=self._loaded_max,
                ef_construction=200,
                M=16,
            )
            self._index.set_ef(50)

    def rebuild_from(
        self,
        notes: Iterable[tuple[int, str]],
        *,
        batch_size: int = 64,
    ) -> int:
        """Re-encode every active note and re-add it to a fresh index.

        Accepts an iterable of ``(note_id, content)`` so the caller can
        stream rows from SQLite without materializing the full list.
        Returns the number of notes added.
        """
        self.reset()
        total = 0
        ids: list[int] = []
        texts: list[str] = []
        for note_id, content in notes:
            ids.append(int(note_id))
            texts.append(content)
            if len(ids) >= batch_size:
                self._add_batch(ids, texts)
                total += len(ids)
                ids.clear()
                texts.clear()
        if ids:
            self._add_batch(ids, texts)
            total += len(ids)
        logger.info("rebuilt notes hnsw index from %d active notes", total)
        return total

    def _add_batch(self, ids: list[int], texts: list[str]) -> None:
        vectors = np.asarray(self._embedder.encode(texts), dtype=np.float32)
        with self._lock:
            self._ensure_capacity_for(len(ids))
            self._index.add_items(vectors, np.asarray(ids, dtype=np.int64))

    # ----- search ----------------------------------------------------
    def search(self, *, query_text: str, top_k: int = 5) -> list[NoteHit]:
        vec = self._embed(query_text)
        with self._lock:
            current_count = self._index.get_current_count()
            if current_count == 0:
                return []
            k = min(top_k, current_count)
            try:
                labels, distances = self._index.knn_query(
                    vec.reshape(1, -1),
                    k=k,
                )
            except RuntimeError:
                # Empty index raises; treat as "no hits".
                return []
        hits = []
        for label, dist in zip(labels[0], distances[0]):
            sim = 1.0 - float(dist)  # cosine distance → similarity
            hits.append(NoteHit(note_id=int(label), sim_score=sim))
        return hits

    # ----- helpers ---------------------------------------------------
    def _embed(self, text: str) -> np.ndarray:
        # Embedder.encode() takes a sequence and returns shape (N, dim).
        return np.asarray(self._embedder.encode([text])[0], dtype=np.float32)
