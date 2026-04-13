# demo+memory 分支介绍文档

## 1. 分支概述

`demo+memory` 基于 `demo` 分支合并了 `memory` 分支的 **student_memory** 模块，形成了一个具备跨会话学习记忆能力的 AI 教育助手。

```
demo 分支（主应用）          memory 分支（记忆服务）
├── LangGraph 状态机        ├── 语义检索引擎
├── 知识图谱生成            ├── 时间衰减重排
├── 诊断测评               ├── 向量索引 (hnswlib)
├── 课程/PPT 生成           └── SQLite 元数据存储
└── 前端交互界面
         ↓  合并
   demo+memory 分支
   ├── 完整学习流程
   ├── 跨会话记忆（检索+写入）
   └── HTTP 接口对接
```

### 核心价值

- **有记忆的教学**：学生上次哪里薄弱，这次 AI 记得
- **零侵入集成**：两个服务通过 HTTP 通信，代码完全解耦
- **优雅降级**：记忆服务不可用时，主应用照常工作

---

## 2. 系统架构

### 2.1 整体架构

```
浏览器 (HTML/JS)
    │
    ▼
┌─────────────────────────────────────────────┐
│  openail 主应用 (FastAPI, port 8080)         │
│                                             │
│  LangGraph 状态机                            │
│  ├── intent_node    意图识别                  │
│  ├── chat_node ────── POST /retrieve ──┐    │
│  ├── knowledge_graph_node              │    │
│  ├── quiz_node                         │    │
│  ├── grading_node                      │    │
│  ├── diagnosis_node                    │    │
│  ├── lesson_node                       │    │
│  └── slides_node                       │    │
│                                        │    │
│  REST API                              │    │
│  └── submit_quiz ──── POST /ingest ──┐ │    │
│                                      │ │    │
│  SQLite (sessions.db)                │ │    │
└──────────────────────────────────────┼─┼────┘
                                       │ │
                                       ▼ ▼
┌─────────────────────────────────────────────┐
│  student_memory 记忆服务 (FastAPI, port 8090)│
│                                             │
│  POST /ingest   ← 写入学习记录               │
│  POST /retrieve ← 检索历史记忆               │
│  GET  /health   ← 健康检查                   │
│                                             │
│  SQLite (memory.db) + hnswlib (hnsw.bin)    │
└─────────────────────────────────────────────┘
```

### 2.2 目录结构

```
openail/
├── run/serve.py                    # 主应用入口
├── src/
│   ├── memory_client.py            # [新增] 记忆服务 HTTP 客户端
│   ├── agent/nodes/
│   │   ├── chat.py                 # [修改] 接入 /retrieve
│   │   └── ...
│   ├── api/chat.py                 # [修改] 接入 /ingest
│   └── ...
├── student_memory/                 # [新增] 记忆服务（完整独立项目）
│   ├── app/
│   │   ├── main.py                 # FastAPI 工厂
│   │   ├── api/                    # 路由 + Pydantic 模型
│   │   ├── core/                   # 配置 + 日志
│   │   ├── db/                     # SQLite 仓库 + 迁移
│   │   ├── embedding/              # 嵌入模型（可插拔）
│   │   ├── index/                  # hnswlib 向量索引
│   │   ├── memory/                 # 核心逻辑（解析/归一化/检索/重排/格式化）
│   │   └── utils/                  # 时间工具
│   ├── tests/                      # 82 个单元测试
│   ├── scripts/rebuild_index.py    # 离线索引重建
│   └── pyproject.toml              # 独立依赖管理
├── tests/
│   ├── test_memory_client.py       # [新增] 记忆客户端单元测试 (19)
│   ├── test_cross_session_memory.py# [新增] 跨会话集成测试 (8)
│   ├── test_product_memory.py      # [新增] 产品级审计测试 (29)
│   └── ...
└── docs/
    └── demo-memory-branch.md       # 本文档
```

### 2.3 技术栈

| 组件 | 主应用 | 记忆服务 |
|------|-------|---------|
| 框架 | FastAPI + LangGraph | FastAPI |
| 数据库 | SQLite (sessions.db) | SQLite (memory.db) |
| 向量索引 | — | hnswlib |
| 嵌入模型 | — | BAAI/bge-m3 (1024维) / FakeEmbedder (测试) |
| HTTP 客户端 | httpx | — |
| Python | 3.11+ | 3.11+ |
| 包管理 | uv | uv |

---

## 3. 学习流程

完整的用户旅程不变（详见 `architecture.md`），记忆系统在以下两个点介入：

