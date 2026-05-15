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

## 所需设备推荐

主服务本身**不跑模型**（推理走外部 LLM 网关），机器压力主要来自 `student_memory` 的本地嵌入模型。按使用方式分档：

### 仅跑主服务（外部 LLM，不启用 memory 子服务）

| 资源 | 推荐 | 最低 |
|---|---|---|
| CPU | 4 核 | 2 核 |
| 内存 | 4 GB | 2 GB |
| 磁盘 | 2 GB（含依赖 + `sessions.db`） | 1 GB |
| GPU | 不需要 | 不需要 |
| 网络 | 能访问 `OPENAI_BASE_URL` 即可；延迟越低响应越快 | — |

> 真正决定体验的是 **LLM 网关的延迟和并发额度**，不是本机性能。流式接口（`/chat/stream` 等）的首字时间几乎等于网关首字时间。

### 启用 memory 子服务（含本地向量嵌入）

memory 服务把全部嵌入推理放在本机，按嵌入器选择决定硬件需求：

| 嵌入器 | `SM_EMBEDDING_MODEL` | 维度 | 内存占用 | 磁盘 | GPU |
|---|---|---|---|---|---|
| **FakeEmbedder**（开发/演示） | `fake-test` | 16 | < 100 MB | < 10 MB | 不需要 |
| **bge-small-zh-v1.5**（轻量） | `BAAI/bge-small-zh-v1.5` | 512 | ~1 GB | ~500 MB | 可选（CPU 可跑） |
| **bge-m3**（默认，多语言） | `BAAI/bge-m3` | 1024 | ~3 GB | ~2.5 GB | 推荐 |

主服务 + memory 服务**部署在同一台机器**时的综合推荐：

| 场景 | CPU | 内存 | 磁盘 | GPU |
|---|---|---|---|---|
| 开发 / 演示（FakeEmbedder） | 4 核 | 4 GB | 2 GB | 无 |
| 单机生产 · bge-small-zh，纯 CPU | 8 核 | 6 GB | 4 GB | 无 |
| 单机生产 · bge-m3，CPU | 8 核 | 8 GB | 6 GB | 无（响应稍慢） |
| 单机生产 · bge-m3，GPU 加速 | 4 核 | 8 GB | 6 GB | ≥ 6 GB 显存（如 RTX 3060 / T4） |

实操建议：

- **首次启动需要下载模型**：bge-m3 ≈ 2 GB、bge-small-zh ≈ 500 MB。提前预热或挂代理。
- **没有 GPU 也能跑** bge-m3，但单条 `/retrieve` 推理在 CPU 上约 200–500 ms；高并发场景建议切 bge-small-zh 或加 GPU。
- **Mac Apple Silicon (M1/M2/M3)** 可用 `SM_EMBEDDING_DEVICE=mps` 走 Metal 加速；8 GB 统一内存机型建议用 bge-small-zh。
- **多进程禁用**：memory 服务的 hnswlib 索引非进程安全，**只跑单 worker**，通过线程并发。

---

## 快速开始

### 1. 安装依赖

```bash
uv sync                              # 主服务
cd student_memory && uv sync && cd ..  # memory 子服务（按需）
```

### 2. 写入 `.env`

```bash
# .env （仓库根目录）
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 可选 —— 不写则使用默认值
MEMORY_SERVICE_URL=http://127.0.0.1:8090
ALLOWED_ORIGINS=*
REVIEW_MIN_AGE_SECONDS=86400

# 可选 —— ClassBA 外部 Agent，仅在调用 run/classba_client.py 时需要
# CLASSBA_AUTH=...
# CLASSBA_API_URL=...
# CLASSBA_AGENT_NAME=...
```

### 3. 启动

**主服务（8080）**

```bash
uv run python run/serve.py
# Uvicorn → http://0.0.0.0:8080
```

**Memory 子服务（8090，可选）**

```bash
cd student_memory
SM_USE_FAKE_EMBEDDER=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> Memory 服务不可用时，主服务**自动优雅降级**（不注入 context_text、不写 ingest），不会阻塞对话。

### 4. 访问前端

浏览器打开 `http://localhost:8080/`（FastAPI 已挂载 `static/`）。

---

## 环境变量

主服务读取的全部变量（在 `.env` 或进程环境中）：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPENAI_API_KEY` | — | **必填**，LLM 鉴权 |
| `OPENAI_BASE_URL` | — | **必填**，OpenAI 兼容端点 |
| `LLM_MODEL` | — | **必填**，模型名 |
| `MEMORY_SERVICE_URL` | `http://127.0.0.1:8090` | student_memory 服务地址 |
| `ALLOWED_ORIGINS` | `*` | CORS 白名单，逗号分隔 |
| `REVIEW_MIN_AGE_SECONDS` | `86400` | 复盘节点最低间隔（秒） |
| `CLASSBA_AUTH` / `CLASSBA_API_URL` / `CLASSBA_AGENT_NAME` | — | 可选，调用 ClassBA 外部 Agent |

