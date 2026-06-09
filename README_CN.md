<p align="center">
  <img src="resources/logo.png" alt="LearningMAP logo" width="300">
</p>

# LearningMAP：基于记忆与个性化的学习

<p align="center">
  <a href="./README.md"><img alt="English" src="https://img.shields.io/badge/English-CDCFD4?style=flat-square"></a>
  <a href="./README_CN.md"><img alt="简体中文" src="https://img.shields.io/badge/简体中文-BCDCF7?style=flat-square"></a>
</p>

<p align="center"><b>一个主题进 → 一门自适应课程出。</b><br>
知识图谱 → 测验 → 诊断 → 课程 → 幻灯片 → 复盘,即时生成,跨会话记忆。</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/built%20with-LangGraph-purple.svg" alt="LangGraph">
  <img src="https://img.shields.io/badge/packaging-uv-orange.svg" alt="uv">
  <img src="https://img.shields.io/badge/status-internal%20research-lightgrey.svg" alt="Status">
</p>

<p align="center">
  <a href="resources/cs_demo.mov"><img src="resources/cs_demo.gif" alt="LearningMAP demo" width="760"></a>
</p>

---

## 目录

- [概览](#概览)
- [Demo](#demo)
- [快速开始](#快速开始)
- [核心特性](#核心特性)
- [能力总览](#能力总览)
- [安装](#安装)
- [配置](#配置)
- [状态](#状态)

> **技术参考** —— 架构、API、前端页面、仓库结构、硬件建议与开发约定详见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 概览

- **一个主题进,一份完整学习计划出。** 学生输入一个主题,LearningMAP 围绕它端到端构建一条自适应学习闭环。
- **完整可运行的 Demo。** 全流程 —— 对话、测验、学习、复盘 —— 全部实时流式渲染到浏览器。
- 🚀 **为每位学习者个性化。** 适配真实掌握度,并通过类人记忆系统跨会话记住学习者。
- 🚀 **聚焦考试要点。** 由专家提炼的知识点级 skills 让每节课都打在考点上。**今天我们开源 `865` 条精炼的 K-12 数学 skills —— 后续还会有更多 skills 与更多学科。**

LearningMAP 把学习者输入的主题送入一条自适应闭环 —— 知识图谱、测验、诊断、定向课程、幻灯片 —— 并把整个过程记下来供下次会话使用。

---

## Demo

两段不同学科上的端到端学习闭环演示:

> 录屏经过加速(约 6×)。点击截图可打开完整的 `.mov` 原速回放。

### 计算机科学

[![Computer Science demo](resources/cs_demo.gif)](resources/cs_demo.mov)

### 物理

[![Physics demo](resources/phy_demo.gif)](resources/phy_demo.mov)

---

## 快速开始

三步拉起主服务(跨会话记忆是可选项 —— 见 [安装](#安装))。

> 为获得最佳效果,我们推荐使用 **DeepSeek-V4 或同等及以上能力的模型**。任何 OpenAI 兼容模型均可工作,但能力较弱的模型会显著影响课程质量与 KC-skill 解析。

```bash
# 1. 安装主服务
uv sync

# 2. 配置 —— 指向任意 OpenAI 兼容 LLM 网关
cat > .env <<'EOF'
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
EOF

# 3. 启动,然后打开 http://localhost:8080
uv run python run/serve.py
```

---

## 核心特性

### 📚 学习内容流水线

一条线性闭环,把一个主题转成多形态学习材料:

- **知识图谱** —— 带掌握度标注的实体 / 技能图(`mastery_annotator.py`)。
- **自适应测验** —— 题目按当前 KG 掌握状态校准。
- **判分与诊断** —— 逐题判分喂入诊断卡片与学习雷达图。
- **Markdown 课程** —— 针对诊断暴露的薄弱点定向生成,通过 SSE 流式返回。
- **幻灯片** —— 大纲优先的流水线,通过 `python-pptx` 导出 `.pptx`。后处理链强制 1000 × 562.5 画布、修复 LaTeX/CJK 元素并分配稳定 ID(详见 [ARCHITECTURE.md](ARCHITECTURE.md#slide-post-processing-chain))。

### 🧠 持久化记忆

一个独立的 FastAPI 微服务(`student_memory/`)提供:

- `POST /retrieve` —— 把语义召回注入 chat 节点的系统提示。
- `POST /ingest` —— 在诊断 / 课程结束时写入子主题粒度的 MemoryUnit。
- `POST /notes` 与 `GET /notes/{uid}/neighbors` —— 用于错题复盘的 4 类 L2 反思笔记。

整个接缝是单一文件 `src/memory_client.py`。**当该服务不可用时,主服务会优雅降级** —— 不注入上下文、不写 ingest、不阻塞对话。

### 🎓 可插拔的教学法 Skills

教学风格是可替换的工件,不是代码:

- 每种教学法是一个目录,包含一份 `SKILL.md`(如 `skills/feynman/`、`skills/scaffolding/`、`skills/socratic/`)。
- 新增一种教学法是 **一个新目录 + 一份 `SKILL.md` —— 零 Python 改动**。
- 另有一份独立的 **KC-skill 库**(通过 `run/build_kc_skills.py` 离线构建),由 `kc_skill_resolver.py` 按主题解析,让 skill 注入可以限定到特定的知识点。
- Skills 在运行时通过 `POST /api/chat/{sid}/set-skill` 切换。

### 🔁 复盘闭环(L2 反思)

主学习闭环之后,复盘节点会:

- 生成 4 类错题归因摘要。
- 通过 `/notes` 持久化到 `student_memory`。
- 后续会话再通过 `/retrieve` 把这些笔记浮现回来,闭合跨会话反馈环。

两次复盘之间的最短间隔由 `REVIEW_MIN_AGE_SECONDS` 控制(默认 24 小时)。

---

## 能力总览

三个核心卖点 —— 以及它们各自所依托的技术。

### 1 · 个性化学习体验

适配每位学生的真实掌握度,由类人记忆系统驱动跨会话记忆 —— 历史会话以系统提示增强形式回流,教学风格运行时可切换。

> **依托:** 知识图谱 + 跨会话记忆(`student_memory/`,通过单一接缝解耦、离线时优雅降级)+ 可插拔教学法(`skills/<name>/SKILL.md`)。

### 2 · 专家背书的知识点级 skills

从真实考题与专家教学中提炼,绑定到具体知识点 —— 材料直击考点,绝不泛泛而谈。

> **依托:** KC-skill 库(通过 `run/build_kc_skills.py` 离线构建,由 `kc_skill_resolver.py` 按主题解析)。

### 3 · 全链路、产品级 Demo

一个可运行的端到端 Demo,覆盖完整旅程:chat → quiz → grading → diagnosis → lesson → review,所有状态在刷新后均可从会话状态推导,并实时流式渲染到浏览器。

> **依托:** FastAPI + 全链路 SSE 流式(chat / lesson / slides),Markdown 课程重渲染为 16:9 幻灯片(1000 × 562.5 画布、LaTeX/CJK 感知),并支持 `.pptx` 导出。

---

## 安装

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
cd student_memory && uv sync && cd ..  # 记忆子服务(可选)
```

### 运行

**主服务(端口 8080)**

```bash
uv run python run/serve.py
# Uvicorn → http://0.0.0.0:8080
```

**记忆子服务(端口 8090,可选)**

```bash
cd student_memory
SM_USE_FAKE_EMBEDDER=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8090
```

> 当记忆服务不可用时,主服务会**优雅降级**(不注入 `context_text`、不写 `ingest`),不会阻塞对话。

主服务启动后打开 `http://localhost:8080/` —— FastAPI 把 `static/` 挂载到根路径。

---

## 配置

主服务读取的全部变量(在 `.env` 或进程环境中设置):

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPENAI_API_KEY` | — | **必填**。LLM 鉴权。 |
| `OPENAI_BASE_URL` | — | **必填**。OpenAI 兼容端点。 |
| `LLM_MODEL` | — | **必填**。模型名。 |
| `MEMORY_SERVICE_URL` | `http://127.0.0.1:8090` | `student_memory` 服务地址。 |
| `ALLOWED_ORIGINS` | `*` | 逗号分隔的 CORS 白名单。 |
| `REVIEW_MIN_AGE_SECONDS` | `86400` | 两次复盘节点调用之间的最短间隔(秒)。 |
| `CLASSBA_AUTH` / `CLASSBA_API_URL` / `CLASSBA_AGENT_NAME` | — | 可选。用于 ClassBA 外部 Agent 调用。 |

LLM 客户端写死会**绕过操作系统 HTTP 代理**(见 commit `fc7e51c`),无需额外的代理配置。

`.env` 示例:

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

## 状态

内部研究项目。未发布。

---

仓库结构、硬件建议与开发约定详见 **[ARCHITECTURE.md](ARCHITECTURE.md)**。
