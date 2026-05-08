# Hybrid Agent Memory — 学生记忆 + 学习进程 统一设计

**状态**：草案 / 待评审
**日期**：2026-05-08
**范围**：`student_memory/`、`src/memory_client.py`、`src/reviewer.py`（新建）、`src/progress_aggregator.py`（新建）、`src/agent/state.py`、`src/agent/nodes/{chat,knowledge_graph,quiz,lesson,review}.py`、`src/api/chat.py`、`src/llm/schemas.py`、`src/prompts/`
**Supersedes**：`docs/superpowers/specs/2026-05-06-l2-misconception-design.md`（misconception-only 路线，未实施）
**前置文档**：
- `docs/superpowers/specs/2026-04-30-agent-memory-spec.md`（三层模型完整方案，本 spec 是其工程化执行版）
- `docs/superpowers/specs/2026-04-30-review-flow-spec.md`（review 流程，本 spec 在其上挂触发与消费）

---

## 1. 设计立场与借鉴来源

本 spec 把项目现有 L1（结构化向量召回）+ L3（即时上下文）扩展出**统一的 L2 主观笔记**与**结构化学习进程**。两者**共享 reviewer 触发点和服务端基础设施**，但走分离的写路径：

- **L2 主观笔记**：reviewer 用 LLM 决策，写 4 类 notes（misconception / preference / style / goal）
- **学习进程**：纯 SQL 聚合 L1 mastery 数据，无 LLM 参与，确定性渲染

借鉴自两个先进 agentic memory 框架：

### 1.1 借自 Mem0（写路径）

- **5-action 语义 diff**：reviewer 不是单向追加，而是用 LLM 在 `ADD / UPDATE / DELETE / NOOP / REJECT` 中选一。新候选事实先用 hnswlib 召回 top-K 相似旧 note，让 LLM 在小窗口里做决策。
- **REJECT 是必备 action**（Mem0 公开审计 #4573 的教训：97.8% 条目是垃圾，根因是没有"不写"出口）
- **Hash-dedup 预过滤**：进 LLM 之前先 sha256 砍掉字面重复（最便宜的去重）
- **Speaker tag**：extract prompt 标 `[STUDENT]` / `[TUTOR]`，避免给 AI 自己的话写成"该学生偏好"
- **审计日志与检索索引解耦**：`notes_history` 永不删，含 NOOP/REJECT，便于事后 prompt 迭代

### 1.2 借自 Hermes-agent（读路径）

- **冻结快照不变量**：session 启动时拉一次 snapshot 进 `LearningState.memory_snapshot`，**整个 session 不再变**。reviewer 中途写的新 note 下次 session 才生效。这保留 prompt prefix cache 友好性（项目暂未启用 cache，但保留不变量便于未来加）。
- **Forked sub-agent reviewer**：reviewer 用单独的 `REVIEWER_MODEL`（便宜模型），与主流程 LLM 解耦
- **紧凑字符预算**：snapshot ≤ 3500 chars 硬上限，强迫 reviewer 做 curation 而非 accumulation
- **Server 端 Markdown 渲染**：snapshot 在 student_memory 服务渲染为人类可读 Markdown，与 Hermes 的 MEMORY.md 同构

### 1.3 项目原生贡献

- **进度纯 SQL 聚合**：避免 LLM 写进度数据带来的幻觉风险
- **复用现有 hnswlib + bge-m3**：notes 走独立 hnsw index 文件，与 memories 用同一套 embedding 基础设施
- **review 路径例外（实时 fetch）**：保留"reviewer 写完下次 review 立刻见效"的紧密反馈环

---

## 2. 决策摘要

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | 整体架构 | Mem0 写 + Hermes 读 + L1 共享 | 单独任一框架都有缺角：Mem0 没 cache 不变量；Hermes 没结构化查询 |
| 2 | L2 categories | misconception / preference / style / goal（4 类） | 与原 agent-memory-spec §1 对齐；review-flow 只用 misconception |
| 3 | 进度数据 | 纯 SQL 聚合，无 LLM | 确定性、零幻觉风险、可重建；留接口扩展点支持未来 LLM 润色 |
| 4 | 进度颗粒度 | 主题级里程碑 + KP 级 mastery 曲线（10 采样点上限） | 足够画趋势，不无限增长 |
| 5 | Reviewer 触发点 | submit-quiz / submit-review / generate-lesson | slides 与 lesson 信号重复，二选一选 lesson；chat-N-轮暂不做 |
| 6 | 读路径注入 | chat / knowledge_graph / quiz / lesson 走 frozen snapshot；review 走实时 fetch | 前者覆盖 chat 热路径，后者保紧密反馈环 |
| 7 | 邻居召回 | 同 category 内 hnswlib top-5 | 跨 category 召回会让 reviewer 对齐困难，无收益 |
| 8 | 5-action 决策 | 逐条 candidate 调一次 LLM call B | 批量决策注意力稀释；便宜模型下 5 次 call 也 ~1 分钱 |
| 9 | Hash-dedup 时机 | 进 call B 之前 | 字面重复占比高，省 LLM 成本 |
| 10 | Snapshot 优先级 | 进度 > 易混点 > 风格偏好 > 目标 | 进度最具体最影响 AI 行为；目标最弱 |
| 11 | retrieve_memory 处理 | 保留但加 ≥10 字门槛，仅 chat_node | snapshot 是摘要，细节召回仍有用；门槛挡掉"嗯"/"继续" |
| 12 | 灰度开关 | `ENABLE_MEMORY_REVIEW` 同时门控读写 | 关闭时全链路无副作用、prompt 字节级不变 |
| 13 | 模型 | `REVIEWER_MODEL` env，与主流程解耦 | 便宜模型即可，错了影响小 |

---

## 3. 架构与数据流

### 3.1 整体图

```
                   ┌──────────────── 写路径 (Mem0-style consolidation, 旁路异步) ────────────────┐
                   │                                                                              │
   submit-quiz ────┤                                                                              │
   submit-review ──┼──► asyncio.to_thread(run_memory_review, state, source_event)                │
   generate-lesson ┘         │                                                                    │
                             ▼                                                                    │
                   src/reviewer.py                                                                 │
                     ① _extract_candidates(state, source_event)  ← LLM call A (fact extract)     │
                     ② for each candidate:                                                        │
                          hash-dedup pre-filter; injection-scan pre-filter                        │
                          retrieve top-5 similar active notes via hnswlib (同 category)           │
                          ③ _consolidate(candidate, neighbors)   ← LLM call B (5-action)         │
                          ④ apply: ADD / UPDATE / DELETE / NOOP / REJECT                          │
                              ├─ writes notes + notes_history (含 NOOP/REJECT)                   │
                              └─ LRU 淘汰: 单 (user, category) active > 30 时 archived=1 最老者   │
                                                                                                  │
   submit-quiz ────┐                                                                              │
   submit-review ──┼──► asyncio.to_thread(refresh_progress, user_id, topic, state)               │
   generate-lesson ┘         │  纯结构化, 无 LLM                                                  │
                             └─ UPSERT progress_snapshots + INSERT mastery_curve (10 采样上限)    │
                                                                                                  │
                   └──────────────────────────────────────────────────────────────────────────────┘

                   ┌──────────────── 读路径 (Hermes-style frozen snapshot) ───────────────────┐
                   │                                                                          │
   POST /api/chat (新 session) ──► server 拉一次 GET /memory/snapshot/{user_id}              │
                                       │                                                       │
                                       ├─ active notes (≤30/cat) + progress + mastery_curve   │
                                       ├─ Jinja 渲染 + 优先级装填 (≤3500 chars)                │
                                       └─ 写入 LearningState.memory_snapshot ← 整 session 冻结
                                                                                              │
   chat_node / knowledge_graph_node / quiz_node / lesson_node                                │
       └─ system prompt 末尾拼 state["memory_snapshot"], session 内零 HTTP                    │
                                                                                              │
   chat_node 额外: 当 user 消息 ≥ 10 字时调 retrieve_memory (L1 向量) 拼最相关片段             │
                                                                                              │
   generate-review-questions (例外, 实时 fetch)                                               │
       └─ fetch_active_notes(user_id, category='misconception'), 不冻结                       │
                                                                                              │
                   └──────────────────────────────────────────────────────────────────────────┘
```

