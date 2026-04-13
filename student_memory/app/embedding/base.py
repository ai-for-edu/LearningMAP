"""``Embedder`` abstract base class.

The contract documented here is the seam between the embedding layer and
every consumer (the ingestor, the retriever, the hnswlib wrapper, and the
test fixtures). Pin it once, here, and never bend it.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence

import numpy as np


class Embedder(abc.ABC):
    """Abstract embedding backend.

    Implementations MUST honor the following invariants:

    1. ``encode(texts)`` returns a 2-D ``numpy.ndarray`` of shape
       ``(len(texts), self.dimension)`` and dtype ``float32``.
    2. Every row is L2-normalized to unit length within ``1e-5``. This
       guarantees that cosine similarity equals the dot product, which is
       what hnswlib's ``space="cosine"`` and the reranker both assume.
    3. ``self.dimension`` is fixed for the lifetime of the instance.
    4. Encoding the empty list returns an array of shape
       ``(0, self.dimension)``, NOT an exception.
    5. ``self.model_name`` is recorded alongside the persisted hnsw index
       so a model swap triggers an index rebuild instead of producing
       silently corrupted similarities (see ``CLAUDE.md`` §11).
    """

    @property
    @abc.abstractmethod
    def dimension(self) -> int: ...

    @property
    @abc.abstractmethod
    def model_name(self) -> str: ...

    @abc.abstractmethod
    def encode(self, texts: Sequence[str]) -> np.ndarray: ...
