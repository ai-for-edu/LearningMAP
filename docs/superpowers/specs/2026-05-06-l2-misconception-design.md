# L2 Misconception 记忆 — Review 出题增强 设计

**状态**：草案 / 待评审
**日期**：2026-05-06
**范围**：`student_memory/`、`src/memory_client.py`、`src/reviewer.py`（新建）、`src/agent/nodes/review.py`、`src/prompts/`、`src/llm/schemas.py`、`src/api/chat.py`
**前置文档**：
- `docs/superpowers/specs/2026-04-30-agent-memory-spec.md`（三层模型完整方案，本 spec 是其简化执行版）
- `docs/superpowers/specs/2026-04-30-review-flow-spec.md`（review 流程，本 spec 在其上挂触发与消费）

---

## 1. 背景与范围收敛

`agent-memory-spec` 把记忆拆成 L1（结构化事实）/ L2（LLM 主观判断）/ L3（即时上下文）。当前 L1、L3 均已上线，L2 缺失。

原 spec 设计了 4 个 category（preference / misconception / style / goal）+ 重构 `chat_node` system prompt 拼装 + L1 retrieve 智能跳过，工作量 ~800 行 / 3 PR。spec 头部留了一段降级备注：**"很可能可简化为仅 `misconception` 类别，专门为 review 出题提供'该学生易混淆 X 与 Y'的提示。"**

review-flow 现已落地（`generate_review_questions` / `grade_review` / 前端"先复习一下"入口）。本 spec 采用降级路线：

- **只做 misconception 一类**
- **不动 `chat_node`**
- **只在 `grade_review` 之后异步触发 reviewer**
- **只在 `generate_review_questions` 之前消费 misconception**

成环最快、风险最小、可独立验证收益。其他 category 与 chat 集成留作后续独立 spec。

---

## 2. 决策摘要

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | L2 范围 | misconception-only | review 出题精度是最直接的产品收益；其余 category 边际价值未证 |
| 2 | reviewer 触发点 | `grade_review` 后异步 | 用户刚错的就是 misconception 最佳证据；写完下次 review 立刻见效 |
| 3 | 注入方式 | 每次 `generate_review_questions` 前 fetch | review 不是热路径；保留"reviewer 写完下次生效"的反馈环 |
| 4 | 废弃机制 | LRU + soft delete (`archived` 字段) | 不引入 supersede 链；reviewer 只负责写 |
| 5 | reviewer 输入 | 只看本次 review 结果 | 信息密度足够（错题选项已是机械化的诱迫项）；token 可控 |
| 6 | 灰度 | `ENABLE_MEMORY_REVIEW` env，默认 off | 写 + 读双侧门控；off 时全链路无副作用 |
| 7 | reviewer 模型 | 复用 `REVIEWER_MODEL` env | review 流程已使用；不增配置项 |
| 8 | prompt 模板 | system + user 双模板 | 与 slides 同构；稳定段在 system，变化段在 user，cache 友好 |

---

## 3. 架构与数据流

