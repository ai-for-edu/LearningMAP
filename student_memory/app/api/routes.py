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

import hashlib
import re
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status

from app.api.schemas import (
    ErrorLog,
    ErrorLogResponse,
    ErrorOut,
    ErrorsListResponse,
    HealthResponse,
    HistoryCreate,
    HistoryListResponse,
    HistoryOut,
    IngestRequest,
    IngestResponse,
    MasteryItem,
    MasteryResponse,
    NeighborOut,
    NeighborsResponse,
    NoteCreate,
    NoteDetailOut,
    NoteOut,
    NotePatch,
    NotesListResponse,
    NoteWriteResult,
    ProgressUpsert,
    ProgressUpsertResponse,
    RetrievedMemory,
    RetrieveRequest,
    RetrieveResponse,
    SnapshotResponse,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.sqlite_repo import SqliteRepo
from app.embedding.factory import build_embedder
from app.index.hnsw_index import HnswIndex
from app.index.label_store import SqliteLabelStore
from app.memory.ingestor import DuplicateSessionError, Ingestor
from app.memory.notes_index import NotesIndex
from app.memory.parser import IngestValidationError
from app.memory.reranker import RerankConfig
from app.memory.retriever import Retriever
from app.memory.snapshot import render_snapshot

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


def get_repo(request: Request) -> SqliteRepo:
    return request.app.state.repo


def get_notes_index(request: Request) -> NotesIndex:
    return request.app.state.notes_index


# ----------------------------------------------------------------------
# Injection scan + content helpers
# ----------------------------------------------------------------------
_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore (previous|all|the above) instructions"),
    re.compile(r"(?i)disregard (the )?(system|prior) (prompt|message)"),
    re.compile(r"(?i)you are now"),
    re.compile(r"<\|.*?\|>"),
    re.compile(r"(?i)system\s*:"),
]


def _is_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def _content_hash(text: str) -> str:
    norm = text.lower().strip().encode("utf-8")
    return hashlib.sha256(norm).hexdigest()[:16]


def _now_ts() -> int:
    return int(time.time())


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


@router.get("/mastery/{user_id}", response_model=MasteryResponse)
def mastery(
    user_id: str,
    topic: str | None = None,
    since_days: int | None = None,
    min_seen: int = 1,
    repo: SqliteRepo = Depends(get_repo),
    settings: Settings = Depends(get_settings_dep),
) -> MasteryResponse:
    if min_seen < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_seen must be >= 1",
        )
    if since_days is not None and since_days < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="since_days must be >= 1",
        )
    rows = repo.aggregate_mastery(
        user_id,
        topic=topic,
        since_days=since_days,
        min_seen=min_seen,
        half_life_days=settings.half_life_days,
    )
    return MasteryResponse(
        user_id=user_id,
        items=[MasteryItem(**r) for r in rows],
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

    if not hasattr(app.state, "notes_index"):
        notes_index = NotesIndex(
            index_path=settings.data_dir / "notes_hnsw.bin",
            embedder=app.state.embedder,
            dimension=settings.embedding_dim,
            max_elements=10000,
        )
        notes_index.load()  # graceful: corruption / missing → empty
        # Recovery: SQLite is canonical for notes. If active notes exist
        # but the index is empty (file was missing, corrupt, or built with
        # a different embedding model), rebuild from SQLite.
        active_count = app.state.repo.count_active_notes()
        stored_notes_model = app.state.repo.get_meta("notes_embedding_model")
        model_changed = (
            stored_notes_model is not None
            and stored_notes_model != settings.embedding_model
        )
        if active_count > 0 and (notes_index.count == 0 or model_changed):
            if model_changed:
                logger.warning(
                    "notes embedding model changed from %s to %s — rebuilding "
                    "notes hnsw index",
                    stored_notes_model,
                    settings.embedding_model,
                )
            else:
                logger.info(
                    "notes hnsw index empty but SQLite has %d active notes — "
                    "rebuilding",
                    active_count,
                )
            notes_index.rebuild_from(app.state.repo.iter_active_notes())
            notes_index.save()
        if active_count > 0 and stored_notes_model is None:
            # First time we've populated the notes index — record the
            # embedding model so future fingerprint checks have a baseline.
            app.state.repo.set_meta("notes_embedding_model", settings.embedding_model)
        elif model_changed:
            app.state.repo.set_meta("notes_embedding_model", settings.embedding_model)
        app.state.notes_index = notes_index


# ----------------------------------------------------------------------
# /notes
# ----------------------------------------------------------------------
@router.post("/notes", response_model=NoteWriteResult)
def create_note(
    payload: NoteCreate,
    repo: SqliteRepo = Depends(get_repo),
    notes_index: NotesIndex = Depends(get_notes_index),
) -> NoteWriteResult:
    if _is_injection(payload.content):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="injection pattern detected",
        )

    chash = _content_hash(payload.content)
    now_ts = _now_ts()
    try:
        note_id = repo.add_note(
            user_id=payload.user_id,
            category=payload.category,
            content=payload.content,
            content_hash=chash,
            source_session_id=payload.source_session_id,
            source_event=payload.source_event,
            now_ts=now_ts,
        )
    except sqlite3.IntegrityError:
        # Unique partial index on (user_id, content_hash) WHERE archived=0
        # caught the duplicate at commit time. Two concurrent inserts for
        # the same content can both pass an in-Python check but only one
        # wins here. Audit the rejected attempt and return 409.
        repo.add_history(
            note_id=None,
            event="REJECT",
            old_content=None,
            new_content=payload.content,
            reason="race-hash-conflict",
            source_session_id=payload.source_session_id,
            source_event=payload.source_event,
            now_ts=now_ts,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="duplicate content_hash for active note",
        ) from None
    notes_index.add(note_id=note_id, text=payload.content)

    active = repo.list_active_notes(
        user_id=payload.user_id,
        category=payload.category,
    )
    return NoteWriteResult(note_id=note_id, active_count_in_category=len(active))


