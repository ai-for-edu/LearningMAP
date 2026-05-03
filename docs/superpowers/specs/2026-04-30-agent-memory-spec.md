# Agent Memory 三层模型 — OpenAILearning 集成规范

**状态**：草案 / **降级为 P2，等待 review-flow 上线后再启动**
**日期**：2026-04-30
**适用范围**：`src/agent/`、`src/memory_client.py`、`student_memory/`

> **优先级调整说明（2026-05-03）**
>
> 本 spec 设计的 L2（LLM 主观笔记）解决的是"个性化判断"问题——影响 chat 风格、避开已知误区。
> 但产品场景的核心循环是「回访 → 复习 → 调整重点」，由 L1 结构化数据驱动，**不依赖 L2 即可完成**。
>
> 因此本 spec **降级为 P2**，前置依赖：`docs/plans/2026-04-30-review-flow-spec.md`。
>
> 待 review-flow 上线、积累一段使用数据后，再评估 L2 的边际价值（很可能可简化为
> 仅 `misconception` 类别，专门为 review 出题提供"该学生易混淆 X 与 Y"的提示）。
>
> 下面的设计内容保留不动，仅作为后续启动时的参考。

本规范把 `hermes-agent` 的"自进化记忆"机制抽象为**三层模型**，并指出 OpenAILearning 当前已有什么、缺什么、怎么加。

---

## 1. 三层模型（核心抽象）

任何 agent 的"记忆"都可以拆成三层。混在一起是大多数实现失控的原因。

```
┌─────────────────────────────────────────────────────────────────┐
│                       LLM 调用前的 system prompt                │
│                                                                 │
│   [base prompt] + [skills] + [L3 即时] + [L2 主观] + [L1 召回]  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                                  ▲
       ┌──────────────────────────┼──────────────────────────┐
       │                          │                          │
   ┌───┴────┐                ┌────┴─────┐                ┌───┴────┐
   │   L1   │                │    L2    │                │   L3   │
   │ 客观   │                │  主观    │                │ 即时   │
   │ 记录   │                │  判断    │                │ 上下文 │
   └───┬────┘                └────┬─────┘                └───┬────┘
       │                          │                          │
  写: 确定性管线              写: sub-LLM 评审             写: 当前节点
  触发: lifecycle             触发: lifecycle / 计数器     触发: 每次调用
  存: 向量库                  存: 结构化笔记 (per-user)    存: 内存 state
  读: 语义召回 top-K          读: 启动会话拉一次快照       读: 直接拼
  失败: 不可降级              失败: 静默, 跳过             失败: 不可降级
```

### 三层的判别问题

| 问题 | 答案 → 是哪一层 |
|---|---|
| 这条信息是从结构化事件自动派生的吗？ | 是 → **L1** |
| 这条信息需要 LLM 看一段对话/会话才能形成判断吗？ | 是 → **L2** |
| 这条信息只在当前会话里有效吗？ | 是 → **L3** |

例子（学习场景）：

- L1：学生在第 3 题选了 B（错）；过去一周薄弱知识点是"乘法分配律"
- L2：这个学生倾向用"先具体后抽象"的解释才听得懂；他经常把"分配律"和"结合律"混淆
- L3：当前会话主题是"分式运算"，刚做完诊断 60%

L1 和 L2 经常被实现成同一坨——这是错误的。L1 是事实，L2 是关于事实的解释。**只有 L2 需要 LLM 写、需要单独的 review 循环**。

---

## 2. OpenAILearning 当前状态

| 层 | 状态 | 在哪 |
|---|---|---|
| L1 | ✅ 已实现 | `student_memory/` 服务（SQLite + hnswlib + bge-m3）；`ingest_session()` 在 `lesson` / `slides` 完成后异步调用 |
| L2 | ❌ 缺失 | —— |
| L3 | ✅ 已实现 | `LearningState`（`src/agent/state.py`）；`_build_context_summary()` 在 `chat_node` 拼接 |

现状的两个具体问题：

