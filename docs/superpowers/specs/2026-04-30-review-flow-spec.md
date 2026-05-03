# Review Flow 全栈实施规范 — 复习驱动的个性化学习循环

**状态**：草案 / 实施前规范
**日期**：2026-04-30
**范围**：**全栈**——从 `student_memory/` SQLite 聚合查询，经 agent / API / prompt 层，到 `static/` 前端按钮
**涉及目录**：`student_memory/`、`src/memory_client.py`、`src/llm/`、`src/agent/`、`src/prompts/`、`src/api/`、`static/`、`tests/`
**优先级**：P0（产品可见的核心循环）
**关联文档**：`docs/plans/2026-04-30-agent-memory-spec.md`（L2 主观笔记，本 spec 上线后再做）

> **范围提示**：本 spec 不仅是 memory 子系统改动。17 项文件改动中只有 #1-5 属于 `student_memory/` 服务，其余 12 项是把 memory 数据驱动到用户可见复习流程所需的全栈管线（agent 节点、API 端点、prompt 注入、前端 UI）。memory 部分（PR-1）可独立合入。

---

## 1. 场景与动机

现状：用户走完 topic → 诊断 → lesson 这条线后，下次回到平台只能重新选一个 topic 从零开始；过去的学习成果在 `student_memory` 服务里躺着但**不参与下一次的学习决策**。

目标循环（产品可见）：

```
首次:  topic → 知识图谱 → 诊断quiz → 诊断结果 → lesson → ingest_session
              ↓
回访:  检测到该用户有历史 → 复习quiz(针对曾掌握但已隔时的KP) → 复习结果
              ↓
       → 调整本次学习重点(强化薄弱 / 衔接新内容) → 知识图谱 → ...
```

关键判断：**这一循环不需要 LLM 主观笔记（L2）就能跑起来**。L1 已经按 `(user_id, subtopic, timestamp, accuracy)` 结构化存了，缺的只是一条聚合查询接口和一条 review 通道。

---

## 2. 现状盘点

| 能力 | 状态 | 在哪 |
|---|---|---|
| L1 按 KP 存历史成绩 | ✅ | `student_memory/app/db/migrations.py` `memories` 表，含 `user_id / subtopic / timestamp / accuracy` 与 `idx_memories_user_subtopic` |
| 把 session 写进 L1 | ✅ | `src/api/tasks.py:275-277, 350-352` |
| 按 KP 聚合查询历史 | ❌ | 仅有向量 `/retrieve`，无结构化 mastery 接口 |
| Agent 中的 review 节点 | ❌ | `src/agent/graph.py` 只有 chat / graph / quiz / grading / diagnosis / lesson |
| 前端"复习"入口 | ❌ | `static/index.html` 只有"开始学习" |

---

## 3. 数据层：`/mastery` 结构化查询

### 3.1 接口

```http
GET /mastery/{user_id}?topic=<optional>&since_days=<optional>&min_seen=<optional>
Returns:
{
  "items": [
    {
      "subtopic": "乘法分配律",
      "topic": "有理数运算",
      "last_accuracy": 0.6,         # 0~1
      "last_seen_at": "2026-04-15T08:23:11Z",
      "session_count": 3,           # 该 user 在该 subtopic 上累计 session 数
      "total_questions": 8,
      "total_correct": 5
    },
    ...
  ]
}
```

排序：`last_seen_at DESC`。`min_seen=1` 时过滤掉只见过 1 次的 KP（信号弱）。

### 3.2 SQL（无新表，纯聚合）

```sql
SELECT
  subtopic, topic,
  MAX(timestamp) AS last_seen_at,
  COUNT(DISTINCT session_id) AS session_count,
  SUM(question_count) AS total_questions,
  SUM(correct_count) AS total_correct,
  -- last_accuracy = 最近一次 session 的 accuracy
  (SELECT accuracy FROM memories m2
     WHERE m2.user_id = m.user_id AND m2.subtopic = m.subtopic
     ORDER BY timestamp DESC LIMIT 1) AS last_accuracy
FROM memories m
WHERE user_id = ?
  AND (? IS NULL OR topic = ?)
  AND (? IS NULL OR timestamp >= datetime('now', ?))
GROUP BY subtopic, topic
HAVING session_count >= ?
ORDER BY last_seen_at DESC;
```