@router.get("/notes/by-id/{note_id}", response_model=NoteDetailOut)
def get_note_by_id(
    note_id: int,
    repo: SqliteRepo = Depends(get_repo),
) -> NoteDetailOut:
    note = repo.get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return NoteDetailOut(
        id=note.id,
        user_id=note.user_id,
        category=note.category,
        content=note.content,
        content_hash=note.content_hash,
        created_at=note.created_at,
        updated_at=note.updated_at,
        archived=bool(note.archived),
        source_session_id=note.source_session_id,
        source_event=note.source_event,
    )


@router.patch("/notes/by-id/{note_id}", response_model=NoteOut)
def patch_note(
    note_id: int,
    payload: NotePatch,
    repo: SqliteRepo = Depends(get_repo),
    notes_index: NotesIndex = Depends(get_notes_index),
) -> NoteOut:
    if _is_injection(payload.content):
        raise HTTPException(status_code=422, detail="injection pattern detected")

    existing = repo.get_note(note_id)
    if existing is None or existing.archived:
        raise HTTPException(status_code=404, detail="note not found or archived")

    new_hash = _content_hash(payload.content)
    if new_hash != existing.content_hash and repo.hash_exists_active(
        existing.user_id, new_hash
    ):
        raise HTTPException(status_code=409, detail="hash conflict")

    now_ts = _now_ts()
    ok = repo.update_note(
        note_id=note_id,
        content=payload.content,
        content_hash=new_hash,
        now_ts=now_ts,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="note not found or archived")
    notes_index.update(note_id=note_id, text=payload.content)

    updated = repo.get_note(note_id)
    return NoteOut(
        id=updated.id,
        category=updated.category,
        content=updated.content,
        content_hash=updated.content_hash,
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


@router.delete("/notes/by-id/{note_id}")
def delete_note(
    note_id: int,
    repo: SqliteRepo = Depends(get_repo),
    notes_index: NotesIndex = Depends(get_notes_index),
) -> dict:
    ok = repo.archive_note(note_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail="note not found or already archived"
        )
    notes_index.remove(note_id=note_id)
    return {"note_id": note_id, "archived": True}


@router.get("/notes/{user_id}/neighbors", response_model=NeighborsResponse)
def neighbors(
    user_id: str,
    category: Literal["misconception", "preference", "style", "goal"],
    query_text: str,
    top_k: int = 5,
    repo: SqliteRepo = Depends(get_repo),
    notes_index: NotesIndex = Depends(get_notes_index),
) -> NeighborsResponse:
    if not query_text.strip():
        return NeighborsResponse(neighbors=[])

    # 召回足量后再筛 (user, category), 避免单次召回里全是其他用户的 hits
    raw = notes_index.search(query_text=query_text, top_k=top_k * 4)

    out: list[NeighborOut] = []
    now = _now_ts()
    for hit in raw:
        note = repo.get_note(hit.note_id)
        if note is None or note.archived:
            continue
        if note.user_id != user_id or note.category != category:
            continue
        age_days = max((now - note.created_at) / 86400, 0)
        out.append(
            NeighborOut(
                id=note.id,
                content=note.content,
                sim_score=hit.sim_score,
                age_days=age_days,
            )
        )
        if len(out) >= top_k:
            break
    return NeighborsResponse(neighbors=out)


@router.get("/notes/{user_id}", response_model=NotesListResponse)
def list_notes(
    user_id: str,
    category: Literal["misconception", "preference", "style", "goal"],
    repo: SqliteRepo = Depends(get_repo),
) -> NotesListResponse:
    notes = repo.list_active_notes(user_id=user_id, category=category)
    return NotesListResponse(
        notes=[
            NoteOut(
                id=n.id,
                category=n.category,
                content=n.content,
                content_hash=n.content_hash,
                created_at=n.created_at,
                updated_at=n.updated_at,
            )
            for n in notes
        ]
    )


# ----------------------------------------------------------------------
# /progress
# ----------------------------------------------------------------------
def _next_status(
    curr: str | None,
    mastery: float,
    last_seen_at: int,
    now: int,
) -> str:
    age_days = (now - last_seen_at) / 86400
    if curr is None:
        return "in_progress"
    if mastery >= 0.85 and age_days < 7:
        return "mastered"
    if curr != "mastered" and age_days > 30:
        return "abandoned"
    if curr == "abandoned" and mastery > 0:
        return "in_progress"
    return curr


@router.post("/progress", response_model=ProgressUpsertResponse)
def upsert_progress(
    payload: ProgressUpsert,
    repo: SqliteRepo = Depends(get_repo),
) -> ProgressUpsertResponse:
    now = _now_ts()
    existing = repo.get_progress(payload.user_id, payload.topic)
    started_at = existing.started_at if existing else now
    # Pass the previous row's last_seen_at, NOT now: age_days is meant to
    # reflect how long it has been since the topic was actually touched.
    # Using `now` would peg age_days at 0 and make the abandoned branch
    # in _next_status unreachable.
    previous_seen = existing.last_seen_at if existing else now
    new_status = _next_status(
        curr=existing.status if existing else None,
        mastery=payload.mastery_overall,
        last_seen_at=previous_seen,
        now=now,
    )

    repo.upsert_progress(
        user_id=payload.user_id,
        topic=payload.topic,
        status=new_status,
        started_at=started_at,
        last_seen_at=now,
        mastery_overall=payload.mastery_overall,
        last_session_id=payload.last_session_id,
    )

    for s in payload.kp_samples:
        repo.add_mastery_sample(
            user_id=payload.user_id,
            topic=payload.topic,
            knowledge_point=s.kp,
            score=s.score,
            ts=now,
            source=s.source,
        )

    return ProgressUpsertResponse(
        topic=payload.topic,
        new_status=new_status,
        sample_count=len(payload.kp_samples),
    )


# ----------------------------------------------------------------------
# /memory/snapshot
# ----------------------------------------------------------------------
@router.get("/memory/snapshot/{user_id}", response_model=SnapshotResponse)
def get_snapshot(
    user_id: str,
    repo: SqliteRepo = Depends(get_repo),
) -> SnapshotResponse:
    return render_snapshot(repo, user_id=user_id, now_ts=_now_ts())


# ----------------------------------------------------------------------
# /errors (sink + queries)
# NOTE: /errors/correlation/{correlation_id} MUST be registered BEFORE
#       /errors/{user_id} so FastAPI does not match "correlation" as a
#       user_id path segment.
# ----------------------------------------------------------------------
@router.get("/errors/correlation/{correlation_id}", response_model=ErrorsListResponse)
def errors_for_correlation(
    correlation_id: str,
    repo: SqliteRepo = Depends(get_repo),
) -> ErrorsListResponse:
    rows = repo.list_errors_for_correlation(correlation_id)
    return ErrorsListResponse(
        errors=[
            ErrorOut(
                id=r.id,
                ts=r.ts,
                source=r.source,
                user_id=r.user_id,
                session_id=r.session_id,
                correlation_id=r.correlation_id,
                op=r.op,
                status=r.status,
                error_class=r.error_class,
                error_msg=r.error_msg,
                payload_excerpt=r.payload_excerpt,
            )
            for r in rows
        ]
    )


@router.post("/errors/log", response_model=ErrorLogResponse)
def errors_log(
    payload: ErrorLog,
    repo: SqliteRepo = Depends(get_repo),
) -> ErrorLogResponse:
    """永不返回 5xx. 写库失败也返回 200 (logged=False) + server stderr warning."""
    try:
        repo.log_op_error(
            ts=_now_ts(),
            source=payload.source,
            user_id=payload.user_id,
            session_id=payload.session_id,
            correlation_id=payload.correlation_id,
            op=payload.op,
            status=payload.status,
            error_class=payload.error_class,
            error_msg=payload.error_msg,
            payload_excerpt=payload.payload_excerpt,
        )
        return ErrorLogResponse(logged=True)
    except Exception:
        logger.exception("errors/log itself failed; returning 200 anyway")
        return ErrorLogResponse(logged=False)


@router.get("/errors/{user_id}", response_model=ErrorsListResponse)
def errors_for_user(
    user_id: str,
    op: str | None = None,
    since: int | None = None,
    limit: int = 100,
    repo: SqliteRepo = Depends(get_repo),
) -> ErrorsListResponse:
    if limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 1000",
        )
    rows = repo.list_errors_for_user(user_id, op=op, since=since, limit=limit)
    return ErrorsListResponse(
        errors=[
            ErrorOut(
                id=r.id,
                ts=r.ts,
                source=r.source,
                user_id=r.user_id,
                session_id=r.session_id,
                correlation_id=r.correlation_id,
                op=r.op,
                status=r.status,
                error_class=r.error_class,
                error_msg=r.error_msg,
                payload_excerpt=r.payload_excerpt,
            )
            for r in rows
        ]
    )


