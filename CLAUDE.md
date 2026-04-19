# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Uses `uv` (required; `.python-version` pins 3.11).

```bash
uv sync                                  # install deps
uv run python run/serve.py               # start FastAPI on :8080 (reads .env)
uv run python -m pytest tests/ -v        # full test suite
uv run python -m pytest tests/test_api.py::test_name -v  # single test
uv run ruff check src/ run/ tests/       # lint
uv run ruff format src/ run/ tests/      # format
```

The `student_memory/` subproject is an independent FastAPI service with its own `pyproject.toml` and tests. It runs on port 8090 and is called over HTTP by `src/memory_client.py`. It is NOT installed by the root `uv sync` — `cd student_memory && uv sync` separately, and start it before the main app if memory features are needed.

`.env` must contain `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` (OpenAI-compatible; DeepSeek etc. work). Optional `MEMORY_SERVICE_URL` (default `http://127.0.0.1:8090`), `ALLOWED_ORIGINS`.

## Architecture

Full-stack AI tutoring app. Flow: topic → knowledge graph → adaptive quiz → diagnosis → personalized lesson (Markdown or PPT).

**Backend orchestration is a LangGraph state machine** (`src/agent/graph.py`) with single-turn execution per HTTP request. The `intent` node is the entry router, dispatching to `chat`, `knowledge_graph`, `quiz`, or `grading→diagnosis→lesson`. State is `LearningState` (`src/agent/state.py`), persisted per session.

**Sessions** (`src/api/store.py`) live in an in-memory dict + SQLite (`sessions.db`). Messages and Pydantic models are serialized to JSON; on load they are reconstructed. The frontend rebuilds the full conversation from `GET /api/chat/{id}/state` — so anything you want replayed after reload (charts, graphs, diagnosis cards, lesson buttons) must be derivable from state, not just from the message stream.

**LLM calls** go through `src/llm/client.py` (OpenAI SDK + Instructor for structured output). Pydantic schemas in `src/llm/schemas.py` (core) and `src/llm/slide_schemas.py` (PPT elements). All LLM calls are wrapped in `asyncio.to_thread` — do not block the event loop.

**Prompts** are Jinja2 templates in `src/prompts/*.j2` loaded via `loader.py`. Never inline prompt strings in nodes; add a `.j2` file and load it. `slide_content_system.j2` is ~961 lines ported from OpenMAIC — treat as load-bearing, do not casually edit.

**Slides pipeline** (`src/agent/nodes/slides.py`) is two-stage: (1) outline → list of 8-15 `SlideOutline`; (2) per-page = content draft call + layout-JSON call. The layout JSON goes through a post-processing chain modeled on OpenMAIC's `scene-generator.ts`: `parse_json_response` → `_fix_element_defaults` → `_process_latex_elements` (CJK → TextElement) → `_enforce_canvas_bounds` (1000×562.5) → `_assign_ids_and_rotation` → Pydantic. JSON from LLMs is brittle; `src/utils/json_repair.py` has a 4-strategy repair — prefer extending it over adding ad-hoc fixes in nodes.

**Skills** (`skills/{feynman,scaffolding,socratic}/SKILL.md`, loaded by `src/skills/loader.py`) are pluggable pedagogy prompt fragments injected into the chat node based on intent/state. Add a new skill = new directory with `SKILL.md`, no code change.

**Frontend** is vanilla JS + Tailwind across 5 static HTML pages (`static/`). `api.js` is the shared client (plus `escapeHtml`, `safeMarkdown`, export helpers); `sidebar.js` handles session list. Navigation is full-page transitions: `index.html` ↔ `diagnosis.html` ↔ `lesson.html` / `slides.html`, all keyed by `?session_id=`. All `innerHTML` writes MUST use `escapeHtml` or DOMPurify.

**PPTX export** (`src/export/pptx_export.py`) re-renders the same `SlideData` the browser sees. TextElement parses HTML inline styles; LatexElement is rendered via matplotlib to PNG and embedded. When adding a new `PPTElement` variant, update: `slide_schemas.py`, the slides post-processing chain, `slides.html` renderer, and `pptx_export.py`.

## Branch context

Current branch is `demo+memory`. Notable design docs in `docs/plans/` cover skill system, chat context injection, and slide pipeline — consult these before non-trivial changes to those subsystems.

## Subagent decomposition for this codebase

When delegating work, these splits map cleanly onto the architecture and minimize context overlap:

- **graph/node agent** — changes to `src/agent/**`, `src/llm/schemas.py`, prompt `.j2` files. Owns LangGraph wiring, state shape, LLM call contracts.
- **slides pipeline agent** — `src/agent/nodes/slides.py`, `src/llm/slide_schemas.py`, `src/export/pptx_export.py`, `static/slides.html`, slide `.j2` prompts. Keep slides work isolated because the post-processing chain and OpenMAIC prompt are intricate.
- **frontend agent** — `static/*.html`, `api.js`, `sidebar.js`. Owns XSS discipline, state-reconstruction rendering, page transitions.
- **api/persistence agent** — `src/api/**`, `run/serve.py`, session serialization. Any change to request/response shape also touches frontend, so this agent should hand off an interface contract.
- **memory-service agent** — scoped to `student_memory/` (separate project) + `src/memory_client.py` as the only integration seam.
- **skills/prompts agent** — `skills/**`, `src/skills/loader.py`, `src/prompts/**`. Pedagogy/content changes with no Python-logic impact.
- **test agent** — `tests/**`. Parallelizable with any of the above after interface is settled.

Run agents in parallel when their file sets don't overlap (e.g., slides + memory-service + skills). Serialize when a schema change in `src/llm/schemas.py` must land before node/frontend consumers.