### 3.2 三个关键不变量

1. **冻结快照（Hermes）**：chat 系列节点用 `state["memory_snapshot"]`，session 内整段 prompt 字节级稳定。reviewer 写完的新条目下次 session 才生效。
2. **进度纯结构化**：reviewer 只写 notes，不参与 progress 推断。`progress_snapshots / mastery_curve` 由 SQL 聚合 + L1 数据派生，可重建。
3. **写路径独立**：reviewer 和 progress aggregator 互不依赖，任一失败不影响另一。也都不影响 HTTP 主响应。

### 3.3 三个具体场景（验证设计）

#### 场景 1 — 第一次诊断（建立基线）

T+0：小明做完 10 题诊断 → `submit-quiz`。

`refresh_progress` 写入：
```sql
INSERT INTO progress_snapshots VALUES
  ('xiaoming', '勾股定理', 'in_progress', 1715000000, 1715000000, 0.6, 's_001');
INSERT INTO mastery_curve VALUES
  ('xiaoming', '勾股定理', '勾股定理公式',  0.5, 1715000000, 'quiz'),
  ('xiaoming', '勾股定理', '坐标距离公式',  0.0, 1715000000, 'quiz');
```

`run_memory_review` LLM call A 提取 3 条候选（misconception × 2 + style × 1），全部 hash-dedup pass，全部 LLM call B 决策 ADD（库为空），写 notes + notes_history。

**关键**：本 session 内 chat 仍看不到这 3 条 note —— snapshot 在 session 启动时已拉，不再变。

#### 场景 2 — 三天后开新 session（frozen snapshot 生效）

新 session_id → server 拉 snapshot → 渲染：

```markdown
## 该学生的学习进程
- 已开始：勾股定理（3天前，整体掌握度 60%）
- 当前薄弱 KP：坐标距离公式（0%）、勾股定理公式（50%）

## 该学生的历史易混点
- 把"坐标两点距离"误用为"两点坐标差"，未平方再开方
- 勾股定理只在直角三角形成立，但学生在锐角三角形上也套用

## 该学生的学习风格与偏好
- (style) 倾向选最长选项，疑似凭长度猜答案
```

总长 ~480 chars。session 内 chat / knowledge_graph / quiz / lesson 节点都用这一段，**零 HTTP**。

#### 场景 3 — 复习暴露重复 misconception（5-action 真正发挥作用）

review 出题用上述 misconception 做 distractor → 小明又选错 → `submit-review` 触发 reviewer。

LLM call A 提取候选："在坐标距离题里仍然用'两点坐标差'而不平方"。
LLM call B 召回 top-5 邻居，命中场景 1 写的那条（sim=0.91）。
LLM 决策：

```json
{"action": "NOOP", "target_id": 1,
 "reason": "已有几乎相同的 note (id=1)，本次只是再次确认"}
```

→ 不写新条目，但 `notes_history` 记一行 NOOP（含 reason）。**避免了 Mem0 公开审计的"668 条相似垃圾"失败模式**。

反过来，若小明在另一题暴露**新型**误解，reviewer 决策 ADD；若小明同时在某 KP 上表现出对旧 misconception 的纠正，reviewer 可决策 DELETE（archived=1 软删）。

---

## 4. 数据模型

四张表，全在 `student_memory/data/memory.db`。

### 4.1 `notes`（L2 主观笔记）

```sql
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('misconception', 'preference', 'style', 'goal')),
    content TEXT NOT NULL,                  -- 1 ≤ len ≤ 200
    content_hash TEXT NOT NULL,             -- sha256(lower(strip(content))).hexdigest()[:16]
    source_session_id TEXT,
    source_event TEXT NOT NULL,             -- 'submit-quiz' | 'submit-review' | 'generate-lesson'
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0     -- DELETE action / LRU 淘汰都置 1
);

CREATE INDEX idx_notes_user_active ON notes(user_id, category, created_at DESC) WHERE archived = 0;
CREATE INDEX idx_notes_user_hash   ON notes(user_id, content_hash) WHERE archived = 0;
```

**embedding 不存 BLOB 列**：走独立 `notes_hnsw.bin` 索引文件，与 `student_memory` 现有 memories 用同一套 hnswlib 基础设施。`note_id ↔ vector_id` 映射在 hnswlib 内部维护。

**LRU 淘汰**：`add_note` 之后检查 `(user_id, category)` 的 active 计数；> 30 则 archived=1 最老一条（按 `created_at` ASC），不物理删。

### 4.2 `notes_history`（Mem0-style 审计）

```sql
CREATE TABLE notes_history (
    id INTEGER PRIMARY KEY,
    note_id INTEGER,                        -- ADD/UPDATE/DELETE/NOOP 必填; REJECT 可为 NULL
    event TEXT NOT NULL CHECK(event IN ('ADD', 'UPDATE', 'DELETE', 'NOOP', 'REJECT')),
    old_content TEXT,                        -- UPDATE / DELETE 时填
    new_content TEXT,                        -- ADD / UPDATE / REJECT(被拒的候选) 时填
    reason TEXT NOT NULL,                    -- LLM 给的决策理由 (NOOP/REJECT 必填)
    source_session_id TEXT,
    source_event TEXT,
    ts INTEGER NOT NULL,
    FOREIGN KEY (note_id) REFERENCES notes(id)
);

CREATE INDEX idx_history_note ON notes_history(note_id, ts);
CREATE INDEX idx_history_event ON notes_history(event, ts);  -- 便于"过去 7 天 REJECT 集中在哪些原因"查询
```

**永不删除**——含 NOOP/REJECT 全留，事后调 prompt 用。

### 4.3 `progress_snapshots`（主题级里程碑）

```sql
CREATE TABLE progress_snapshots (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('in_progress', 'mastered', 'abandoned')),
    started_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    mastery_overall REAL NOT NULL,           -- [0, 1], = AVG(mastery_curve.score 当下采样)
    last_session_id TEXT,
    UNIQUE(user_id, topic)
);

CREATE INDEX idx_progress_user_recent ON progress_snapshots(user_id, last_seen_at DESC);
```

**Status 转移规则**（`refresh_progress` 内部计算）：

```python
def _next_status(curr: str, mastery: float, last_seen_at: int, now: int) -> str:
    age_days = (now - last_seen_at) / 86400
    if curr is None:                                  return 'in_progress'
    if mastery >= 0.85 and age_days < 7:              return 'mastered'
    if curr != 'mastered' and age_days > 30:          return 'abandoned'
    if curr == 'abandoned' and mastery > 0:           return 'in_progress'  # 捡回来
    return curr
```

### 4.4 `mastery_curve`（KP 级时间序列）

```sql
CREATE TABLE mastery_curve (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    knowledge_point TEXT NOT NULL,
    score REAL NOT NULL,                     -- [0, 1]
    ts INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('quiz', 'review', 'lesson_completion'))
);

CREATE INDEX idx_curve_user_kp ON mastery_curve(user_id, topic, knowledge_point, ts);
```

**采样上限**：每 `(user_id, topic, knowledge_point)` 最多保留 10 个最近采样点；`add_mastery_sample` 之后若超限删最老的。

### 4.5 `op_errors`（错误追踪 / bug triage）

所有静默回退点（HTTP 5xx、timeout、422、LLM parse error、unexpected exception）都在此表留痕，闭合"失败静默"带来的可观测性真空。

