# OpenAILearning

> Chinese version available on the [`zh` branch](../../tree/zh).

An AI tutoring assistant for adaptive learning scenarios. The frontend is a set of static pages; the backend is a FastAPI service built on a **LangGraph** state machine. This branch plugs an independent **learning memory subservice** (`student_memory/`) on top of the base demo, providing cross-session semantic retrieval and L2 reflection notes.

> Each HTTP request drives one single-round LangGraph execution: `intent → (chat | knowledge_graph | quiz | grading→diagnosis→lesson | slides)`. Session state is persisted in memory and SQLite.

---

## Table of Contents

- [Feature Pipeline](#feature-pipeline)
- [Repository Structure](#repository-structure)
- [Runtime Environment](#runtime-environment)
- [Hardware Recommendations](#hardware-recommendations)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [API Endpoints](#api-endpoints)
- [Frontend Pages](#frontend-pages)
- [LangGraph Nodes](#langgraph-nodes)
- [Skills / Prompts System](#skills--prompts-system)
- [Memory Subservice](#memory-subservice)
- [Testing](#testing)
- [Development Conventions](#development-conventions)

---

## Feature Pipeline

```
User enters a topic
      │
      ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ knowledge_   │──►│ Adaptive Quiz│──►│  Grading +   │
│  graph node  │   │  (quiz node) │   │  Diagnosis   │
└──────────────┘   └──────────────┘   └──────┬───────┘
                                             │
                       ┌─────────────────────┴───────────────┐
                       ▼                                     ▼
                ┌──────────────┐                      ┌──────────────┐
                │ Markdown     │                      │  PPT Slides  │
                │ Lesson       │                      │ (slides node)│
                │ (lesson node)│                      │              │
                └──────────────┘                      └──────────────┘
                       │                                     │
                       └─────────────┬───────────────────────┘
                                     ▼
                       Review node (recap / L2 notes)
                                     │
                                     ▼
               POST /ingest + /notes  →  student_memory
                                     │
              next session's chat node  ◄── POST /retrieve
```

- The **chat node** injects historical memory snapshots as a system-prompt augmentation; safely degrades when the store is empty.
- The **review node** triggers L2 reflection and persists wrong-answer summaries into memory `/notes` (4-category).
- **All LLM calls** go through `src/llm/client.py` (OpenAI SDK + Instructor structured output), wrapped in `asyncio.to_thread` so the event loop is never blocked.

---

## Repository Structure

```
OpenAILearning/
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
│   ├── sidebar.js               # Session list
│   └── session-data.js
├── tests/                       # pytest cases (incl. manual/ smoke scripts, skipped by default)
├── student_memory/              # Independent FastAPI microservice (see below)
├── pyproject.toml               # Main project dependencies
├── .python-version              # 3.11
└── sessions.db                  # SQLite session persistence (created at runtime)
```

> `student_memory/` is an **independent sub-project** with its own `pyproject.toml` / `uv.lock` / `tests/`. The root `uv sync` will **not** install it.

---

## Runtime Environment

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.11 | Locked by `.python-version` |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | Only supported package manager |
| SQLite | 3.35+ | Bundled with Python |
| OpenAI-compatible LLM | — | DeepSeek / Moonshot / any OpenAI-compatible gateway |

See [Hardware Recommendations](#hardware-recommendations) below for hardware sizing.

---

## Hardware Recommendations

The main service itself **does not run models locally** (inference is offloaded to an external LLM gateway). Machine load comes mostly from `student_memory`'s local embedding model. Recommendations by deployment profile:

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

## Quick Start

### 1. Install dependencies

```bash
uv sync                              # main service
cd student_memory && uv sync && cd ..  # memory subservice (optional)
```

### 2. Write your `.env`

```bash
# .env (repository root)
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# Optional — defaults are used if omitted
MEMORY_SERVICE_URL=http://127.0.0.1:8090
ALLOWED_ORIGINS=*
REVIEW_MIN_AGE_SECONDS=86400

# Optional — ClassBA external Agent, only needed when invoking run/classba_client.py
# CLASSBA_AUTH=...
# CLASSBA_API_URL=...
# CLASSBA_AGENT_NAME=...
```

### 3. Start the services

**Main service (port 8080)**

```bash
uv run python run/serve.py
# Uvicorn → http://0.0.0.0:8080
```

**Memory subservice (port 8090, optional)**

```bash
cd student_memory
SM_USE_FAKE_EMBEDDER=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> When the memory service is unavailable, the main service **gracefully degrades** (no `context_text` injection, no `ingest` writes) without blocking the conversation.

### 4. Open the frontend

Open `http://localhost:8080/` in a browser (FastAPI serves `static/`).

---

## Environment Variables

All variables read by the main service (set in `.env` or process environment):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required**. LLM authentication. |
| `OPENAI_BASE_URL` | — | **Required**. OpenAI-compatible endpoint. |
| `LLM_MODEL` | — | **Required**. Model name. |
| `MEMORY_SERVICE_URL` | `http://127.0.0.1:8090` | student_memory service address. |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS whitelist. |
| `REVIEW_MIN_AGE_SECONDS` | `86400` | Minimum interval between review-node invocations (seconds). |
| `CLASSBA_AUTH` / `CLASSBA_API_URL` / `CLASSBA_AGENT_NAME` | — | Optional. For ClassBA external Agent calls. |

The LLM client has been hardcoded to **bypass the OS HTTP proxy** (see commit `fc7e51c`); no additional proxy configuration is needed.

---

## API Endpoints

Main routes (all mounted under the `/api` prefix; see `src/api/chat.py` for the full list):

| Method | Path | Description |
|---|---|---|
| POST | `/api/chat` | Single-round chat (synchronous) |
| POST | `/api/chat/stream` | Single-round chat (SSE streaming) |
| POST | `/api/chat/{sid}/generate-quiz/stream` | Generate adaptive quiz |
| POST | `/api/chat/{sid}/submit-quiz` | Submit answers → grading → diagnosis |
| POST | `/api/chat/{sid}/generate-lesson` | Generate Markdown lesson |
| POST | `/api/chat/{sid}/generate-lesson/stream` | Same, streamed via SSE |
| POST | `/api/chat/{sid}/generate-slides` | Generate slides |
| POST | `/api/chat/{sid}/generate-slides/stream` | Same, streamed via SSE |
| GET  | `/api/chat/{sid}/export-pptx` | Export `.pptx` file |
| GET  | `/api/chat/{sid}/state` | Fetch full session snapshot (for frontend reconstruction) |
| POST | `/api/chat/{sid}/generate/{task_type}` | Generic background-task dispatcher |
| GET  | `/api/chat/{sid}/generate/{task_type}/status` | Get task status |
| POST | `/api/chat/{sid}/generate/{task_type}/cancel` | Cancel a task |
| GET  | `/api/skills` | List pedagogy skills |
| POST | `/api/chat/{sid}/set-skill` | Switch the active skill |
| POST | `/api/chat/{sid}/toggle-knowledge-skills` | Toggle KS injection |
| POST | `/api/chat/{sid}/toggle-kc-skills` | Toggle KC-skill injection |
| POST | `/api/chat/{sid}/start-review` | Start the L2 review flow |
| POST | `/api/chat/{sid}/submit-review` | Submit review results |
| GET  | `/health` | Health check |

> The frontend uses `GET /api/chat/{sid}/state` to **fully reconstruct** the session view (charts, diagnosis cards, lesson buttons). Any view element that must survive a page refresh **must be derivable from `state`** — relying on the message stream alone is not enough.

---

## Frontend Pages

| Page | URL | Responsibility |
|---|---|---|
| `index.html` | `/` | Main chat, quiz answering, streaming rendering |
| `diagnosis.html` | `/diagnosis.html?session_id=…` | Diagnosis cards + learning radar chart |
| `lesson.html` | `/lesson.html?session_id=…` | Markdown lesson reader |
| `slides.html` | `/slides.html?session_id=…` | PPT renderer (shares `SlideData` with PPTX export) |
| `results.html` | `/results.html?session_id=…` | Quiz summary |

Shared scripts:

- `api.js` — HTTP client + `escapeHtml` / `safeMarkdown` / export utilities
- `sidebar.js` — Session list
- `session-data.js` — `state` deserialization

**XSS discipline:** every `innerHTML` write **must** be sanitized via `escapeHtml` or DOMPurify.

---

## LangGraph Nodes

The state machine assembled in `src/agent/graph.py` branches based on the `intent` node's verdict:

```
intent ──► chat
       ├─► knowledge_graph
       ├─► quiz
       ├─► grading ──► diagnosis ──► lesson
       └─► slides   (outline → per-page content + layout)
```

Slide pipeline post-processing chain (ported from OpenMAIC's `scene-generator.ts`):

```
LLM JSON
   │
   ▼
parse_json_response  →  _fix_element_defaults
                              │
                              ▼
                      _process_latex_elements  (CJK → TextElement)
                              │
                              ▼
                      _enforce_canvas_bounds   (1000 × 562.5)
                              │
                              ▼
                      _assign_ids_and_rotation
                              │
                              ▼
                          Pydantic validation
```

Brittle LLM JSON output is handled by the **4-strategy repair** in `src/utils/json_repair.py`. **Do not** add ad-hoc fixes inside individual nodes.

---

## Skills / Prompts System

- **Skills** (`skills/<name>/SKILL.md`): pluggable pedagogy prompt fragments, loaded by `src/skills/loader.py`, injected into the chat node based on intent and state.
  - Built-in: `feynman` / `scaffolding` / `socratic`
  - **Adding a new skill = a new directory + `SKILL.md`, zero code changes**
- **Prompts** (`src/prompts/*.j2`): every LLM prompt is a Jinja2 template. **Never inline prompt strings inside nodes.**
  - `slide_content_system.j2` is ~961 lines, ported from OpenMAIC, treated as load-bearing — modify with care.

---

## Memory Subservice

`student_memory/` is an independent FastAPI service; see its [README](student_memory/README.md) for details. This repository talks to it through the single seam **`src/memory_client.py`** and has no other coupling.

Main interaction points (see `docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`):

- `chat` node → `POST /retrieve`: injects `context_text` into the system prompt
- End-of-diagnosis / end-of-lesson → `POST /ingest`: writes sub-topic-level MemoryUnits
- `review` node → `POST /notes` / `GET /notes/{uid}/neighbors`: 4-category L2 reflection notes

> Default port 8090; override via `MEMORY_SERVICE_URL`. **When unavailable, the main service automatically skips memory calls.**

---

## 测试

```bash
# 主项目
uv run python -m pytest tests/ -v
uv run python -m pytest tests/test_api.py::test_name -v   # 单例

# memory 子项目
cd student_memory && uv run pytest -v
```

约定：

- `tests/manual/` 下是手动 smoke 脚本，pytest 默认通过 `norecursedirs` **跳过**。
- 一次性回归脚本写完跑过即 `rm`，**不要 `git add`**（见 `MEMORY.md`）。

Lint / Format：

```bash
uv run ruff check src/ run/ tests/
uv run ruff format src/ run/ tests/
```

---

## 开发约定

- **包管理**：仅 `uv`，不混用 pip / poetry。
- **LLM 调用**：全部走 `src/llm/client.py`，全部 `asyncio.to_thread` 包裹。
- **Prompt 修改**：仅改 `.j2` 文件，禁止在 Python 里拼接 prompt 字符串。
- **状态可重放**：任何前端需要在刷新后还原的视觉元素，都要能从 `GET /api/chat/{sid}/state` 推导。
- **PPTElement 扩展**：新增类型时同步修改 5 处 ——`slide_schemas.py`、slides 后处理链、`slides.html` 渲染器、`pptx_export.py`、`slide_content_system.j2`。
- **XSS**：前端所有 `innerHTML` 必须先 `escapeHtml` 或 DOMPurify。

---

## License / Authors

未发布，内部研究项目。
