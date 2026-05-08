"""Thin wrapper around ``hnswlib.Index``.

Pinned conventions (do not change without updating tests):

- ``space="cosine"``. ``hnswlib.knn_query`` returns cosine **distance** in
  ``[0, 2]``. We convert at this boundary to similarity via
  ``sim_score = 1 - distance``. Nothing outside this module ever sees a
  raw distance — all consumers receive the normalized similarity.
- All vectors fed in are L2-normalized (the ``Embedder`` ABC contract).
- Empty-index search returns ``[]``. ``k`` is clamped to current count.
- Persistent ``label ↔ memory_id`` mapping lives in a ``LabelStore``,
  not in the index file. The hnswlib index file alone is NOT enough to
  reconstruct the service — that's the point of ``CLAUDE.md`` §11
  ("hnswlib is not canonical").
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

import hnswlib
import numpy as np

from app.core.logging import get_logger
from app.embedding.base import Embedder
from app.index.label_store import LabelStore

logger = get_logger(__name__)


class HnswIndex:
    def __init__(
        self,
        dim: int,
        index_path: str | Path,
        label_store: LabelStore,
        *,
        space: str = "cosine",
        max_elements: int = 100_000,
        ef_construction: int = 200,
        m: int = 16,
        ef_search: int = 64,
    ) -> None:
        self._dim = dim
        self._path = Path(index_path)
        self._labels = label_store
        self._space = space
        self._initial_max_elements = max_elements
        self._ef_construction = ef_construction
        self._m = m
        self._ef_search = ef_search
        self._lock = threading.Lock()
        self._index: hnswlib.Index | None = None
        self._loaded = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @property
    def dim(self) -> int:
        return self._dim

    @property
    def count(self) -> int:
        if self._index is None:
            return 0
        return int(self._index.get_current_count())

    def load_or_init(self) -> None:
        """Load the index file if present; otherwise init an empty index.

        Does NOT auto-rebuild from SQLite — the caller (``Retriever``
        startup) is responsible for noticing a missing/empty index and
        calling ``rebuild_from(...)``.
        """
        with self._lock:
            self._index = hnswlib.Index(space=self._space, dim=self._dim)
            if self._path.exists():
                try:
                    self._index.load_index(
                        str(self._path), max_elements=self._initial_max_elements
                    )
                    self._index.set_ef(self._ef_search)
                    self._loaded = True
                    logger.info(
                        "loaded hnsw index from %s (count=%d)",
                        self._path,
                        self._index.get_current_count(),
                    )
                    return
                except (RuntimeError, OSError) as exc:
                    logger.warning(
                        "failed to load hnsw index from %s (%s); reinitializing empty",
                        self._path,
                        exc,
                    )
                    self._index = hnswlib.Index(space=self._space, dim=self._dim)
            self._index.init_index(
                max_elements=self._initial_max_elements,
                ef_construction=self._ef_construction,
                M=self._m,
            )
            self._index.set_ef(self._ef_search)
            self._loaded = True
            logger.info("initialized empty hnsw index at %s", self._path)

    def save(self) -> None:
        if self._index is None:
            return
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._index.save_index(str(self._path))

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def add(self, memory_id: str, vector: np.ndarray, label: int | None = None) -> int:
        labels = self.add_batch(
            [memory_id], vector.reshape(1, -1), [label] if label is not None else None
        )
        return labels[0]

    def add_batch(
        self,
        memory_ids: Sequence[str],
        vectors: np.ndarray,
        labels: Sequence[int] | None = None,
    ) -> list[int]:
        """Add a batch of vectors. Assigns labels via the LabelStore unless
        the caller pre-assigned them (used by ``rebuild_from``).
        """
        if self._index is None:
            raise RuntimeError("HnswIndex.load_or_init() has not been called")
        if len(memory_ids) == 0:
            return []
        if vectors.ndim != 2 or vectors.shape[1] != self._dim:
            raise ValueError(
                f"vectors must have shape (n, {self._dim}), got {vectors.shape}"
            )
        if vectors.shape[0] != len(memory_ids):
            raise ValueError(
                f"vectors and memory_ids length mismatch: "
                f"{vectors.shape[0]} vs {len(memory_ids)}"
            )
        with self._lock:
            assigned = (
                list(labels)
                if labels is not None
                else self._labels.assign_batch(memory_ids)
            )
            current = self._index.get_current_count()
            cap = self._index.get_max_elements()
            needed = current + len(assigned)
            if needed > cap:
                new_cap = max(needed, cap * 2)
                logger.info("resizing hnsw index from %d to %d", cap, new_cap)
                self._index.resize_index(new_cap)
            self._index.add_items(
                np.asarray(vectors, dtype=np.float32),
                np.asarray(assigned, dtype=np.int64),
            )
        return assigned

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]:
        """Return ``[(memory_id, sim_score), ...]`` for the top-``k`` neighbors.

        ``sim_score = 1 - cosine_distance``. Empty index returns ``[]``.
        ``k`` is clamped to ``count``.
        """
        if self._index is None or self.count == 0 or k <= 0:
            return []
        if query.ndim == 1:
            query = query.reshape(1, -1)
        if query.shape[1] != self._dim:
            raise ValueError(
                f"query must have shape (1, {self._dim}), got {query.shape}"
            )
        k = min(k, self.count)
        with self._lock:
            labels, distances = self._index.knn_query(
                np.asarray(query, dtype=np.float32), k=k
            )
        out: list[tuple[str, float]] = []
        for label, dist in zip(labels[0], distances[0]):
            mid = self._labels.memory_id_for(int(label))
            if mid is None:
                # Orphan label — possible if the SQLite row was deleted
                # but the index has not been rebuilt yet. Skip silently;
                # the retriever pre-filter would drop it anyway.
                logger.debug("orphan hnsw label %s with no memory_id mapping", label)
                continue
            out.append((mid, float(1.0 - dist)))
        return out

    # ------------------------------------------------------------------
    # admin / recovery
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Wipe the index and the label mapping. Used by ``rebuild_from``."""
        with self._lock:
            self._labels.clear()
            self._index = hnswlib.Index(space=self._space, dim=self._dim)
            self._index.init_index(
                max_elements=self._initial_max_elements,
                ef_construction=self._ef_construction,
                M=self._m,
            )
            self._index.set_ef(self._ef_search)
            self._loaded = True

    def rebuild_from(
        self,
        records: Iterable,
        embedder: Embedder,
        *,
        batch_size: int = 64,
    ) -> int:
        """Re-encode every record and re-add it to a fresh index.

        ``records`` is an iterable of ``MemoryRecord`` objects (typed
        loosely to avoid a circular import). Used by the recovery path
        from ``ARCHITECTURE.md`` §16.
        """
        self.reset()
        total = 0
        batch_ids: list[str] = []
        batch_text: list[str] = []
        for rec in records:
            batch_ids.append(rec.memory_id)
            batch_text.append(rec.retrieval_text)
            if len(batch_ids) >= batch_size:
                vectors = embedder.encode(batch_text)
                self.add_batch(batch_ids, vectors)
                total += len(batch_ids)
                batch_ids.clear()
                batch_text.clear()
        if batch_ids:
            vectors = embedder.encode(batch_text)
            self.add_batch(batch_ids, vectors)
            total += len(batch_ids)
        self.save()
        logger.info("rebuilt hnsw index from %d records", total)
        return total
