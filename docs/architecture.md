# OpenAILearning 系统架构文档

## 概述

全栈 AI 教育助手。学生输入学习主题，系统自动生成知识图谱、进行自适应诊断测验、分析结果并生成针对薄弱知识点的个性化教案。

```
浏览器 (HTML/JS)  ←→  FastAPI (Python)  ←→  LLM (OpenAI 兼容 API)
                          ↕
                    SQLite (sessions.db)
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | 原生 JS + Tailwind CSS |
| 后端 | FastAPI + LangGraph |
| 大模型 | OpenAI 兼容 API（通过 Instructor 实现结构化输出） |
| 持久化 | SQLite（会话状态）+ localStorage（侧边栏） |
| 可视化 | vis.js Network（知识图谱）、marked.js + DOMPurify（Markdown 渲染） |

---

## 目录结构

```
openail/
├── run/
│   └── serve.py                  # FastAPI 应用入口
├── src/
│   ├── api/
│   │   ├── chat.py               # REST API 接口
│   │   └── store.py              # SQLite 会话持久化
│   ├── agent/
│   │   ├── state.py              # LearningState 状态定义
│   │   ├── graph.py              # LangGraph 状态机编排
│   │   └── nodes/                # 7 个图节点
│   │       ├── intent.py         # 意图识别
│   │       ├── chat.py           # 通用对话
│   │       ├── knowledge_graph.py # 知识图谱生成
│   │       ├── quiz.py           # 诊断出题
│   │       ├── grading.py        # 答案判定
│   │       ├── diagnosis.py      # 诊断分析
│   │       └── lesson.py         # 教案生成
│   ├── llm/
│   │   ├── client.py             # OpenAI + Instructor 客户端封装
│   │   └── schemas.py            # Pydantic 数据模型
│   └── prompts/
│       ├── loader.py             # Jinja2 模板加载器
│       ├── intent.j2             # 意图分类提示词
│       ├── knowledge_graph.j2    # 知识图谱生成提示词
│       ├── quiz_generate.j2      # 出题提示词
│       ├── grading.j2            # 判题提示词
│       ├── lesson_plan.j2        # 薄弱知识点教案提示词
│       └── lesson_review.j2      # 全部掌握时复习教案提示词
├── static/
│   ├── index.html                # 主聊天页面
│   ├── diagnosis.html            # 诊断答题页面
│   ├── results.html              # 结果展示页面
│   ├── lesson.html               # 教案学习页面
│   ├── api.js                    # API 客户端 + XSS 防护工具
│   └── sidebar.js                # 会话管理侧边栏
├── tests/
│   ├── test_api.py               # API 接口测试
│   ├── test_schemas.py           # 数据模型测试
│   ├── test_prompts.py           # 提示词模板测试
│   ├── test_graph.py             # 状态机结构测试
│   ├── test_llm_client.py        # LLM 客户端测试
│   └── test_e2e.py               # 端到端集成测试
└── docs/
    └── architecture.md           # 本文档
