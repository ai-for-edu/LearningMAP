# OpenAILearning — 后端架构设计

日期：2026-03-28

---

## 1. 架构概述

后端核心是一个**有状态的对话 Agent**，学生所有消息走同一入口，Agent 内部通过状态机自动路由到对应处理流程。诊断和教案是对话中触发的子流程，结束后自动回到对话模式。

**技术栈：**

| 层次 | 选型 | 理由 |
|------|------|------|
| Web 框架 | FastAPI | 原生 async，SSE 流式响应，Pydantic 集成 |
| 工作流编排 | LangGraph | 状态机管理对话/诊断/教案流程切换 |
| LLM 调用 | Anthropic/OpenAI SDK + instructor | 直接调用，结构化输出，无过度抽象 |
| Prompt 管理 | Jinja2 模板 | 轻量，可维护 |
| ORM | SQLAlchemy 2.0 (async) | SQLite → PostgreSQL 无缝迁移 |
| 数据库（MVP） | SQLite | 零配置启动 |
| 数据库（生产） | PostgreSQL + pgvector | 关系 + 向量一库搞定 |
| 流式响应 | SSE（sse-starlette） | LLM 输出单向流，实现最简 |

---

## 2. 核心架构：对话 Agent 状态机

### 2.1 状态机节点

```
                        ┌─────────┐
                   ┌───►│ 普通对话  │───► 返回回复
                   │    └─────────┘
                   │
┌──────┐    ┌──────┤    ┌──────────┐    ┌──────┐    ┌──────┐    ┌──────────┐
│ 入口  │───►│意图   │───►│知识图谱   │───►│出题   │───►│判题   │───►│诊断结果   │
│      │    │识别   │    │生成      │    │      │    │      │    │         │
└──────┘    └──────┘    └──────────┘    └──┬───┘    └──────┘    └────┬─────┘
                                           │                        │
                                           │◄── 还有下一题 ──────────┘
                                           │
                                      ┌────▼─────┐    ┌──────┐
                                      │ 教案生成   │───►│回到   │
                                      │          │    │对话   │
                                      └──────────┘    └──────┘
```

### 2.2 各节点职责

| 节点 | 输入 | 做什么 | 输出 |
|------|------|--------|------|
| **入口** | 学生消息 | 加入对话历史 | 传给意图识别 |
| **意图识别** | 对话历史 | LLM 判断：普通提问 / 学习主题 / 继续流程 | 路由到对应节点 |
| **普通对话** | 对话历史 | LLM 直接回答问题 | 返回回复 |
| **知识图谱生成** | 主题名 | LLM 生成知识点网络（结构化 JSON） | 图谱数据 |
| **出题** | 图谱 + 当前知识点 | LLM 生成选择题（每知识点 2-3 题） | 题目 JSON，等待学生作答 |
| **判题** | 题目 + 学生答案 | 判对错，答对高阶则跳过前置 | 更新图谱状态 |
| **诊断结果** | 全部作答数据 | 汇总正确率，三色标记（绿/红/灰） | 结果 JSON |
| **教案生成** | 薄弱知识点（红色） | LLM 生成个性化讲解 | 教案内容 |
| **回到对话** | 教案完成 | 状态重置为 chat 模式 | 继续提问 |

### 2.3 全局状态（State）

```python
@dataclass
class LearningState:
    # 对话
    messages: list              # [{"role": "user/assistant", "content": "..."}]
    mode: str                   # "chat" | "graph" | "quiz" | "result" | "lesson"

    # 诊断（诊断时才有值）
    topic: str | None
    knowledge_graph: dict | None    # {nodes: [...], edges: [...]}
    current_question: int
    questions: list
    answers: list
    diagnosis_result: dict | None   # {score, breakdown, graph_nodes}

    # 教案
    lesson_content: str | None
```

---

## 3. API 设计

从之前的 7 个 API 简化为 2 个核心 API：

| API | 方法 | 用途 |
|-----|------|------|
| `/chat` | POST | 发送消息，返回 AI 回复（SSE 流式） |
| `/chat/{session_id}/state` | GET | 获取当前会话状态（前端判断显示哪个 UI） |

### 3.1 POST /chat

**请求：**
```json
{
  "session_id": "abc123",
  "message": "我想学习二元一次方程"
}
```

**响应（SSE 流式）：**
```json
{
  "mode": "graph",
  "message": "我已经为你梳理了二元一次方程的知识图谱...",
  "graph": {
    "topic": "二元一次方程",
    "nodes": [...],
    "edges": [...]
  }
}
```

### 3.2 前端根据 mode 切换 UI

| mode | 前端展示 |
|------|---------|
| `chat` | 聊天界面 |
| `graph` | 聊天界面 + 内嵌知识图谱 + "开始诊断"按钮 |
| `quiz` | 切换到诊断分区界面（左题目右作答） |
| `result` | 回到聊天界面，展示诊断结果卡片 |
| `lesson` | 切换到三栏教案界面 |

---

## 4. 数据模型

### 4.1 会话表（sessions）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | str | 会话名称（"二元一次方程"） |
| created_at | datetime | 创建时间 |
| state_json | JSON | LangGraph 状态快照 |

### 4.2 消息表（messages）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| session_id | FK | 关联会话 |
| role | str | "user" / "assistant" |
| content | text | 消息内容 |
| metadata | JSON | mode、graph、question 等附加数据 |
| created_at | datetime | 时间戳 |

### 4.3 知识图谱数据结构

```json
{
  "topic": "二元一次方程",
  "nodes": [
    {"id": "n1", "name": "一元一次方程", "description": "...", "level": "prerequisite"},
    {"id": "n2", "name": "二元一次方程组", "description": "...", "level": "core"}
  ],
  "edges": [
    {"from": "n1", "to": "n2", "relation": "prerequisite"}
  ]
}
```