零迁移，零新索引（`idx_memories_user_subtopic` 已经够）。

### 3.3 客户端

`src/memory_client.py` 加：

```python
def fetch_mastery(
    user_id: str,
    topic: str | None = None,
    since_days: int | None = None,
) -> list[dict]:
    """启动 review 时拉一次。失败返回 []，调用方自行降级。"""
```

风格与现有 `retrieve_memory` 一致：失败静默。

---

## 4. Agent 流程：review 不进 LangGraph 主图

### 4.1 设计选型对比

| 方案 | 改动量 | 复杂度 | 选 |
|---|---|---|---|
| A. 加 `review` mode 进 intent 路由，做 review_node + review_diagnosis_node | 中 | 高（需要重路由 grading） | ❌ |
| B. review 作为 session 状态上的旁路：独立端点出题/收答，结果写入 `review_result`；后续 `knowledge_graph_node` / `lesson_node` 读取 | 小 | 低 | ✅ |

选 B：复习是**首次进入新一轮学习前的检验步骤**，与主流程的对话/诊断不在同一时间窗口。把它当独立 HTTP 子流程，最干净。

### 4.2 时序

```
前端 GET /chat/{sid}/state
  → 后端检测 user_id 在 L1 有 ≥1 个 subtopic     [前端拿到 has_history=true]
  → 前端显示"开始复习"按钮
用户点击 → POST /chat/{sid}/start-review
  → 后端 fetch_mastery() → 选 3~5 个候选 KP
  → LLM 出题 → 写入 state.review_questions
  → 返回 questions[] 给前端
用户答题 → POST /chat/{sid}/submit-review
  → 后端逐题判分（已知 answer_index，无需 LLM）
  → LLM 总结 → state.review_result = {focus_directive, weak_kps, strong_kps}
  → 返回 review_result 给前端
用户继续 → 选新 topic → 走原有 graph 流程
  → knowledge_graph_node 与 lesson_node 的 prompt
    在拼装时若 state.review_result 存在则注入 focus_directive
```

### 4.3 候选 KP 选择规则（review_node 内）

输入：`fetch_mastery(user_id)` 返回的 items。

```
候选打分:
  staleness = clamp((now - last_seen_at).days / 14, 0, 1)
  uncertainty = 1 - abs(last_accuracy - 0.5) * 2   # 0.5 最不确定
  priority = 0.6 * staleness + 0.4 * uncertainty
取 top 3~5（按用户历史 KP 总数: <=5 全选, 6~10 选 4, >10 选 5）
排除: last_seen_at < 1 day（刚学完不复习）
```

不调 LLM。纯排序，确定性。

### 4.4 出题策略

LLM 调用一次，输入 = 候选 KP 列表 + 各自上次错题摘要（从 L1 `raw_json` 取）。输出 = `list[ReviewQuestion]`，每 KP 1 题。

`ReviewQuestion` 与现有 `QuizQuestion` 同 schema（4 选 1 + answer index + knowledge_point），便于复用前端渲染。

### 4.5 总结策略

提交答案后**不再调 LLM 出诊断**。直接：

```python
review_result = {
    "weak_kps": [kp for kp, ok in graded if not ok],
    "strong_kps": [kp for kp, ok in graded if ok],
    "focus_directive": _render_directive(weak_kps, strong_kps),
}
```

`_render_directive` 是模板字符串：「学生在 X、Y 上仍薄弱，本次教案应优先衔接这些点；Z 已掌握可以略过基础部分。」 后续 `knowledge_graph` / `lesson` 节点直接把这段文字注入它们的 system prompt。

---

## 5. State 改动

`src/agent/state.py` 加：

```python
# Review (复习)
mastery_snapshot: list[dict] | None       # session 启动时拉一次, 中途不变
review_questions: list[QuizQuestion] | None
review_answers: list[int] | None
review_result: dict | None                # {weak_kps, strong_kps, focus_directive}
```

