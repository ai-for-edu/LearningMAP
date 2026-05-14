# Student Memory Service

A local-first FastAPI microservice that provides time-decay-aware semantic retrieval over student learning records. Called over HTTP by the demo-branch conversation system; zero code coupling between services.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Deployment](#deployment)
- [Integration with the Demo Branch](#integration-with-the-demo-branch)
- [Operations](#operations)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [FAQ](#faq)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                Student Memory Service               │
│                                                     │
│  POST /ingest ──► Parser ──► Normalizer ──► Embed   │
│                                  │            │     │
│                            SQLite (SoT)   hnswlib   │
│                                  │            │     │
│  POST /retrieve ◄── Formatter ◄─ Reranker ◄──┘     │
│                                                     │
│  GET /health ──► {"status": "ok"}                   │
└─────────────────────────────────────────────────────┘
```

- **SQLite** is the single Source of Truth
- **hnswlib** is the derived vector index; can be rebuilt from SQLite at any time
- **Embedding model**: default `BAAI/bge-m3` (1024-dim); configurable to any sentence-transformers model
- **Scoring formula**: `final = 0.75×sim + 0.20×recency + 0.05×match`, half-life 30 days

---

## Requirements

| Dependency | Minimum | Notes |
|---|---|---|
| Python | 3.11+ | `.python-version` pins 3.11 |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | Package manager (same as the demo branch) |
| SQLite | 3.35+ | Bundled with Python; no separate install needed |

Hardware requirements depend on the embedding model:

| Scenario | RAM | Disk | GPU |
|---|---|---|---|
| FakeEmbedder (test/dev) | 128 MB | < 10 MB | Not needed |
| bge-small-en-v1.5 (lightweight) | ~1 GB | ~500 MB | Optional |
| bge-m3 (default/production) | ~3 GB | ~2.5 GB | Recommended |

---

## Quick Start

### 1. Install core dependencies only (dev/test — no model download)

```bash
cd student_memory
uv sync
```

### 2. Start with FakeEmbedder (no GPU required, good for development)

```bash
SM_USE_FAKE_EMBEDDER=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

Verify the service is up:

```bash
curl http://127.0.0.1:8090/health
# {"status":"ok"}
```

### 3. Start with a real embedding model (production)

```bash
# Install ML extras (first run downloads torch + sentence-transformers, ~2 GB)
uv sync --extra ml

# Start (first run downloads the bge-m3 model, ~2 GB)
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> If the machine has no GPU, the model automatically falls back to CPU. You can also set `SM_EMBEDDING_DEVICE=cpu` explicitly.

---

## Configuration

All configuration is loaded from **environment variables** (prefix `SM_`) or a **`.env` file** in the project root.

### Full configuration reference

| Variable | Default | Description |
|---|---|---|
| **Service** | | |
| `SM_SERVICE_HOST` | `127.0.0.1` | Bind address |
| `SM_SERVICE_PORT` | `8090` | Bind port (demo branch uses 8080 — avoid conflicts) |
| `SM_LOG_LEVEL` | `INFO` | Log level: `DEBUG` / `INFO` / `WARNING` |
| **Storage** | | |
| `SM_DATA_DIR` | `data` | Data directory (SQLite and index files) |
| `SM_SQLITE_PATH` | `data/memory.db` | SQLite database path |
| `SM_INDEX_PATH` | `data/hnsw.bin` | hnswlib index file path |
| **Embedding model** | | |
| `SM_EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding model name |
| `SM_EMBEDDING_DIM` | `1024` | Embedding dimension (must match the model) |
| `SM_EMBEDDING_DEVICE` | `None` (auto) | Inference device: `cpu` / `cuda` / `mps` |
| `SM_USE_FAKE_EMBEDDER` | `0` | Set to `1` to skip the real model (test/dev) |
| **Retrieval** | | |
| `SM_TOP_K` | `5` | Default number of results returned |
| `SM_RECALL_K` | `50` | HNSW recall candidate count (must be ≥ `TOP_K`) |
| **Scoring weights** (must sum to 1.0) | | |
| `SM_W_SIM` | `0.75` | Semantic similarity weight |
| `SM_W_TIME` | `0.20` | Recency decay weight |
| `SM_W_MATCH` | `0.05` | Topic match weight |
| `SM_HALF_LIFE_DAYS` | `30` | Recency decay half-life (days) |
| **hnswlib tuning** | | |
| `SM_HNSW_MAX_ELEMENTS` | `100000` | Index capacity (auto-expands) |
| `SM_HNSW_EF_CONSTRUCTION` | `200` | Build quality parameter |
| `SM_HNSW_M` | `16` | Graph connectivity |
| `SM_HNSW_EF_SEARCH` | `64` | Search accuracy parameter |

### Example `.env` file

```bash
# student_memory/.env
SM_SERVICE_PORT=8090
SM_EMBEDDING_MODEL=BAAI/bge-m3
SM_EMBEDDING_DIM=1024
SM_LOG_LEVEL=INFO
SM_DATA_DIR=data
```

### Common model configurations

| Model | `SM_EMBEDDING_MODEL` | `SM_EMBEDDING_DIM` | RAM | Use case |
|---|---|---|---|---|
| bge-m3 | `BAAI/bge-m3` | `1024` | ~3 GB | Multilingual production |
| bge-small-en | `BAAI/bge-small-en-v1.5` | `384` | ~500 MB | Lightweight / low-resource |
| FakeEmbedder | `fake-test` | `16` | < 10 MB | Dev / test (`SM_USE_FAKE_EMBEDDER=1`) |

> **You must rebuild the index after switching models.** Vectors from different models are incompatible. The service detects model-fingerprint changes at startup and triggers a rebuild automatically; you can also run `uv run python -m scripts.rebuild_index` manually.

---

## API Reference

### `GET /health`

```bash
curl http://127.0.0.1:8090/health
```

```json
{"status": "ok"}
```

### `POST /ingest` — write a learning record

```bash
curl -X POST http://127.0.0.1:8090/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u_001",
    "session_id": "s_001",
    "timestamp": "2026-04-01T10:30:00Z",
    "goal_text": "study quadratic equations",
    "topic": "Quadratic Equations",
    "subtopics": [
      {
        "name": "Discriminant",
        "summary": "student repeatedly confused root cases",
        "questions": [
          {
            "question_id": "q1",
            "question_text": "How many roots does x²-4x+4=0 have?",
            "student_answer": "two distinct real roots",
            "is_correct": false,
            "error_type": "discriminant confusion"
          }
        ]
      }
    ]
  }'
```

Success response (200):

```json
{
  "status": "ok",
  "session_id": "s_001",
  "memory_count": 1
}
```

> `memory_count` = number of subtopics (not questions). Each subtopic produces one memory unit.

Error responses:
- `422` — missing `user_id`, malformed timestamp, or no subtopics
- `409` — duplicate `session_id` (atomic dedup; no data is written for a duplicate session)

### `POST /retrieve` — semantic retrieval

```bash
curl -X POST http://127.0.0.1:8090/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u_001",
    "query": "I want to study quadratic equations",
    "top_k": 3
  }'
```

Success response (200):

```json
{
  "query": "I want to study quadratic equations",
  "results": [
    {
      "memory_id": "m_abc123def456",
      "topic": "Quadratic Equations",
      "subtopic": "Discriminant",
      "timestamp": "2026-04-01T10:30:00Z",
      "sim_score": 0.92,
      "recency_score": 0.85,
      "match_score": 0.0,
      "final_score": 0.86,
      "retrieval_text": "Learning goal: study quadratic equations\nTopic: ..."
    }
  ],
  "context_text": "Relevant past learning records:\n1. Learning goal: ..."
}
```

> `context_text` is ready for LLM prompt injection. The demo-branch chat node injects this field directly into the system prompt context block. Returns `"(no relevant past learning records)"` when the database is empty.

---

## Deployment

### Option 1: Local process (recommended for dev / single-machine production)

```bash
cd student_memory

# Install dependencies
uv sync --extra ml

# Create .env (optional)
cat > .env << 'EOF'
SM_SERVICE_PORT=8090
SM_EMBEDDING_MODEL=BAAI/bge-m3
SM_EMBEDDING_DIM=1024
SM_LOG_LEVEL=INFO
EOF

# Start the service
uv run uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8090 \
  --workers 1
```

> **Do not use multiple workers.** The hnswlib index file is not process-safe. Single worker + thread concurrency is sufficient (the service uses an internal write lock).

### Option 2: Docker

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .python-version ./
COPY app/ app/
COPY scripts/ scripts/

# Install dependencies (including ML extras)
RUN uv sync --extra ml --no-dev

# Data directory (mount point)
RUN mkdir -p /app/data
VOLUME /app/data

# Default configuration
ENV SM_SERVICE_HOST=0.0.0.0
ENV SM_SERVICE_PORT=8090
ENV SM_DATA_DIR=/app/data
ENV SM_SQLITE_PATH=/app/data/memory.db
ENV SM_INDEX_PATH=/app/data/hnsw.bin

EXPOSE 8090

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
```

Create `docker-compose.yml`:

```yaml
services:
  student-memory:
    build: .
    ports:
      - "8090:8090"
    volumes:
      - ./data:/app/data
    environment:
      - SM_EMBEDDING_MODEL=BAAI/bge-m3
      - SM_EMBEDDING_DIM=1024
      - SM_LOG_LEVEL=INFO
    restart: unless-stopped
```

Build and start:

```bash
docker compose up -d
docker compose logs -f student-memory
```

### Option 3: Lightweight (no GPU / low-resource machine)

```bash
cat > .env << 'EOF'
SM_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
SM_EMBEDDING_DIM=384
SM_EMBEDDING_DEVICE=cpu
SM_SERVICE_PORT=8090
EOF

uv sync --extra ml
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

### Option 4: Pure dev/demo mode (no model download)

No model download; uses the deterministic FakeEmbedder:

```bash
uv sync  # --extra ml not needed
SM_USE_FAKE_EMBEDDER=1 SM_EMBEDDING_DIM=16 \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> FakeEmbedder generates deterministic vectors from keyword hashing. Good for functional verification and integration debugging; not suitable for real semantic retrieval.

---

## Integration with the Demo Branch

### Architecture

```
Demo Branch (:8080)                  Memory Service (:8090)
┌──────────────────┐                ┌──────────────────┐
│  LangGraph Chat  │ ── POST /retrieve ──►│                  │
│     Node         │◄── context_text ─────│  Student Memory  │
│                  │                      │    Service       │
│  Post-Diagnosis  │ ── POST /ingest ────►│                  │
│  Post-Lesson     │                      └──────────────────┘
└──────────────────┘
```

### Example call from the demo side

```python
import httpx

MEMORY_BASE = "http://127.0.0.1:8090"

# In the chat node: retrieve context and inject into system prompt
async def get_memory_context(user_id: str, query: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MEMORY_BASE}/retrieve",
            json={"user_id": user_id, "query": query, "top_k": 5},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["context_text"]

# After diagnosis / lesson: write the learning record
async def ingest_session(payload: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MEMORY_BASE}/ingest",
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
```

### Integration notes

1. **Start the memory service before the demo service.** The demo side should gracefully degrade on failure (simply omit `context_text`); the memory service being unavailable must not block the conversation.
2. **Avoid port conflicts**: memory defaults to 8090, demo defaults to 8080.
3. **`context_text` can be injected directly into the prompt.** When the database is empty the value is `"(no relevant past learning records)"`; the demo side can use this to decide whether to inject.
4. **No authentication**: v1 binds only to localhost; no API key required. For non-local deployments, add a reverse proxy or network isolation.

---

## Operations

### Data backup

SQLite is the only file that needs backing up. The hnswlib index can be rebuilt from SQLite at any time.

```bash
# Backup
cp data/memory.db data/memory.db.bak

# Restore
cp data/memory.db.bak data/memory.db
rm data/hnsw.bin  # delete index; it will be rebuilt automatically at startup
```

### Rebuilding the index

Rebuild the index in these situations:

- After switching the embedding model
- If you suspect the index is out of sync with SQLite
- After restoring SQLite from a backup
- If the index file is corrupted

```bash
# Option 1: run the script manually
uv run python -m scripts.rebuild_index

# Option 2: delete the index file and restart (auto-rebuild)
rm data/hnsw.bin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
# Logs will show: hnsw index empty but SQLite has N records — rebuilding
```

> The service detects and recovers from two anomalies at startup:
> 1. Index file missing / corrupted → rebuild from SQLite
> 2. Embedding model version changed → log WARNING and force rebuild

### Switching the embedding model

```bash
# 1. Stop the service

# 2. Update .env
SM_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
SM_EMBEDDING_DIM=384

# 3. Rebuild index (optional — will also happen automatically at startup)
uv run python -m scripts.rebuild_index

# 4. Restart the service
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

### Viewing data statistics

```bash
# Total record count
sqlite3 data/memory.db "SELECT COUNT(*) FROM memories;"

# Per-user breakdown
sqlite3 data/memory.db "SELECT user_id, COUNT(*) FROM memories GROUP BY user_id;"

# Per-topic breakdown
sqlite3 data/memory.db "SELECT topic, COUNT(*) FROM memories GROUP BY topic ORDER BY COUNT(*) DESC;"

# Current embedding model fingerprint
sqlite3 data/memory.db "SELECT * FROM index_meta;"

# List ingested sessions
sqlite3 data/memory.db "SELECT user_id, session_id, ingested_at FROM ingested_sessions ORDER BY ingested_at DESC LIMIT 10;"
```

---

## Testing

### Run all unit tests (no GPU or model required)

```bash
uv sync  # core dependencies are sufficient
uv run pytest -v
# ~82 passed, 1 deselected (the deselected test is the `ml`-marked real-model case)
```

### Run tests that require a real model

```bash
uv sync --extra ml
uv run pytest -v -m ml
```

### Test coverage

| Test file | Module covered |
|---|---|
| `test_health.py` | App startup, config validation, lazy torch loading |
| `test_sqlite_repo.py` | Full SQLite repo read/write operations |
| `test_embedder.py` | Embedder ABC, FakeEmbedder determinism, lazy loading |
| `test_hnsw_index.py` | Vector index: CRUD, save/load, rebuild, resize |
| `test_parser_normalizer.py` | Payload parsing, normalisation, retrieval_text template |
| `test_ingest.py` | End-to-end ingest flow, deduplication, model fingerprint |
| `test_reranker.py` | Scoring formula, weights, decay, tie-break |
| `test_retriever.py` | Recall + rerank, empty index, user isolation |
| `test_formatter.py` | context_text formatting |
| `test_api.py` | HTTP routes, Pydantic validation, error codes |
| `test_integration.py` | Cross-phase integration: ingest→retrieve→restart persistence→index recovery |

---

## Project Structure

```
student_memory/
├── app/
│   ├── __init__.py              # __version__ = "0.1.0"
│   ├── main.py                  # FastAPI factory + lifespan
│   ├── api/
│   │   ├── routes.py            # /health, /ingest, /retrieve + wire_app
│   │   └── schemas.py           # Pydantic request/response models
│   ├── core/
│   │   ├── config.py            # Settings (SM_ prefix env vars)
│   │   └── logging.py           # Logging configuration
│   ├── db/
│   │   ├── migrations.py        # DDL: memories, hnsw_labels, ingested_sessions, index_meta
│   │   └── sqlite_repo.py       # SqliteRepo (atomic transactions, label mapping)
│   ├── embedding/
│   │   ├── __init__.py          # Exports only the Embedder ABC (no torch pull)
│   │   ├── base.py              # Embedder abstract base class
│   │   ├── fake_embedder.py     # Deterministic test embedder (no ML dependencies)
│   │   ├── factory.py           # build_embedder() (no HTTP dependencies)
│   │   └── local_embedder.py    # BgeEmbedder (sentence-transformers lazy-loaded)
│   ├── index/
│   │   ├── hnsw_index.py        # HnswIndex (cosine space, sim = 1 - distance)
│   │   └── label_store.py       # label ↔ memory_id mapping abstraction
│   ├── memory/
│   │   ├── parser.py            # Raw payload parsing + validation
│   │   ├── normalizer.py        # Split into MemoryUnits by subtopic
│   │   ├── retrieval_text.py    # Deterministic retrieval text template
│   │   ├── ingestor.py          # Ingestor (atomic write: session + memories + labels)
│   │   ├── reranker.py          # Scoring formula + sorting
│   │   ├── retriever.py         # Recall → pre-filter → rerank → format
│   │   └── formatter.py         # context_text generation
│   └── utils/
│       └── time.py              # Time parsing, formatting, age_days
├── tests/                       # pytest tests (~82 cases)
├── scripts/
│   └── rebuild_index.py         # Offline index rebuild script
├── data/                        # SQLite + hnsw data (gitignored)
├── pyproject.toml
├── .python-version              # 3.11
└── README.md                    # This file
```

---

## FAQ

### Q: Getting `RuntimeError: sentence-transformers is not installed` at startup

Install ML extras:

```bash
uv sync --extra ml
```

Or skip the real model by using FakeEmbedder:

```bash
SM_USE_FAKE_EMBEDDER=1 SM_EMBEDDING_DIM=16 uv run uvicorn app.main:app --port 8090
```

### Q: Retrieval results are wrong after switching models

Different models have different vector dimensions and semantic spaces; you must rebuild the index:

```bash
uv run python -m scripts.rebuild_index
```

The service also detects model changes at startup and rebuilds automatically, but running manually gives you more control.

### Q: Can I deploy with multiple workers?

**No.** The hnswlib index file is not process-safe. Deploy with a single worker:

```bash
uv run uvicorn app.main:app --workers 1
```

The service uses `threading.Lock` internally to support concurrent reads/writes within a single process.

### Q: Can I migrate `data/memory.db` between machines?

Yes. The SQLite file is self-contained. Steps:

1. Stop the service
2. Copy `data/memory.db` to the target machine
3. You do not need to copy `data/hnsw.bin` (it will be rebuilt automatically at startup)
4. Verify that `SM_EMBEDDING_MODEL` and `SM_EMBEDDING_DIM` are configured correctly on the target
5. Start the service

### Q: How do I wipe all data and start fresh?

```bash
rm -rf data/memory.db data/hnsw.bin
# An empty database will be created automatically on next startup
```

### Q: How should the demo branch handle the memory service being unavailable?

The demo side should degrade gracefully: if `POST /retrieve` times out or returns non-200, simply omit `context_text` — do not block the conversation flow. The memory service is an enhancement component, not a required one.

---

## L2 / Progress Endpoints (PR-1, 2026-05-08)

| Method | Path | Purpose |
|---|---|---|
| POST | `/notes` | Create a 4-category note (reviewer ADD action) |
| GET  | `/notes/{user_id}?category=` | List active notes (filtered by category) |
| GET  | `/notes/{user_id}/neighbors` | hnswlib top-K recall (reviewer call B input) |
| GET  | `/notes/by-id/{note_id}` | Read a single note (reviewer UPDATE/DELETE fetch old value) |
| PATCH | `/notes/by-id/{note_id}` | Update content (reviewer UPDATE action) |
| DELETE | `/notes/by-id/{note_id}` | Soft-delete (reviewer DELETE action) |
| POST | `/progress` | UPSERT topic milestone + append mastery samples |
| GET  | `/memory/snapshot/{user_id}` | Render frozen snapshot (≤3500 chars) |
| POST | `/errors/log` | Error sink (never 5xx) |
| GET  | `/errors/{user_id}` | Query the most recent 100 errors for a user |
| GET  | `/errors/correlation/{corr}` | Fetch a complete reviewer call chain |
| GET  | `/history` | Query notes_history (reviewer decision audit) |

See `docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md` for the full spec.
