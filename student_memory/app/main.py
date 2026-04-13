"""FastAPI application factory.

This module is intentionally minimal at import time: it does NOT import
``hnswlib``, ``sentence_transformers``, ``torch``, or any other heavy
dependency at module load. The full service wiring happens inside the
``lifespan`` context manager so that ``import app.main`` stays cheap and
test-friendly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build and tear down the long-lived service singletons.

    Wiring deferred until startup so unit tests that import
    ``app.main`` do not pay for SQLite, hnswlib, or model loads.
    """
    settings: Settings = app.state.settings if hasattr(app.state, "settings") else get_settings()
    app.state.settings = settings
    configure_logging(settings.log_level)
    logger.info(
        "student_memory starting: host=%s port=%s sqlite=%s index=%s model=%s",
        settings.service_host,
        settings.service_port,
        settings.sqlite_path,
        settings.index_path,
        settings.embedding_model,
    )

    # Imports are deferred to keep ``import app.main`` cheap.
    from app.api.routes import wire_app

    wire_app(app, settings)
    try:
        yield
    finally:
        repo = getattr(app.state, "repo", None)
        if repo is not None:
            repo.close()
        index = getattr(app.state, "index", None)
        if index is not None:
            try:
                index.save()
            except Exception:  # pragma: no cover - best-effort shutdown
                logger.exception("failed to save hnsw index on shutdown")
        logger.info("student_memory stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fresh FastAPI app. Tests pass a tmp-scoped ``Settings``."""
    app = FastAPI(
        title="student-memory",
        version="0.1.0",
        description="Local recency-aware semantic retrieval over student learning records.",
        lifespan=lifespan,
    )
    if settings is not None:
        app.state.settings = settings

    # The /health endpoint is registered eagerly so smoke tests that hit
    # ``TestClient(create_app()).get("/health")`` do not depend on the
    # lifespan having run (the FastAPI TestClient does run lifespan by
    # default, but registering eagerly keeps the contract obvious).
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