```
用户输入主题
  ↓
knowledge_graph_node → 生成知识图谱
  ↓
用户开始诊断 → quiz_node → 出题
  ↓
用户提交答案 → diagnosis_node → 诊断分析
  ↓                                    ┌──────────────────────┐
  ├── ★ POST /ingest ────────────────→ │ 记忆服务：存储本次    │
  │   （后台，非阻塞）                    │ 学习记录到 memory.db │
  ↓                                    └──────────────────────┘
用户选择学习格式 → lesson/slides 生成
  ↓
下次用户开新 session，聊天时
  ↓                                    ┌──────────────────────┐
  ├── ★ POST /retrieve ──────────────→ │ 记忆服务：检索历史    │
  │   （同步，注入 prompt）               │ 学习记忆             │
  ↓                                    └──────────────────────┘
chat_node 带着历史记忆回答用户
```

---

## 4. Student Memory 记忆服务

### 4.1 设计原则

| 原则 | 说明 |
|------|------|
| **本地优先** | 零云依赖，单机运行，SQLite + hnswlib |
| **SQLite 是唯一真相源** | hnswlib 索引可从 SQLite 完整重建 |
| **优雅降级** | 空索引/空库/服务不可用均返回稳定空响应 |
| **确定性测试** | FakeEmbedder 保证测试无 ML 依赖 |
| **懒加载** | torch/sentence-transformers 仅在首次调用时导入 |

### 4.2 数据模型

每次学习会话按**子知识点**（subtopic）粒度拆分存储：

```sql
CREATE TABLE memories (
    memory_id    TEXT PRIMARY KEY,    -- m_{12-hex}
    user_id      TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    memory_type  TEXT NOT NULL,       -- "subtopic"
    topic        TEXT,                -- 学习主题
    subtopic     TEXT,                -- 知识点名称
    timestamp    TEXT NOT NULL,       -- ISO-8601 UTC
    retrieval_text TEXT NOT NULL,     -- 用于嵌入和检索的文本
    raw_json     TEXT NOT NULL,       -- 原始数据切片
    question_count INTEGER,
    correct_count  INTEGER,
    accuracy       REAL,
    error_types    TEXT
);
```

### 4.3 检索文本格式

每条记忆生成一段确定性文本，用于向量嵌入和检索展示：

```
Learning goal: 学习 勾股定理
Topic: 勾股定理
Subtopic: 勾股定理公式
Summary: weak: 1/2 需复习
Stats: 2 questions, 1 correct, accuracy 50%
Error types: wrong_answer
```

### 4.4 评分与重排公式

```
final_score = 0.75 × sim_score + 0.20 × recency_score + 0.05 × match_score
```

| 分量 | 计算方式 | 默认权重 |
|------|---------|---------|
| **sim_score** | 1 - cosine_distance（向量语义相似度） | 0.75 |
| **recency_score** | exp(-ln2 × age_days / 30)（30天半衰期指数衰减） | 0.20 |
| **match_score** | 主题/子主题精确匹配奖励 | 0.05 |

排序优先级：final_score desc → sim_score desc → timestamp desc

### 4.5 API 接口

#### POST /ingest — 写入学习记录

```json
// 请求
{
  "user_id": "default_user",
  "session_id": "abc-123",
  "goal_text": "学习 勾股定理",
  "topic": "勾股定理",
  "subtopics": [
    {
      "name": "勾股定理公式",
      "summary": "weak: 1/2 需复习",
      "questions": [
        {
          "question_id": "q2",
          "question_text": "3²+4²=?",
          "student_answer": "1",
          "is_correct": true,
          "error_type": null
        }
      ]
    }
  ]
}

// 响应
{"status": "ok", "session_id": "abc-123", "memory_count": 2}

// 错误
// 409: 重复的 session_id
// 422: 请求校验失败
```

#### POST /retrieve — 检索历史记忆

```json
// 请求
{"user_id": "default_user", "query": "我之前学了什么", "top_k": 5}

// 响应
{
  "query": "我之前学了什么",
  "results": [
    {
      "memory_id": "m_abc123def45",
      "topic": "勾股定理",
      "subtopic": "勾股定理公式",
      "timestamp": "2026-04-13T12:00:00Z",
      "sim_score": 0.89,
      "recency_score": 0.98,
      "match_score": 0.0,
      "final_score": 0.86,
      "retrieval_text": "Learning goal: 学习 勾股定理\nTopic: ..."
    }
  ],
  "context_text": "Relevant past learning records:\n1. Learning goal: ..."
}
```