```sql
CREATE TABLE op_errors (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('main_app', 'memory_service')),
    user_id TEXT,
    session_id TEXT,
    correlation_id TEXT,             -- reviewer / progress 触发的 UUID, 串起一次调用链
    op TEXT NOT NULL,                -- 'fetch_snapshot' | 'write_note' | 'llm_call_a' | 'llm_call_b' | 'refresh_progress' | ...
    status TEXT NOT NULL,            -- 'http_5xx' | 'http_4xx' | 'timeout' | 'parse_error' | 'unexpected'
    error_class TEXT,                -- exception class name (e.g., 'httpx.ConnectTimeout')
    error_msg TEXT,                  -- str(exc)[:500]
    payload_excerpt TEXT             -- 出错时的关键输入摘要[:500], PII 脱敏
);

CREATE INDEX idx_op_errors_user_ts ON op_errors(user_id, ts DESC);
CREATE INDEX idx_op_errors_corr   ON op_errors(correlation_id);
CREATE INDEX idx_op_errors_op_ts  ON op_errors(op, ts DESC);
```

**Retention**：表行 ts > 30 天的启动时清理一次（与 `student_memory` 现有清理机制同地点；不与 `notes_history` 复用 retention，因为 history 必须永久保留）。

**截断**：`error_msg` / `payload_excerpt` 各 ≤ 500 chars，超长截断 + `…`。`payload_excerpt` 必须脱敏（不直接塞 user message 全文，仅塞类型 + 长度 + 关键字段名）。

**写入端**：
- `student_memory` 服务端：`routes.py` 在 5xx / 422 / 内部异常分支自己 INSERT
- 主应用：`memory_client.log_error()` 通过 `POST /errors/log` 写（fire-and-forget；client 自身也 try/except）

---

## 5. 服务端 API

新增 10 个 endpoint，全在 `student_memory/app/api/routes.py`。

### 5.1 `POST /notes`

```http
Body: {user_id, category, content, source_session_id, source_event}
```

服务端职责：
1. 校验 `1 ≤ len(content) ≤ 200`，非纯空白
2. 校验 `category` 在 4 个白名单值中
3. injection-scan（5 条正则，见 §6.4）
4. **服务端计算** `content_hash = sha256(content.lower().strip()).hexdigest()[:16]`，存入 `notes.content_hash` 列；客户端不传 hash
5. 与同 user 同 hash 的 active 行冲突 → 409（注：reviewer 在 hash-dedup 预过滤里就该挡掉，到这里冲突说明 race，仍记一行 `notes_history.event=REJECT, reason='race-hash-conflict'`）

任一不过 → 422 / 409，**不静默丢弃**。
通过则插入 + 触发 LRU 淘汰 + 加入 hnswlib 索引（向量化在服务端做，复用现有 bge-m3）。

返回：`{note_id, active_count_in_category}`

### 5.2 `GET /notes/{user_id}?category=<cat>`

返回该 user 该 category 的 active notes，按 `created_at DESC`，上限 30。

返回：`{notes: [{id, category, content, content_hash, created_at, updated_at}]}`

**`content_hash` 必须返回**：reviewer 用客户端 hash-dedup（§6.3）需要拿到这个字段做 set 查询。

### 5.3 `GET /notes/{user_id}/neighbors`

```http
Query: category, query_text, top_k (default 5)
```

服务端嵌入 `query_text` → hnswlib 召回该 user 该 category 的 top-K 最相似 active note，附 `sim_score`。

返回：`{neighbors: [{id, content, sim_score, age_days}]}`

### 5.4 `POST /progress`（UPSERT）

```http
Body: {user_id, topic, mastery_overall, last_session_id, kp_samples: [{kp, score, source}]}
```

服务端：
1. UPSERT `progress_snapshots`（按 `_next_status` 计算 status）
2. INSERT 一行/KP 到 `mastery_curve`，触发采样上限淘汰

返回：`{topic, new_status, sample_count}`

### 5.5 `GET /memory/snapshot/{user_id}`

服务端拼装 Markdown 快照（见 §6 渲染算法），返回：

```python
class SnapshotResponse(BaseModel):
    snapshot_md: str = Field(..., max_length=3500)
    char_count: int
    truncated: bool
    sections: dict[str, int]   # {"progress": 240, "misconception": 180, ...}
```

### 5.6 `GET /notes/{user_id}/history`（可选，运营/调试用）

```http
Query: event (optional), limit (default 100)
```

返回 `notes_history` 行，按 `ts DESC`。本 spec 不在主流程使用，但 PR-1 一起做掉，便于事后 prompt 迭代。

### 5.7 `GET /notes/{note_id}`（by-id 单条）

reviewer 在 UPDATE / DELETE action 中需要读旧值供审计。返回 `{id, user_id, category, content, content_hash, created_at, updated_at, archived}`。404 若不存在。

### 5.8 `PATCH /notes/{note_id}`

```http
Body: {content}
```

reviewer UPDATE action 调用。服务端：
1. 校验 content 长度 / 非空 / injection-scan / **重算 content_hash 并更新**
2. 校验 hash 与同 user 同 category active 不冲突（除自身以外）→ 冲突 409
3. UPDATE `content / content_hash / updated_at`；保留原 hnswlib 向量条目（重新嵌入新文本，复用 vector_id）

返回 `{note_id, updated_at}`。

### 5.9 `DELETE /notes/{note_id}`

reviewer DELETE action 调用。**软删**：置 `archived=1`，**不**物理删；从 hnswlib 索引中移除该 vector_id。返回 `{note_id, archived: true}`。

### 5.10 `POST /errors/log`（错误 sink，永不 5xx）

```http
Body: {source, user_id, session_id, correlation_id, op, status, error_class, error_msg, payload_excerpt}
```

服务端职责：
1. 截断 `error_msg` / `payload_excerpt` 到 ≤ 500 chars
2. INSERT 到 `op_errors` 表
3. **任何写入失败必须返回 200**（sink 端点不能再触发上游重试 —— 那会形成"错误日志失败 → 主流程降级 → 又写错误日志"的循环）。失败时仅 server stderr `logger.warning`，不抛 5xx

返回：`{logged: true | false}`（false 表示写库失败，但 HTTP 仍 200）。

**配套的查询用 endpoint** —— 不在主流程使用，PR-1 一起做掉便于运营：

```http
GET /errors/{user_id}?op=<op>&since=<ts>&limit=100
GET /errors/correlation/{correlation_id}    # 拉一次完整调用链
```

---

## 6. Reviewer（`src/reviewer.py`）

### 6.1 入口

```python
def run_memory_review(user_id: str, session_id: str, state: dict, source_event: str) -> None:
    """旁路异步任务，任意异常 logger.exception 吞掉 + 写 op_errors。
    被 src/api/chat.py 在 ENABLE_MEMORY_REVIEW=true 时通过 asyncio.to_thread 调用。
    """
    correlation_id = uuid.uuid4().hex   # 串起本次 reviewer 调用链
    try:
        _do_review(user_id, session_id, state, source_event, correlation_id)
    except Exception as exc:
        logger.exception("memory_review failed")
        memory_client.log_error(
            source='main_app', user_id=user_id, session_id=session_id,
            correlation_id=correlation_id, op='run_memory_review',
            status='unexpected', error_class=type(exc).__name__, error_msg=str(exc)[:500],
            payload_excerpt=f"source_event={source_event}",
        )
```

**`correlation_id`** 在本次 reviewer 全流程贯穿：传入 `_extract_candidates`（call A）、每条 `_consolidate`（call B）、所有 `memory_client.write_note / update_note / archive_note / write_history`，并写入每行 `notes_history.reason` 的前缀（格式 `[corr=<id>] <reason>`），便于事后 join：

```sql
SELECT h.* FROM notes_history h WHERE h.reason LIKE '[corr=' || ? || ']%' ORDER BY h.ts;
SELECT e.* FROM op_errors e WHERE e.correlation_id = ? ORDER BY e.ts;
```

### 6.2 LLM Call A — Fact Extract

**Prompt 双模板**（cache 友好）：

`src/prompts/memory_extract_system.j2`（稳定段）：