# ----------------------------------------------------------------------
# /history
# ----------------------------------------------------------------------
@router.post("/history", status_code=204)
def write_history(
    payload: HistoryCreate,
    repo: SqliteRepo = Depends(get_repo),
) -> None:
    """Reviewer-driven write of an audit row. Mirrors ``repo.add_history``
    but exposes it over HTTP so ``memory_client.write_history(...)`` can
    record every ADD/UPDATE/DELETE/NOOP/REJECT decision."""
    repo.add_history(
        note_id=payload.note_id,
        event=payload.event,
        old_content=payload.old_content,
        new_content=payload.new_content,
        reason=payload.reason,
        source_session_id=payload.source_session_id,
        source_event=payload.source_event,
        now_ts=_now_ts(),
    )


@router.get("/history", response_model=HistoryListResponse)
def list_history(
    event: Literal["ADD", "UPDATE", "DELETE", "NOOP", "REJECT"] | None = None,
    limit: int = 100,
    repo: SqliteRepo = Depends(get_repo),
) -> HistoryListResponse:
    rows = repo.list_history(event=event, limit=limit)
    return HistoryListResponse(
        history=[
            HistoryOut(
                id=r.id,
                note_id=r.note_id,
                event=r.event,
                old_content=r.old_content,
                new_content=r.new_content,
                reason=r.reason,
                source_session_id=r.source_session_id,
                source_event=r.source_event,
                ts=r.ts,
            )
            for r in rows
        ]
    )