1. **`chat_node` 每次调用都同步 `retrieve_memory()` 一次 HTTP**（`src/agent/nodes/chat.py:89,134`）——没有 per-session snapshot，热路径上有 ~10ms-200ms 延迟，且如果服务没起会拖慢每个 turn。
2. **没有"agent 自己沉淀的判断"**——所有 ingest 都是结构化字段（quiz 答对率、知识点状态），缺少"这个学生学习偏好如何"这种跨会话的、需要 LLM 阅读对话才能得出的洞察。

---

## 3. L2 设计（这次要加的）

### 3.1 数据模型

复用 `student_memory/` 服务，**不要起新存储**。在现有 SQLite 里加一张 `notes` 表：

```sql
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,        -- 'preference' | 'misconception' | 'style' | 'goal'
    content TEXT NOT NULL,         -- 一行 Markdown
    source_session_id TEXT,        -- 由哪个会话的 review 产生
    created_at INTEGER NOT NULL,
    superseded_by INTEGER,         -- 被哪条新笔记替代了 (软删除链)
    FOREIGN KEY (superseded_by) REFERENCES notes(id)
);

CREATE INDEX idx_notes_user_active ON notes(user_id) WHERE superseded_by IS NULL;
```

字符上限：单条 ≤ 200 字符；per-user active notes ≤ 30 条（超过则按 LRU 让 reviewer 决定 supersede）。

### 3.2 服务端 API（在 `student_memory/app/api/routes.py` 加）

```http
POST /notes
Body: {user_id, category, content, source_session_id, supersedes?: int}
Returns: {note_id, active_count}

GET /notes/{user_id}?categories=preference,style
Returns: {notes: [{id, category, content, created_at}, ...]}
        # 已按 active 过滤；上限 30 条，按 created_at desc
```

**安全检查**（`POST /notes` 写入前必须过）：

1. 长度 ≤ 200 chars；非空；非纯空白
2. 不含 prompt-injection 模式：`r"ignore (previous|all) instructions"`、`r"system\s*:"`、`r"<\|.*\|>"` 等
3. category 在白名单里
4. 命中任意一条 → 拒绝写并返回 422，**不静默丢弃**（reviewer 才能从 422 里学到边界）

### 3.3 客户端（`src/memory_client.py` 加 2 个函数）

```python
def fetch_notes(user_id: str) -> list[dict]:
    """启动会话时拉一次。失败返回 []。"""

def write_note(
    user_id: str,
    category: str,
    content: str,
    source_session_id: str,
    supersedes: int | None = None,
) -> int | None:
    """成功返回 note_id，失败（含 422）返回 None。"""
```

风格与现有 `retrieve_memory` 一致：失败静默，永不抛。

---

## 4. Reviewer：什么时候触发，写什么

### 4.1 触发点（按优先级）

OpenAILearning 是 LangGraph 状态机，没有持续的 tool-iteration 循环，所以**用 lifecycle 事件触发，不用计数器**。

| 触发位置 | 文件:行 | 触发条件 |
|---|---|---|
| **A. lesson 生成完成后** | `src/api/tasks.py:277` 现有 ingest 调用旁边 | 必触发 |
| B. slides 生成完成后 | `src/api/tasks.py:352` 同上 | 必触发 |
| C. chat 累计 N 轮后 | `src/agent/nodes/chat.py` | 可选，N 默认 12 |

A/B 和现有 `ingest_session()` 同地点：

```python
# 现状
asyncio.get_event_loop().run_in_executor(
    None, ingest_session, DEFAULT_USER_ID, f"{session_id}_lesson", state_snapshot,
)

# 改后
asyncio.get_event_loop().run_in_executor(
    None, ingest_session, DEFAULT_USER_ID, f"{session_id}_lesson", state_snapshot,
)
asyncio.get_event_loop().run_in_executor(
    None, run_memory_review, DEFAULT_USER_ID, session_id, state_snapshot,
)
```

两个调用**相互独立**，`run_memory_review` 失败不影响 `ingest_session`，反之亦然。

### 4.2 Reviewer 实现（新建 `src/reviewer.py`）

