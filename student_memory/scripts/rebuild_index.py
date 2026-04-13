"""Rebuild the hnsw index from SQLite.

Run this after:

- Changing ``SM_EMBEDDING_MODEL`` (vectors from different models are not
  interchangeable — the service will also detect this on startup, but
  running the script makes the rebuild explicit and offline-safe).
- Suspecting drift between SQLite and the on-disk index.
- Restoring SQLite from a backup that has no matching ``hnsw.bin``.

The script honors the same ``SM_*`` environment variables as the
service, so a ``.env`` file or shell exports are picked up automatically.
It must NOT import ``app.api`` or FastAPI — keeps the offline tool free
of HTTP-layer dependencies.

Usage::

    cd student_memory
    uv run python -m scripts.rebuild_index
"""

from __future__ import annotations

import sys

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger
from app.db.sqlite_repo import SqliteRepo
from app.embedding.factory import build_embedder
from app.index.hnsw_index import HnswIndex
from app.index.label_store import SqliteLabelStore

logger = get_logger(__name__)


def rebuild(settings: Settings) -> int:
    """Rebuild the index file at ``settings.index_path`` from SQLite.

    Returns the number of records that were re-indexed. Suitable for
    direct unit testing — no global state, no ``sys.exit``.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    repo = SqliteRepo(settings.sqlite_path)
    try:
        embedder = build_embedder(settings)
        index = HnswIndex(
            dim=settings.embedding_dim,
            index_path=settings.index_path,
            label_store=SqliteLabelStore(repo),
            max_elements=settings.hnsw_max_elements,
            ef_construction=settings.hnsw_ef_construction,
            m=settings.hnsw_m,
            ef_search=settings.hnsw_ef_search,
        )
        index.load_or_init()

        sql_count = repo.count_memories()
        logger.info(
            "rebuilding hnsw index from %d memory rows (model=%s, path=%s)",
            sql_count,
            settings.embedding_model,
            settings.index_path,
        )
        total = index.rebuild_from(repo.iter_all(), embedder)
        # Update the embedding-model fingerprint so the service does not
        # trigger another rebuild on the next startup.
        repo.set_meta("embedding_model", settings.embedding_model)
        logger.info(
            "rebuild complete: %d records, model=%s",
            total,
            settings.embedding_model,
        )
        return total
    finally:
        repo.close()


def main() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    rebuild(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