### 4.6 嵌入模型

| 模型 | 维度 | 用途 | 说明 |
|------|------|------|------|
| BAAI/bge-m3 | 1024 | 生产默认 | 中英文多语言，首次需下载 ~2GB |
| BAAI/bge-small-zh-v1.5 | 512 | 轻量备选 | 中文专用，更小更快 |
| FakeEmbedder | 16 | 测试/开发 | 零 ML 依赖，hash 生成确定性向量 |

### 4.7 持久化与恢复

```
正常运行:
  SQLite (memory.db)  ← 唯一真相源
  hnswlib (hnsw.bin)  ← 派生的向量索引

索引丢失/损坏:
  启动时检测 → 自动从 SQLite 重建 → 日志警告

嵌入模型切换:
  启动时检测模型名变化 → 自动重建索引 → 更新模型指纹
```

---

## 5. 集成实现

### 5.1 记忆客户端 (`src/memory_client.py`)

封装了与 student_memory 服务的所有 HTTP 交互：

```python
# 检索历史记忆（用于 chat_node）
retrieve_memory(user_id, query, top_k=5) -> str
# 返回 context_text 或空字符串（失败时优雅降级）

# 写入学习记录（用于 submit_quiz）
ingest_session(user_id, session_id, state) -> None
# 将 openail 的 state 转换为 /ingest payload 并发送

# 共享常量
DEFAULT_USER_ID = "default_user"
MEMORY_SERVICE_URL = os.getenv("MEMORY_SERVICE_URL", "http://127.0.0.1:8090")
```

设计要点：
- **线程安全**：`threading.Lock` 保护 httpx.Client 初始化
- **短连接超时**：connect=2s, total=10s，避免服务不可用时阻塞主应用
- **精确异常捕获**：`httpx.HTTPError` 和 `Exception` 分级处理
- **409 去重**：重复 ingest 静默跳过

### 5.2 检索接入 — chat_node

`src/agent/nodes/chat.py` 在构建 LLM system prompt 前调用 `/retrieve`：

```python
def chat_node(state):
    # 1. 构建当前会话上下文（主题、图谱、诊断结果）
    context = _build_context_summary(state)
    system_prompt = _BASE_SYSTEM_PROMPT + context

    # 2. ★ 检索历史学习记忆
    user_msg = messages[-1].content
    memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
    if memory_context:
        system_prompt += f"\n\n## 历史学习记忆\n{memory_context}"

    # 3. 调用 LLM
    ...
```

### 5.3 写入接入 — submit_quiz

`src/api/chat.py` 在诊断完成后后台写入记忆：

```python
async def submit_quiz(session_id, req):
    # 1. 运行诊断
    diag_result = await asyncio.to_thread(diagnosis_node, state)
    state.update(diag_result)
    _save(session_id, state)

    # 2. ★ 后台写入记忆（非阻塞，不影响响应速度）
    asyncio.get_event_loop().run_in_executor(
        None, ingest_session, DEFAULT_USER_ID, session_id, state
    )

    # 3. 返回诊断结果
    return {"session_id": ..., "diagnosis_result": ...}
```

### 5.4 数据映射

openail 的 `LearningState` 到 student_memory 的 `IngestRequest` 的映射关系：

```
LearningState                    IngestRequest
├── topic ──────────────────────→ topic, goal_text
├── questions[]                   subtopics[]
│   ├── knowledge_point ────────→   name (按知识点分组)
│   ├── question/options ───────→   questions[].question_text
│   └── answer ─────────────────→   questions[].is_correct (与 answers[] 比对)
├── answers[] ──────────────────→ questions[].student_answer
└── diagnosis_result
    └── breakdown[] ────────────→ subtopics[].summary (status + detail)
```

---

## 6. 测试体系

### 6.1 测试覆盖

| 测试文件 | 数量 | 覆盖范围 |
|---------|------|---------|
| `student_memory/tests/` | 82 | 记忆服务全模块（解析、存储、嵌入、索引、检索、重排、API） |
| `tests/test_memory_client.py` | 19 | 记忆客户端（数据映射、HTTP 调用、异常处理） |
| `tests/test_cross_session_memory.py` | 8 | 跨会话集成（写入→检索、累积、用户隔离、去重） |
| `tests/test_product_memory.py` | 29 | 产品级审计（写入覆盖、读取覆盖、用户旅程、数据质量） |

### 6.2 运行测试

