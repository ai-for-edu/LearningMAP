"""Notes-only hnswlib index wrapper.

Stores embeddings of L2 notes in a separate index file (``notes_hnsw.bin``)
so the existing ``memories`` index is untouched. Index labels are exactly
the SQLite ``notes.id`` values (no separate label table needed since note
ids are already monotonic integers managed by SQLite).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import hnswlib
import numpy as np

from app.embedding.base import Embedder


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
        if not self._path.exists():
            return False
        with self._lock:
            self._index.load_index(str(self._path), max_elements=self._loaded_max)
            self._index.set_ef(50)
        return True

    # ----- mutate ----------------------------------------------------
    def add(self, *, note_id: int, text: str) -> None:
        vec = self._embed(text)
        with self._lock:
            self._index.add_items(vec.reshape(1, -1), np.array([note_id]))

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