```

---

## 完整流程：用户旅程

```
┌─────────────────────────────────────────────────────────────┐
│                    index.html（主对话页）                      │
│                                                             │
│  1. 用户输入："我想学习勾股定理"                                 │
│     └─► POST /api/chat                                      │
│         └─► intent_node → 识别为 "learn_topic"               │
│             └─► knowledge_graph_node → 生成知识图谱            │
│  2. AI 回复：[vis.js 交互式知识图谱] + [开始诊断] 按钮           │
│                                                             │
│  3. 用户点击"开始诊断"                                         │
│     └─► 跳转至 diagnosis.html?session_id=xxx                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                diagnosis.html（诊断答题页）                     │
│                                                             │
│  4. 页面加载：POST /api/chat {message: "开始诊断"}             │
│     └─► quiz_node → 一次性生成所有题目                         │
│     └─► 返回：{questions: [...约10道题]}                      │
│                                                             │
│  5. 前端本地处理答题流程：                                      │
│     - 逐题展示，支持上一题/下一题导航                             │
│     - 所有答案在本地收集                                       │
│                                                             │
│  6. 提交：POST /api/chat/{id}/submit-quiz {answers: [...]}   │
│     └─► diagnosis_node → 生成 DiagnosisResult                │
│                                                             │
│  7. 跳转回 /?session_id=xxx&show=results                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              index.html（对话中显示诊断结果）                    │
│                                                             │
│  8. 从后端状态重建完整对话：                                     │
│     - 用户消息 + 知识图谱                                      │
│     - 诊断结果卡片（正确率环形图、知识点分析）                      │
│     - [开始学习] 按钮                                         │
│                                                             │
│  9. 用户点击"开始学习"                                         │
│     └─► 跳转至 lesson.html?session_id=xxx                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  lesson.html（教案学习页）                      │
│                                                             │
│  10. 页面加载：POST /api/chat/{id}/generate-lesson            │
│      └─► lesson_node → 生成 Markdown 教案内容                 │
│      - 蓝色加载动画等待生成                                     │
│      - 使用 marked.js + DOMPurify 渲染                       │
│      - 左侧：目录导航                                         │
│      - 右侧：知识图谱（薄弱点标红）                              │
│                                                             │
│  11. 点击"完成学习" → 回到对话页                                │
│      显示 [Review Lesson] 按钮                                │
└─────────────────────────────────────────────────────────────┘
```

---

## LangGraph 状态机

```
                    ┌──────────┐
                    │  intent  │（入口节点）
                    └────┬─────┘
                         │
            ┌────────────┼────────────┬──────────┐
            ▼            ▼            ▼          ▼
        ┌──────┐  ┌───────────┐  ┌──────┐  ┌────────┐
        │ chat │  │ knowledge │  │ quiz │  │grading │
        │ 对话  │  │   graph   │  │ 出题  │  │ 判题   │
        └──┬───┘  │  知识图谱  │  └──┬───┘  └───┬────┘
           │      └─────┬─────┘     │          │
           ▼            ▼           ▼          │
          END          END         END    ┌────┴────┐
                                         │         │
                                   mode="quiz" mode="result"
                                         │         │
                                         ▼         ▼
                                       quiz   ┌──────────┐
                                       出题    │diagnosis │
                                               │  诊断    │
                                               └────┬─────┘
                                                    │
                                                    ▼
                                               ┌────────┐
                                               │ lesson │
                                               │  教案   │
                                               └───┬────┘
                                                   │
                                                   ▼
                                                  END
