# Hybrid Agent Memory — PR-4 读路径节点注入

**状态**：草案 / 待评审
**日期**：2026-05-10
**范围**：`src/agent/nodes/_helpers.py`（新建）、`src/agent/nodes/{chat,knowledge_graph,quiz,lesson,review}.py`、`src/prompts/{knowledge_graph,quiz_generate,lesson_plan,lesson_point,lesson_review,review_generate_system}.j2`、`tests/test_baseline_prompts.py`（新建）、`tests/test_snapshot_injection.py`（新建）
**前置**：
- `docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`（总设计；本 spec 落地其 §9 + §11 PR-4）
- `docs/superpowers/specs/2026-05-10-hybrid-memory-pr3-write-path.md`（PR-3 已上线，提供 `state["memory_snapshot"]`）
- `docs/superpowers/plans/2026-05-10-hybrid-memory-pr3-write-path.md`（PR-3 plan）

---

## 1. 范围

PR-4 = **读路径节点注入**。把 PR-3 已经填好的 `state["memory_snapshot"]`（session 启动时一次拉取，之后冻结）真正注入到 4 个 LangGraph 节点的 prompt 里：

- `chat` / `knowledge_graph` / `quiz` / `lesson` 走 frozen snapshot（cache 友好）
- `review` 走**实时** `fetch_active_notes('misconception')`（紧密反馈环 — reviewer 写完下次 review 立刻见效）

PR-4 还顺手做两件相关清理：
- chat node 的 `retrieve_memory` 加 `≥10` 字符门槛（短消息没必要触发向量检索）
- 新建 `_helpers.py` 集中 snapshot block 渲染逻辑

env=off 时（`ENABLE_MEMORY_REVIEW` 未启用）：
- 5 个 j2 模板（`knowledge_graph` / `quiz_generate` / `lesson_plan` / `lesson_point` / `lesson_review`）渲染输出**字节级与 PR-4 改动前一致**（通过 jinja `{{- ... }}` whitespace control 实现，详见 §5.5）
- `review_generate_system.j2` 渲染不含 misconceptions 段（`{% if misconceptions %}` 早返）
- review 路径不调 `fetch_active_notes`
- chat 节点唯一变化是 `<10` 字消息不再触发 `retrieve_memory`（cleanup，刻意偏差）

---

## 2. 架构图

```
PR-3 (已落地):                              PR-4 (本次):
session 启动 fetch_snapshot ───────►    state["memory_snapshot"] (frozen)
                                                  │
                                                  ▼
                              ┌──────────────────────────────────┐
                              │ memory_snapshot_block(state)     │
                              │   src/agent/nodes/_helpers.py    │
                              └────────┬─────────────────────────┘
                                       │
        ┌─────────────────┬────────────┼────────────────┬─────────────────┐
        ▼                 ▼            ▼                ▼                 ▼
  chat_node         knowledge_graph   quiz_generate   lesson_plan/    review (例外)
  + retrieve_       node + j2         node + j2       _point/_review  实时 fetch
    memory ≥10     {{ block }}       {{ block }}     + j2            active notes
    char threshold                                    {{ block }}     misconception
                                                                       软指导段
```

### 关键不变量

1. **Frozen snapshot**：4 个节点读 `state["memory_snapshot"]`，session 内整段 prompt 字节级稳定
2. **review 实时 fetch**：每次生成复习题都重新拉 misconception notes，reviewer 写新 note 后立刻见效
3. **env=off 字节级稳定**：3 个完整 j2 模板（knowledge_graph / quiz_generate / 3 个 lesson）+ chat 节点的 system prompt 在 env=off 下与改动前字节级一致（`<10` 字 retrieve_memory 短路是唯一例外）

---

## 3. `memory_snapshot_block` helper

### 3.1 `src/agent/nodes/_helpers.py`（新建）