```
┌────────────── 写路径（reviewer，旁路异步） ──────────────┐
│                                                            │
│  user submits review (POST /api/chat/{id}/submit-review)  │
│        │                                                   │
│        ▼                                                   │
│  grade_review() 同步评分 → 返回 HTTP 响应                 │
│        │                                                   │
│        └─(if ENABLE_MEMORY_REVIEW)──►                     │
│              asyncio.to_thread(run_memory_review, state)  │
│                    │                                       │
│                    ▼                                       │
│              src/reviewer.py                              │
│                  - 取错题 (top-4) + 现有 misconceptions   │
│                  - 渲染 system + user .j2                 │
│                  - 调 REVIEWER_MODEL（instructor）        │
│                  - 写 0~3 条 to /notes                    │
│                                                            │
└────────────────────────────────────────────────────────────┘

┌────────────── 读路径（消费侧，非热路径） ─────────────────┐
│                                                            │
│  user clicks "先复习一下" (POST /api/chat/{id}/start-review)│
│        │                                                   │
│        ▼                                                   │
│  generate_review_questions(state)                         │
│      ① fetch_misconceptions(user_id) [HTTP, 失败→[]]      │
│      ② render review_generate_system.j2(candidates,       │
│                                          misconceptions)  │
│      ③ instructor → list[QuizQuestion]                    │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

**关键不变量**：

- 写路径与 HTTP 响应完全解耦（与现有 `ingest_session` 同模式），reviewer 任意失败不影响用户拿到评分。
- 读路径每次重新 fetch，**不缓存到 `LearningState`**；前一次 reviewer 写的 note 下次 generate 立刻可见。
- `ENABLE_MEMORY_REVIEW` 同时门控写和读；关闭时整条链路无 LLM 额外开销、无 HTTP 调用、无 prompt 变化（基线字节级）。

---

## 4. 数据模型 + 服务端 API

### 4.1 SQLite 表

```sql
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('misconception')),
    content TEXT NOT NULL,
    source_session_id TEXT,
    created_at INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_notes_user_active
    ON notes(user_id, created_at DESC) WHERE archived = 0;
```

字段约束（在 repo / route 层强制，不是 SQL 层）：

- `content`：1 ≤ len ≤ 200 chars，非纯空白
- 单 user `archived = 0` 上限 30；超限时把最老一条置 `archived = 1`，不物理删

### 4.2 仓库方法

`student_memory/app/db/sqlite_repo.py` 加：

```python
def add_misconception(user_id: str, content: str, source_session_id: str) -> int:
    """插入一条 active note，返回 id；触发 LRU 淘汰。"""

def list_active_misconceptions(user_id: str, limit: int = 30) -> list[Note]:
    """按 created_at desc 取 active 条目。"""
```

### 4.3 HTTP API

```http
POST /notes
  Body: {user_id, category="misconception", content, source_session_id}
  422: 长度违规 / category 不在白名单 / 命中 prompt-injection 模式
  200: {note_id, active_count}

GET /notes/{user_id}?category=misconception
  200: {notes: [{id, content, created_at}, ...]}     # 上限 30, 按时间倒序
```

### 4.4 Prompt-injection 扫描

写入前必过：

```python
INJECTION_PATTERNS = [
    r"(?i)ignore (previous|all|the above) instructions",
    r"(?i)disregard (the )?(system|prior) (prompt|message)",
    r"(?i)you are now",
    r"<\|.*?\|>",
    r"(?i)system\s*:",
]
```

命中即 422，**不静默丢弃**——让 reviewer prompt 在迭代时能从 422 学到边界。

### 4.5 Pydantic schemas

`student_memory/app/api/schemas.py`：

```python
class NoteCreate(BaseModel):
    user_id: str
    category: Literal["misconception"]
    content: str = Field(..., min_length=1, max_length=200)
    source_session_id: str | None = None

class NoteOut(BaseModel):
    id: int
    content: str
    created_at: int

class NotesResponse(BaseModel):
    notes: list[NoteOut]
```

### 4.6 客户端

`src/memory_client.py` 加：

```python
def fetch_misconceptions(user_id: str) -> list[dict]:
    """启动 review 出题前同步调用一次，失败返回 []。"""

def write_misconception(user_id: str, content: str, source_session_id: str) -> int | None:
    """成功返回 note_id；失败 / 422 返回 None；从不抛。"""
```

风格与现有 `retrieve_memory` 一致（失败静默）。

---

## 5. Reviewer 模块（`src/reviewer.py`）

### 5.1 入口

```python
def run_memory_review(user_id: str, session_id: str, state: dict) -> None:
    """旁路异步任务；任意异常被吞掉，logger.exception 记录。
    被 src/api/chat.py:submit_review 在 ENABLE_MEMORY_REVIEW=true 时通过
    asyncio.to_thread 调用。
    """