```python
def run_memory_review(user_id: str, session_id: str, state: dict) -> None:
    """评审一段会话, 把值得跨会话保留的判断写入 L2。
    
    - 读: state 里的对话片段 + diagnosis_result + 现有 L2 笔记
    - 写: 0~3 条新 notes (经过 sub-LLM 选择)
    - 失败静默
    """
    try:
        existing = fetch_notes(user_id)
        prompt = _build_review_prompt(state, existing)
        response = _call_review_llm(prompt)        # 用便宜 model
        actions = _parse_review_actions(response)  # JSON: [{action, category, content, supersedes?}]
        for a in actions[:3]:                      # 硬上限, 防失控
            if a["action"] == "add":
                write_note(user_id, a["category"], a["content"], session_id)
            elif a["action"] == "supersede":
                write_note(user_id, a["category"], a["content"], session_id, supersedes=a["target"])
    except Exception:
        logger.exception("memory review failed for %s", session_id)
```

### 4.3 Review prompt 关键点

- 喂给 reviewer 的输入：**最近一轮对话 + 诊断结果 + 已有 notes 列表**
- 系统指令大致是：

```
你在评审一次学习会话。判断是否有"跨会话有用、且不能从结构化数据自动推出"的洞察。

只输出以下三类的 JSON 数组，最多 3 条：
- {"action": "add", "category": "preference|misconception|style|goal", "content": "<≤200 chars>"}
- {"action": "supersede", "target": <note_id>, "category": ..., "content": ...}
- {"action": "noop", "reason": "..."}

不要写：
- 已经在 L1 向量库里能查到的事实（例如"答对了 X 题"）
- 已经在现有 notes 里说过的判断
- 单次会话的临时状态

宁愿什么都不写, 也不要写质量低的笔记。
```

**模型选择**：用比主流程小一档的（DeepSeek-V3-Lite / GPT-4o-mini 之类）。Reviewer 错了影响小，便宜重要。

---

## 5. System Prompt 拼装（chat_node 重构）

### 5.1 当前 vs 目标

```
现状 (chat.py:75-91):                  目标:
  base                                   base
  + skill                                + skill
  + L3 (context_summary, 每次重算)       + L3 (context_summary, 每次重算)
  + L1 (retrieve_memory, 每次 HTTP)      + L2 (notes snapshot, 会话级缓存) ← 新
                                         + L1 (retrieve_memory, 仅当 query 信息熵高时调用)
```

### 5.2 Snapshot 机制（关键不变量）

L2 笔记**会话级缓存一次**。在 `LearningState` 里加字段：

```python
# src/agent/state.py 新增
memory_notes_snapshot: list[dict] | None  # session 启动时填一次, 中途不变
```

填充时机：`POST /api/chat/start` 创建 session 时调一次 `fetch_notes(user_id)` 写入 state。这样：

- chat_node 不再每次 HTTP；快
- session 中途 reviewer 写新笔记，**当前 session 看不到**——下个 session 才生效。和 hermes 的 prompt 缓存友好性同理（这个项目暂时没用 prompt cache，但保留这个不变量便于未来加）。

### 5.3 L1 召回的优化（顺手做）

现状：每次用户消息都 retrieve。改为：

- 用户消息 < 6 字（"嗯"、"好的"、"继续"）→ 跳过 retrieve
- query 与上一轮 retrieve 的 query 相似度 > 0.9 → 复用上次结果（缓存在 state 里）

这一项不是 L2 的核心，但放在同一 PR 里做收益最大。

---

## 6. 文件改动清单

| # | 文件 | 改动 | 行数估计 |
|---|---|---|---|
| 1 | `student_memory/app/db/migrations.py` | 加 `notes` 表 + 索引 | +20 |
| 2 | `student_memory/app/db/sqlite_repo.py` | 加 `add_note / list_active_notes / supersede_note` | +60 |
| 3 | `student_memory/app/api/schemas.py` | `NoteCreate / NoteOut / NotesResponse` | +25 |
| 4 | `student_memory/app/api/routes.py` | `POST /notes` + `GET /notes/{user_id}` + 安全扫描 | +80 |
| 5 | `student_memory/tests/test_notes.py` | 新建 | +120 |
| 6 | `src/memory_client.py` | 加 `fetch_notes / write_note` | +50 |
| 7 | `src/reviewer.py` | **新建**：`run_memory_review` + prompt + 解析 | +180 |
| 8 | `src/prompts/memory_review_system.j2` | 新建 review 提示词 | +30 |
| 9 | `src/agent/state.py` | 加 `memory_notes_snapshot` 字段 | +2 |
| 10 | `src/api/chat.py` | session 启动时 `fetch_notes` 写入 state | +5 |
| 11 | `src/agent/nodes/chat.py` | 重构 system prompt 拼装；用 snapshot 替代每次 retrieve | ~30 |
| 12 | `src/api/tasks.py` | lesson/slides 完成后并发触发 `run_memory_review` | +10 |
| 13 | `tests/test_reviewer.py` | mock LLM 单元测试 | +100 |
| 14 | `tests/test_memory_client.py` | 加 `fetch_notes / write_note` 用例 | +50 |