**snapshot 持久化策略**：`mastery_snapshot` 与 `review_result` **进** `sessions.db` 序列化（`src/api/store.py` 的白名单），保证页面 reload 后状态一致；`review_questions` / `review_answers` 同理。

回访 session 时如果 `mastery_snapshot` 已存在但超过 24h，`POST /start-review` 会强制重拉一次（避免基于陈旧数据出题）。

---

## 6. Prompt 改动（最小集）

| 文件 | 改动 |
|---|---|
| `src/prompts/review_generate_system.j2` | **新建**。输入：候选 KP 列表 + 上次错题。输出：JSON 题目数组（schema 与 QuizQuestion 一致）|
| `src/prompts/knowledge_graph_system.j2` | **加一段**：`{% if focus_directive %}{{ focus_directive }}{% endif %}`，让出图时优先衔接薄弱 KP |
| `src/prompts/lesson_system.j2` | 同上注入 focus_directive |

`review_generate_system.j2` 关键约束：
- 每个 KP 出 1 题，不要"组合题"
- 难度对齐用户上次成绩（acc < 0.5 出基础检验题，acc ≥ 0.7 出应用题）
- 输出严格 JSON，长度等于输入 KP 数

---

## 7. API 端点（`src/api/chat.py`）

```http
POST /chat/{session_id}/start-review
  Body: {}                              # session 中已有 user_id
  Returns: {questions: [...]}           # 同 generate-quiz 的 question schema
  失败: 502 if memory service down; 404 if no history

POST /chat/{session_id}/submit-review
  Body: {answers: [int, ...]}
  Returns: {
    review_result: {
      weak_kps: [...],
      strong_kps: [...],
      focus_directive: "...",
      score: 0.6
    }
  }
```

`GET /chat/{session_id}/state` 现有响应里**追加** `has_history: bool` 字段，前端用来决定是否显示"复习"按钮。判断逻辑：`fetch_mastery(user_id, min_seen=1)` 非空。

---

## 8. 前端改动（`static/index.html`、`static/api.js`）

最小可用：
1. 页面加载完调一次 `GET /api/chat/{sid}/state`，看 `has_history`
2. 若 true，在"开始学习"按钮旁边加一颗"先复习一下"按钮
3. 点击 → 调 `start-review` → 复用现有 quiz UI 渲染（同 `QuizQuestion` schema）
4. 提交 → `submit-review` → 渲染一张简短卡片（已掌握 / 需巩固两列）→ 用户点"继续"返回主流程

不做：单独的 `review.html` 页面、复习历史回顾、错题本。这些是后续迭代。

---

## 9. 文件改动清单

| # | 文件 | 改动 | 行数估计 |
|---|---|---|---|
| 1 | `student_memory/app/db/sqlite_repo.py` | 加 `aggregate_mastery(user_id, topic, since_days, min_seen)` | +50 |
| 2 | `student_memory/app/api/schemas.py` | `MasteryItem` / `MasteryResponse` | +20 |
| 3 | `student_memory/app/api/routes.py` | `GET /mastery/{user_id}` | +30 |
| 4 | `student_memory/tests/test_mastery.py` | 新建：聚合 SQL 单测 + 端点测试 | +120 |
| 5 | `src/memory_client.py` | 加 `fetch_mastery` | +30 |
| 6 | `src/llm/schemas.py` | `ReviewResult` Pydantic 模型 | +15 |
| 7 | `src/agent/state.py` | 4 个新字段 | +5 |
| 8 | `src/agent/nodes/review.py` | **新建**：`select_candidate_kps`, `generate_review_questions`, `grade_review`, `_render_directive` | +180 |
| 9 | `src/prompts/review_generate_system.j2` | **新建**出题 prompt | +40 |
| 10 | `src/prompts/knowledge_graph_system.j2` | 注入 `focus_directive` 段 | +5 |
| 11 | `src/prompts/lesson_system.j2` | 同上 | +5 |
| 12 | `src/api/chat.py` | 加 `start-review` / `submit-review`；扩 `state` 端点的 `has_history` | +90 |
| 13 | `src/api/store.py` | 序列化白名单加 4 个新 state 字段 | +5 |
| 14 | `static/index.html` | "先复习一下"按钮 + 触发逻辑 | +30 |
| 15 | `static/api.js` | `startReview / submitReview` 客户端方法 | +25 |
| 16 | `tests/test_review_node.py` | 候选选择 + 出题 + 评分单测（mock LLM）| +150 |
| 17 | `tests/test_chat_api_review.py` | 端到端：fake L1 → start → submit → review_result 落盘 | +120 |

