"""Embedding backend abstraction.

Only the ``Embedder`` ABC is re-exported here so that ``import
app.embedding`` does NOT pull in heavy ML dependencies (torch,
sentence-transformers). The real backend lives in
``app.embedding.local_embedder`` and is imported lazily by ``app.main``.
"""

from app.embedding.base import Embedder

__all__ = ["Embedder"]