合计 ~800 行，单个 PR 可控。

---

## 7. 实施顺序（建议 3 个 PR）

**PR-1（服务端，独立可上线）**
- 改动 1-5
- 验收：`pytest student_memory/tests/test_notes.py`；手动 curl `POST /notes` 和 `GET /notes/{user}`
- 没有调用方，纯加 endpoint，零风险

**PR-2（客户端 + reviewer，灰度开关）**
- 改动 6-8, 13-14
- 加 env var `ENABLE_MEMORY_REVIEW`，默认 false
- 验收：单元测试通过；手动跑一次 `run_memory_review` 看 notes 表是否多出合理条目

**PR-3（接进主流程）**
- 改动 9-12
- 仍受 PR-2 的 env var 控制
- 验收：开关关上时行为完全不变（diff 只在 chat_node 系统 prompt 多一段且为空）；开关打开时跑一次完整学习流程，看下次 session 启动时 system prompt 里有 notes 段

---

## 8. 不变量（验收清单）

- [ ] L2 写盘前过 prompt-injection 扫描，命中返回 422
- [ ] reviewer 抛任何异常都被 swallow，不影响 lesson/slides 主流程
- [ ] `fetch_notes` HTTP 失败返回 `[]`，chat_node 仍可工作
- [ ] 同一 session 内调用 `chat_node` N 次，notes 段保持不变（snapshot）
- [ ] 单 user active notes 不超过 30 条；reviewer 主动 supersede 老条目
- [ ] reviewer 用的 LLM 与主流程**不同**（便宜模型；通过单独的 env var 配置）
- [ ] 关闭 `ENABLE_MEMORY_REVIEW` 后，`tests/` 全套通过且 chat 行为字节级不变

---

## 9. 不做的事（避免范围蔓延）

- ❌ **不做 skill 自我改进**：本项目的 `skills/{feynman,socratic,scaffolding}` 是人工编写的教学法，不是 LLM 工件；加自动 patch 价值低风险高。
- ❌ **不做 FTS5 跨会话搜索**：已有 hnswlib 向量召回，重复造轮子。
- ❌ **不做 dialectic provider**：单老师/单学生场景没必要。
- ❌ **不做 reviewer 计数器触发**：lifecycle 事件足够；计数器在长 chat 场景是可选项 (§4.1 C)。
- ❌ **不引入 SOUL.md / MEMORY.md 文件**：本项目不是终端 agent，user_id 是数据库主键，统一走 `student_memory` 服务。

---

## 10. 与 hermes 原方案的对照

| hermes 概念 | 本项目映射 |
|---|---|
| `MemoryStore` (L2 builtin) | `student_memory` 服务的 `notes` 表 |
| `MemoryProvider` ABC + manager | 不需要，单后端 |
| `MEMORY.md` snapshot | `LearningState.memory_notes_snapshot` |
| `_spawn_background_review` (fork sub-agent) | `run_memory_review` (executor + 便宜 LLM) |
| `_MEMORY_REVIEW_PROMPT` | `src/prompts/memory_review_system.j2` |
| Memory nudge 计数器 | lifecycle 事件触发；可选 chat 轮数计数 |
| `skill_manage` 自我改进 | 不做 |
| `session_search` (FTS5) | 复用现有 hnswlib `retrieve` |
| 安全扫描 + 原子写 + 回滚 | 服务端 `POST /notes` 422 拒写 |

整体复杂度从 hermes 的 ~12 个核心模块缩到本项目的 **3 个新文件 + 改 5 个旧文件**。
