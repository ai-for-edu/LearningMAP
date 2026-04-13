"""Real local embedding backend backed by ``sentence-transformers``.

CRITICAL: this module must NOT import ``sentence_transformers`` or
``torch`` at module load time. Importing ``app.embedding.local_embedder``
is allowed in unit-test environments that do not have the ``ml`` extra
installed; the heavy import is delayed until the first ``encode()`` call.

Install the model dependencies with::

    uv sync --extra ml
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from app.embedding.base import Embedder


class BgeEmbedder(Embedder):
    """SentenceTransformer backend, lazy-loaded.

    The default model is ``BAAI/bge-m3`` (1024-dim, multilingual). The
    fallback ``BAAI/bge-small-zh-v1.5`` is 512-dim. Pass the configured
    name through ``Settings.embedding_model``; pass the matching
    dimension through ``Settings.embedding_dim`` because we do not import
    the model just to read its dimension.
    """

    def __init__(
        self,
        model_name: str,
        dimension: int,
        device: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._dim = dimension
        self._device = device
        self._model: Any | None = None

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is not installed. Run "
                    "`uv sync --extra ml` or set SM_USE_FAKE_EMBEDDER=1."
                ) from exc
            self._model = SentenceTransformer(self._model_name, device=self._device)
        return self._model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if len(texts) == 0:
            return np.zeros((0, self._dim), dtype=np.float32)
        model = self._load()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        arr = np.asarray(vectors, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.shape[1] != self._dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {self._dim}, "
                f"got {arr.shape[1]} from model {self._model_name}"
            )
        return arr