```
你在阅读一段学习会话，提取"跨会话有用、且不能从结构化数据自动推出"的事实候选。

输出 JSON: {"candidates": [{"category": ..., "content": ...}]}

约束:
- category 必须是 misconception / preference / style / goal
- content ≤ 200 字符，单一观点，标准陈述句
- 最多 5 条；宁可少不可多

不要写:
- 答对/错的题数 (这是 L1 已存的事实)
- 单次会话临时状态
- "学生很努力" 这类情绪化判断
- 字面重复已有 notes 的内容

speaker tag: 当前消息说话者是 [STUDENT] 还是 [TUTOR], 区分清楚再判断属于哪一方。
[STUDENT] 的话才能反映学生的认知/偏好/目标。
```

`src/prompts/memory_extract_user.j2`（可变段）：

```jinja
## 本次 source_event: {{ source_event }}

{% if wrong_items %}
## 错题片段
{% for w in wrong_items %}
[STUDENT] Q: {{ w.question_text }}
         选项: A={{ w.options[0] }} B={{ w.options[1] }} C={{ w.options[2] }} D={{ w.options[3] }}
         正确答案: {{ w.correct_label }}, 学生答案: {{ w.student_label }}
         所属 KP: {{ w.kp }}, 此前正确率: {{ w.last_accuracy }}
{% endfor %}
{% endif %}

{% if dialogue_tail %}
## 对话片段（session 内最近 10 轮）
{% for m in dialogue_tail %}
[{{ m.role|upper }}] {{ m.content[:300] }}
{% endfor %}
{% endif %}

{% if existing %}
## 该学生现有 active notes（不要重复）
{% for n in existing %}
- ({{ n.category }}) {{ n.content }}
{% endfor %}
{% endif %}

请按 system 指令输出 candidates。
```

**输入截断规则**（在 `_collect_extract_input` 里实现）：
- `wrong_items` 取错题最多 4 道，按 (last_accuracy 升序, 题号升序) 排序选前 4；超长 question_text 截到 400 chars + `…`
- `dialogue_tail` 取 state 最近 10 条 message；每条 content 截到 300 chars
- `existing` 全部入（服务端已保证 ≤30/category × 4 = ≤120 条，~24KB 上限）

**输入预算**：错题 ≤ 4 道 × 600 chars + 对话尾部 ≤ 10 条 × 300 chars + 现有 notes ≤ 30 × 200 chars = ~10KB（~3K tokens）。

**Pydantic schema**（`src/llm/schemas.py`）：

```python
class FactCandidate(BaseModel):
    category: Literal["misconception", "preference", "style", "goal"]
    content: str = Field(..., min_length=1, max_length=200)

class ExtractResult(BaseModel):
    candidates: list[FactCandidate] = Field(..., max_length=5)
```

### 6.3 Hash-dedup 预过滤

```python
def _hash(text: str) -> str:
    return hashlib.sha256(text.lower().strip().encode()).hexdigest()[:16]

# 一次 review 启动时, 把该 user 的全部 active notes hash 拉成 set
existing_hashes: set[str] = set()
for cat in CATEGORIES:
    for n in memory_client.fetch_active_notes(user_id, category=cat):
        existing_hashes.add(n["content_hash"])

# 对每条 candidate
for candidate in extract_result.candidates:
    if _hash(candidate.content) in existing_hashes:
        memory_client.write_history(
            note_id=None, event='REJECT', new_content=candidate.content,
            old_content=None, reason='hash duplicate',
            source_session_id=session_id, source_event=source_event,
        )
        continue   # 跳过 LLM call B
```

**实现要点**：
- hash 集合在单次 review 启动时拉一次（4 个 GET /notes 调用），不在每条 candidate 时重复查
- 同 review 内若某条候选被 ADD 写入，**也加入 `existing_hashes`**，防止同次提取的两条 candidate 字面重复都写
- hash 比较与 category 无关：跨 category 字面重复也算 dup（很罕见但应该挡）

### 6.4 Injection-scan 预过滤

```python
INJECTION_PATTERNS = [
    r"(?i)ignore (previous|all|the above) instructions",
    r"(?i)disregard (the )?(system|prior) (prompt|message)",
    r"(?i)you are now",
    r"<\|.*?\|>",
    r"(?i)system\s*:",
]

def _is_injection(content: str) -> bool:
    return any(re.search(p, content) for p in INJECTION_PATTERNS)

# 命中即 REJECT 入 history, 不进 LLM call B
```

### 6.5 LLM Call B — 5-Action Consolidation

对每条通过预过滤的 candidate：

```python
neighbors = memory_client.fetch_neighbors(
    user_id=user_id,
    category=candidate.category,         # 同 category 内召回
    query_text=candidate.content,
    top_k=5,
)
decision = _consolidate(candidate, neighbors)
_apply_decision(decision, candidate, ...)
```

**Prompt 双模板**：

`src/prompts/memory_consolidate_system.j2`（稳定段）：

```
你在维护一个学习记忆库。给定一条新候选事实和它最相似的 5 条已存条目，
决定如何合并：

ADD     — 候选是新信息, 与所有邻居都不冲突也不重复 → 新增
UPDATE  — 候选与某条邻居说的是同一件事但有更新/纠正 → 替换该邻居
DELETE  — 候选证明某条邻居已不成立（学生已纠正） → 软删该邻居, 不写新条目
NOOP    — 候选与某条邻居语义重复 → 不动
REJECT  — 候选质量低 / 自相矛盾 / 偏离 category 定义 → 不写, 记录理由

硬规则:
- 一次决策只动 0 或 1 条邻居（不批量）
- 输出必须包含 reason，便于事后复盘
- 宁愿 NOOP / REJECT，也不写质量低的笔记
```

`src/prompts/memory_consolidate_user.j2`（可变段）：

```jinja
## 候选
({{ candidate.category }}) {{ candidate.content }}

{% if neighbors %}
## 同 category 最相似的 5 条 active 邻居
{% for n in neighbors %}
[id={{ n.id }}, sim={{ "%.2f"|format(n.sim_score) }}, age_days={{ n.age_days }}]
  {{ n.content }}
{% endfor %}
{% else %}
## 邻居：无（同 category 当前为空集）
{% endif %}

请输出一个 ConsolidationDecision JSON。
```

**输出 schema**：

```python
class ConsolidationDecision(BaseModel):
    action: Literal["ADD", "UPDATE", "DELETE", "NOOP", "REJECT"]
    target_id: int | None = None              # ADD/REJECT 时为 None；其余必填
    new_content: str | None = None            # ADD/UPDATE 时填
    reason: str = Field(..., min_length=1, max_length=200)
```

校验：
- `action == 'ADD'` → `target_id is None and new_content is not None`
- `action == 'UPDATE'` → `target_id is not None and new_content is not None`
- `action == 'DELETE'` → `target_id is not None`
- `action == 'NOOP'` → `target_id is not None`
- `action == 'REJECT'` → `target_id is None`

非法组合 → 当作 REJECT 入 history（reason=`'invalid decision schema'`），不 raise。

### 6.6 应用决策

```python
def _apply_decision(decision, candidate, user_id, session_id, source_event):
    ts = int(time.time())
    if decision.action == 'ADD':
        note_id = memory_client.write_note(
            user_id, candidate.category, decision.new_content, session_id, source_event,
        )
        if note_id is None:
            return  # 服务端 422/409 拒写, 服务端已记 history
        memory_client.write_history(
            note_id=note_id, event='ADD',
            new_content=decision.new_content, old_content=None,
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
        )

    elif decision.action == 'UPDATE':
        old = memory_client.get_note(decision.target_id)        # 读旧值供审计
        ok = memory_client.update_note(decision.target_id, decision.new_content)
        if ok:
            memory_client.write_history(
                note_id=decision.target_id, event='UPDATE',
                old_content=old["content"], new_content=decision.new_content,
                reason=decision.reason,
                source_session_id=session_id, source_event=source_event,
            )

    elif decision.action == 'DELETE':
        old = memory_client.get_note(decision.target_id)
        memory_client.archive_note(decision.target_id)
        memory_client.write_history(
            note_id=decision.target_id, event='DELETE',
            old_content=old["content"], new_content=None,
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
        )

    elif decision.action == 'NOOP':
        memory_client.write_history(
            note_id=decision.target_id, event='NOOP',
            old_content=None, new_content=None,
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
        )

    elif decision.action == 'REJECT':
        memory_client.write_history(
            note_id=None, event='REJECT',
            old_content=None, new_content=candidate.content,    # 记被拒的候选
            reason=decision.reason,
            source_session_id=session_id, source_event=source_event,
        )
```

