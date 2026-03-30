# OpenAILearning Architecture

## Overview

Full-stack AI education assistant. Students input a learning topic, the system generates a knowledge graph, conducts adaptive diagnostic quizzes, analyzes results, and produces personalized lessons for weak areas.

```
Browser (HTML/JS)  ←→  FastAPI (Python)  ←→  LLM (OpenAI-compatible API)
                          ↕
                    SQLite (sessions.db)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla JS + Tailwind CSS |
| Backend | FastAPI + LangGraph |
| LLM | OpenAI-compatible API (via Instructor for structured output) |
| Persistence | SQLite (session state) + localStorage (sidebar) |
| Visualization | vis.js Network (knowledge graph), marked.js + DOMPurify (markdown) |

---

## Directory Structure

```
openail/
├── run/
│   └── serve.py                  # FastAPI app entry point
├── src/
│   ├── api/
│   │   ├── chat.py               # REST API endpoints
│   │   └── store.py              # SQLite session persistence
│   ├── agent/
│   │   ├── state.py              # LearningState definition
│   │   ├── graph.py              # LangGraph state machine
│   │   └── nodes/                # 7 graph nodes
│   │       ├── intent.py         # Intent classification
│   │       ├── chat.py           # General conversation
│   │       ├── knowledge_graph.py # Knowledge graph generation
│   │       ├── quiz.py           # Quiz question generation
│   │       ├── grading.py        # Answer grading
│   │       ├── diagnosis.py      # Diagnostic analysis
│   │       └── lesson.py         # Lesson generation
│   ├── llm/
│   │   ├── client.py             # OpenAI + Instructor client
│   │   └── schemas.py            # Pydantic data models
│   └── prompts/
│       ├── loader.py             # Jinja2 template renderer
│       ├── intent.j2             # Intent classification prompt
│       ├── knowledge_graph.j2    # Graph generation prompt
│       ├── quiz_generate.j2      # Quiz generation prompt
│       ├── grading.j2            # Answer grading prompt
│       ├── lesson_plan.j2        # Weak-area lesson prompt
│       └── lesson_review.j2      # Mastery review prompt
├── static/
│   ├── index.html                # Main chat page
│   ├── diagnosis.html            # Quiz page
│   ├── results.html              # Results page
│   ├── lesson.html               # Lesson page
│   ├── api.js                    # API client + XSS utilities
│   └── sidebar.js                # Session management
├── tests/
│   ├── test_api.py               # API endpoint tests
│   ├── test_schemas.py           # Schema validation tests
│   ├── test_prompts.py           # Prompt template tests
│   ├── test_graph.py             # LangGraph structure tests
│   ├── test_llm_client.py        # LLM client tests
│   └── test_e2e.py               # End-to-end flow tests
└── docs/
    └── architecture.md           # This file
```

---

## Pipeline: User Journey

```
┌─────────────────────────────────────────────────────────────┐
│                     index.html (Chat)                       │
│                                                             │
│  1. User: "我想学习勾股定理"                                   │
│     └─► POST /api/chat                                      │
│         └─► intent_node → "learn_topic"                     │
│             └─► knowledge_graph_node → KnowledgeGraph       │
│  2. AI: [vis.js interactive graph] + [Start Diagnosis]      │
│                                                             │
│  3. User clicks "Start Diagnosis"                           │
│     └─► Redirect to diagnosis.html?session_id=xxx           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  diagnosis.html (Quiz)                       │
│                                                             │
│  4. Page load: POST /api/chat {message: "开始诊断"}           │
│     └─► quiz_node → generates all questions at once         │
│     └─► Returns: {questions: [...10 items]}                 │
│                                                             │
│  5. Frontend handles quiz locally:                          │
│     - Shows questions one by one                            │
│     - Previous / Next navigation                            │
│     - Collects all answers locally                          │
│                                                             │
│  6. Submit: POST /api/chat/{id}/submit-quiz {answers: [...]}│
│     └─► diagnosis_node → DiagnosisResult                    │
│                                                             │
│  7. Redirect to /?session_id=xxx&show=results               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                index.html (Results in Chat)                  │
│                                                             │
│  8. Rebuilds conversation from state:                       │
│     - User message + Knowledge graph                        │
│     - Diagnosis result card (score, breakdown)              │
│     - [Start Learning] button                               │
│                                                             │
│  9. User clicks "Start Learning"                            │
│     └─► Redirect to lesson.html?session_id=xxx              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   lesson.html (Lesson)                       │
│                                                             │
│  10. Page load: POST /api/chat/{id}/generate-lesson         │
│      └─► lesson_node → markdown lesson content              │
│      - Blue loading spinner while generating                │
│      - Renders with marked.js + DOMPurify                   │
│      - Left: outline navigation                             │
│      - Right: knowledge graph (weak points in red)          │
│                                                             │
│  11. "Finish Learning" → back to chat with                  │
│      [Review Lesson] button                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## LangGraph State Machine

```
                    ┌──────────┐
                    │  intent  │ (entry point)
                    └────┬─────┘
                         │
            ┌────────────┼────────────┬──────────┐
            ▼            ▼            ▼          ▼
        ┌──────┐  ┌───────────┐  ┌──────┐  ┌────────┐
        │ chat │  │ knowledge │  │ quiz │  │grading │
        └──┬───┘  │   graph   │  └──┬───┘  └───┬────┘
           │      └─────┬─────┘     │          │
           ▼            ▼           ▼          │
          END          END         END    ┌────┴────┐
                                         │         │
                                    mode="quiz" mode="result"
                                         │         │
                                         ▼         ▼
                                       quiz   ┌──────────┐
                                               │diagnosis │
                                               └────┬─────┘
                                                    │
                                                    ▼
                                               ┌────────┐
                                               │ lesson │
                                               └───┬────┘
                                                   │
                                                   ▼
                                                  END
```

