# LearningMAP — Technical Reference

Repository layout, hardware sizing, and development conventions. For the project overview, demos, quickstart, high-level architecture, and API reference, see the [main README](README.md).

## Table of Contents

- [Repository Structure](#repository-structure)
- [Hardware Recommendations](#hardware-recommendations)
- [Testing & Development Conventions](#testing--development-conventions)

---

## Repository Structure

```
LearningMAP/
├── run/
│   ├── serve.py                 # FastAPI entry point (port 8080)
│   ├── classba_client.py        # External ClassBA Agent client (optional)
│   └── build_kc_skills.py       # Offline build of the KC-skill knowledge table
├── src/
│   ├── api/                     # FastAPI router + session store (SQLite)
│   │   ├── chat.py              # All HTTP endpoints
│   │   ├── store.py             # In-memory dict + sessions.db dual-write
│   │   └── tasks.py             # Background async tasks (generate lesson / slides)
│   ├── agent/
│   │   ├── graph.py             # LangGraph state machine assembly
│   │   ├── state.py             # LearningState (Pydantic)
│   │   ├── mastery_annotator.py # Mastery annotation on KG nodes
│   │   └── nodes/
│   │       ├── intent.py        # Entry router: chat / kg / quiz / lesson / slides
│   │       ├── chat.py          # Free-form Q&A + memory snapshot injection
│   │       ├── knowledge_graph.py
│   │       ├── quiz.py          # Adaptive question generation
│   │       ├── grading.py       # Grading
│   │       ├── diagnosis.py     # Learning diagnosis
│   │       ├── lesson.py        # Markdown lesson
│   │       ├── slides.py        # PPT outline + per-page layout
│   │       ├── review.py        # L2 reflection / wrong-answer review
│   │       ├── kc_skill_resolver.py
│   │       └── _helpers.py
│   ├── llm/
│   │   ├── client.py            # OpenAI-compatible client + Instructor
│   │   ├── schemas.py           # Core Pydantic models
│   │   └── slide_schemas.py     # PPTElement / SlideData
│   ├── prompts/                 # All Jinja2 .j2 templates
│   ├── skills/                  # Pedagogy skills loader
│   ├── forgetting/              # Forgetting curve (Ebbinghaus)
│   ├── export/pptx_export.py    # PPT → .pptx export (python-pptx)
│   ├── utils/json_repair.py     # 4-strategy JSON repair
│   ├── memory_client.py         # The single seam to student_memory
│   ├── progress_aggregator.py
│   └── reviewer.py
├── skills/                      # Pluggable pedagogies (feynman / scaffolding / socratic)
│   └── <skill>/SKILL.md
├── static/                      # Frontend (vanilla JS + Tailwind)
│   ├── index.html               # Main chat page
│   ├── diagnosis.html           # Diagnosis card + learning radar chart
│   ├── lesson.html              # Markdown lesson reader
│   ├── slides.html              # PPT renderer
│   ├── results.html
│   ├── api.js                   # Shared HTTP client
│   └── sidebar.js               # Session list
├── tests/                       # pytest cases (incl. manual/ smoke scripts, skipped by default)
├── student_memory/              # Independent FastAPI microservice (see below)
├── pyproject.toml               # Main project dependencies
├── .python-version              # 3.11
└── sessions.db                  # SQLite session persistence (created at runtime)
```

> `student_memory/` is an **independent sub-project** with its own `pyproject.toml` / `uv.lock` / `tests/`. The root `uv sync` will **not** install it.

---

## Hardware Recommendations

The main service itself **does not run models locally** (inference is offloaded to an external LLM gateway). Machine load comes mostly from `student_memory`'s local embedding model.

### Main service only (external LLM, memory subservice disabled)

| Resource | Recommended | Minimum |
|---|---|---|
| CPU | 4 cores | 2 cores |
| Memory | 4 GB | 2 GB |
| Disk | 2 GB (deps + `sessions.db`) | 1 GB |
| GPU | Not required | Not required |
| Network | Reachable `OPENAI_BASE_URL`; lower latency = faster responses | — |

> What really determines the user experience is **the LLM gateway's latency and concurrency quota**, not local machine performance. The time-to-first-token of the streaming endpoints (`/chat/stream` etc.) is essentially the gateway's TTFT.

### Memory subservice enabled (with local vector embeddings)

The memory service runs all embedding inference locally; hardware requirements depend on the embedder you pick:

| Embedder | `SM_EMBEDDING_MODEL` | Dim | RAM | Disk | GPU |
|---|---|---|---|---|---|
| **FakeEmbedder** (dev/demo) | `fake-test` | 16 | < 100 MB | < 10 MB | Not required |
| **bge-small-zh-v1.5** (lightweight) | `BAAI/bge-small-zh-v1.5` | 512 | ~1 GB | ~500 MB | Optional (CPU works) |
| **bge-m3** (default, multilingual) | `BAAI/bge-m3` | 1024 | ~3 GB | ~2.5 GB | Recommended |

Recommended sizing when the main service and the memory service share a single host:

| Profile | CPU | RAM | Disk | GPU |
|---|---|---|---|---|
| Dev / demo (FakeEmbedder) | 4 cores | 4 GB | 2 GB | None |
| Single-host prod · bge-small-zh, CPU only | 8 cores | 6 GB | 4 GB | None |
| Single-host prod · bge-m3, CPU | 8 cores | 8 GB | 6 GB | None (slightly slower) |
| Single-host prod · bge-m3, GPU-accelerated | 4 cores | 8 GB | 6 GB | ≥ 6 GB VRAM (e.g. RTX 3060 / T4) |

Practical notes:

- **First start downloads the model**: bge-m3 ≈ 2 GB, bge-small-zh ≈ 500 MB. Warm up beforehand or use a proxy.
- **GPU is not strictly required** for bge-m3, but a single `/retrieve` inference on CPU takes ~200–500 ms; for high-concurrency scenarios switch to bge-small-zh or add a GPU.
- **Mac Apple Silicon (M1/M2/M3)** can use `SM_EMBEDDING_DEVICE=mps` for Metal acceleration; on 8 GB unified-memory models, prefer bge-small-zh.
- **Single-worker only**: the memory service's hnswlib index is not process-safe. **Run a single worker** and rely on thread-level concurrency.

---

## Testing & Development Conventions

```bash
# Main project
uv run python -m pytest tests/ -v
uv run python -m pytest tests/test_api.py::test_name -v   # single test

# Memory subproject
cd student_memory && uv run pytest -v
```

Lint / format:

```bash
uv run ruff check src/ run/ tests/
uv run ruff format src/ run/ tests/
```

Conventions:

- **Package management**: `uv` only; do not mix in pip / poetry.
- **LLM calls**: always through `src/llm/client.py`, always wrapped in `asyncio.to_thread`.
- **Prompt edits**: only inside `.j2` files; never concatenate prompt strings in Python.
- **Replayable state**: any visual element the frontend needs to restore after a refresh must be derivable from `GET /api/chat/{sid}/state`.
- **Extending `PPTElement`**: when adding a new type, update all 5 sites — `slide_schemas.py`, the slide post-processing chain, the `slides.html` renderer, `pptx_export.py`, and `slide_content_system.j2`.
- **XSS**: every frontend `innerHTML` write must go through `escapeHtml` or DOMPurify first.
- **Tests under `tests/manual/`** are smoke scripts; pytest skips them via `norecursedirs` by default.
- **One-shot regression scripts** should be `rm`'d after running — do not `git add` them.