```

### 5.2 输入选取与预算（`_collect_review_input`，纯函数，可单测）

| 字段 | 来源 | 预算 |
|---|---|---|
| `wrong_items: list[WrongItem]` | `state["review_questions"]` × `state["review_answers"]` 配对（过滤答错的）→ 按 `knowledge_point` 字段 join `state["review_candidates"]` 拿 `last_accuracy` / `days_ago` | 最多 **4 道**；超出按 `(last_accuracy 升序, days_ago 降序)` 取前 4 |
| `kp_summary: list[str]` | `state["review_result"].kp_results`，每条 ≤ 80 chars | 全部入（候选池本就 ≤ 8） |
| `existing: list[dict]` | `fetch_misconceptions(user_id)`，失败 `[]`；条目形如 `{"id", "content", "created_at"}` | 服务端保证 ≤ 30 |

`WrongItem` 字段：`kp_subtopic, last_accuracy, question_text, options[4], correct_idx, user_idx`，每条 ≤ **600 chars**（题干 + 选项渲染后超长则 `question_text` 截断到 400 chars + `…`）。

总输入预算 ~5KB。

### 5.3 Prompt 双模板

**`src/prompts/memory_review_system.j2`**（稳定段）：

- 角色定义："你是学习记忆评审员"
- misconception 类别说明：跨会话有用、不能从结构化数据推出、≤200 chars、单条单一观点
- 输出 schema 文字描述（与 Pydantic 互为冗余防护）
- 不要做的事：不重复已存在的 note；不写情绪化判断；不写"答错 X 题"这种事实
- 硬约束：最多 3 条；宁可输出空列表也不写质量低的

**`src/prompts/memory_review_user.j2`**（可变段）：

- `## 本次复习错题`：渲染人类可读 Markdown（题干 / 选项 / 正确 + 用户答案 / KP 名 + 上次正确率）
- `## 知识点状态摘要`：从 `kp_summary` 平铺
- `## 该用户已有 misconception 笔记`：`{% if existing %}` 包整段
- 末尾："请评估这次会话是否暴露出新的、值得跨会话保留的混淆点。最多 3 条。"

变化段全在 user 模板，符合 cache 不变量。

### 5.4 LLM 调用

`run_memory_review` 本身是同步函数，由 `src/api/chat.py:submit_review` 通过 `asyncio.to_thread(...)` 在工作线程跑（与 `ingest_session` 同模式）。LLM 调用因此是直接同步：

```python
from src.llm.client import get_llm_client, get_reviewer_model_name

client = get_llm_client()
result: list[Misconception] = client.chat.completions.create(
    model=get_reviewer_model_name(),
    response_model=list[Misconception],
    max_tokens=512,
    messages=[
        {"role": "system", "content": render("memory_review_system.j2")},
        {"role": "user",   "content": render("memory_review_user.j2", **input_dict)},
    ],
)
```

`src/llm/schemas.py` 新增：

```python
class Misconception(BaseModel):
    content: str = Field(..., min_length=1, max_length=200)
```

服务端 200 字符限制 + Pydantic `max_length=200` 双重保护——LLM 越界由 instructor 重试机制纠正，仍越界则 422 拒写。

### 5.5 写入

```python
written = 0
for m in result[:3]:                        # 硬上限
    if write_misconception(user_id, m.content, session_id):
        written += 1
logger.info("memory review wrote %d misconceptions for session %s", written, session_id)
```

---

## 6. 注入侧（review 出题）

### 6.1 Prompt 改动

`src/prompts/review_generate_system.j2` 在 `候选知识点` 块之后、`要求` 之前插入：

```jinja
{% if misconceptions %}

## 该学生历史易混点
{% for m in misconceptions %}
- {{ m }}
{% endfor %}

出题时若覆盖到上述 KP 的相关概念, 优先把已知混淆点设计成 distractor 选项, 让题目能精确检出这些已知薄弱处.
{% endif %}
```

- `{% if %}` 包整段——无 note 时整段不出现，向后兼容。
- 插入位置同构于 `chat_node`：先候选 KP，再历史易混点，最后使用提示。

### 6.2 节点改动

`src/agent/nodes/review.py:generate_review_questions` LLM 调用前加 fetch：