### Node Details

| Node | Input | LLM Call | Output |
|------|-------|----------|--------|
| **intent** | User message | Yes (structured: IntentResult) | mode, topic |
| **chat** | Messages history | Yes (raw completion) | AI reply |
| **knowledge_graph** | Topic string | Yes (structured: KnowledgeGraph) | Graph with nodes/edges |
| **quiz** | Knowledge graph | Yes (structured: list[QuizQuestion]) | 10 questions |
| **grading** | Answer + question | No (local comparison) | Correct/incorrect + next index |
| **diagnosis** | All answers | No (local computation) | Score, breakdown, weak points |
| **lesson** | Weak points + graph | Yes (raw completion) | Markdown lesson content |

### Routing Logic

- **After intent**: Route by `mode` field → chat / knowledge_graph / quiz / grading / diagnosis / lesson
- **After grading**: If all answered → diagnosis; else → quiz (next question)
- **After diagnosis**: Always → lesson
- **Quiz shortcut**: If `mode="quiz"` and questions exist, skip to grading directly

---

## API Endpoints

| Method | Path | Purpose | Request | Response |
|--------|------|---------|---------|----------|
| POST | `/api/chat` | Send message, run graph | `{session_id?, message}` | `{session_id, mode, message, data}` |
| POST | `/api/chat/{id}/submit-quiz` | Submit quiz answers | `{answers: [int]}` | `{session_id, diagnosis_result}` |
| POST | `/api/chat/{id}/generate-lesson` | Generate lesson | — | `{session_id, lesson_content}` |
| GET | `/api/chat/{id}/state` | Get session state | — | Full state with graph, results, lesson |
| GET | `/health` | Health check | — | `{status: "ok"}` |

### Response `mode` Values

| Mode | Meaning | `data` Field |
|------|---------|-------------|
| `chat` | General conversation | — |
| `graph` | Knowledge graph generated | `{graph: KnowledgeGraph}` |
| `quiz` | Questions ready | `{questions: [QuizQuestion]}` |
| `result` | Diagnosis complete | `{result: DiagnosisResult}` |
| `lesson` | Lesson generated | `{lesson: string}` |

---

## Data Models (Pydantic Schemas)

```
KnowledgeGraph
├── topic: str
├── nodes: [KnowledgeNode]
│   ├── id: str
│   ├── name: str
│   ├── description: str
│   └── level: "prerequisite" | "core" | "extension"
└── edges: [KnowledgeEdge]
    ├── source: str (node id)
    ├── target: str (node id)
    └── relation: str

QuizQuestion
├── id: str
├── knowledge_point: str
├── question: str
├── options: [str]  (4 choices)
└── answer: int     (correct index 0-3)

DiagnosisResult
├── score: int      (percentage)
├── total: int
├── correct: int
├── incorrect: int
└── breakdown: [DiagnosisBreakdown]
    ├── name: str
    ├── status: "mastered" | "weak" | "skipped"
    └── detail: str
```

---

## Session Persistence

```
Browser                    Backend                      SQLite
  │                          │                            │
  │── POST /api/chat ───────►│                            │
  │                          │── graph.invoke(state) ────►│
  │                          │◄── result ────────────────│
  │                          │── save_session(id, state)─►│
  │                          │   (serialize → JSON)       │
  │◄── response ────────────│                            │
  │                          │                            │
  │── (server restart) ──────│                            │
  │                          │                            │
  │── GET /api/chat/{id}/state►│                           │
  │                          │── load_session(id) ───────►│
  │                          │◄── deserialize ───────────│
  │◄── state ───────────────│                            │
```

- **In-memory cache**: `_sessions` dict for fast access
- **SQLite fallback**: `sessions.db` with `(id TEXT, data TEXT)` table
- **Serialization**: Messages → `{type, content}`, Pydantic → `.model_dump()`, JSON
- **Deserialization**: Rebuild `AIMessage`/`HumanMessage`, reconstruct Pydantic models

---

## Frontend Architecture

### Page Flow

```
index.html ──► diagnosis.html ──► index.html (results) ──► lesson.html ──► index.html
   chat          quiz/answer         show results          learn lesson      review
```

### Script Dependencies

```
CDN Libraries:
├── Tailwind CSS (styling)
├── Google Material Symbols (icons)
├── vis.js Network (knowledge graph)
├── marked.js (markdown rendering)
└── DOMPurify (XSS sanitization)

Local Scripts:
├── api.js (shared: fetch, escapeHtml, safeMarkdown)
└── sidebar.js (shared: session list management)
```

### Session Naming

- **New Session**: Creates "未命名" entry in sidebar
- **Auto-rename**: When LLM returns a topic (knowledge graph), rename to topic name
- **Fallback**: After 3+ rounds of chat, use first 15 chars of message
- **Manual**: Inline rename via three-dot menu

---

## Security Measures

| Threat | Mitigation |
|--------|-----------|
| XSS | `escapeHtml()` for all user content, DOMPurify for markdown |
| CORS | Restricted to `localhost:8080`, credentials disabled |
| Input validation | Message max 5000 chars, answer indices 0-3 |
| SQL injection | Parameterized queries via `?` placeholders |
| Blocking I/O | `asyncio.to_thread` for LLM calls |
| Error handling | Try/except with logging in all LLM nodes |
| Resource leaks | SQLite context managers (`with` statement) |
| Session errors | HTTP 404 via `HTTPException` for missing sessions |

---

## Startup

```bash
# Install dependencies
uv sync

# Configure API
cp .env.example .env  # Set OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL

# Run server
uv run python run/serve.py  # http://localhost:8080

# Run tests
uv run python -m pytest tests/ -v
```
