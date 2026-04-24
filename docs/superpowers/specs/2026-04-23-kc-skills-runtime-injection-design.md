# KC Skills 运行时注入设计

- **日期**：2026-04-23
- **分支**：`demo+memory`
- **前置**：`2026-04-23-xes3g5m-kc-skills-offline-generation.md`（离线生成 `XES3G5M/metadata/kc_skills.json`，已实现）
- **本文档范围**：把已生成的 `kc_skills.json` 作为运行时上下文注入 agent 全链路（quiz / diagnosis / lesson / slides），并在 lesson 页面向学生展示其中的教学提示

## 背景

离线阶段已经跑通：`run/build_kc_skills.py` 把 XES3G5M 题库按 route 聚合成知识点级"技能"，包含 5 个字段——`description / 考察重点 / 考察形式 / 易错点 / 难度提示`。成品落在 `XES3G5M/metadata/kc_skills.json`，并有 `KCSkill` Pydantic schema (`src/llm/schemas.py`)。

此前提出过"pedagogy skills"系统（`skills/feynman | socratic | scaffolding`），走 `active_skill_content` 槽位，回答的是"怎么教"。

KC skills 是另一个正交维度——"教什么"。两套机制要并存，而不是合并。

## 目标

1. 让 agent 在 quiz 生成、诊断、lesson 讲解、slides 生成**四个节点**都能拿到当前知识点对应的 KCSkill 作为 LLM 上下文
2. lesson 页向学生展示该知识点的"考察重点 / 易错点 / 难度提示"为可折叠卡片
3. 接入过程不破坏当前"自由文本 topic → 知识图谱"主流程；拿不到 KC skill 时系统**静默降级**，链路照常跑

## 非目标

- 不把 XES3G5M 的 route 层级当作系统主知识库（图谱仍由 LLM 自由生成）
- 不引入 embedding 相似度（本仓库暂无 embedding 基础设施）
- 不改 pedagogy skill 的加载与注入逻辑
- 不在诊断页展示 KC skill（学生端仅 lesson 页可见）
- 不新增 REST 端点——state 序列化已承载一切

## 架构

```
user topic
  ↓
intent ─→ knowledge_graph  (LLM 自由生成图节点)
              ↓
         kc_skill_resolver  ← 新增 LangGraph 节点
              ├── Step 1: leaf 名规则匹配（无 token 成本，主力）
              └── Step 2: 未命中节点 → 一次 LLM 批量判定（兜底）
              ↓
         state.knowledge_skills: {kp_name: KCSkill}   # 仅键入命中的节点
              ↓
  ┌───────────┼───────────┬───────────┐
  quiz     diagnosis    lesson      slides
  └── 每个消费点：render_kc_skill_block(kc_skill) → {{ kc_skill_block }}
                               ↓
                          前端 lesson.html
                          读 state.knowledge_skills
                          渲染 <details> 可折叠技能卡
```

### 单元边界

| 单元 | 位置 | 职责 |
|---|---|---|
| `kc_loader` | `src/skills/kc_loader.py` | 读 `kc_skills.json`，按 route 和 leaf 名建立正向 + 倒排索引，单例缓存 |
| `kc_skill_resolver` 节点 | `src/agent/nodes/kc_skill_resolver.py` | 对图节点执行规则匹配 + LLM 兜底，填 `state.knowledge_skills` |
| `kc_skill_block` 模板 | `src/prompts/kc_skill_block.j2` | 把 `KCSkill` 对象渲染为提示文本（空对象→空字符串） |
| `kc_resolver` 模板 | `src/prompts/kc_resolver.j2` | LLM 兜底用：给定未命中节点 + 候选 leaf 列表，输出映射 |
| prompt 消费者 | 4 个既有 `.j2` 加一行 `{{ kc_skill_block }}` | quiz_generate / lesson_point / diagnosis / slide_outline + slide_content_draft |
| 节点消费者 | 4 个既有 `.py` 加 3 行取值 + 传 ctx | quiz / diagnosis / lesson / slides |
| 前端卡片 | `static/lesson.html` | `<details>` 可折叠，展示 3 个 KC skill 字段 |
| 开关 | `.env` 的 `USE_KC_SKILLS`（默认 true） | 全局关掉时 resolver 短路、prompt 变量空 |

每个单元都可以独立理解：loader 只关心 JSON；resolver 只关心"图节点名 → KCSkill"；prompt block 只关心一个 KCSkill 的渲染。

## 数据流 / state 形状

### 复用已有槽位（不改 schema）

`src/agent/state.py` 已预留：

```python
knowledge_skills: dict[str, KCSkill] | None = None   # {图节点 name: KCSkill}
use_knowledge_skills: bool = False
```

- **键**：图谱节点 `name`（如 "乘法原理"、"染色计数问题"）。以 name 而非 route 为键，是因为下游消费者已拿到 `kp_name`，一次命中，省去二次查表
- **值**：完整 `KCSkill` Pydantic 对象
- **未命中的节点不写键**（不填 `None`）——下游用 `dict.get(name)` 判断
- **开关语义**：`use_knowledge_skills=True` 时 resolver 才跑；`.env` 的 `USE_KC_SKILLS=false` 整体关掉