### 4.4 诊断结果结构

```json
{
  "score": 70,
  "total": 10,
  "correct": 7,
  "incorrect": 3,
  "breakdown": [
    {"name": "一元一次方程", "status": "mastered", "detail": "2/2 已掌握"},
    {"name": "二元一次方程组", "status": "weak", "detail": "1/3 需复习"},
    {"name": "因式分解", "status": "skipped", "detail": "已跳过"}
  ]
}
```

---

## 5. 诊断跳过逻辑

```
当学生答对节点 X 的多数题目（如 2/3）:
  1. 标记 X 为绿色（mastered）
  2. 沿 edge 反向遍历所有前置节点
  3. 前置节点标记为灰色（skipped），移出待诊断队列
```

---

## 6. 项目目录结构

```
openail/
├── run/
│   └── serve.py                      # FastAPI 启动入口
├── src/
│   ├── api/                          # API 路由层
│   │   ├── __init__.py
│   │   └── chat.py                   # POST /chat, GET /state
│   ├── agent/                        # LangGraph Agent
│   │   ├── __init__.py
│   │   ├── graph.py                  # 状态机定义（nodes + edges）
│   │   ├── state.py                  # LearningState 定义
│   │   └── nodes/                    # 各节点实现
│   │       ├── __init__.py
│   │       ├── intent.py             # 意图识别
│   │       ├── chat.py               # 普通对话
│   │       ├── knowledge_graph.py    # 知识图谱生成
│   │       ├── quiz.py               # 出题
│   │       ├── grading.py            # 判题
│   │       ├── diagnosis.py          # 诊断结果
│   │       └── lesson.py             # 教案生成
│   ├── llm/                          # LLM 调用封装
│   │   ├── __init__.py
│   │   ├── client.py                 # LLM 客户端工厂
│   │   └── schemas.py                # Pydantic 输出 schema
│   ├── prompts/                      # Jinja2 prompt 模板
│   │   ├── intent.j2
│   │   ├── knowledge_graph.j2
│   │   ├── quiz_generate.j2
│   │   ├── grading.j2
│   │   └── lesson_plan.j2
│   ├── data/                         # 数据层
│   │   ├── __init__.py
│   │   ├── models.py                 # SQLAlchemy ORM models
│   │   ├── database.py               # DB 连接管理
│   │   └── repository.py             # 数据访问层
│   └── utils/
│       ├── __init__.py
│       └── logging.py
├── tests/
├── .env                              # API keys (gitignored)
├── pyproject.toml
└── docs/plans/
```

---

## 7. 技术选型调研依据

### 7.1 为什么选 FastAPI

| 对比 | FastAPI | Flask | Django |
|------|---------|-------|--------|
| 异步 | 原生 async | WSGI 受限 | 不完整 |
| LLM 流式 | StreamingResponse 原生 | 需额外适配 | 需 Channels |
| 结构化输出 | Pydantic 原生 | 需 marshmallow | DRF serializers |
| 自动文档 | Swagger UI 自动生成 | 手动 | 手动 |

### 7.2 为什么选 LangGraph 而非 LangChain

- LangChain：通用 LLM 编排，抽象层多，API 变更频繁
- LangGraph：专注状态机工作流，适合"对话中触发子流程"的场景
- 本项目不需要 LangChain 的 Chain/Agent 抽象，LLM 调用直接用 SDK

### 7.3 为什么不用 OpenMAIC / OpenClaw

| 项目 | 不适合原因 |
|------|-----------|
| OpenMAIC | 全栈单体（前后端耦合），核心是多 Agent 课堂模拟，无知识图谱/自适应学习 |
| OpenClaw | 通用个人助手平台，与教育场景无关 |

### 7.4 数据库演进路径

| 阶段 | 关系型 | 向量存储 |
|------|--------|---------|
| MVP（0-3月） | SQLite | 无（记忆模块后续加） |
| 生产（3月+） | PostgreSQL | pgvector |

---

## 8. 对话流示例

```
学生: 你好
  → Agent: mode=chat → "你好！我是你的学习助手，有什么想学的吗？"

学生: 我想学习二元一次方程
  → Agent: 意图识别=学习主题 → 知识图谱生成 → mode=graph
  → 返回图谱 JSON + "我为你梳理了知识图谱，准备好了点开始诊断"

学生: 开始诊断
  → Agent: mode=quiz → 出题 → 返回第1题

学生: B
  → Agent: 判题（对了）→ 还有题 → 出题 → 返回第2题

...(10题答完)...

  → Agent: 诊断结果 → mode=result → 返回70%正确率 + 三色图谱
  → 教案生成 → mode=lesson → 返回教案内容

学生: 不等式那部分我还是不太懂
  → Agent: mode=chat → LLM 针对不等式详细解释

学生: 帮我再出几道不等式的题
  → Agent: 意图识别=局部诊断 → 出题（仅不等式）→ mode=quiz
```

---

## 9. MVP 边界

### 做

- 对话式交互（单会话内有状态）
- LLM 实时生成知识图谱
- 选择题诊断（答对跳前置）
- 三色结果展示
- 纯文本教案生成
- 对话中自由提问

### 不做（后续迭代）

- 跨会话记忆
- 向量检索
- 用户认证
- BKT/IRT 学生能力建模
- 多媒体教案
- 再测环节

---

## 10. 风险提示

1. **LLM 延迟**：知识图谱生成 5-30 秒，务必流式返回 + 前端 loading
2. **结构化输出失败**：LLM 有时不遵循 JSON schema，需重试机制（instructor 内置）
3. **成本控制**：每次生成图谱/教案消耗大量 token，相同主题可缓存
4. **并发限制**：LLM API 有 rate limit，需请求队列