```python
"""Shared helpers for agent nodes (PR-4)."""
from __future__ import annotations

from src.agent.state import LearningState


def memory_snapshot_block(state: LearningState) -> str:
    """Return the rendered memory snapshot block, or empty string.

    Empty when the snapshot field is absent / None / empty / whitespace —
    which covers env=off (PR-3's _ensure_memory_snapshot is a no-op),
    fetch failure (field is ""), and old sessions (field absent).
    Non-empty: returns "\n\n<snapshot>" so callers can append directly to
    a system prompt or drop into a Jinja template at the end.
    """
    snap = (state.get("memory_snapshot") or "").strip()
    if not snap:
        return ""
    return f"\n\n{snap}"
```

### 3.2 设计要点

- **Env 门控隐式**：helper 不查 `_MEMORY_REVIEW_ENABLED`。PR-3 已经决定了"env=off → 字段保持 None"，helper 只看 state 字段值。这让测试只需要构造 state，不需要 monkeypatch 模块常量
- **三种 falsy 都返回空串**：`None`（env=off / 老 session）、`""`（env=on 但 fetch 失败）、`"   \n"`（保护性 strip）
- **Leading separator**：返回 `f"\n\n{snap}"`，调用端无需考虑分隔符
- **不加 section header**：snapshot 自身已经是渲染好的 Markdown（带 `## 该学生的学习进程` 等），helper 不再包一层

---

## 4. `chat.py` 节点改动

### 4.1 现状（line 70-113 + 116-158 stream 镜像）

```python
system_prompt = _BASE_SYSTEM_PROMPT
if skill_text: system_prompt += f"\n\n## 教学方法\n{skill_text}"
if context: system_prompt += f"\n\n{context}"

user_msg = messages[-1].content if messages else ""
memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)        # 无条件
if memory_context:
    system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答..."
```

### 4.2 改动后

```python
from src.agent.nodes._helpers import memory_snapshot_block

system_prompt = _BASE_SYSTEM_PROMPT
if skill_text:
    system_prompt += f"\n\n## 教学方法\n{skill_text}"
if context:
    system_prompt += f"\n\n{context}"

# Frozen snapshot (PR-4): cache-friendly, before retrieve_memory
system_prompt += memory_snapshot_block(state)

# Per-turn L1 retrieval (existing). PR-4 adds ≥10-char threshold to skip
# tiny utterances ("嗯"/"继续") that produce noisy embeddings. NOT env-gated:
# pre-PR-4 this was unconditional, so env=off keeps L1 retrieval enabled.
user_msg = messages[-1].content if messages else ""
if len(user_msg.strip()) >= 10:
    memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
    if memory_context:
        system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答，但不要直接复述。"
```

`chat_node_stream`（line 116-158）做**对称改动**。两处不抽公共逻辑（属于范围外重构）。

### 4.3 拼接顺序与 cache 友好性

```
1. _BASE_SYSTEM_PROMPT                  ┐
2. ## 教学方法 (skill, 大多数会话内稳定) │ prefix —— 整 session 稳定
3. ## 当前学习上下文 (state-derived)     │ prefix cache 命中区
4. <snapshot block> (frozen per-session) ┘
─────────────────────────────────────
5. ## 历史学习记忆 (per-turn, 破坏 cache)
```

snapshot 放在 prefix 末尾，retrieve_memory 段独占 cache 之外。与总 spec §9.6 一致。

### 4.4 异类注释

在 `chat.py` 顶端 `_BASE_SYSTEM_PROMPT` 之后加注释，把"为什么不一致"显式化：

```python
# NOTE (PR-4): chat is the only agent node that does NOT use a Jinja
# template. The streaming variant (chat_node_stream) and runtime decisions
# (≥10-char threshold for retrieve_memory) make Python construction
# more direct than j2 + filters. Other 3 nodes (knowledge_graph / quiz /
# lesson) use render_prompt(...) and inject {{ memory_snapshot_block }}
# at template end.
```

### 4.5 env=off 行为

