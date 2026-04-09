# OpenAILearning 系统架构文档

## 概述

全栈 AI 教育助手。学生输入学习主题，系统自动生成知识图谱、进行自适应诊断测验、分析结果并生成针对薄弱知识点的个性化教案（Markdown 或 PPT 幻灯片）。

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
| 可视化 | vis.js Network（知识图谱）、ECharts（图表）、KaTeX（公式）、marked.js + DOMPurify（Markdown） |
| 导出 | python-pptx（PPTX 导出）、matplotlib（LaTeX 公式渲染） |

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
│   │   └── nodes/                # 图节点
│   │       ├── intent.py         # 意图识别
│   │       ├── chat.py           # 通用对话（含学习上下文注入）
│   │       ├── knowledge_graph.py # 知识图谱生成
│   │       ├── quiz.py           # 诊断出题
│   │       ├── grading.py        # 答案判定
│   │       ├── diagnosis.py      # 诊断分析
│   │       ├── lesson.py         # Markdown 教案生成
│   │       └── slides.py         # PPT 幻灯片生成（两阶段管线）
│   ├── llm/
│   │   ├── client.py             # OpenAI + Instructor 客户端封装
│   │   ├── schemas.py            # 核心 Pydantic 数据模型
│   │   └── slide_schemas.py      # PPT 元素模型（Text/LaTeX/Shape/Line/Chart/Table）
│   ├── export/
│   │   └── pptx_export.py        # PPTX 文件导出（python-pptx）
│   ├── utils/
│   │   ├── json_repair.py        # JSON 解析修复（4 策略）
│   │   └── text.py               # 文本工具（CJK 检测等）
│   └── prompts/
│       ├── loader.py             # Jinja2 模板加载器
│       ├── intent.j2             # 意图分类提示词
│       ├── knowledge_graph.j2    # 知识图谱生成提示词
│       ├── quiz_generate.j2      # 出题提示词
│       ├── grading.j2            # 判题提示词
│       ├── lesson_plan.j2        # 薄弱知识点教案提示词
│       ├── lesson_review.j2      # 全部掌握时复习教案提示词
│       ├── slide_outline.j2      # 幻灯片大纲提示词
│       ├── slide_content_system.j2  # 幻灯片排版系统提示词（961 行，源自 OpenMAIC）
│       ├── slide_content_user.j2    # 幻灯片排版用户提示词
│       └── slide_content_draft.j2   # 幻灯片内容草稿提示词
├── static/
│   ├── index.html                # 主聊天页面
│   ├── diagnosis.html            # 诊断答题页面
│   ├── results.html              # 结果展示页面
│   ├── lesson.html               # Markdown 教案学习页面
│   ├── slides.html               # PPT 幻灯片查看页面
│   ├── api.js                    # API 客户端 + XSS 防护 + 导出工具
│   └── sidebar.js                # 会话管理侧边栏（Rename / Export / Delete）
├── tests/
│   ├── test_api.py               # API 接口测试
│   ├── test_schemas.py           # 数据模型测试
│   ├── test_slide_schemas.py     # PPT 元素模型测试
│   ├── test_json_repair.py       # JSON 修复测试
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
│  7. 跳转回 /?session_id=xxx                                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              index.html（对话中显示诊断结果）                    │
│                                                             │
│  8. 从后端状态重建完整对话：                                     │
│     - 用户消息 + 知识图谱                                      │
│     - 诊断结果卡片（正确率环形图、知识点分析）                      │
│     - [开始学习] 按钮 → 弹窗选择 Markdown 或 PPT               │
│                                                             │
│  9. 用户选择格式：                                             │
│     ├── Markdown → lesson.html?session_id=xxx               │
│     └── PPT 幻灯片 → slides.html?session_id=xxx             │
└─────────────────────────────────────────────────────────────┘
                         ┌────┴────┐
                         ▼         ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│  lesson.html（Markdown）  │  │  slides.html（PPT 幻灯片）    │
