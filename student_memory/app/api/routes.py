"""HTTP routes and lifespan wiring.

The router is mounted from ``app.main.create_app`` via ``wire_app`` so
that all heavy singletons live on ``app.state`` and can be replaced via
FastAPI dependency overrides in tests.

The dependency overrides are wired through three thin getter functions
(``get_ingestor``, ``get_retriever``, ``get_settings_dep``) so that
``test_api.py`` can swap them for fake-embedder backed instances without
touching ``app.state`` directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status

from app.api.schemas import (
    HealthResponse,
    IngestRequest,
    IngestResponse,
    RetrievedMemory,
    RetrieveRequest,
    RetrieveResponse,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.sqlite_repo import SqliteRepo
from app.embedding.factory import build_embedder
from app.index.hnsw_index import HnswIndex
from app.index.label_store import SqliteLabelStore
from app.memory.ingestor import DuplicateSessionError, Ingestor
from app.memory.parser import IngestValidationError
from app.memory.reranker import RerankConfig
from app.memory.retriever import Retriever

logger = get_logger(__name__)

router = APIRouter()


# ----------------------------------------------------------------------
# Dependency getters (overridable in tests)
# ----------------------------------------------------------------------
def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_ingestor(request: Request) -> Ingestor:
    return request.app.state.ingestor


def get_retriever(request: Request) -> Retriever:
    return request.app.state.retriever


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post("/ingest", response_model=IngestResponse)
def ingest(
    payload: IngestRequest,
    ingestor: Ingestor = Depends(get_ingestor),
) -> IngestResponse:
    try:
        result = ingestor.ingest(payload.model_dump(exclude_none=True))
    except IngestValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except DuplicateSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return IngestResponse(
        status="ok", session_id=result.session_id, memory_count=result.memory_count
    )


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve(
    payload: RetrieveRequest,
    retriever: Retriever = Depends(get_retriever),
) -> RetrieveResponse:
    try:
        resp = retriever.retrieve(
            user_id=payload.user_id,
            query=payload.query,
            top_k=payload.top_k,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return RetrieveResponse(
        query=resp.query,
        results=[
            RetrievedMemory(
                memory_id=r.memory_id,
                topic=r.topic,
                subtopic=r.subtopic,
                timestamp=r.timestamp,
                sim_score=r.sim_score,
                recency_score=r.recency_score,
                match_score=r.match_score,
                final_score=r.final_score,
                retrieval_text=r.retrieval_text,
            )
            for r in resp.results
        ],
        context_text=resp.context_text,
    )


# ----------------------------------------------------------------------
# Lifespan wiring
# ----------------------------------------------------------------------
def wire_app(app: FastAPI, settings: Settings) -> None:
    """Build the long-lived singletons and mount the router.

    Idempotent: if singletons are already on ``app.state`` (e.g. tests
    injected them), reuse them. The router is mounted unconditionally.
    """
    if not getattr(app.state, "_router_mounted", False):
        app.include_router(router)
        app.state._router_mounted = True

    # Tests may pre-stash a repo / index / embedder. Honor that.
    if not hasattr(app.state, "repo"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.repo = SqliteRepo(settings.sqlite_path)

    if not hasattr(app.state, "embedder"):
        app.state.embedder = build_embedder(settings)

    if not hasattr(app.state, "index"):
        index = HnswIndex(
            dim=settings.embedding_dim,
            index_path=settings.index_path,
            label_store=SqliteLabelStore(app.state.repo),
            max_elements=settings.hnsw_max_elements,
            ef_construction=settings.hnsw_ef_construction,
            m=settings.hnsw_m,
            ef_search=settings.hnsw_ef_search,
        )
        index.load_or_init()
        # Recovery: if SQLite has rows but the index is empty (or the
        # index was missing), rebuild from SQLite. This is the
        # ARCHITECTURE.md §16 recovery path.
        sql_count = app.state.repo.count_memories()
        if sql_count > 0 and index.count == 0:
            logger.info(
                "hnsw index empty but SQLite has %d records — rebuilding", sql_count
            )
            index.rebuild_from(app.state.repo.iter_all(), app.state.embedder)
        # Embedding model fingerprint check.
        stored_model = app.state.repo.get_meta("embedding_model")
        if (
            stored_model is not None
            and stored_model != settings.embedding_model
            and sql_count > 0
        ):
            logger.warning(
                "embedding model changed from %s to %s — forcing index rebuild",
                stored_model,
                settings.embedding_model,
            )
            index.rebuild_from(app.state.repo.iter_all(), app.state.embedder)
            app.state.repo.set_meta("embedding_model", settings.embedding_model)
        app.state.index = index

    if not hasattr(app.state, "ingestor"):
        app.state.ingestor = Ingestor(
            app.state.repo, app.state.embedder, app.state.index
        )

    if not hasattr(app.state, "retriever"):
        app.state.retriever = Retriever(
            app.state.repo,
            app.state.embedder,
            app.state.index,
            RerankConfig.from_settings(settings),
            recall_k=settings.recall_k,
            top_k_default=settings.top_k,
        )