- `state["memory_snapshot"]` 是 None（PR-3 早返）
- `memory_snapshot_block(state)` 返回 `""`
- system_prompt 末尾追加空串 → 没有任何字节变化
- ≥10 字门槛**对 env=off 也生效**（按已选策略，刻意偏差）—— 短消息现在不再调 retrieve_memory

---

## 5. 三个 j2 节点的注入

### 5.1 `knowledge_graph.py`（1 处 `render_prompt`，line 74）

```python
from src.agent.nodes._helpers import memory_snapshot_block

prompt = render_prompt(
    "knowledge_graph",
    topic=topic,
    max_nodes=MAX_NODES,
    skill_instructions=skill_instructions,
    focus_directive=focus_directive,
    review_weak_kps=review_weak_kps,
    review_strong_kps=review_strong_kps,
    existing_mastery=existing_mastery or [],
    memory_snapshot_block=memory_snapshot_block(state),     # ← 新加
)
```

### 5.2 `quiz.py`（2 处 `render_prompt`：line 122 + 193）

两处都对应 `quiz_generate.j2`。两处都加 `memory_snapshot_block=memory_snapshot_block(state)`。

### 5.3 `lesson.py`（5 处 `render_prompt`，对应 3 个模板）

| 调用点（行号） | 模板 | 是否注入 |
|---|---|---|
| line 21 | `lesson_plan` | ✅ |
| line 144 | `lesson_point` | ✅ |
| line 177 | `lesson_review` | ✅ |
| line 267, 284 | `lesson_point`（review_lesson 子调用） | ✅ |
| line 318, 347 | `lesson_point`（background tasks 路径） | ✅ |

全部都注入。Plan 阶段精确列出每行的 `state` 来源（有些是参数 `state`，有些是 `state_snapshot`）。

### 5.4 5 个 j2 模板末尾追加

`knowledge_graph.j2`、`quiz_generate.j2`、`lesson_plan.j2`、`lesson_point.j2`、`lesson_review.j2` —— 每个末尾各加一行（注意 `{{-` 的 leading dash 用来 strip 前置换行）：

```jinja
{{- memory_snapshot_block }}
```

不嵌套在任何 `{% if %}` 或 `{% for %}` 里，env 判定完全交给 helper。这保证：
- 一处改 helper 影响所有节点
- 模板里没有条件分支扰动 prefix cache

**Whitespace control 说明** —— `{{- ... }}` 的 leading dash 在 var 为空串时把前面的换行 strip 掉，从而做到 var=`""` 时输出 = 改动前；var 非空时 helper 自带 `\n\n` 前缀做分隔符。

### 5.5 字节级稳定性

设 pre-PR-4 模板末尾为 `existing\n`，加完一行 `{{- memory_snapshot_block }}` 后变成 `existing\n{{- memory_snapshot_block }}\n`。

env=off 时（`memory_snapshot_block(state) == ""`）：
- `{{-` strip 掉前面的 `\n`，var 渲染为 `""`，文件末尾 `\n` 保留
- 渲染输出 = `existing\n` —— **与改动前字节级一致** ✅

env=on 且 snapshot 非空（`memory_snapshot_block(state) == "\n\n## 该学生的学习进程\n..."`）：
- `{{-` strip 前置 `\n`，var 渲染为 `\n\n## 该学生...`，末尾 `\n` 保留
- 渲染输出 = `existing\n\n## 该学生...\n`

---

## 6. review 路径例外（实时 fetch + misconception 注入）

review 路径**不读 snapshot**，每次实时 fetch active misconception notes 注入 prompt。这保留紧密反馈环 — reviewer 写完下次 review 立刻见效。

### 6.1 `src/agent/nodes/review.py` `generate_review_questions` 改动