│                          │  │                              │
│  POST generate-lesson    │  │  POST generate-slides        │
│  └─► Markdown 教案       │  │  └─► 两阶段生成：              │
│  marked.js 渲染          │  │     阶段 1：大纲（8-15 页）     │
│  左侧目录 + 右侧知识图谱  │  │     阶段 2a：内容草稿（~500字） │
│                          │  │     阶段 2b：排版 JSON         │
│                          │  │  ECharts 图表 + KaTeX 公式    │
│                          │  │  翻页导航 + 缩略图             │
│                          │  │  [Export PPTX] 下载按钮        │
│                          │  │                              │
│  完成 → 回到对话          │  │  完成 → 回到对话               │
└──────────────────────────┘  └──────────────────────────────┘
```

---

## PPT 幻灯片生成管线（参考 OpenMAIC）

### 两阶段生成架构

```
用户点击 Start Learning → 选 PPT 幻灯片
  │
  ├── 阶段 1：大纲生成
  │   POST /api/chat/{id}/generate-slides
  │   LLM → [{title, description, keyPoints}, ...] （8-15 页）
  │
  └── 阶段 2：逐页生成（每页两次 LLM 调用）
      │
      ├── 2a：内容草稿
      │   LLM → 详细教学内容（~500 字，含讲解、公式、例题）
      │   模拟 OpenMAIC 的 PDF 教材内容输入
      │
      └── 2b：排版 JSON
          LLM → {background, elements: [TextElement, LatexElement, ...]}
          使用 OpenMAIC 的 961 行 system prompt
```

### 后处理管线（适配自 OpenMAIC scene-generator.ts）

```
LLM 原始输出
  ↓ parse_json_response()     # JSON 修复（代码块、LaTeX 转义、截断修复）
  ↓ _fix_element_defaults()   # 元素默认值（line/text/shape/image）
  ↓ _process_latex_elements() # LaTeX 验证（CJK 转文本、空值移除）
  ↓ _enforce_canvas_bounds()  # 画布边界裁剪（1000×562.5）
  ↓ _assign_ids_and_rotation()# UUID ID + rotate=0
  ↓ _to_pydantic_elements()   # 转 Pydantic 模型
  ↓ SlideData
```

### 支持的 PPT 元素类型

| 元素 | 前端渲染 | PPTX 导出 |
|------|---------|----------|
| TextElement | HTML + DOMPurify | python-pptx TextBox（完整 HTML 样式解析） |
| LatexElement | KaTeX | matplotlib 渲染为 PNG |
| ShapeElement | SVG path | Rectangle / Oval / RoundedRect |
| LineElement | SVG + arrow markers | Connector + 虚线样式 |
| ChartElement | ECharts（8 种图表） | 占位框 |
| TableElement | HTML table（合并单元格 + 样式） | python-pptx Table |
| ImageElement | 暂不支持 | 灰色占位框 |

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
| **chat** 通用对话 | 消息历史 + 学习上下文摘要 | 是（原始对话补全） | AI 回复 |
| **knowledge_graph** 知识图谱 | 主题字符串 | 是（结构化：KnowledgeGraph） | 带节点和边的图谱 |
| **quiz** 出题 | 知识图谱 | 是（结构化：list[QuizQuestion]） | 约 10 道选择题 |
| **grading** 判题 | 答案 + 题目 | 否（本地比对） | 对/错 + 下一题索引 |
| **diagnosis** 诊断 | 所有答案 | 否（本地计算） | 分数、知识点分析、薄弱点 |
| **lesson** 教案 | 薄弱点 + 图谱 | 是（原始对话补全） | Markdown 教案内容 |
| **slides** 幻灯片 | 大纲 + 内容草稿 | 是（两阶段调用） | SlideData JSON |

### 对话上下文注入

chat 节点每次调 LLM 前，从 session state 提取关键数据组装摘要（~200 tokens），注入 system prompt：

```
当前学习上下文：
- 学习主题：勾股定理
- 知识图谱：直角三角形(前置)、勾股定理(核心)、平方根(前置)
- 诊断结果：70%（7/10），薄弱：平方根、坐标距离
- 教案：已生成
```

这样用户在对话中问"我哪里比较弱"时 AI 能回答。

---

## API 接口

| 方法 | 路径 | 功能 | 请求体 | 响应 |
|------|------|------|--------|------|
| POST | `/api/chat` | 发送消息，运行状态机 | `{session_id?, message}` | `{session_id, mode, message, data}` |
| POST | `/api/chat/{id}/submit-quiz` | 提交答题答案，运行诊断 | `{answers: [int]}` | `{session_id, diagnosis_result}` |
| POST | `/api/chat/{id}/generate-lesson` | 生成 Markdown 教案 | — | `{session_id, lesson_content}` |
| POST | `/api/chat/{id}/generate-slides` | 生成 PPT 幻灯片（大纲+逐页） | — | `{session_id, outlines, slides}` |
| GET | `/api/chat/{id}/export-pptx` | 下载 PPTX 文件 | — | .pptx 文件流 |
| GET | `/api/chat/{id}/state` | 获取会话完整状态 | — | 图谱、结果、教案、幻灯片、题目、答案、消息 |
| GET | `/health` | 健康检查 | — | `{status: "ok"}` |

---

## 数据模型（Pydantic）

```
KnowledgeGraph 知识图谱
├── topic: str
├── nodes: [KnowledgeNode]
│   ├── id, name, description, level
└── edges: [KnowledgeEdge]
    ├── source, target, relation