服务端补充端点（PR-1 内做掉）：
- `GET /notes/{note_id}` — 读单条 note（UPDATE/DELETE 时取旧值）
- `PATCH /notes/{note_id}` — 改 content + updated_at（UPDATE）
- `DELETE /notes/{note_id}` — 软删，置 archived=1（DELETE action）

这三个 by-id endpoint 在 §5.7-5.9 已展开。

### 6.7 硬上限

```python
MAX_CANDIDATES_AFTER_EXTRACT = 5         # ExtractResult Pydantic max_length
MAX_LLM_CALLS_PER_REVIEW = 1 + 5 = 6     # call A + 最多 5 次 call B
```

### 6.8 可观测性

每次触发输出一行结构化日志：

```python
logger.info(
    "memory_review corr=%s session=%s event=%s extract=%d hash_reject=%d "
    "inj_reject=%d add=%d update=%d delete=%d noop=%d reject=%d duration_ms=%d",
    correlation_id, session_id, source_event, len(candidates), hash_rej, inj_rej,
    add_n, upd_n, del_n, noop_n, rej_n, duration_ms,
)
```

事后调试三件套：

```sql
-- 1. 看某次 reviewer 调用的所有决策
SELECT * FROM notes_history WHERE reason LIKE '[corr=' || ? || ']%' ORDER BY ts;

-- 2. 看某次 reviewer 调用的所有错误
SELECT * FROM op_errors WHERE correlation_id = ? ORDER BY ts;

-- 3. prompt 迭代: 过去 7 天 REJECT 集中在哪些原因
SELECT reason, COUNT(*) FROM notes_history
WHERE event='REJECT' AND ts > strftime('%s','now','-7 days')
GROUP BY reason ORDER BY 2 DESC LIMIT 20;
```

---

## 7. Progress Aggregator（`src/progress_aggregator.py`）

```python
def refresh_progress(user_id: str, session_id: str, state: dict) -> None:
    """旁路异步任务，纯结构化，无 LLM。失败 logger.exception 吞掉 + 写 op_errors。"""
    correlation_id = uuid.uuid4().hex
    try:
        topic = state.get("topic")
        if not topic:
            return

        kp_samples = _extract_kp_samples(state)
        if not kp_samples:
            return

        mastery_overall = sum(s.score for s in kp_samples) / len(kp_samples)
        memory_client.upsert_progress(
            user_id=user_id,
            topic=topic,
            mastery_overall=mastery_overall,
            last_session_id=session_id,
            kp_samples=kp_samples,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.exception("refresh_progress failed")
        memory_client.log_error(
            source='main_app', user_id=user_id, session_id=session_id,
            correlation_id=correlation_id, op='refresh_progress',
            status='unexpected', error_class=type(exc).__name__, error_msg=str(exc)[:500],
            payload_excerpt=f"topic={state.get('topic')}",
        )
```

**`_extract_kp_samples`**（纯函数，可单测）：

```python
def _extract_kp_samples(state) -> list[MasterySample]:
    samples = []
    # 来源 1: 诊断结果
    if dr := state.get("diagnosis_result"):
        for kp in dr.breakdown:
            samples.append(MasterySample(kp=kp.name, score=_status_to_score(kp.status), source='quiz'))
    # 来源 2: review 结果（如果存在）
    if rr := state.get("review_result"):
        for kp_res in rr.kp_results:
            samples.append(MasterySample(kp=kp_res.kp, score=kp_res.accuracy, source='review'))
    # 来源 3: lesson 完成事件 (无具体分数, 用启发式: 已读 lesson 的 KP 全部 +0.1, 不超过 1.0)
    # 实际实现里这条来源较弱, 可在 PR-2 跳过, 后续 spec 再加
    return samples
```

`_status_to_score` 把 `DiagnosisBreakdown.status`（`mastered/partial/weak`）映射到 [0, 1]：mastered=1.0, partial=0.6, weak=0.2。

---

## 8. 客户端（`src/memory_client.py`）

新增 10 个函数，与现有 `retrieve_memory` / `ingest_session` 同风格（**失败静默 + 自动写 op_errors**，永不抛）：

```python
def fetch_snapshot(user_id: str, *, correlation_id: str | None = None) -> str:
    """session 启动时调一次，返回 Markdown snapshot 或空串。"""

def fetch_active_notes(user_id: str, category: str,
                       *, correlation_id: str | None = None) -> list[dict]:
    """返回 [{id, content, content_hash, created_at, updated_at}, ...] 或 []。
    content_hash 字段必须存在, reviewer 用它做 hash-dedup。"""

def fetch_neighbors(user_id: str, category: str, query_text: str, top_k: int = 5,
                    *, correlation_id: str | None = None) -> list[dict]:
    """reviewer call B 之前调，返回 [{id, content, sim_score, age_days}, ...] 或 []。"""

def get_note(note_id: int, *, correlation_id: str | None = None) -> dict | None:
    """reviewer UPDATE/DELETE 取旧值用。404 / 失败返回 None。"""

def write_note(user_id: str, category: str, content: str,
               source_session_id: str, source_event: str,
               *, correlation_id: str | None = None) -> int | None:
    """ADD action 用; 成功返回 note_id；失败/422/409 返回 None。"""

def update_note(note_id: int, content: str,
                *, correlation_id: str | None = None) -> bool:
    """UPDATE action 用; 成功 True; 失败/422/409/404 False。"""

def archive_note(note_id: int, *, correlation_id: str | None = None) -> bool:
    """DELETE action 用; 软删, archived=1。成功 True; 失败 False。"""

def write_history(note_id: int | None, event: str, new_content: str | None,
                  old_content: str | None, reason: str,
                  source_session_id: str, source_event: str,
                  *, correlation_id: str | None = None) -> None:
    """reviewer 各 action 都调；失败静默。reason 自动加 [corr=<id>] 前缀。"""

def upsert_progress(user_id: str, topic: str, mastery_overall: float,
                    last_session_id: str, kp_samples: list[dict],
                    *, correlation_id: str | None = None) -> None:
    """progress aggregator 调；失败静默。"""

def log_error(source: str, user_id: str | None, session_id: str | None,
              correlation_id: str | None, op: str, status: str,
              error_class: str, error_msg: str, payload_excerpt: str = "") -> None:
    """fire-and-forget; 自身被 try/except 包裹, 不允许 cascade。
    所有上述 9 个函数在静默回退路径自动调它。"""
```

**所有非 `log_error` 函数的实现规范**：

```python
def fetch_snapshot(user_id, *, correlation_id=None):
    try:
        resp = _client.get(f"/memory/snapshot/{user_id}", timeout=10)
        if resp.status_code != 200:
            log_error('main_app', user_id, None, correlation_id,
                      op='fetch_snapshot', status=f'http_{resp.status_code // 100}xx',
                      error_class='HTTPError', error_msg=resp.text[:500])
            return ""
        return resp.json().get('snapshot_md', '')
    except httpx.HTTPError as exc:
        log_error('main_app', user_id, None, correlation_id,
                  op='fetch_snapshot', status='timeout' if isinstance(exc, httpx.TimeoutException) else 'http_5xx',
                  error_class=type(exc).__name__, error_msg=str(exc)[:500])
        return ""
    except Exception as exc:
        log_error('main_app', user_id, None, correlation_id,
                  op='fetch_snapshot', status='unexpected',
                  error_class=type(exc).__name__, error_msg=str(exc)[:500])
        return ""
```