```python
import os
from src.memory_client import DEFAULT_USER_ID, fetch_active_notes

_MEMORY_REVIEW_ENABLED = os.environ.get("ENABLE_MEMORY_REVIEW", "false").lower() == "true"

def generate_review_questions(candidates: list[dict]) -> list:
    if not candidates:
        return []

    from src.llm.schemas import QuizQuestion
    from src.prompts.loader import render_prompt

    # ... candidate enrichment (unchanged) ...

    # PR-4: live-fetch misconception notes (review path is the exception —
    # snapshot is too stale; reviewer-written notes must take effect on the
    # very next review). env=off skips the fetch, prompt has no
    # misconceptions section.
    misconceptions: list[str] = []
    if _MEMORY_REVIEW_ENABLED:
        notes = fetch_active_notes(DEFAULT_USER_ID, "misconception")
        misconceptions = [n["content"] for n in notes if n.get("content")]

    user_prompt = render_prompt(
        "review_generate_system",
        candidates=enriched,
        misconceptions=misconceptions,
    )

    # ... LLM call (unchanged) ...
```

**关键点**：
- `fetch_active_notes` 失败时 PR-2 内部 try/except 返回 `[]`（保留 graceful 降级）
- env=off 时不调 fetch，`misconceptions=[]` 透传给模板
- 与 chat node 的 retrieve_memory 不同：review 这里**完整 env-gate**（pre-PR-4 review 根本没调过这个接口，所以加 gate 不引入回退）

### 6.2 `src/prompts/review_generate_system.j2` 改动

在「要求」上方插入软指导段：

```jinja
{% if misconceptions %}
该学生历史易混点（出干扰项时**优先**从中选取，但若与本题 KP 不相关则可不用）:
{% for m in misconceptions %}
- {{ m }}
{% endfor %}

{% endif %}
要求:
1. 每个知识点出 **1 道** 4 选 1 单选题, 总数 = {{ candidates | length }}
...
```

**软指导**用「优先 ... 但 ... 可不用」措辞，给 LLM 灵活性。env=off 时 `misconceptions=[]`，整段早返不渲染，prompt 字节级稳定。

### 6.3 失败模式

| 场景 | 行为 |
|---|---|
| env=off | `misconceptions=[]`, 模板 if-段早返, 跟改动前一致 |
| env=on, fetch 5xx/timeout | `fetch_active_notes` 返 `[]` (PR-2 graceful), 同上 |
| env=on, 学生新 / notes 表空 | 同上 |
| env=on, 有 notes 但 content 空字符串 | 过滤掉 (`if n.get("content")`), 不进 prompt |

---

## 7. 验收不变量

### 7.1 Helper

- [ ] `memory_snapshot_block({})` 返回 `""`
- [ ] `memory_snapshot_block({"memory_snapshot": ""})` 返回 `""`
- [ ] `memory_snapshot_block({"memory_snapshot": "  \n  "})` 返回 `""`
- [ ] `memory_snapshot_block({"memory_snapshot": "## X"})` 返回 `"\n\n## X"`

### 7.2 J2 模板字节级稳定（env=off）

- [ ] `knowledge_graph.j2` env=off 渲染 = pre-PR-4 字节级一致
- [ ] `quiz_generate.j2` env=off 渲染 = pre-PR-4 字节级一致
- [ ] `lesson_plan.j2` env=off 渲染 = pre-PR-4 字节级一致
- [ ] `lesson_point.j2` env=off 渲染 = pre-PR-4 字节级一致
- [ ] `lesson_review.j2` env=off 渲染 = pre-PR-4 字节级一致

### 7.3 Snapshot 注入（env=on, snapshot 非空）

- [ ] `knowledge_graph.j2` 渲染含 snapshot 内容
- [ ] `quiz_generate.j2` 渲染含 snapshot 内容
- [ ] `lesson_plan.j2` 渲染含 snapshot 内容
- [ ] `lesson_point.j2` 渲染含 snapshot 内容
- [ ] `lesson_review.j2` 渲染含 snapshot 内容
- [ ] chat_node 系统消息含 snapshot 内容
- [ ] chat_node_stream 系统消息含 snapshot 内容