```

### 节点详情

| 节点 | 输入 | 是否调用 LLM | 输出 |
|------|------|-------------|------|
| **intent** 意图识别 | 用户消息 | 是（结构化：IntentResult） | mode, topic |
| **chat** 通用对话 | 消息历史 | 是（原始对话补全） | AI 回复 |
| **knowledge_graph** 知识图谱 | 主题字符串 | 是（结构化：KnowledgeGraph） | 带节点和边的图谱 |
| **quiz** 出题 | 知识图谱 | 是（结构化：list[QuizQuestion]） | 约 10 道选择题 |
| **grading** 判题 | 答案 + 题目 | 否（本地比对） | 对/错 + 下一题索引 |
| **diagnosis** 诊断 | 所有答案 | 否（本地计算） | 分数、知识点分析、薄弱点 |
| **lesson** 教案 | 薄弱点 + 图谱 | 是（原始对话补全） | Markdown 教案内容 |

### 路由逻辑

- **意图识别之后**：根据 `mode` 字段路由 → 对话 / 知识图谱 / 出题 / 判题 / 诊断 / 教案
- **判题之后**：如果全部作答完 → 诊断；否则 → 出题（下一题）
- **诊断之后**：固定跳转 → 教案
- **快捷路径**：如果 `mode="quiz"` 且已有题目，直接进入判题

---

## API 接口

| 方法 | 路径 | 功能 | 请求体 | 响应 |
|------|------|------|--------|------|
| POST | `/api/chat` | 发送消息，运行状态机 | `{session_id?, message}` | `{session_id, mode, message, data}` |
| POST | `/api/chat/{id}/submit-quiz` | 提交答题答案，运行诊断 | `{answers: [int]}` | `{session_id, diagnosis_result}` |
| POST | `/api/chat/{id}/generate-lesson` | 生成个性化教案 | — | `{session_id, lesson_content}` |
| GET | `/api/chat/{id}/state` | 获取会话完整状态（含导出数据） | — | 图谱、结果、教案、题目、答案、消息历史 |
| GET | `/health` | 健康检查 | — | `{status: "ok"}` |

### 响应 `mode` 取值

| Mode | 含义 | `data` 字段 |
|------|------|------------|
| `chat` | 通用对话 | — |
| `graph` | 知识图谱已生成 | `{graph: KnowledgeGraph}` |
| `quiz` | 题目已就绪 | `{questions: [QuizQuestion]}` |
| `result` | 诊断完成 | `{result: DiagnosisResult}` |
| `lesson` | 教案已生成 | `{lesson: string}` |

---

## 数据模型（Pydantic）

```
KnowledgeGraph 知识图谱
├── topic: str                          # 学习主题
├── nodes: [KnowledgeNode]              # 知识点节点
│   ├── id: str                         # 节点 ID
│   ├── name: str                       # 知识点名称
│   ├── description: str                # 描述
│   └── level: "prerequisite"|"core"|"extension"  # 层级
└── edges: [KnowledgeEdge]              # 关联边
    ├── source: str                     # 源节点 ID
    ├── target: str                     # 目标节点 ID
    └── relation: str                   # 关系类型

QuizQuestion 诊断题目
├── id: str                             # 题目 ID
├── knowledge_point: str                # 对应知识点
├── question: str                       # 题目内容
├── options: [str]                      # 4 个选项
└── answer: int                         # 正确答案索引（0-3）

DiagnosisResult 诊断结果
├── score: int                          # 正确率百分比
├── total: int                          # 总题数
├── correct: int                        # 答对数
├── incorrect: int                      # 答错数
└── breakdown: [DiagnosisBreakdown]     # 各知识点分析
    ├── name: str                       # 知识点名称
    ├── status: "mastered"|"weak"|"skipped"  # 掌握状态
    └── detail: str                     # 详情描述
```

---

## 会话持久化

```
浏览器                     后端                       SQLite
  │                          │                          │
  │── POST /api/chat ───────►│                          │
  │                          │── graph.invoke(state) ──►│
  │                          │◄── result ──────────────│
  │                          │── save_session() ───────►│
  │                          │   （序列化为 JSON）        │
  │◄── 响应 ────────────────│                          │
  │                          │                          │
  │── （服务器重启）────────────│                          │
  │                          │                          │
  │── GET /state ───────────►│                          │
  │                          │── load_session() ───────►│
  │                          │◄── 反序列化 ─────────────│
  │◄── 状态 ────────────────│                          │
```

- **内存缓存**：`_sessions` 字典，快速读取
- **SQLite 持久化**：`sessions.db`，表结构 `(id TEXT, data TEXT)`
- **序列化**：消息 → `{type, content}` 字典，Pydantic 模型 → `.model_dump()`，整体 JSON 编码
- **反序列化**：重建 `AIMessage`/`HumanMessage` 消息对象，重建 Pydantic 模型实例

---

## 前端架构

### 页面跳转流程

```
index.html ──► diagnosis.html ──► index.html（结果） ──► lesson.html ──► index.html
  主对话          答题诊断           显示诊断结果          学习教案         回到对话