**`log_error` 的实现要点**：

```python
def log_error(...):
    try:
        _client.post("/errors/log", json={...}, timeout=2)   # 短超时
    except Exception:
        # cascade 防护: log_error 自身失败也不抛
        try:
            logger.error("log_error itself failed: op=%s err_class=%s", op, error_class)
        except Exception:
            pass    # 真到这一步, 让它静默
```

线程安全 + 短连接超时（connect=2s, total=10s），与现有 `retrieve_memory` 一致。

---

## 9. 读路径详细

### 9.1 Snapshot 渲染算法（`student_memory/app/memory/snapshot.py`）

```python
def render_snapshot(user_id: str, budget: int = 3500) -> SnapshotResponse:
    progress = list_progress_snapshots(user_id)              # 按 last_seen_at DESC
    weak_kps = list_weak_mastery_curve(user_id, threshold=0.7)   # 当前 mastery < 0.7 的 KP
    notes_by_cat = {
        cat: list_active_notes(user_id, cat, limit=15)
        for cat in ('misconception', 'preference', 'style', 'goal')
    }

    sections_in_priority_order = [
        ('progress',      _render_progress(progress, weak_kps)),
        ('misconception', _render_misconceptions(notes_by_cat['misconception'])),
        ('style_pref',    _render_style_pref(notes_by_cat['style'], notes_by_cat['preference'])),
        ('goal',          _render_goals(notes_by_cat['goal'])),
    ]

    out, used, included = "", 0, {}
    for name, text in sections_in_priority_order:
        if not text:
            continue
        if used + len(text) <= budget:
            out += text
            used += len(text)
            included[name] = len(text)
        # 超 budget 整段丢，不拆 section

    truncated = sum(len(s[1]) for s in sections_in_priority_order) > used
    return SnapshotResponse(
        snapshot_md=out, char_count=used, truncated=truncated, sections=included,
    )
```

### 9.2 Markdown 模板（`snapshot.j2`）

```jinja
{% if progress %}
## 该学生的学习进程
{% for p in progress %}
- {{ p.status_label }}：{{ p.topic }}（{{ p.elapsed_label }}，整体掌握度 {{ p.mastery_pct }}%）
{% endfor %}
{% if weak_kps %}
- 当前薄弱 KP：{% for k in weak_kps %}{{ k.knowledge_point }}（{{ k.score_pct }}%{% if k.trend %}，{{ k.trend }}{% endif %}）{% if not loop.last %}、{% endif %}{% endfor %}
{% endif %}
{% endif %}

{% if misconceptions %}
## 该学生的历史易混点
{% for m in misconceptions %}- {{ m.content }}
{% endfor %}
{% endif %}

{% if style_pref %}
## 该学生的学习风格与偏好
{% for s in style_pref %}- ({{ s.category }}) {{ s.content }}
{% endfor %}
{% endif %}

{% if goals %}
## 该学生的长期目标
{% for g in goals %}- {{ g.content }}
{% endfor %}
{% endif %}
```

`status_label / elapsed_label / trend` 派生规则：

```python
STATUS_LABELS = {'in_progress': '已开始', 'mastered': '已掌握', 'abandoned': '长期未碰'}

def _elapsed(then: int, now: int) -> str:
    age_h = (now - then) / 3600
    if age_h < 24:    return '今天'
    age_d = age_h / 24
    if age_d < 7:     return f'{int(age_d)}天前'
    if age_d < 30:    return f'{int(age_d / 7)}周前'
    return f'{int(age_d / 30)}月前'

def _trend(samples: list[MasterySample]) -> str | None:
    """取最近 3 个采样点。单调上升 → '上升中'；单调下降 → '退步中'；其他 → None"""
    pts = samples[-3:]
    if len(pts) < 3: return None
    if pts[0].score < pts[1].score < pts[2].score: return '上升中'
    if pts[0].score > pts[1].score > pts[2].score: return '退步中'
    return None
```

### 9.3 LearningState 改动

```python
# src/agent/state.py
class LearningState(TypedDict):
    # ... 已有字段 ...
    memory_snapshot: str | None              # session 启动时填一次, 之后冻结
    memory_snapshot_loaded_at: int | None
```

### 9.4 Session 启动时填充

```python
# src/api/chat.py
@router.post("/api/chat")
async def chat(req: ChatRequest):
    sid = req.session_id or _new_session_id()
    state = _load_or_init(sid)

    if state.get("memory_snapshot") is None and ENABLE_MEMORY_REVIEW:
        snap = await asyncio.to_thread(memory_client.fetch_snapshot, DEFAULT_USER_ID)
        state["memory_snapshot"] = snap or ""        # 失败也写空串占位, 不再重试
        state["memory_snapshot_loaded_at"] = int(time.time())

    # 后续 graph.invoke 不变
```

**关键**：字段一旦填了（即使是空串），session 内永不再变。

### 9.5 节点侧消费

```python
# src/agent/nodes/_helpers.py
def memory_snapshot_block(state: LearningState) -> str:
    snap = state.get("memory_snapshot") or ""
    if not snap.strip():
        return ""
    return f"\n\n{snap}\n"
```

四个节点的 prompt 模板（`chat.j2 / knowledge_graph.j2 / quiz_generate.j2 / lesson_plan.j2`）末尾加：

```jinja
{{ memory_snapshot_block }}
```

### 9.6 chat_node 的 retrieve_memory 处理

```python
# src/agent/nodes/chat.py (重构)
def chat_node(state):
    base = _build_base_prompt()
    skill = _select_skill(state)
    context = _build_context_summary(state)
    snapshot = memory_snapshot_block(state)         # frozen, cache 友好

    system_prompt = base + skill + context + snapshot

    user_msg = state["messages"][-1].content
    if len(user_msg.strip()) >= 10 and ENABLE_MEMORY_REVIEW:    # 门槛
        extra = retrieve_memory(DEFAULT_USER_ID, user_msg, top_k=3)
        if extra:
            system_prompt += f"\n\n## 最相关的过往片段\n{extra}"

    # 注意: retrieve_memory 这一段每 turn 不同, 破坏 prefix cache;
    # 故放在 system prompt 末尾, prefix (base + skill + context + snapshot) 仍可被 cache。
```

### 9.7 review 路径例外

```python
# src/agent/nodes/review.py: generate_review_questions
def generate_review_questions(state):
    misconceptions = []
    if ENABLE_MEMORY_REVIEW:
        notes = memory_client.fetch_active_notes(DEFAULT_USER_ID, category='misconception')
        misconceptions = [n["content"] for n in notes]
    # 注入 review_generate_system.j2 (与原 misconception spec 一致)
```

**为什么例外**：紧密反馈环—reviewer 写完下次 review 立刻见效。review 又不是热路径，多一次 HTTP 不痛。

---

## 10. 文件改动清单