```bash
# 记忆服务测试（独立 venv）
cd student_memory && uv run python -m pytest tests/ -v

# 主应用测试（含集成测试，需 student_memory venv 已 sync）
cd openail && uv run python -m pytest tests/ -v
```

---

## 7. 启动与使用

### 7.1 首次安装

```bash
# 1. 安装主应用依赖
cd openail
uv sync

# 2. 安装记忆服务依赖
cd student_memory
uv sync --extra dev    # 开发/测试用 FakeEmbedder
uv sync --extra ml     # 生产用真实嵌入模型（需下载 bge-m3）
```

### 7.2 启动服务

```bash
# 终端 1: 记忆服务
cd student_memory
SM_USE_FAKE_EMBEDDER=1 uv run uvicorn app.main:app --port 8090
# 生产模式去掉 SM_USE_FAKE_EMBEDDER=1

# 终端 2: 主应用
cd openail
uv run python run/serve.py

# 浏览器打开 http://localhost:8080
```

### 7.3 环境变量

**主应用 (.env)**

| 变量 | 默认值 | 说明 |
|------|-------|------|
| OPENAI_API_KEY | — | LLM API 密钥 |
| OPENAI_BASE_URL | — | LLM API 地址 |
| LLM_MODEL | gpt-4o | 模型名称 |
| MEMORY_SERVICE_URL | http://127.0.0.1:8090 | 记忆服务地址 |

**记忆服务 (环境变量，SM_ 前缀)**

| 变量 | 默认值 | 说明 |
|------|-------|------|
| SM_USE_FAKE_EMBEDDER | false | 使用测试用假嵌入模型 |
| SM_EMBEDDING_MODEL | BAAI/bge-m3 | 嵌入模型名称 |
| SM_DATA_DIR | data/ | 数据目录 |
| SM_SERVICE_PORT | 8090 | 服务端口 |
| SM_W_SIM | 0.75 | 语义相似度权重 |
| SM_W_TIME | 0.20 | 时间衰减权重 |
| SM_HALF_LIFE_DAYS | 30 | 衰减半衰期（天） |

---

## 8. 记忆系统缺失能力（待实现）

基于 29 项产品级审计测试，当前 20 通过，9 项缺失：

### 8.1 记忆写入覆盖不足

| 缺失 | 现状 | 产品预期 | 难度 |
|------|------|---------|------|
| **只生成知识图谱时无记忆** | 用户浏览了主题但不做诊断 → 下次完全不记得 | 知识图谱的 nodes 应转为 subtopics 写入 | 中 |
| **课程生成后不写入** | generate_lesson / generate_slides 不触发 ingest | 应记录"学过什么课、针对哪些薄弱点" | 低 |
| **知识图谱模式不写入** | /api/chat 在 mode=graph 时不触发 ingest | 探索主题即应留痕 | 中 |
| **纯聊天无记忆** | 闲聊讨论学习内容，完全不留痕 | 应提取对话主题作为轻量记忆 | 高 |

### 8.2 记忆读取覆盖不足

| 缺失 | 现状 | 产品预期 | 难度 |
|------|------|---------|------|
| **lesson_node 不读取历史** | 课程生成不考虑过往薄弱点 | 应注入历史："上次你这里弱，这次重点讲" | 低 |
| **knowledge_graph_node 不读取历史** | 知识图谱不考虑学习历史 | 应标注"这个知识点你之前薄弱" | 低 |
| **quiz_node 不读取历史** | 出题不侧重历史薄弱点 | 应多出之前错过的知识点的题 | 低 |

### 8.3 数据映射不足

| 缺失 | 现状 | 产品预期 | 难度 |
|------|------|---------|------|
| **知识图谱 nodes 无法转为 subtopics** | `_build_ingest_payload` 仅从 questions 构建 subtopics | 无诊断时应从 knowledge_graph.nodes 构建 | 中 |

### 8.4 优先级建议

```
优先级 1（每项 3-5 行改动，高收益）:
  ├── lesson_node 加 retrieve_memory
  ├── knowledge_graph_node 加 retrieve_memory
  ├── quiz_node 加 retrieve_memory
  ├── generate_lesson 加 ingest_session
  └── generate_slides 加 ingest_session

优先级 2（中等改动，扩展 _build_ingest_payload）:
  ├── 支持从 knowledge_graph.nodes 构建 subtopics
  └── /api/chat graph 模式触发 ingest

优先级 3（需要 LLM 辅助，后期做）:
  └── 纯聊天对话记忆（需从对话中提取主题摘要）
```