### session 持久化

`KCSkill` 已是 Pydantic，现有 `src/api/store.py` 的 `model_dump()` 路径直接处理 `state.knowledge_skills`。刷新页面时 `GET /api/chat/{id}/state` 回传的 state 自动带上这份数据，前端不需要新端点。

## 组件详述

### `kc_loader.py`

```python
_KC_SKILLS: dict[str, KCSkill] | None = None       # route -> KCSkill
_LEAF_INDEX: dict[str, list[str]] | None = None    # normalized leaf -> [routes]

DEFAULT_PATH = Path("XES3G5M/metadata/kc_skills.json")

def load_kc_skills(path: Path = DEFAULT_PATH) -> dict[str, KCSkill]: ...
def get_kc_skills() -> dict[str, KCSkill]: ...     # 懒加载
def leaf_index() -> dict[str, list[str]]: ...
def resolve_by_leaf(name: str) -> KCSkill | None:
    # 1. 精确：normalize(name) == normalize(leaf)
    # 2. 包含：normalize(name) ⊇ normalize(leaf)  或反之
    # 3. 多候选：按对应 route 的 "----" 段数最多者优先（最具体）；
    #    段数相同时按 route 字典序取第一（结果稳定）
```

`normalize(s)`：去空白、去标点、转小写。**硬规则，无模糊打分**——命中就是命中。

JSON 缺失或解析失败时 `load_kc_skills()` 写一条 warning 日志，返回 `{}`；后续调用全部降级为空。

### `kc_skill_resolver` 节点

```python
async def kc_skill_resolver(state: LearningState) -> LearningState:
    if not state.use_knowledge_skills or not state.knowledge_graph:
        return state

    resolved: dict[str, KCSkill] = {}
    unresolved: list[str] = []

    # Step 1: 规则匹配
    for node in state.knowledge_graph.nodes:
        hit = resolve_by_leaf(node.name)
        if hit:
            resolved[node.name] = hit
        else:
            unresolved.append(node.name)

    # Step 2: LLM 兜底（一次调用，批量处理）
    # 候选列表为空（JSON 缺失或全空）时直接跳过，避免浪费一次 LLM 调用
    if unresolved and leaf_index():
        try:
            llm_map = await _llm_resolve(unresolved, list(leaf_index().keys()))
            for name, route in llm_map.items():
                if route and route in get_kc_skills():
                    resolved[name] = get_kc_skills()[route]
        except Exception as e:
            logger.warning("KC skill LLM resolver failed: %s", e)
            # 仅保留规则匹配的结果，不抛

    state.knowledge_skills = resolved
    return state
```

LLM 兜底的结构化输出：

```python
class ResolverMapping(BaseModel):
    mapping: dict[str, str | None]   # node_name -> route | None
```

通过 Instructor + `kc_resolver.j2` 模板完成。模板内容：
- 解释任务：为给定的每个"图节点名"在候选 leaf 列表中选最匹配的一个，选不到就返回 null
- 输入：`unresolved_nodes`（列表）、`candidate_leaves`（列表，含对应 route）
- 输出约束：严格返回 `ResolverMapping` JSON

**错误处理与降级**：
- `kc_skills.json` 缺失 → loader 返回 `{}` → resolver 所有节点都 unresolved → `leaf_index()` 为空，Step 2 跳过 → `state.knowledge_skills = {}`
- 图谱为空 → resolver 短路返回
- LLM 异常 → 保留规则结果，warning 日志

### LangGraph 布线

`src/agent/graph.py`：

```
knowledge_graph --> kc_skill_resolver --> (既有下游分支)
```

直接串在 `knowledge_graph` 后面；不走条件边，resolver 内部按 `use_knowledge_skills` 开关自己决定是否跑。

### `kc_skill_block.j2` 模板

```jinja
{% if kc_skill %}
## 当前知识点的教学上下文
- 考察重点：{{ kc_skill["考察重点"] }}
- 考察形式：{{ kc_skill["考察形式"] }}
- 常见易错点：{{ kc_skill["易错点"] }}
- 难度提示：{{ kc_skill["难度提示"] }}
{% endif %}
```

空/None → 整段不渲染，prompt 不留空标题。

### `render_kc_skill_block` helper

放在 `src/prompts/loader.py`（保持模板加载统一入口）：

```python
def render_kc_skill_block(kc_skill: KCSkill | None) -> str:
    if kc_skill is None:
        return ""
    return env.get_template("kc_skill_block.j2").render(
        kc_skill=kc_skill.model_dump()
    )
```

下游节点统一调这个 helper，返回字符串塞进模板上下文的 `kc_skill_block` 变量。

### 消费节点改动（4 处，模式一致）

每个节点在已经拿到 `knowledge_point_name` 的位置加 3 行：

```python
kc_skill = (state.knowledge_skills or {}).get(kp_name)
kc_skill_block = render_kc_skill_block(kc_skill)
ctx = {..., "kc_skill_block": kc_skill_block, ...}
```