| # | 文件 | 改动 | 行数 |
|---|---|---|---|
| **PR-1：服务端基础设施** | | | |
| 1 | `student_memory/app/db/migrations.py` | 5 张表 + 索引（含 op_errors） | +75 |
| 2 | `student_memory/app/db/sqlite_repo.py` | notes / notes_history / progress / mastery / op_errors CRUD + LRU + UPSERT + 30 天 prune | +220 |
| 3 | `student_memory/app/api/schemas.py` | `NoteCreate` / `NoteOut` / `NeighborOut` / `SnapshotResponse` / `ProgressUpsert` / `ErrorLog` | +70 |
| 4 | `student_memory/app/api/routes.py` | 10 个 endpoint + injection scan + `POST /errors/log` cascade 防护 | +210 |
| 5 | `student_memory/app/memory/snapshot.py` | render_snapshot + 优先级装填 + Jinja 模板 | +120 |
| 6 | `student_memory/app/memory/embeddings_for_notes.py` | notes 文本 → bge-m3 → `notes_hnsw.bin` 独立 index | +80 |
| 7 | `student_memory/tests/test_notes.py` | CRUD / 422 / LRU / injection / 4 category | +180 |
| 8 | `student_memory/tests/test_progress.py` | UPSERT / status 转移 / mastery_curve 上限 | +90 |
| 9 | `student_memory/tests/test_snapshot.py` | 渲染 / 优先级截断 / 空集 / 字符预算 | +110 |
| 9b | `student_memory/tests/test_errors.py` | `POST /errors/log` 200-on-failure / cascade 防护 / prune / correlation 查询 | +90 |
| **PR-2：客户端 + Reviewer** | | | |
| 10 | `src/memory_client.py` | 10 个新方法（含 `log_error`） + 每个非 log_error 函数的静默回退路径自动调 log_error + cascade 防护 | +220 |
| 11 | `src/llm/schemas.py` | `FactCandidate` / `ExtractResult` / `ConsolidationDecision` / `MasterySample` | +30 |
| 12 | `src/prompts/memory_extract_system.j2` + `_user.j2` | 提取 prompt | +80 |
| 13 | `src/prompts/memory_consolidate_system.j2` + `_user.j2` | consolidation prompt | +60 |
| 14 | `src/reviewer.py` | `run_memory_review` + correlation_id 贯穿 + 5 个内部函数 + 顶层 try/except 写 op_errors | +320 |
| 15 | `src/progress_aggregator.py` | `refresh_progress` + correlation_id + `_extract_kp_samples` + `_next_status` + 顶层 try/except 写 op_errors | +120 |
| 16 | `tests/test_memory_client_v2.py` | 10 个新方法的 HTTP mock + 静默回退路径自动写 op_errors 的验证 | +180 |
| 17 | `tests/test_reviewer.py` | LLM mock + 5-action 各一例 + dedup + injection + 失败静默 + correlation_id 串联验证 | +240 |
| 18 | `tests/test_progress_aggregator.py` | UPSERT / status 状态机 / 幂等 + 失败时 op_errors 写入 | +100 |
| **PR-3：写路径接入** | | | |
| 19 | `src/agent/state.py` | 加 `memory_snapshot` / `memory_snapshot_loaded_at` 字段 | +5 |
| 20 | `src/api/chat.py` | session 启动 fetch_snapshot；3 个触发点并发跑 reviewer + progress | +50 |
| 21 | `tests/test_chat_integration.py` | 灰度 on/off 完整流程 | +120 |
| **PR-4：读路径节点注入** | | | |
| 22 | `src/agent/nodes/_helpers.py` | `memory_snapshot_block` | +15 |
| 23 | `src/agent/nodes/chat.py` | snapshot block + retrieve_memory ≥10 字门槛 | +20 / -15 |
| 24 | `src/agent/nodes/knowledge_graph.py` | 拼 snapshot block | +5 |
| 25 | `src/agent/nodes/quiz.py` | 拼 snapshot block | +5 |
| 26 | `src/agent/nodes/lesson.py` | 拼 snapshot block | +5 |
| 27 | `src/agent/nodes/review.py` | `fetch_active_notes(category='misconception')` 实时 | +10 |
| 28 | `src/prompts/{chat,knowledge_graph,quiz_generate,lesson_plan}.j2` | 末尾加 `{{ memory_snapshot_block }}` | +4 × 4 |
| 29 | `tests/test_baseline_prompts.py` | env=off 下 4 个 .j2 字节级快照 | +80 |
| 30 | `tests/test_snapshot_injection.py` | env=on 验证 4 节点都拼上 snapshot | +60 |

合计 ~3000 行（含测试）；非测试改动约 1850 行；测试约 1150 行。

---

## 11. PR 拆分

### PR-1：服务端基础设施（独立可上线，零调用方）

文件 1–9。验收：`pytest student_memory/tests/`；手工 curl 6 条新 endpoint。零调用方，main 不变。

### PR-2：客户端 + Reviewer（带 env 开关，未接入主流程）

文件 10–18。`ENABLE_MEMORY_REVIEW` env var 引入但**仅声明**，无调用方。验收：单测全过；fixture state 手工跑 `run_memory_review` + `refresh_progress`，看 SQLite 多出条目。

### PR-3：写路径接入

文件 19–21。仍受 env 控制；off 时整条链路无副作用。验收：env=off 时 `pytest tests/` 全绿；env=on 时手工跑完整学习流程，看数据库多 notes / progress / mastery 行。

### PR-4：读路径节点注入

文件 22–30。验收：
- env=off：`test_baseline_prompts` 验证 4 个 .j2 渲染结果与 PR-3 之前**字节级一致**
- env=on：完整跑两个 session，验证第二 session 的 chat system prompt 含 progress / misconception 段；验证 review 路径用的是实时 fetch 不是 snapshot

### 依赖关系

```
PR-1 (服务端)  ──independent──►  可上线
PR-2 (客户端 + reviewer)  ──需 PR-1 endpoint──►  上线但未接入
PR-3 (写路径)  ──需 PR-2 reviewer──►  上线 (受 env 控制)
PR-4 (读路径)  ──需 PR-3 state 字段──►  上线 (受 env 控制)
```

---

## 12. 验收不变量

### 12.1 失败静默 / 优雅降级

- [ ] reviewer 抛任何异常被 `logger.exception` 吞掉，不影响 submit-quiz / submit-review / generate-lesson 的 HTTP 响应
- [ ] `fetch_snapshot` HTTP 失败返回 `""`，写入 state；节点降级为不注入
- [ ] `write_note` 422 / 网络失败返回 `None`；reviewer 继续处理下一条 candidate
- [ ] `fetch_active_notes` 失败返回 `[]`；`generate_review_questions` 不抛、不阻塞
- [ ] `refresh_progress` 抛任何异常被吞掉，不影响 reviewer 也不影响 HTTP

### 12.2 灰度开关字节级不变

- [ ] `ENABLE_MEMORY_REVIEW=false` 时：reviewer / progress aggregator 不被触发；任何 `fetch_*` / `write_*` 不被调用
- [ ] env=off 下 4 个 chat 系列节点的 system prompt 渲染结果与改动前**字节级一致**——基线快照测试覆盖以下文件：
  - `src/prompts/chat.j2`
  - `src/prompts/knowledge_graph.j2`
  - `src/prompts/quiz_generate.j2`
  - `src/prompts/lesson_plan.j2`
- [ ] env=off 下 SQLite 4 张新表存在但永不写入

### 12.3 Frozen snapshot 不变量

- [ ] 同一 session 内 chat_node 调用 N 次，`state["memory_snapshot"]` **永不变**
- [ ] 同一 session 内 reviewer 写新 note 后，当前 session 的下一次 chat 仍用旧 snapshot
- [ ] 新建 session 才重新 fetch；既有 session 从 SQLite 反序列化后保留原 snapshot

### 12.4 Reviewer 行为

- [ ] 单次触发硬上限：extract ≤ 5 candidates；call B ≤ 5 次；写库 ≤ 5 行
- [ ] hash-dedup 在 LLM call B 之前跑；命中即记 `notes_history.event=REJECT`，reason 含 `'hash duplicate'`
- [ ] injection-scan 在 LLM call B 之前跑；命中即记 `notes_history.event=REJECT`
- [ ] 5 个 action（ADD/UPDATE/DELETE/NOOP/REJECT）各被 `notes_history` 记录，含 reason 字段
- [ ] reviewer 只在同 category 内召回邻居（hnswlib filter 校验）
- [ ] consolidation 输出非法 schema（如 ADD 但带 target_id）时降级为 REJECT，不 raise

### 12.5 retrieve_memory 门槛

- [ ] `chat_node` 在 user 消息（strip 后）字符数 < 10 时**不调** `retrieve_memory`
- [ ] env=off 时 `chat_node` 完全不调 `retrieve_memory`

### 12.6 数据约束

