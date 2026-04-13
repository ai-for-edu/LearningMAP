"""Embedder factory: pick a backend from a ``Settings`` instance.

Lives outside ``app.api.routes`` so non-HTTP entry points (CLIs, the
``scripts/rebuild_index.py`` runner, ad-hoc REPL sessions) can build an
embedder without pulling FastAPI into their import graph.
"""

from __future__ import annotations

from app.core.config import Settings
from app.embedding.base import Embedder


def build_embedder(settings: Settings) -> Embedder:
    """Pick the embedder backend from settings.

    The fake embedder is selected when ``SM_USE_FAKE_EMBEDDER=1``;
    otherwise the real bge backend is constructed. ``BgeEmbedder`` is
    itself lazy-loaded so this call does NOT pull torch unless the bge
    branch actually fires.
    """
    if settings.use_fake_embedder:
        from app.embedding.fake_embedder import FakeEmbedder

        return FakeEmbedder(
            dimension=settings.embedding_dim,
            model_name=settings.embedding_model,
        )
    from app.embedding.local_embedder import BgeEmbedder

    return BgeEmbedder(
        model_name=settings.embedding_model,
        dimension=settings.embedding_dim,
        device=settings.embedding_device,
    )