### 7.4 retrieve_memory 门槛

- [ ] `chat_node` user 消息 strip 后 `<10` 字符 → `retrieve_memory` **不被调**（任何 env 状态下）
- [ ] `chat_node` user 消息 strip 后 `>=10` 字符 → `retrieve_memory` 被调一次
- [ ] `chat_node_stream` 同上

### 7.5 review 路径

- [ ] env=off `generate_review_questions` 不调 `fetch_active_notes`
- [ ] env=off `review_generate_system.j2` 渲染不含 misconceptions 段
- [ ] env=on, notes 非空：`fetch_active_notes('misconception')` 调一次，渲染含 misconception 内容
- [ ] env=on, fetch 失败 → `misconceptions=[]`，模板早返，不抛异常
- [ ] env=on, content 为空字符串的 notes 被过滤

### 7.6 与总 spec §12 的对应索引

| 总 spec §12 子节 | PR-4 是否覆盖 |
|---|---|
| §12.1 失败静默 | PR-2 内置；PR-4 不破坏（review 路径 fetch 失败仍 graceful）|
| §12.2 灰度开关字节级不变 | ✅ §7.2（带 chat 短消息例外）|
| §12.3 Frozen snapshot | PR-3 责任 |
| §12.4 Reviewer 行为 | PR-2 责任 |
| §12.5 retrieve_memory 门槛 | ✅ §7.4 |
| §12.6 数据约束 | PR-1 责任 |
| §12.7 性能 | snapshot 已 cache 友好（§4.3 拼接顺序）|
| §12.8 模型与配置 | PR-2 责任 |
| §12.9 可观测性 | PR-2 + PR-3 已覆盖；PR-4 不引入新静默路径 |

---

## 8. 文件改动清单

| # | 文件 | 改动 | 估算行数 |
|---|---|---|---|
| 1 | `src/agent/nodes/_helpers.py` | 新建 — `memory_snapshot_block(state) -> str` | +20 |
| 2 | `src/agent/nodes/chat.py` | 末尾追加 snapshot block；retrieve_memory 加 `≥10` 字门槛；stream + 非 stream 两处 | +12 / -6 |
| 3 | `src/agent/nodes/knowledge_graph.py` | `render_prompt(..., memory_snapshot_block=...)` | +2 |
| 4 | `src/agent/nodes/quiz.py` | 同上（2 处 render_prompt 都加） | +4 |
| 5 | `src/agent/nodes/lesson.py` | 同上（5 处 render_prompt 都加） | +6 |
| 6 | `src/agent/nodes/review.py` | env=on 时调 `fetch_active_notes('misconception')`，传给 prompt | +12 |
| 7 | `src/prompts/knowledge_graph.j2` | 末尾加 `{{ memory_snapshot_block }}` | +1 |
| 8 | `src/prompts/quiz_generate.j2` | 末尾加 | +1 |
| 9 | `src/prompts/lesson_plan.j2` | 末尾加 | +1 |
| 10 | `src/prompts/lesson_point.j2` | 末尾加 | +1 |
| 11 | `src/prompts/lesson_review.j2` | 末尾加 | +1 |
| 12 | `src/prompts/review_generate_system.j2` | 加 misconceptions 软指导段 | +6 |
| 13 | `tests/test_baseline_prompts.py` | 新建 — env=off 5 个 j2 字节级稳定 | +90 |
| 14 | `tests/test_snapshot_injection.py` | 新建 — env=on snapshot 注入 + retrieve_memory 门槛 + review misconception | +140 |

合计 ~300 行（含测试 ~230 行）；非测试改动约 70 行。

---

## 9. 测试矩阵

### 9.1 `tests/test_baseline_prompts.py`

每个 j2 模板：
- 用 env=off 状态（`memory_snapshot_block=""`）渲染
- 与锁定的字节字符串对比