合计 ~920 行，3 个 PR 切分。

---

## 10. 实施顺序

**PR-1（数据层，独立可上线）** — 改动 1-5
- 验收：`pytest student_memory/tests/test_mastery.py`
- 验收：手动 `curl http://localhost:8090/mastery/default_user`，看是否返回当前 ingest 过的 KP 聚合
- 没有调用方，零风险

**PR-2（review 后端 + agent，灰度）** — 改动 6-13, 16-17
- 加 env var `ENABLE_REVIEW_FLOW`，默认 false
- 验收：`ENABLE_REVIEW_FLOW=false` 时所有现有测试通过、行为字节级不变
- 验收：`ENABLE_REVIEW_FLOW=true` 时手动 curl `/start-review` 看出题质量
- 验收：`review_result` 写入 state 后下一次 `lesson` 生成的 prompt 里能看到 `focus_directive`

**PR-3（前端）** — 改动 14-15
- 仍受 PR-2 的 env var 控制（前端读 `/state` 的 `has_history` 判断；env 关时后端永远返回 false）
- 验收：浏览器手动跑一次完整循环

---

## 11. 不变量（验收清单）

- [ ] `fetch_mastery` HTTP 失败返回 `[]`，前端"复习"按钮静默隐藏，不阻塞主流程
- [ ] `mastery_snapshot` 在 session 启动时拉一次，中途不重拉（除非 >24h）
- [ ] 候选 KP 选择是确定性的（同输入 → 同输出），便于回放调试
- [ ] 复习出题与现有 `QuizQuestion` schema 完全一致，前端零分支渲染
- [ ] `review_result` 落盘到 `sessions.db`，页面 reload 后仍可读
- [ ] `ENABLE_REVIEW_FLOW=false` 时：所有 `tests/` 通过；`/state` 端点不返回 `has_history` 字段（或恒为 false）
- [ ] 候选 KP 排除 `last_seen_at < 1 day`（防止刚学完立刻复习）
- [ ] review 评分**不调 LLM**（仅 ReviewResult 总结调一次便宜模型，可选）

---

## 12. 不做的事

- ❌ **不把 review 接进 LangGraph 主图**：旁路设计已够，进图徒增 grading_node 的分支复杂度
- ❌ **不复用历史原题**：候选题 schema 与 raw_json 里的 question_text 不一定能直接对齐，先 LLM 生成新题，等 PR-1 上线后看数据再决定要不要做"题库复用"
- ❌ **不做错题本 / 复习历史页**：与本循环无关
- ❌ **不做间隔重复算法（SM-2 等）**：先用简单 staleness 公式跑出数据，证明有用再升级
- ❌ **不依赖 L2 笔记**：L2 spec（`2026-04-30-agent-memory-spec.md`）作为本流程的增强项，独立、并行、可推后到 P2
- ❌ **不做多用户切换 UI**：仍用 `DEFAULT_USER_ID`；多账号是另一条线

---

## 13. 与 L2 spec 的关系

| 维度 | 本 spec（review-flow） | L2 spec（agent-memory）|
|---|---|---|
| 数据来源 | L1 结构化字段（accuracy / timestamp）| LLM 阅读对话后的判断 |
| 写入触发 | session 完成时已有 ingest 写入 | lesson/slides 完成后另开 reviewer LLM |
| 读出时机 | 用户进入 review → 确定性查询 | 每次 chat 启动加载 snapshot |
| 用户可见性 | 直接可见（复习题、focus 卡片）| 隐式（影响 chat 风格）|
| 优先级 | P0 | P2，**本 spec 上线后再启动** |

L2 上线后的角色：为 `review_generate_system.j2` 提供"该学生易混淆 X 与 Y"这类提示，让出题更精准。但**没有 L2，review 流程也完整可用**。