- [ ] `notes.content` 长度 1-200 chars；服务端 + Pydantic 双重保护
- [ ] `notes.category` 只能是 4 个白名单值（SQL CHECK + Pydantic Literal）
- [ ] 单 (user, category) active 上限 30；超限 LRU archived=1 不物理删
- [ ] 单 (user, topic) `progress_snapshots` 唯一一行（UNIQUE 约束）
- [ ] 单 (user, topic, KP) `mastery_curve` 最多 10 个采样点；超限删最老
- [ ] `notes_history` 永不删除（含 NOOP/REJECT），便于事后 prompt 迭代

### 12.7 性能

- [ ] session 启动后 chat_node 单 turn 内**至多 1 次 HTTP**（仅当 user 消息 ≥ 10 字时调 `retrieve_memory`）
- [ ] reviewer 完成时间 ≤ 30s（单触发；3 候选 × 1.5s LLM call B + extract 3s + IO < 5s）
- [ ] snapshot 渲染 ≤ 200ms（4 个 SQL + Jinja，无 LLM）

### 12.8 模型与配置

- [ ] reviewer 用 `REVIEWER_MODEL` env，与主流程 `LLM_MODEL` 解耦
- [ ] `ENABLE_MEMORY_REVIEW` env 默认 false；同时门控写和读
- [ ] system prompt 是稳定段（`*_system.j2`），变化数据全部在 user 模板（cache 友好）

### 12.9 可观测性

- [ ] reviewer 每次触发输出一行结构化日志，含 8 个计数：extract / hash_reject / inj_reject / add / update / delete / noop / reject
- [ ] snapshot endpoint 返回 `truncated` 字段；client 端将 truncated=true 写日志
- [ ] **所有静默回退路径都对应一行 `op_errors`**，含 (source, user_id, session_id, correlation_id, op, status, error_class, error_msg)；具体覆盖：
  - client 端 `fetch_snapshot / fetch_active_notes / fetch_neighbors / get_note / write_note / update_note / archive_note / write_history / upsert_progress` 9 个函数的失败路径
  - reviewer 顶层 try/except、progress aggregator 顶层 try/except
  - 服务端 5xx / 内部异常分支
- [ ] reviewer 一次调用的 `correlation_id` 贯穿其全部 client 调用，并写入相关 `notes_history.reason` 前缀（格式 `[corr=<id>] <reason>`）
- [ ] `POST /errors/log` 永不返回 5xx；写库失败也返回 200，仅 server stderr `logger.warning`
- [ ] `memory_client.log_error` 自身被 try/except 包裹，cascade 异常不向上传播
- [ ] `op_errors` 表行 ts > 30 天的启动时被清理一次（与 `student_memory` 现有清理机制同地点）
- [ ] `op_errors.error_msg` 和 `op_errors.payload_excerpt` 各自截断到 ≤ 500 chars；payload_excerpt 必须脱敏（不直接塞 user message 全文）

### 12.10 与 supersede 关系

- [ ] `docs/superpowers/specs/2026-05-06-l2-misconception-design.md` 顶部添加 "已被 2026-05-08 spec 超集替代" 注记
- [ ] 本 spec 顶部声明 supersedes 旧 spec

---

## 13. 不做的事（避免范围蔓延）

- ❌ **不做 chat 累计 N 轮触发**：lifecycle 事件足够；计数器在长 chat 场景是可选项，等数据证明有用再加
- ❌ **不做 generate-slides 触发**：与 lesson 信号重复
- ❌ **不做 LLM 写进度数据**：纯结构化更可信；预留扩展点支持未来润色
- ❌ **不做跨 category 邻居召回**：reviewer 对齐困难，无收益
- ❌ **不做用户切换 / 多账号**：仍用 `DEFAULT_USER_ID`
- ❌ **不做 skill 自我改进**：本项目的 `skills/{feynman,socratic,scaffolding}` 是人工编写的教学法
- ❌ **不做前端可视化**：notes 是隐式增强，用户不直接看到列表（progress 数据未来可做前端，超出本 spec）
- ❌ **不引入 supersede 链**：简化为 archived 软删除位 + LRU + Mem0-style UPDATE
- ❌ **不接入外部 telemetry**（OpenTelemetry / Sentry / Datadog 等）：当前规模不需要；`op_errors` 表 + 结构化日志已足够覆盖 bug triage

---

## 14. 与原 spec 关系

本 spec **完全 supersede** `2026-05-06-l2-misconception-design.md`。简化版的 misconception-only 路线**未实施**，没有代码包袱。

旧 spec 的设计细节（misconception category 的 reviewer prompt 思路、injection-scan 规则、archived 软删除位）**全部纳入本 spec 作为 misconception category 的实现指引**。

继承的关键设计：

| 原 spec 元素 | 本 spec 中的位置 |
|---|---|
| `notes` 表 + injection-scan | §4.1 + §6.4 |
| `archived` 软删除位 + LRU 30 上限 | §4.1 |
| reviewer 用 `REVIEWER_MODEL` env | §6 + §12.8 |
| `ENABLE_MEMORY_REVIEW` env 门控 | §12.2 |
| review 路径实时 fetch（不冻结） | §9.7 |
| Pydantic + 服务端长度双重保护 | §6.5 |

新增的关键设计：

| 元素 | 位置 |
|---|---|
| 4-category 扩展（misconception/preference/style/goal） | §4.1 |
| 5-action consolidation（ADD/UPDATE/DELETE/NOOP/REJECT） | §6.5 |
| Hash-dedup 预过滤 | §6.3 |
| Speaker tag in prompt | §6.2 |
| `notes_history` 审计表 | §4.2 |
| `progress_snapshots` + `mastery_curve` | §4.3-4.4 |
| `op_errors` 错误追踪表 + correlation_id | §4.5 + §5.10 + §6.1 + §8 + §12.9 |
| Frozen snapshot 不变量 | §9.4-9.5 + §12.3 |
| `LearningState.memory_snapshot` 字段 | §9.3 |
| chat_node retrieve_memory ≥10 字门槛 | §9.6 + §12.5 |

---

## 15. 与 hermes-agent 和 Mem0 的对照

| 概念 | hermes-agent | Mem0 | 本 spec |
|---|---|---|---|
| Memory store | MemoryStore + MEMORY.md (~2200 chars) | vector store + entity store + history | `notes` 表 + `notes_history` |
| 长期主观判断 | MEMORY.md / USER.md 文件 | 自由文本 facts | 4-category notes |
| 写决策 | reviewer 整体重写 MEMORY.md | ADD / UPDATE / DELETE / NOOP | ADD / UPDATE / DELETE / NOOP / **REJECT** |
| 邻居召回 | 无（重写式） | top-K 同向量库 | top-5 同 category |
| Hash 去重 | 无 | 无（Mem0 公开审计 #4573 的失败点） | **有** |
| Speaker tag | 无 | 无 | **有** |
| Reviewer 触发 | 每 N 轮 + session 结束 | 每个 user message | lifecycle 事件（quiz/review/lesson） |
| Reviewer 模型 | 同主模型 | 同主模型 | **便宜模型 `REVIEWER_MODEL`** |
| Snapshot 缓存 | session 启动一次冻结 | 每次 fetch | **session 启动一次冻结**（沿用 hermes） |
| 进度数据 | 无 | 无 | **结构化 SQL，无 LLM** |
| 字符预算 | ≤ 3575 chars 硬上限 | 无 | ≤ 3500 chars 硬上限 |
| 安全 | 关键词扫描 + 审批 prompt | 无 | injection-scan + 422 拒写 |
| 错误追踪 | 文件日志 | 文件日志 | **`op_errors` SQL 表 + correlation_id + 双写日志** |

整体复杂度比 hermes 低（无 fork sub-agent、无 MemoryProvider ABC），比 Mem0 高（多 5th action REJECT、多 hash-dedup、多 speaker tag、多结构化进度），是为本项目教学场景定制的中间形态。

---