```python
def generate_review_questions(state: LearningState) -> dict:
    candidates = state["review_candidates"]

    misconceptions: list[str] = []
    if os.environ.get("ENABLE_MEMORY_REVIEW", "false").lower() == "true":
        notes = fetch_misconceptions(DEFAULT_USER_ID)        # 失败 []
        misconceptions = [n["content"] for n in notes]

    system_prompt = render_template(
        "review_generate_system.j2",
        candidates=candidates,
        misconceptions=misconceptions,
    )
    # 后续 LLM 调用不变
```

### 6.3 门控不变量

- env off → `misconceptions = []` → `{% if %}` 整段不出现 → prompt **字节级与本改动前一致**。基线快照测试覆盖。
- env on 但 fetch 失败 → 同样 `[]` → 同样降级为不注入。

---

## 7. 文件改动清单

| # | 文件 | 改动 | 行数 |
|---|---|---|---|
| 1 | `student_memory/app/db/migrations.py` | 加 `notes` 表 + 索引 | +20 |
| 2 | `student_memory/app/db/sqlite_repo.py` | `add_misconception` / `list_active_misconceptions` + LRU 淘汰 | +60 |
| 3 | `student_memory/app/api/schemas.py` | `NoteCreate` / `NoteOut` / `NotesResponse` | +25 |
| 4 | `student_memory/app/api/routes.py` | `POST /notes` + `GET /notes/{user_id}` + injection 扫描 | +70 |
| 5 | `student_memory/tests/test_notes.py` | 新建：CRUD + 422 边界 + LRU 淘汰 + 注入扫描 | +130 |
| 6 | `src/memory_client.py` | `fetch_misconceptions` / `write_misconception` | +50 |
| 7 | `src/llm/schemas.py` | `Misconception` Pydantic 模型 | +5 |
| 8 | `src/prompts/memory_review_system.j2` | 新建稳定段 prompt | +35 |
| 9 | `src/prompts/memory_review_user.j2` | 新建可变段 prompt | +25 |
| 10 | `src/reviewer.py` | 新建：`run_memory_review` + `_collect_review_input` + LLM 调用 | +160 |
| 11 | `src/api/chat.py` | `submit_review` 末尾加 `asyncio.to_thread(run_memory_review, ...)`，门控 env | +10 |
| 12 | `src/prompts/review_generate_system.j2` | 新增 `{% if misconceptions %}` 块 | +12 |
| 13 | `src/agent/nodes/review.py` | `generate_review_questions` LLM 调用前 fetch | +10 |
| 14 | `tests/test_memory_client_notes.py` | `fetch_misconceptions` / `write_misconception` 单测（mock HTTP） | +60 |
| 15 | `tests/test_reviewer.py` | mock LLM 单测：输入截断 / 解析 / 写入计数 / 失败静默 | +120 |
| 16 | `tests/test_review_generate_with_notes.py` | env on/off 各跑一次，断言 prompt 字节级差异（off 必须不变） | +50 |

合计 ~840 行，全后端，无前端改动。

---

## 8. PR 拆分

### PR-1：服务端独立可上线

- 文件 1–5（`student_memory/` 内）
- 验收：`uv run python -m pytest student_memory/tests/test_notes.py -v`；手动 curl `POST /notes`、`GET /notes/{user_id}`，含 422 路径
- 风险：零调用方，纯加 endpoint

### PR-2：客户端 + reviewer（带灰度开关，但未接入主流程）

- 文件 6–10、14–15
- `ENABLE_MEMORY_REVIEW` env var 引入，但**仅声明**——本 PR 没有调用方
- 验收：单测全过；手动用 fixture state 跑一次 `run_memory_review`，看 SQLite 多出合理 1–3 条；用注入串测一次 422 路径
- 风险：reviewer 全部进仓但未被调用

### PR-3：接进主流程

- 文件 11–13、16
- 仍受 PR-2 引入的 `ENABLE_MEMORY_REVIEW` 控制
- 验收：
  - env **off**：`pytest tests/` 全套通过；`test_review_generate_with_notes` 验证 prompt 字节级与改动前一致（基线快照）
  - env **on**：手动跑一次完整 review 流程 → reviewer logger.info 命中、SQLite 多条目、下一次 `start-review` 的 system prompt 含 `## 该学生历史易混点` 段
