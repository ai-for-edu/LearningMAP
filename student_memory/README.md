# Student Memory Service

本地优先的 FastAPI 微服务，为学生学习记录提供基于时间衰减的语义检索能力。通过 HTTP 接口被 demo 分支的对话系统调用，服务之间零代码耦合。

## 目录

- [架构概览](#架构概览)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [API 接口](#api-接口)
- [部署方式](#部署方式)
- [与 Demo 分支集成](#与-demo-分支集成)
- [运维操作](#运维操作)
- [测试](#测试)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

---

## 架构概览

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

- **SQLite** 是唯一的数据源（Source of Truth）
- **hnswlib** 是派生的向量索引，可随时从 SQLite 重建
- **嵌入模型**：默认 `BAAI/bge-m3`（1024 维），可配置为 `BAAI/bge-small-zh-v1.5`（512 维）
- **评分公式**：`final = 0.75×sim + 0.20×recency + 0.05×match`，半衰期 30 天

---

## 环境要求

| 依赖 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 项目 `.python-version` 锁定 3.11 |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | 包管理器（与 demo 分支一致） |
| SQLite | 3.35+ | Python 自带，无需单独安装 |

硬件需求取决于嵌入模型：

| 场景 | 内存 | 磁盘 | GPU |
|---|---|---|---|
| FakeEmbedder（测试/开发） | 128 MB | < 10 MB | 不需要 |
| bge-small-zh-v1.5（轻量） | ~1 GB | ~500 MB | 可选 |
| bge-m3（默认/生产） | ~3 GB | ~2.5 GB | 推荐 |

---

## 快速开始

### 1. 仅安装核心依赖（开发/测试用，不下载模型）

```bash
cd student_memory
uv sync
```

### 2. 使用 FakeEmbedder 快速启动（不需要 GPU，适合开发调试）

```bash
SM_USE_FAKE_EMBEDDER=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

验证服务启动：

```bash
curl http://127.0.0.1:8090/health
# {"status":"ok"}
```

### 3. 使用真实嵌入模型启动（生产环境）

```bash
# 安装 ML 依赖（首次执行，会下载 torch + sentence-transformers，约 2 GB）
uv sync --extra ml

# 启动（首次会下载 bge-m3 模型，约 2 GB）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> 如果机器没有 GPU，模型会自动回退到 CPU。可通过 `SM_EMBEDDING_DEVICE=cpu` 显式指定。

---

## 配置说明

所有配置通过 **环境变量**（前缀 `SM_`）或项目根目录的 **`.env` 文件** 加载。

### 完整配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| **服务** | | |
| `SM_SERVICE_HOST` | `127.0.0.1` | 绑定地址 |
| `SM_SERVICE_PORT` | `8090` | 绑定端口（demo 分支使用 8080，不要冲突） |
| `SM_LOG_LEVEL` | `INFO` | 日志级别：`DEBUG` / `INFO` / `WARNING` |
| **存储** | | |
| `SM_DATA_DIR` | `data` | 数据目录（存放 SQLite 和索引文件） |
| `SM_SQLITE_PATH` | `data/memory.db` | SQLite 数据库路径 |
| `SM_INDEX_PATH` | `data/hnsw.bin` | hnswlib 索引文件路径 |
| **嵌入模型** | | |
| `SM_EMBEDDING_MODEL` | `BAAI/bge-m3` | 嵌入模型名称 |
| `SM_EMBEDDING_DIM` | `1024` | 嵌入维度（必须与模型匹配） |
| `SM_EMBEDDING_DEVICE` | `None`（自动选择） | 推理设备：`cpu` / `cuda` / `mps` |
| `SM_USE_FAKE_EMBEDDER` | `0` | 设为 `1` 跳过真实模型（测试/开发用） |
| **检索** | | |
| `SM_TOP_K` | `5` | 默认返回结果数 |
| `SM_RECALL_K` | `50` | hnsw 召回候选数（必须 ≥ `TOP_K`） |
| **评分权重**（三项之和必须 = 1.0） | | |
| `SM_W_SIM` | `0.75` | 语义相似度权重 |
| `SM_W_TIME` | `0.20` | 时间衰减权重 |
| `SM_W_MATCH` | `0.05` | 主题匹配权重 |
| `SM_HALF_LIFE_DAYS` | `30` | 时间衰减半衰期（天） |
| **hnswlib 调优** | | |
| `SM_HNSW_MAX_ELEMENTS` | `100000` | 索引最大容量（自动扩容） |
| `SM_HNSW_EF_CONSTRUCTION` | `200` | 构建质量参数 |
| `SM_HNSW_M` | `16` | 图连接度 |
| `SM_HNSW_EF_SEARCH` | `64` | 搜索精度参数 |

### `.env` 文件示例

```bash
# student_memory/.env
SM_SERVICE_PORT=8090
SM_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
SM_EMBEDDING_DIM=512
SM_LOG_LEVEL=INFO
SM_DATA_DIR=data
```

### 常用模型配置

| 模型 | `SM_EMBEDDING_MODEL` | `SM_EMBEDDING_DIM` | 内存占用 | 适用场景 |
|---|---|---|---|---|
| bge-m3 | `BAAI/bge-m3` | `1024` | ~3 GB | 多语言生产环境 |
| bge-small-zh | `BAAI/bge-small-zh-v1.5` | `512` | ~1 GB | 纯中文 / 低资源环境 |
| FakeEmbedder | `fake-test` | `16` | < 10 MB | 开发 / 测试（需 `SM_USE_FAKE_EMBEDDER=1`） |

> **切换模型后必须重建索引**：不同模型的向量不兼容。服务会在启动时自动检测模型指纹变更并触发重建，也可手动执行 `uv run python -m scripts.rebuild_index`。

---

## API 接口

### `GET /health`

```bash
curl http://127.0.0.1:8090/health
```

```json
{"status": "ok"}
```

### `POST /ingest` — 写入学习记录

```bash
curl -X POST http://127.0.0.1:8090/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u_001",
    "session_id": "s_001",
    "timestamp": "2026-04-01T10:30:00Z",
    "goal_text": "学习一元二次方程",
    "topic": "一元二次方程",
    "subtopics": [
      {
        "name": "判别式",
        "summary": "学生反复混淆根的情况",
        "questions": [
          {
            "question_id": "q1",
            "question_text": "x²-4x+4=0 有几个根？",
            "student_answer": "两个不同实根",
            "is_correct": false,
            "error_type": "判别式混淆"
          }
        ]
      }
    ]
  }'
```

成功响应（200）：

```json
{
  "status": "ok",
  "session_id": "s_001",
  "memory_count": 1
}
```

> `memory_count` = 子主题数量（非题目数量）。每个子主题生成一条记忆单元。

错误响应：
- `422` — 缺少 `user_id`、时间戳格式错误、缺少子主题
- `409` — `session_id` 重复（原子去重，同 session_id 不会写入任何数据）

### `POST /retrieve` — 语义检索

```bash
curl -X POST http://127.0.0.1:8090/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u_001",
    "query": "我想学习一元二次方程",
    "top_k": 3
  }'
```

成功响应（200）：

```json
{
  "query": "我想学习一元二次方程",
  "results": [
    {
      "memory_id": "m_abc123def456",
      "topic": "一元二次方程",
      "subtopic": "判别式",
      "timestamp": "2026-04-01T10:30:00Z",
      "sim_score": 0.92,
      "recency_score": 0.85,
      "match_score": 0.0,
      "final_score": 0.86,
      "retrieval_text": "Learning goal: 学习一元二次方程\nTopic: ..."
    }
  ],
  "context_text": "Relevant past learning records:\n1. Learning goal: ..."
}
```

> `context_text` 用于 LLM prompt 注入。demo 分支的 chat 节点会将此字段直接拼入系统提示词的"对话上下文注入"块。空库时返回 `"(no relevant past learning records)"`。

---

## 部署方式

### 方式一：本地直接运行（推荐开发/单机生产）

```bash
cd student_memory

# 安装依赖
uv sync --extra ml

# 创建 .env（可选）
cat > .env << 'EOF'
SM_SERVICE_PORT=8090
SM_EMBEDDING_MODEL=BAAI/bge-m3
SM_EMBEDDING_DIM=1024
SM_LOG_LEVEL=INFO
EOF

# 启动服务
uv run uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8090 \
  --workers 1
```

> **不要使用多 worker**：hnswlib 索引文件非进程安全。单 worker + 线程并发已足够（服务内部有写入锁）。

### 方式二：Docker 部署

创建 `Dockerfile`：

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制项目文件
COPY pyproject.toml .python-version ./
COPY app/ app/
COPY scripts/ scripts/

# 安装依赖（含 ML 依赖）
RUN uv sync --extra ml --no-dev

# 数据目录（挂载点）
RUN mkdir -p /app/data
VOLUME /app/data

# 默认配置
ENV SM_SERVICE_HOST=0.0.0.0
ENV SM_SERVICE_PORT=8090
ENV SM_DATA_DIR=/app/data
ENV SM_SQLITE_PATH=/app/data/memory.db
ENV SM_INDEX_PATH=/app/data/hnsw.bin

EXPOSE 8090

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
```

创建 `docker-compose.yml`：

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

构建并启动：

```bash
docker compose up -d
docker compose logs -f student-memory
```

### 方式三：轻量部署（无 GPU / 低资源机器）

如果目标机器没有 GPU 或内存有限：

```bash
cat > .env << 'EOF'
SM_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
SM_EMBEDDING_DIM=512
SM_EMBEDDING_DEVICE=cpu
SM_SERVICE_PORT=8090
EOF

uv sync --extra ml
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

### 方式四：纯开发/演示模式（无模型下载）

不下载任何模型，使用确定性的 FakeEmbedder：

```bash
uv sync  # 不需要 --extra ml
SM_USE_FAKE_EMBEDDER=1 SM_EMBEDDING_DIM=16 \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> FakeEmbedder 基于关键词匹配生成确定性向量，适合功能验证和集成调试，不适用于真实语义检索。

---

## 与 Demo 分支集成

### 集成架构

```
Demo 分支 (:8080)                    Memory 分支 (:8090)
┌──────────────────┐                ┌──────────────────┐
│  LangGraph Chat  │ ── POST /retrieve ──►│                  │
│     Node         │◄── context_text ─────│  Student Memory  │
│                  │                      │    Service       │
│  Post-Diagnosis  │ ── POST /ingest ────►│                  │
│  Post-Lesson     │                      └──────────────────┘
└──────────────────┘
```

### Demo 侧调用示例

```python
import httpx

MEMORY_BASE = "http://127.0.0.1:8090"

# 在 chat 节点调用检索，注入 context_text 到 system prompt
async def get_memory_context(user_id: str, query: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MEMORY_BASE}/retrieve",
            json={"user_id": user_id, "query": query, "top_k": 5},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["context_text"]

# 在诊断/课程结束后写入学习记录
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

### 集成注意事项

1. **先启动 memory 服务，再启动 demo 服务**。demo 侧应在调用失败时优雅降级（不注入 context_text 即可），不要因 memory 不可用而阻塞对话。
2. **端口不要冲突**：memory 默认 8090，demo 默认 8080。
3. **`context_text` 可直接拼入 prompt**。空库时值为 `"(no relevant past learning records)"`，demo 侧可据此判断是否注入。
4. **无鉴权**：v1 仅绑定 localhost，不需要 API key。若部署到非本地网络，需加反向代理或网络隔离。

---

## 运维操作

### 数据备份

SQLite 是唯一需要备份的文件。hnswlib 索引可随时从 SQLite 重建。

```bash
# 备份
cp data/memory.db data/memory.db.bak

# 恢复
cp data/memory.db.bak data/memory.db
rm data/hnsw.bin  # 删除索引，启动时会自动重建
```

### 重建索引

以下场景需要重建索引：

- 切换嵌入模型后
- 怀疑索引与 SQLite 数据不一致
- 从备份恢复 SQLite 后
- 索引文件损坏

```bash
# 方式一：手动执行脚本
uv run python -m scripts.rebuild_index

# 方式二：删除索引文件后重启服务（自动重建）
rm data/hnsw.bin
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
# 日志会显示：hnsw index empty but SQLite has N records — rebuilding
```

> 服务启动时会自动检测两种异常并恢复：
> 1. 索引文件缺失/损坏 → 从 SQLite 重建
> 2. 嵌入模型版本变更 → 日志 WARNING 并强制重建

### 切换嵌入模型

```bash
# 1. 停止服务

# 2. 修改 .env
SM_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
SM_EMBEDDING_DIM=512

# 3. 重建索引（可选，启动时也会自动重建）
uv run python -m scripts.rebuild_index

# 4. 重启服务
uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

### 查看数据统计

```bash
# 记录总数
sqlite3 data/memory.db "SELECT COUNT(*) FROM memories;"

# 按用户统计
sqlite3 data/memory.db "SELECT user_id, COUNT(*) FROM memories GROUP BY user_id;"

# 按主题统计
sqlite3 data/memory.db "SELECT topic, COUNT(*) FROM memories GROUP BY topic ORDER BY COUNT(*) DESC;"

# 查看当前嵌入模型指纹
sqlite3 data/memory.db "SELECT * FROM index_meta;"

# 查看已写入的会话列表
sqlite3 data/memory.db "SELECT user_id, session_id, ingested_at FROM ingested_sessions ORDER BY ingested_at DESC LIMIT 10;"
```

---

## 测试

### 运行全部单元测试（不需要 GPU 或模型）

```bash
uv sync  # 安装核心依赖即可
uv run pytest -v
# 82 passed, 1 deselected（deselected 的是 ml 标记的真实模型用例）
```

### 运行含真实模型的测试

```bash
uv sync --extra ml
uv run pytest -v -m ml
```

### 测试覆盖范围

| 测试文件 | 覆盖模块 |
|---|---|
| `test_health.py` | 应用启动、配置校验、torch 懒加载 |
| `test_sqlite_repo.py` | SQLite 仓储层全部读写操作 |
| `test_embedder.py` | Embedder 抽象、FakeEmbedder 确定性、懒加载 |
| `test_hnsw_index.py` | 向量索引：增删查、保存/加载、重建、扩容 |
| `test_parser_normalizer.py` | 输入解析、归一化、retrieval_text 模板 |
| `test_ingest.py` | 端到端写入流程、去重、模型指纹 |
| `test_reranker.py` | 评分公式、权重、衰减、Tie-break |
| `test_retriever.py` | 召回 + 重排、空库、用户隔离 |
| `test_formatter.py` | context_text 格式化 |
| `test_api.py` | HTTP 路由、Pydantic 校验、错误码 |
| `test_integration.py` | 跨阶段集成：写入→检索→重启持久化→索引恢复 |

---

## 项目结构

```
student_memory/
├── app/
│   ├── __init__.py              # __version__ = "0.1.0"
│   ├── main.py                  # FastAPI factory + lifespan
│   ├── api/
│   │   ├── routes.py            # /health, /ingest, /retrieve + wire_app
│   │   └── schemas.py           # Pydantic 请求/响应模型
│   ├── core/
│   │   ├── config.py            # Settings（SM_ 前缀 env vars）
│   │   └── logging.py           # 日志配置
│   ├── db/
│   │   ├── migrations.py        # DDL: memories, hnsw_labels, ingested_sessions, index_meta
│   │   └── sqlite_repo.py       # SqliteRepo（原子事务、label mapping）
│   ├── embedding/
│   │   ├── __init__.py          # 仅导出 Embedder ABC（不拉 torch）
│   │   ├── base.py              # Embedder 抽象基类
│   │   ├── fake_embedder.py     # 确定性测试用嵌入器（无 ML 依赖）
│   │   ├── factory.py           # build_embedder()（无 HTTP 依赖）
│   │   └── local_embedder.py    # BgeEmbedder（sentence-transformers 懒加载）
│   ├── index/
│   │   ├── hnsw_index.py        # HnswIndex（cosine space, sim = 1 - distance）
│   │   └── label_store.py       # label ↔ memory_id 映射抽象
│   ├── memory/
│   │   ├── parser.py            # 原始 payload 解析 + 校验
│   │   ├── normalizer.py        # 按子主题拆分为 MemoryUnit
│   │   ├── retrieval_text.py    # 确定性检索文本模板
│   │   ├── ingestor.py          # Ingestor（原子写入：session + memories + labels）
│   │   ├── reranker.py          # 评分公式 + 排序
│   │   ├── retriever.py         # 召回 → 预过滤 → 重排 → 格式化
│   │   └── formatter.py         # context_text 生成
│   └── utils/
│       └── time.py              # 时间解析、格式化、age_days
├── tests/                       # pytest 测试（82 用例）
├── scripts/
│   └── rebuild_index.py         # 离线索引重建脚本
├── data/                        # SQLite + hnsw 数据（gitignored）
├── pyproject.toml
├── .python-version              # 3.11
└── README.md                    # 本文件
```

---

## 常见问题

### Q: 启动时报 `RuntimeError: sentence-transformers is not installed`

安装 ML 依赖：

```bash
uv sync --extra ml
```

或者使用 FakeEmbedder 跳过真实模型：

```bash
SM_USE_FAKE_EMBEDDER=1 SM_EMBEDDING_DIM=16 uv run uvicorn app.main:app --port 8090
```

### Q: 切换模型后检索结果不对

不同模型的向量维度和语义空间不同，必须重建索引：

```bash
uv run python -m scripts.rebuild_index
```

服务启动时也会自动检测模型变更并重建，但手动执行更可控。

### Q: 可以多进程部署吗？

**不可以。** hnswlib 索引文件不是进程安全的。使用单 worker 部署：

```bash
uv run uvicorn app.main:app --workers 1
```

服务内部通过 `threading.Lock` 支持多线程并发读写。

### Q: `data/memory.db` 可以跨机器迁移吗？

可以。SQLite 文件是自包含的。迁移步骤：

1. 停止服务
2. 复制 `data/memory.db` 到目标机器
3. 不需要复制 `data/hnsw.bin`（启动时会自动重建）
4. 确保目标机器的 `SM_EMBEDDING_MODEL` 和 `SM_EMBEDDING_DIM` 配置正确
5. 启动服务

### Q: 如何清空所有数据重新开始？

```bash
rm -rf data/memory.db data/hnsw.bin
# 下次启动时会自动创建空库
```

### Q: demo 分支应该如何处理 memory 服务不可用的情况？

demo 侧应优雅降级：如果 `POST /retrieve` 超时或返回非 200，不注入 `context_text` 即可，不应阻塞对话流程。memory 服务是增强组件，不是必要组件。

---

## L2 / Progress Endpoints (PR-1, 2026-05-08)

| Method | Path | 用途 |
|---|---|---|
| POST | `/notes` | 创建一条 4-category note (reviewer ADD action) |
| GET  | `/notes/{user_id}?category=` | 列 active notes (按 category) |
| GET  | `/notes/{user_id}/neighbors` | hnswlib top-K 召回 (reviewer call B 输入) |
| GET  | `/notes/by-id/{note_id}` | 读单条 note (reviewer UPDATE/DELETE 取旧值) |
| PATCH | `/notes/by-id/{note_id}` | 更新 content (reviewer UPDATE action) |
| DELETE | `/notes/by-id/{note_id}` | 软删 (reviewer DELETE action) |
| POST | `/progress` | UPSERT 主题里程碑 + 追加 mastery 采样 |
| GET  | `/memory/snapshot/{user_id}` | 渲染 frozen snapshot (≤3500 chars) |
| POST | `/errors/log` | 错误 sink (永不 5xx) |
| GET  | `/errors/{user_id}` | 查 user 最近 100 条错误 |
| GET  | `/errors/correlation/{corr}` | 拉 reviewer 一次完整调用链 |
| GET  | `/history` | 查 notes_history (reviewer 决策审计) |

详见 `docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`。