变动位置：

| 节点 | 改动点 |
|---|---|
| `quiz.py` | 按 KP 循环生成每题的位置 |
| `diagnosis.py` | 分析每个薄弱 KP 的位置 |
| `lesson.py` | `_generate_kc_skill()` / 每点课件生成 |
| `slides.py` | outline 阶段 + 每页 content draft 阶段（两处都要加；layout JSON 阶段**不加**） |

对应的 prompt 模板改动：仅在合适位置（通常靠近"任务说明 / 知识点名"之后、"输出格式"之前）加一行：

```jinja
{{ kc_skill_block }}
```

### Slides 的特别说明

slides 的 layout JSON prompt 负责的是结构化元素生成，不是内容导向——**不注入** KC skill block，避免无关文本污染 JSON 模式。只注入到：
1. `slide_outline.j2`（决定每页讲什么）
2. `slide_content_draft.j2`（决定每页具体内容）

### 前端：lesson.html 可折叠卡

渲染逻辑：

```html
<!-- 在"当前知识点"区域下方 -->
<details class="kc-skill-card mt-2">
  <summary class="cursor-pointer text-sm text-slate-500">
    AI 教学提示
  </summary>
  <div class="mt-2 space-y-1 text-sm">
    <p><strong>考察重点：</strong><span data-field="考察重点"></span></p>
    <p><strong>常见易错点：</strong><span data-field="易错点"></span></p>
    <p><strong>难度提示：</strong><span data-field="难度提示"></span></p>
  </div>
</details>
```

- `<details>` 原生折叠，不引入 JS 库
- 所有字段通过 `escapeHtml()` 注入（遵守仓库 XSS 纪律）
- 对应 KP 在 `state.knowledge_skills` 中查不到 → 整张 `<details>` 不挂载
- 卡片默认折叠，学生点开才展示——避免"剧透答案"

## 开关与发布

- `.env` 增加 `USE_KC_SKILLS=true`（默认 true）
- `run/serve.py` 在创建新 session 时把环境变量写进 `state.use_knowledge_skills`
- 关闭路径：`USE_KC_SKILLS=false` → resolver 短路 → `state.knowledge_skills` 保持 None → 下游 `render_kc_skill_block(None) == ""` → prompt 模板 `{{ kc_skill_block }}` 为空字符串 → 全链路无感降级

## 测试

- `tests/test_kc_loader.py`
  - 正常加载 JSON；
  - `normalize` 的边界（空白/标点/大小写）；
  - 精确匹配 vs 包含匹配；
  - 多候选时最长 route 优先；
  - JSON 缺失 / 损坏 → 返回 `{}` 不抛。
- `tests/test_kc_skill_resolver.py`
  - 图谱全部节点命中规则 → LLM 不调用；
  - 部分节点走 LLM 兜底（mock Instructor）；
  - LLM 抛错 → 仅保留规则结果，state 不变坏；
  - `use_knowledge_skills=False` → 节点短路，state 不变。
- `tests/test_quiz.py` / 现有节点测试扩展
  - 给定 `state.knowledge_skills` 非空 → 断言渲染后的 prompt 含 "考察重点"；
  - 空 `knowledge_skills` → 断言 prompt 不含 KC skill 段落标题。
- 不做端到端集成测试（会话层测试已覆盖 graph 主链路）

## 风险与权衡

| 风险 | 缓解 |
|---|---|
| 规则匹配把"乘法原理"匹到"乘法原理里的其他类型"，丢失歧义 | 最长 route 优先规则；另一种更具体的 leaf 会赢 |
| LLM 兜底延迟拖慢整个 quiz 流程 | 只在"至少一个节点未命中"时调用一次；规则足够好时零额外开销 |
| `kc_skills.json` 与线上图谱节点完全没交集 | 全链路降级为 empty block，系统行为回到改造前 |
| 模板里多一个变量，遗漏传参会 KeyError | helper 始终返回字符串（至少空串），ctx 始终提供该键 |
| 开关关掉后前端仍尝试读 `knowledge_skills` | 前端在渲染时已判空，`<details>` 不挂载 |

## 未来工作（不在本次 scope）

- 把 KC skill 暴露到诊断页（学生读到"易错点"可能产生教学歧义，先观察效果）
- 把 XES3G5M 的 route 树作为"选项式 topic 入口"的第二条路径
- 用 KC skill 统计反过来影响 quiz 难度自适应（目前只是静态注入）
- 引入 embedding 相似度 resolver（等本仓库引入 embedding 基础设施后）

## 开发节奏建议

分三步提 PR：

1. **loader + resolver + 状态**（底座）；接上 LangGraph；只加测试，不接任何消费者。此步保证：有 JSON 也跑得通、没 JSON 也跑得通。
2. **prompt block + 4 个消费节点**；逐个节点接，每接一个跑一次端到端 smoke test。
3. **前端卡片**；只读渲染，风险最低。

每步都可以独立发布，互相向后兼容。