**Baseline 字节字符串实施时 capture 方法**：
1. 从 `git show <pre-PR-4-SHA>:src/prompts/<name>.j2` 取改前模板
2. 用相同 kwargs 渲染
3. 把输出贴进测试文件的 `EXPECTED_*` 常量

### 9.2 `tests/test_snapshot_injection.py`

| 场景 | 测试函数 |
|---|---|
| Helper：4 种 falsy / 1 种正常 | `test_helper_returns_empty_*` × 3 + `test_helper_returns_block_with_separator` |
| 5 个 j2 注入 snapshot | `test_template_injects_snapshot` (parametrized × 5) |
| chat_node 注入 snapshot（env=on） | `test_chat_node_appends_snapshot_when_present` |
| chat_node env=off 不注入 | `test_chat_node_no_snapshot_when_empty` |
| chat_node `<10` 字不调 retrieve | `test_chat_node_skips_retrieve_for_short_message` |
| chat_node `>=10` 字调 retrieve | `test_chat_node_calls_retrieve_for_long_message` |
| chat_node_stream 同上 4 条 | 类似（参数化或独立） |
| review env=on misconception 注入 | `test_review_injects_misconceptions_env_on` |
| review env=off 不调 fetch | `test_review_no_fetch_when_env_off` |

### 9.3 Mock 边界

- `chat_node` 测试：mock `chat_module.retrieve_memory` + `chat_module.get_raw_client`，从 `client.chat.completions.create.call_args.kwargs["messages"][0]["content"]` 拿到渲染后的 system prompt 内容
- review 测试：`monkeypatch.setenv("ENABLE_MEMORY_REVIEW", ...)` + `monkeypatch.setattr(review_module, "_MEMORY_REVIEW_ENABLED", True/False)`（与 PR-3 chat.py 同模式 — `_MEMORY_REVIEW_ENABLED` 是模块级常量在 import 时计算，setattr 直接覆盖）+ patch `fetch_active_notes` + patch LLM client
- baseline 测试不需要 mock，纯渲染 + 字节比对

---

## 10. 不做的事

- ❌ **不重构 chat 节点为 j2 模板** — stream 变体 + 运行时门槛决策让 Python 拼接更直接（§4.4 注释解释清楚）
- ❌ **不动 PR-3 已锁的 19 个集成测试** — PR-4 不触动 chat.py 入口或 dispatch helper
- ❌ **不引入 prefix cache** — 项目当前未启用，只是 prompt 顺序设计上准备好（§4.3）
- ❌ **不暴露 snapshot 给前端** — PR-3 已锁，PR-4 不动
- ❌ **不改 reviewer / progress aggregator** — 那是写路径，PR-3 已落
- ❌ **不动 `_BASE_SYSTEM_PROMPT` 内容** — chat 系统消息基础段保持原样
- ❌ **不在 review 路径用硬约束** — misconception 注入采用软指导措辞（让 LLM 灵活判断）

---

## 11. 与 PR-3 / 总 spec 的衔接

PR-3 写完后 `LearningState.memory_snapshot` 已经被 PR-3 填好（env=on 路径）。PR-4 接手把它消费掉。

| 工作 | PR-3 责任 | PR-4 责任 |
|---|---|---|
| 加 state 字段 | ✅ | — |
| session 启动 fetch | ✅ | — |
| 字段冻结 | ✅ | — |
| BG 写路径（reviewer / progress / ingest） | ✅ | — |
| **节点读 snapshot** | — | ✅ |
| **prompt 注入** | — | ✅ |
| **review 实时 fetch** | — | ✅ |
| **retrieve_memory 门槛** | — | ✅ |

PR-4 后整个 hybrid agent memory 系统可端到端运行。剩下的工作（如：snapshot 渲染算法的运营调优、prompt 进 cache 的实际启用、reviewer 模型选择 / cost 监控）属于运营迭代，不是 PR 范围。