LLM 客户端已强制 **bypass OS HTTP proxy**（见 commit `fc7e51c`），无需额外配置代理。

---

## API 接口

主要路由（全部挂在 `/api` 前缀下，详见 `src/api/chat.py`）：

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/chat` | 单轮对话（同步） |
| POST | `/api/chat/stream` | 单轮对话（SSE 流式） |
| POST | `/api/chat/{sid}/generate-quiz/stream` | 生成自适应题目 |
| POST | `/api/chat/{sid}/submit-quiz` | 提交答题 → grading → diagnosis |
| POST | `/api/chat/{sid}/generate-lesson` | 生成 Markdown 课时 |
| POST | `/api/chat/{sid}/generate-lesson/stream` | 同上（SSE） |
| POST | `/api/chat/{sid}/generate-slides` | 生成 PPT |
| POST | `/api/chat/{sid}/generate-slides/stream` | 同上（SSE） |
| GET  | `/api/chat/{sid}/export-pptx` | 导出 `.pptx` 文件 |
| GET  | `/api/chat/{sid}/state` | 取会话完整快照（前端重建用） |
| POST | `/api/chat/{sid}/generate/{task_type}` | 通用后台任务调度 |
| GET  | `/api/chat/{sid}/generate/{task_type}/status` | 查任务状态 |
| POST | `/api/chat/{sid}/generate/{task_type}/cancel` | 取消任务 |
| GET  | `/api/skills` | 列教学法 skill |
| POST | `/api/chat/{sid}/set-skill` | 切换当前 skill |
| POST | `/api/chat/{sid}/toggle-knowledge-skills` | 开关 KS 注入 |
| POST | `/api/chat/{sid}/toggle-kc-skills` | 开关 KC-skill 注入 |
| POST | `/api/chat/{sid}/start-review` | 启动 L2 复盘 |
| POST | `/api/chat/{sid}/submit-review` | 提交复盘结果 |
| GET  | `/health` | 健康检查 |

> 前端会用 `GET /api/chat/{sid}/state` **完整重建**会话视图（图表 / 诊断卡 / 课时按钮）。任何想在刷新后保留的视图元素都**必须可从 state 推导**，不能只依赖消息流。

---

## 前端页面

| 页面 | 入口 | 职责 |
|---|---|---|
| `index.html` | `/` | 主对话、题目作答、流式渲染 |
| `diagnosis.html` | `/diagnosis.html?session_id=…` | 诊断卡片 + 学情雷达图 |
| `lesson.html` | `/lesson.html?session_id=…` | Markdown 课时阅读 |
| `slides.html` | `/slides.html?session_id=…` | PPT 渲染（与 PPTX 导出共用 `SlideData`） |
| `results.html` | `/results.html?session_id=…` | 答题汇总 |

共享脚本：

- `api.js`：HTTP 客户端 + `escapeHtml` / `safeMarkdown` / 导出工具
- `sidebar.js`：会话列表
- `session-data.js`：state 反序列化

**XSS 纪律**：所有 `innerHTML` 必须经 `escapeHtml` 或 DOMPurify。

---

## LangGraph 节点

`src/agent/graph.py` 装配的状态机，按 `intent` 节点的判定分支：

```
intent ──► chat
       ├─► knowledge_graph
       ├─► quiz
       ├─► grading ──► diagnosis ──► lesson
       └─► slides   (outline → 逐页内容 + 布局)
```

幻灯片 pipeline 的后处理链（移植自 OpenMAIC `scene-generator.ts`）：

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
                          Pydantic 校验
```

LLM 返回的 JSON 脆弱性问题由 `src/utils/json_repair.py` 的 **4 策略修复**承担，**不要**在 node 里写 ad-hoc 修复。

---

## Skills / Prompts 体系

- **Skills**（`skills/<name>/SKILL.md`）：可插拔教学法 prompt 片段，加载方 `src/skills/loader.py`，在 chat 节点按 intent/state 注入。
  - 已内置：`feynman` / `scaffolding` / `socratic`
  - **新增 skill = 新目录 + `SKILL.md`，零代码改动**
- **Prompts**（`src/prompts/*.j2`）：所有 LLM prompt 都是 Jinja2 模板，**禁止在 node 里 inline 字符串**。
  - `slide_content_system.j2` ≈ 961 行，移植自 OpenMAIC，视为 load-bearing，谨慎修改。

---

## Memory 子服务

`student_memory/` 是独立 FastAPI 服务，详见其 [README](student_memory/README.md)。本仓库通过 **`src/memory_client.py`** 这一个文件与其通信，无其他耦合。

主要交互点（见 `docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`）：

- `chat` 节点 →  `POST /retrieve`：注入 `context_text` 进 system prompt
- 诊断/课程结束 →  `POST /ingest`：写入子主题级 MemoryUnit
- `review` 节点 →  `POST /notes` / `GET /notes/{uid}/neighbors`：4-category L2 反思笔记

> 默认 8090 端口；可通过 `MEMORY_SERVICE_URL` 覆盖。**不可用时主服务自动跳过**。

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