QuizQuestion 诊断题目
├── id, knowledge_point, question, options, answer

DiagnosisResult 诊断结果
├── score, total, correct, incorrect
└── breakdown: [DiagnosisBreakdown]
    ├── name, status, detail

SlideData 幻灯片页面
├── background: {type, color}
└── elements: [PPTElement]
    ├── TextElement: id, type, left, top, width, height, content, defaultColor
    ├── LatexElement: id, type, left, top, width, height, latex, color
    ├── ShapeElement: id, type, left, top, width, height, path, viewBox, fill
    ├── LineElement: id, type, left, top, width, start, end, style, color, points
    ├── ChartElement: id, type, left, top, width, height, chartType, data, themeColors
    └── TableElement: id, type, left, top, width, height, colWidths, data, outline

SlideOutline 幻灯片大纲
├── title, description, keyPoints
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
- **序列化**：消息 → `{type, content}`，Pydantic → `.model_dump()`，JSON 编码
- **反序列化**：重建 Message 对象和 Pydantic 模型
- **会话恢复**：前端从 state API 重建完整对话（结构化内容 + 后续聊天消息）

---

## 前端架构

### 页面跳转流程

```
index.html ──► diagnosis.html ──► index.html（结果）──┬── lesson.html ──► index.html
  主对话          答题诊断           显示诊断结果       └── slides.html ──► index.html
                                  选择教案格式            PPT 幻灯片       回到对话
```

### 脚本依赖

```
CDN 库：
├── Tailwind CSS          # 样式
├── Google Material Icons # 图标
├── vis.js Network        # 知识图谱可视化
├── ECharts               # 图表渲染（slides.html）
├── KaTeX                 # LaTeX 公式渲染（slides.html）
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
| **Markdown** | 学习报告 | 对话记录 → 知识图谱 → 诊断题目 → 诊断结果 → 教案 |
| **JSON** | 结构化数据 | state API 完整原始数据 |
| **PPTX** | PPT 文件 | slides.html 页面的 Export PPTX 按钮 |

### 会话命名策略

- **新建会话**：侧边栏显示"未命名"
- **自动命名**：当 LLM 返回主题时自动以主题命名
- **兜底策略**：对话 3 轮以上后，取用户消息前 15 字符
- **手动修改**：三点菜单中点击 Rename 行内编辑

---

## 安全措施

| 威胁 | 防护手段 |
|------|---------|
| XSS 跨站脚本 | 所有 innerHTML 使用 `escapeHtml()` 或 DOMPurify 消毒 |
| CORS 跨域 | 通过 `ALLOWED_ORIGINS` 环境变量配置 |
| 输入验证 | 消息最长 5000 字符，答案索引限制 0-3 |
| SQL 注入 | 使用 `?` 参数化查询 |
| 阻塞 I/O | LLM 调用通过 `asyncio.to_thread` 异步化 |
| 错误处理 | 所有 LLM 节点包含 try/except + 日志 |
| 资源泄漏 | SQLite 使用 `with` 上下文管理器 |
| JSON 损坏 | 4 策略修复（代码块、LaTeX 转义、截断、控制字符） |
| 中文 LaTeX | 自动检测 CJK 并转为 TextElement |

---

## 安装与部署

### 1. 环境要求

- **Python 3.11+**（项目通过 `.python-version` 锁定 3.11）
- **uv**（Python 包管理器，替代 pip + venv）
- **Git**
- 一个 OpenAI 兼容的 API Key

如果没有 uv，先安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 克隆 + 安装依赖

```bash
git clone <仓库地址>
cd openail
uv sync
```

### 3. 配置 .env

编辑项目根目录的 `.env` 文件，填入你的 API 密钥：

```bash
# .env
OPENAI_API_KEY=你的API密钥
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
# ALLOWED_ORIGINS=https://yourdomain.com  # 可选，生产环境限定 CORS 来源
```

> 注意：如果是公开仓库，请勿将真实 API Key 提交到 Git。可将 `.env` 加入 `.gitignore`。

| 提供商 | OPENAI_BASE_URL | LLM_MODEL |
|--------|----------------|-----------|
| OpenAI 官方 | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 第三方代理 | `https://代理地址/v1` | 代理支持的模型名 |

### 4. 启动 + 访问

```bash
uv run python run/serve.py
```

打开 **http://localhost:8080** 即可使用。

```bash
# 运行测试
uv run python -m pytest tests/ -v
```