- 风险：可控；env 关闭时行为不变是硬不变量

---

## 9. 验收不变量

- [ ] `ENABLE_MEMORY_REVIEW=false` 时：reviewer 不被触发、`fetch_misconceptions` 不被调用、`review_generate_system.j2` 渲染结果与本改动前**字节级一致**（基线快照测试覆盖）
- [ ] reviewer 抛任何异常都被 `logger.exception` 吞掉；submit-review 的 HTTP 响应不受影响
- [ ] `fetch_misconceptions` HTTP 失败返回 `[]`；`generate_review_questions` 不抛、不阻塞
- [ ] `write_misconception` 422 / 网络失败返回 `None`；reviewer 继续处理下一条 candidate
- [ ] 服务端 `POST /notes` 写入前必过 prompt-injection 扫描（含五条正则）；命中 422，不静默丢弃
- [ ] 服务端强制 `len(content) ≤ 200`；客户端 Pydantic `Misconception.content max_length=200` 双重保护
- [ ] 单 user `archived = 0` 上限 30；超限自动 archived=1 最老一条；不物理删
- [ ] reviewer 单次 LLM 调用最多写 **3 条**（硬上限，代码 `result[:3]`）
- [ ] reviewer 用 `REVIEWER_MODEL` env，与主流程 `LLM_MODEL` 解耦
- [ ] reviewer 输入预算：错题 ≤ 4 道 × 600 chars + 现有 notes ≤ 30 × 200 chars，整体 ≤ ~5KB
- [ ] system prompt 是稳定段（`memory_review_system.j2`），user prompt 是可变段（`memory_review_user.j2`）；变化数据全部在 user 模板（cache 友好不变量）
- [ ] reviewer 成功路径打 `logger.info("memory review wrote %d misconceptions for session %s", ...)`，便于事后评估质量
- [ ] `Misconception` Pydantic 模型纳入 `src/llm/schemas.py` 统一管理（与 `ReviewResult` / `QuizQuestion` 同目录）

---

## 10. 不做的事

- ❌ **不做 `preference / style / goal` 三个 category** —— 留扩展位（DB CHECK 当前只允许 misconception，未来加新 category 改 schema 即可）
- ❌ **不修 `chat_node` 的同步 `retrieve_memory`** —— 原 spec §2.1 提到的 ~10–200ms 热路径延迟问题；本次范围不动，留独立 spec
- ❌ **不引入 `LearningState.memory_notes_snapshot`** —— misconception-only 范围下，每次 fetch 已够；snapshot 会破坏"reviewer 写完下次立刻生效"的反馈环
- ❌ **不引入 supersede 链** —— 简化为 archived 软删除位 + LRU；reviewer 只负责写
- ❌ **不在 lesson / slides 完成后触发 reviewer** —— 本 spec 只挂 `grade_review` 后；其它触发点等数据证明有用再加
- ❌ **不做 chat 累计 N 轮触发** —— 同上
- ❌ **不做前端可视化** —— misconception 是隐式增强，用户不直接看到列表
- ❌ **不做用户切换 / 多账号** —— 仍用 `DEFAULT_USER_ID`

---

## 11. 与原 agent-memory-spec 的关系

本 spec 是 `2026-04-30-agent-memory-spec.md` 的**简化执行版**，只覆盖 misconception 一类、只接 review 流程。原 spec 的：

- 4 category 设计 → 留作未来扩展
- `chat_node` system prompt 重构（§5.1–5.2） → 本次不做
- `LearningState.memory_notes_snapshot` 字段 → 本次不引入
- L1 retrieve 智能跳过（§5.3） → 本次不做
- supersede 链 → 简化为 LRU + archived

简化的代价：misconception 不影响 chat 风格。换来的好处：成环最快、可独立验证收益、~840 行可在 3 个小 PR 内交付。