```

### 脚本依赖

```
CDN 库：
├── Tailwind CSS          # 样式
├── Google Material Icons # 图标
├── vis.js Network        # 知识图谱可视化
├── marked.js             # Markdown 渲染
└── DOMPurify             # XSS 消毒

本地脚本：
├── api.js      # 共享：API 请求、escapeHtml、safeMarkdown、exportSession
└── sidebar.js  # 共享：会话列表管理（Rename / Export / Delete）
```

### 会话导出

侧边栏三点菜单 → **Export** → 选择格式：

| 格式 | 说明 | 内容 |
|------|------|------|
| **Markdown** | 学习报告，适合阅读和分享 | 对话记录 → 知识图谱表格 → 诊断题目（含学生答案/正确答案）→ 诊断结果 → 个性化教案 |
| **JSON** | 结构化数据，适合程序处理 | state API 返回的完整原始数据 |

导出在浏览器端完成，不需要额外后端接口。从 `GET /api/chat/{id}/state` 获取数据，前端组装后触发文件下载。

### 会话命名策略

- **新建会话**：侧边栏显示"未命名"
- **自动命名**：当 LLM 返回主题时（知识图谱生成），自动以主题命名
- **兜底策略**：对话 3 轮以上后，取用户消息前 15 字符
- **手动修改**：三点菜单中点击 Rename 行内编辑

---

## 安全措施

| 威胁 | 防护手段 |
|------|---------|
| XSS 跨站脚本 | 所有用户内容使用 `escapeHtml()` 转义，Markdown 使用 DOMPurify 消毒 |
| CORS 跨域 | 通过 `ALLOWED_ORIGINS` 环境变量配置，默认允许所有（开发），生产可限定域名 |
| 输入验证 | 消息最长 5000 字符，答案索引限制 0-3 |
| SQL 注入 | 使用 `?` 参数化查询 |
| 阻塞 I/O | LLM 调用通过 `asyncio.to_thread` 异步化 |
| 错误处理 | 所有 LLM 节点包含 try/except + 日志记录 |
| 资源泄漏 | SQLite 使用 `with` 上下文管理器 |
| 会话错误 | 会话不存在时返回 HTTP 404（`HTTPException`） |

---

## 安装与部署

### 1. 环境要求

- **Python 3.11+**（项目通过 `.python-version` 锁定 3.11）
- **uv**（Python 包管理器，替代 pip + venv）
- **Git**
- 一个 OpenAI 兼容的 API Key（支持 OpenAI、Anthropic 代理、DeepSeek 等）

如果没有 uv，先安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装后重启终端，或执行 `source ~/.zshrc`（macOS）/ `source ~/.bashrc`（Linux）使 `uv` 命令生效。

### 2. 克隆 + 安装依赖

```bash
git clone <仓库地址>
cd openail

# uv 会自动安装 Python 3.11（如果本地没有）并创建虚拟环境
uv sync
```

执行完成后，项目根目录下会出现 `.venv/` 虚拟环境目录，所有依赖已安装。

### 3. 配置 .env

在项目根目录创建 `.env` 文件（已被 `.gitignore` 忽略，不会提交到 Git）：

```bash
# .env
OPENAI_API_KEY=你的API密钥
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
# ALLOWED_ORIGINS=https://yourdomain.com  # 可选，生产环境限定 CORS 来源
```

**不同 API 提供商的配置示例：**

| 提供商 | OPENAI_BASE_URL | LLM_MODEL |
|--------|----------------|-----------|
| OpenAI 官方 | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 第三方代理 | `https://你的代理地址/v1` | 代理支持的模型名 |

> 只要 API 兼容 OpenAI 的 `/v1/chat/completions` 接口格式即可。

### 4. 启动 + 访问

```bash
uv run python run/serve.py
```

看到以下输出表示启动成功：

```
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

打开浏览器访问 **http://localhost:8080**，即可使用。

**运行测试（可选）：**

```bash
uv run python -m pytest tests/ -v
```
