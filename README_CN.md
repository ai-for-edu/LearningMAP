<div align="center">

<img src="images/logo.png" alt="LearningMAP" width="220" style="border-radius: 15px;">

# LearningMAP：基于记忆与个性化的学习

<p align="center">
  <a href="./README.md"><img alt="English" src="https://img.shields.io/badge/English-CDCFD4?style=flat-square"></a>
  <a href="./README_CN.md"><img alt="简体中文" src="https://img.shields.io/badge/简体中文-BCDCF7?style=flat-square"></a>
</p>

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/built%20with-LangGraph-7C3AED?style=flat-square)](https://github.com/langchain-ai/langgraph)
[![uv](https://img.shields.io/badge/packaging-uv-DE5F00?style=flat-square)](https://docs.astral.sh/uv/)
[![Status](https://img.shields.io/badge/status-internal%20research-lightgrey?style=flat-square)](#-状态)

</div>

一个 AI 辅导助手，把用户输入的单个主题转化为一个自适应学习闭环 —— 知识图谱 → 测验 → 诊断 → Markdown 课程 → 幻灯片 → 复盘 —— 并由一个独立子服务提供跨会话语义记忆。

---

## 目录

- [概览](#-概览)
- [核心特性](#-核心特性)
- [架构](#-架构)
- [能力总览](#-能力总览)
- [仓库结构](#-仓库结构)
- [安装](#-安装)
- [配置](#%EF%B8%8F-配置)
- [API 参考](#-api-参考)
- [前端页面](#-前端页面)
- [硬件建议](#-硬件建议)
- [测试与开发约定](#-测试与开发约定)
- [状态](#-状态)

---

## 📖 概览

LearningMAP 是一个全栈辅导 Demo。前端是一组静态页面；后端是一个基于 **LangGraph** 状态机构建的 FastAPI 服务。每一次 HTTP 请求驱动一次单轮图执行；分支由一个 `intent` 节点选择：

```
intent ──► chat
       ├─► knowledge_graph
       ├─► quiz
       ├─► grading ──► diagnosis ──► lesson
       └─► slides   (大纲 → 逐页内容 + 布局)
```

本分支在基础 Demo 之上接入了一个独立的**学习记忆子服务**（`student_memory/`），提供跨会话语义检索与 L2 反思笔记。会话状态以内存与 SQLite 双重持久化。

系统**不读取用户上传的文档** —— 内容由 LLM 直接从主题生成，再可选地与跨会话记忆库做对齐。

---

## ✨ 核心特性

### 📚 学习内容流水线

一条线性闭环，把一个主题转成多形态学习材料：

- **知识图谱** —— 带掌握度标注的实体 / 技能图（`mastery_annotator.py`）。
- **自适应测验** —— 题目按当前 KG 掌握状态校准。
- **判分与诊断** —— 逐题判分喂入诊断卡片与学习雷达图。
- **Markdown 课程** —— 针对诊断暴露的薄弱点定向生成；通过 SSE 流式返回。
- **幻灯片** —— 大纲优先的流水线，通过 `python-pptx` 导出 `.pptx`。后处理链强制 1000 × 562.5 画布、修复 LaTeX/CJK 元素，并分配稳定 ID（见 [架构](#-架构)）。

### 🧠 持久化记忆

一个独立的 FastAPI 微服务（`student_memory/`）提供：

- `POST /retrieve` —— 把语义召回注入 chat 节点的系统提示。
- `POST /ingest` —— 在诊断 / 课程结束时写入子主题粒度的 MemoryUnit。
- `POST /notes` 与 `GET /notes/{uid}/neighbors` —— 用于错题复盘的 4 类 L2 反思笔记。

整个接缝是单一文件 `src/memory_client.py`。**当该服务不可用时，主服务会优雅降级** —— 不注入上下文、不写 ingest、不阻塞。

### 🎓 可插拔的教学法 Skills

教学风格是可替换的工件，不是代码：

- 每种教学法是一个目录，包含一份 `SKILL.md`（如 `skills/feynman/`、`skills/scaffolding/`、`skills/socratic/`）。
- 新增一种教学法是**一个新目录 + 一份 `SKILL.md` —— 零 Python 改动**。
- 另有一份独立的 **KC-skill 库**（通过 `run/build_kc_skills.py` 离线构建），由 `kc_skill_resolver.py` 按主题解析，让 skill 注入可以限定到特定的知识点。
- Skills 在运行时通过 `POST /api/chat/{sid}/set-skill` 切换。

### 🔁 复盘闭环（L2 反思）

主学习闭环之后，复盘节点会：

- 生成 4 类错题归因摘要。
- 通过 `/notes` 持久化到 `student_memory`。
- 后续会话再通过 `/retrieve` 把这些笔记浮现回来，闭合跨会话反馈环。

两次复盘之间的最短间隔由 `REVIEW_MIN_AGE_SECONDS` 控制（默认 24 小时）。

---

## 🏛 架构

### 流水线示意图

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

### LangGraph 状态机

- 每次 HTTP 请求即一次单轮图执行，在 `src/agent/graph.py` 中装配。
- `LearningState`（Pydantic）跨节点承载完整的执行上下文。
- `chat` 节点把历史记忆快照作为系统提示增强注入，在存储为空时安全降级。

### 幻灯片后处理链

脆弱的 LLM JSON 通过一条固定的链路归一化 —— **请勿**在单个节点里添加临时修复：

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

JSON 修复本身集中在 `src/utils/json_repair.py`（4 策略）。

### 记忆与知识层

- **向量库**：`student_memory/` 使用 hnswlib HNSW，配合 BAAI/bge-m3（默认）或 bge-small-zh-v1.5 嵌入。
- **会话存储**：内存 dict + SQLite（`sessions.db`）双写，通过 `GET /api/chat/{sid}/state` 暴露给前端做完整重建。
- **遗忘曲线**：`src/forgetting/` 实现一个 Ebbinghaus 风格的衰减，用于复盘触发器。

### LLM 层

- **Provider**：仅限 OpenAI 兼容网关（DeepSeek / Moonshot / 任意 OpenAI 兼容 API）。没有多 provider 抽象，没有 tool-use 循环。
- **结构化输出**：在 OpenAI SDK 之上使用 Instructor；schema 定义在 `src/llm/schemas.py` 与 `src/llm/slide_schemas.py`。
- **异步纪律**：每一次 LLM 调用都过 `src/llm/client.py`，并包裹在 `asyncio.to_thread` 中，事件循环永远不被阻塞。
- **提示词**：全部位于 `src/prompts/*.j2`，以 Jinja2 模板形式存在。**绝不**在 Python 模块中内联提示词字符串。`slide_content_system.j2`（约 961 行）是承重模块。

---

## 🔑 能力总览

- **自适应学习闭环** —— 主题 → KG → 测验 → 判分 → 诊断 → 定向课程，所有状态在刷新后均可从会话状态推导。
- **幻灯片生成** —— Markdown 课程重新渲染为 16:9 幻灯片（1000 × 562.5 画布），具备 LaTeX / CJK 感知的后处理与 `.pptx` 导出。
- **跨会话记忆** —— 历史会话以系统提示增强的形式回流，通过单一接缝解耦，离线时优雅降级。
- **可插拔教学法** —— 运行时切换教学风格、无需重新部署；新教学法只是普通目录。
- **全链路流式** —— chat、lesson、slides 全部走 SSE。

---

## 📁 仓库结构

```
LearningMAP/
├── run/
│   ├── serve.py                 # FastAPI 入口（端口 8080）
│   ├── classba_client.py        # 外部 ClassBA Agent 客户端（可选）
│   └── build_kc_skills.py       # KC-skill 知识表的离线构建
├── src/
│   ├── api/                     # FastAPI 路由 + 会话存储 (SQLite)
│   │   ├── chat.py              # 所有 HTTP 端点
│   │   ├── store.py             # 内存 dict + sessions.db 双写
│   │   └── tasks.py             # 后台异步任务（生成 lesson / slides）
│   ├── agent/
│   │   ├── graph.py             # LangGraph 状态机装配
│   │   ├── state.py             # LearningState (Pydantic)
│   │   ├── mastery_annotator.py # KG 节点的掌握度标注
│   │   └── nodes/
│   │       ├── intent.py        # 入口路由：chat / kg / quiz / lesson / slides
│   │       ├── chat.py          # 自由问答 + 记忆快照注入
│   │       ├── knowledge_graph.py
│   │       ├── quiz.py          # 自适应出题
│   │       ├── grading.py       # 判分
│   │       ├── diagnosis.py     # 学习诊断
│   │       ├── lesson.py        # Markdown 课程
│   │       ├── slides.py        # PPT 大纲 + 每页布局
│   │       ├── review.py        # L2 反思 / 错题复盘
│   │       ├── kc_skill_resolver.py
│   │       └── _helpers.py
│   ├── llm/
│   │   ├── client.py            # OpenAI 兼容客户端 + Instructor
│   │   ├── schemas.py           # 核心 Pydantic 模型
│   │   └── slide_schemas.py     # PPTElement / SlideData
│   ├── prompts/                 # 全部 Jinja2 .j2 模板
│   ├── skills/                  # 教学法 skills 加载器
│   ├── forgetting/              # 遗忘曲线（Ebbinghaus）
│   ├── export/pptx_export.py    # PPT → .pptx 导出 (python-pptx)
│   ├── utils/json_repair.py     # 4 策略 JSON 修复
│   ├── memory_client.py         # 通向 student_memory 的单一接缝
│   ├── progress_aggregator.py
│   └── reviewer.py
├── skills/                      # 可插拔教学法 (feynman / scaffolding / socratic)
│   └── <skill>/SKILL.md
├── static/                      # 前端（原生 JS + Tailwind）
│   ├── index.html               # 主聊天页
│   ├── diagnosis.html           # 诊断卡 + 学习雷达图
│   ├── lesson.html              # Markdown 课程阅读器
│   ├── slides.html              # PPT 渲染器
│   ├── results.html
│   ├── api.js                   # 共享 HTTP 客户端
│   └── sidebar.js               # 会话列表
├── tests/                       # pytest 用例（含 manual/ 烟囱脚本，默认跳过）
├── student_memory/              # 独立 FastAPI 微服务（见下文）
├── pyproject.toml               # 主项目依赖
├── .python-version              # 3.11
└── sessions.db                  # SQLite 会话持久化（运行时创建）
```

> `student_memory/` 是一个**独立子项目**，拥有自己的 `pyproject.toml` / `uv.lock` / `tests/`。根目录的 `uv sync` **不会**安装它。

---

## 🚀 安装

### 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.11 | 由 `.python-version` 锁定 |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | 唯一支持的包管理器 |
| SQLite | 3.35+ | 随 Python 自带 |
| OpenAI 兼容 LLM | — | DeepSeek / Moonshot / 任意 OpenAI 兼容网关 |

### 本地

```bash
uv sync                                # 主服务
cd student_memory && uv sync && cd ..  # 记忆子服务（可选）
```

### 运行

**主服务（端口 8080）**

```bash
uv run python run/serve.py
# Uvicorn → http://0.0.0.0:8080
```

**记忆子服务（端口 8090，可选）**

```bash
cd student_memory
SM_USE_FAKE_EMBEDDER=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> 当记忆服务不可用时，主服务会**优雅降级**（不注入 `context_text`、不写 `ingest`），不会阻塞对话。

主服务启动后打开 `http://localhost:8080/` —— FastAPI 把 `static/` 挂载到根路径。

---

## ⚙️ 配置

主服务读取的全部变量（在 `.env` 或进程环境中设置）：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPENAI_API_KEY` | — | **必填**。LLM 鉴权。 |
| `OPENAI_BASE_URL` | — | **必填**。OpenAI 兼容端点。 |
| `LLM_MODEL` | — | **必填**。模型名。 |
| `MEMORY_SERVICE_URL` | `http://127.0.0.1:8090` | `student_memory` 服务地址。 |
| `ALLOWED_ORIGINS` | `*` | 逗号分隔的 CORS 白名单。 |
| `REVIEW_MIN_AGE_SECONDS` | `86400` | 两次复盘节点调用之间的最短间隔（秒）。 |
| `CLASSBA_AUTH` / `CLASSBA_API_URL` / `CLASSBA_AGENT_NAME` | — | 可选。用于 ClassBA 外部 Agent 调用。 |

LLM 客户端写死会**绕过操作系统 HTTP 代理**（见 commit `fc7e51c`），无需额外的代理配置。

`.env` 示例：

```bash
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 可选 —— 省略时使用默认值
MEMORY_SERVICE_URL=http://127.0.0.1:8090
ALLOWED_ORIGINS=*
REVIEW_MIN_AGE_SECONDS=86400
```

---

## 🔌 API 参考

所有路由挂载在 `/api` 下（完整列表见 `src/api/chat.py`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 单轮对话（同步） |
| POST | `/api/chat/stream` | 单轮对话（SSE 流式） |
| POST | `/api/chat/{sid}/generate-quiz/stream` | 生成自适应测验 |
| POST | `/api/chat/{sid}/submit-quiz` | 提交答案 → 判分 → 诊断 |
| POST | `/api/chat/{sid}/generate-lesson` | 生成 Markdown 课程 |
| POST | `/api/chat/{sid}/generate-lesson/stream` | 同上，SSE 流式 |
| POST | `/api/chat/{sid}/generate-slides` | 生成幻灯片 |
| POST | `/api/chat/{sid}/generate-slides/stream` | 同上，SSE 流式 |
| GET  | `/api/chat/{sid}/export-pptx` | 导出 `.pptx` 文件 |
| GET  | `/api/chat/{sid}/state` | 获取完整会话快照（用于前端重建） |
| POST | `/api/chat/{sid}/generate/{task_type}` | 通用后台任务分发器 |
| GET  | `/api/chat/{sid}/generate/{task_type}/status` | 获取任务状态 |
| POST | `/api/chat/{sid}/generate/{task_type}/cancel` | 取消任务 |
| GET  | `/api/skills` | 列出教学法 skills |
| POST | `/api/chat/{sid}/set-skill` | 切换当前激活的 skill |
| POST | `/api/chat/{sid}/toggle-knowledge-skills` | 切换 KS 注入 |
| POST | `/api/chat/{sid}/toggle-kc-skills` | 切换 KC-skill 注入 |
| POST | `/api/chat/{sid}/start-review` | 启动 L2 复盘流程 |
| POST | `/api/chat/{sid}/submit-review` | 提交复盘结果 |
| GET  | `/health` | 健康检查 |

> 前端通过 `GET /api/chat/{sid}/state` **完整重建**会话视图（图表、诊断卡、课程按钮）。任何需要在页面刷新后存活的视图元素**必须可从 `state` 推导** —— 仅依赖消息流是不够的。

---

## 🖥 前端页面

| 页面 | URL | 职责 |
|---|---|---|
| `index.html` | `/` | 主对话、答题、流式渲染 |
| `diagnosis.html` | `/diagnosis.html?session_id=…` | 诊断卡片 + 学习雷达图 |
| `lesson.html` | `/lesson.html?session_id=…` | Markdown 课程阅读器 |
| `slides.html` | `/slides.html?session_id=…` | PPT 渲染器（与 PPTX 导出共享 `SlideData`） |
| `results.html` | `/results.html?session_id=…` | 测验小结 |

共享脚本：

- `api.js` —— HTTP 客户端 + `escapeHtml` / `safeMarkdown` / 导出工具
- `sidebar.js` —— 会话列表

**XSS 纪律**：每一处 `innerHTML` 写入**必须**先经过 `escapeHtml` 或 DOMPurify。

---

## 💻 硬件建议

主服务本身**不在本地跑模型**（推理被卸载到外部 LLM 网关）。机器负载主要来自 `student_memory` 的本地嵌入模型。

### 仅主服务（外部 LLM、记忆子服务关闭）

| 资源 | 推荐 | 最低 |
|---|---|---|
| CPU | 4 核 | 2 核 |
| 内存 | 4 GB | 2 GB |
| 磁盘 | 2 GB（依赖 + `sessions.db`） | 1 GB |
| GPU | 不需要 | 不需要 |
| 网络 | 可达 `OPENAI_BASE_URL`；延迟越低响应越快 | — |

> 决定使用体验的是 **LLM 网关的延迟与并发配额**，不是本地机器性能。流式接口（`/chat/stream` 等）的 TTFT 本质上就是网关的 TTFT。

### 启用记忆子服务（本地向量嵌入）

记忆服务把所有嵌入推理放在本地完成；硬件要求取决于所选的嵌入器：

| 嵌入器 | `SM_EMBEDDING_MODEL` | 维度 | 内存 | 磁盘 | GPU |
|---|---|---|---|---|---|
| **FakeEmbedder**（开发 / 演示） | `fake-test` | 16 | < 100 MB | < 10 MB | 不需要 |
| **bge-small-zh-v1.5**（轻量） | `BAAI/bge-small-zh-v1.5` | 512 | ~1 GB | ~500 MB | 可选（CPU 可用） |
| **bge-m3**（默认，多语种） | `BAAI/bge-m3` | 1024 | ~3 GB | ~2.5 GB | 推荐 |

主服务与记忆服务**同机部署**时的推荐规格：

| 场景 | CPU | 内存 | 磁盘 | GPU |
|---|---|---|---|---|
| 开发 / 演示（FakeEmbedder） | 4 核 | 4 GB | 2 GB | 无 |
| 单机生产 · bge-small-zh，纯 CPU | 8 核 | 6 GB | 4 GB | 无 |
| 单机生产 · bge-m3，CPU | 8 核 | 8 GB | 6 GB | 无（稍慢） |
| 单机生产 · bge-m3，GPU 加速 | 4 核 | 8 GB | 6 GB | ≥ 6 GB 显存（如 RTX 3060 / T4） |

实践提示：

- **首次启动会下载模型**：bge-m3 约 2 GB、bge-small-zh 约 500 MB。请提前预热或走代理。
- **GPU 并非强制**：bge-m3 在 CPU 上单次 `/retrieve` 推理约 200–500 ms；高并发场景请切换到 bge-small-zh 或加 GPU。
- **Mac Apple Silicon（M1/M2/M3）** 可通过 `SM_EMBEDDING_DEVICE=mps` 启用 Metal 加速；8 GB 统一内存机型优先选用 bge-small-zh。
- **仅单 worker**：记忆服务的 hnswlib 索引非进程安全。**只跑单 worker**，并依赖线程级并发。

---

## 🧪 测试与开发约定

```bash
# 主项目
uv run python -m pytest tests/ -v
uv run python -m pytest tests/test_api.py::test_name -v   # 单个测试

# 记忆子项目
cd student_memory && uv run pytest -v
```

Lint / 格式化：

```bash
uv run ruff check src/ run/ tests/
uv run ruff format src/ run/ tests/
```

约定：

- **包管理**：只用 `uv`；不要混入 pip / poetry。
- **LLM 调用**：始终通过 `src/llm/client.py`，始终包裹在 `asyncio.to_thread` 中。
- **修改提示词**：仅在 `.j2` 文件中进行；绝不在 Python 中拼接提示词字符串。
- **可重放状态**：任何前端需要在刷新后还原的视图元素，必须可从 `GET /api/chat/{sid}/state` 推导。
- **扩展 `PPTElement`**：新增类型时需同步更新 5 处 —— `slide_schemas.py`、幻灯片后处理链、`slides.html` 渲染器、`pptx_export.py`、`slide_content_system.j2`。
- **XSS**：前端任何 `innerHTML` 写入都要先过 `escapeHtml` 或 DOMPurify。
- **`tests/manual/` 下的测试**是烟囱脚本，pytest 通过 `norecursedirs` 默认跳过。
- **一次性回归脚本**用完即 `rm`，不要 `git add`。

---

## 📝 状态

内部研究项目。未发布。
