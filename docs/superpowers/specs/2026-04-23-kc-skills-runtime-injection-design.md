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
         state["kc_skills"]: {kp_name: dict}   # 仅键入命中的节点；值是 KCSkill.model_dump()
              ↓
  ┌───────────┼───────────┬───────────┐
  quiz     diagnosis    lesson      slides
  └── 每个消费点：render_kc_skill_block(kc_skill_dict) → {{ kc_skill_block }}
                               ↓
                          前端 lesson.html
                          读 state.kc_skills
                          渲染 <details> 可折叠技能卡（独立于现有 inline-ks-card）
```

### 单元边界

| 单元 | 位置 | 职责 |
|---|---|---|
| `kc_loader` | `src/skills/kc_loader.py` | 读 `kc_skills.json`，按 route 和 leaf 名建立正向 + 倒排索引，单例缓存 |
| `kc_skill_resolver` 节点 | `src/agent/nodes/kc_skill_resolver.py` | 对图节点执行规则匹配 + LLM 兜底，填 `state.kc_skills` |
| `kc_skill_block` 模板 | `src/prompts/kc_skill_block.j2` | 把 `KCSkill` 对象渲染为提示文本（空对象→空字符串） |
| `kc_resolver` 模板 | `src/prompts/kc_resolver.j2` | LLM 兜底用：给定未命中节点 + 候选 leaf 列表，输出映射 |
| prompt 消费者 | 4 个既有 `.j2` 加一行 `{{ kc_skill_block }}` | quiz_generate / lesson_point / diagnosis / slide_outline + slide_content_draft |
| 节点消费者 | 4 个既有 `.py` 加 3 行取值 + 传 ctx | quiz / diagnosis / lesson / slides |
| 前端卡片 | `static/lesson.html` | `<details>` 可折叠，展示 3 个 KC skill 字段 |
| 开关 | `.env` 的 `USE_KC_SKILLS`（默认 true） | 全局关掉时 resolver 短路、prompt 变量空 |

每个单元都可以独立理解：loader 只关心 JSON；resolver 只关心"图节点名 → KCSkill"；prompt block 只关心一个 KCSkill 的渲染。

## 数据流 / state 形状

### 新增独立字段（避免和已有 `knowledge_skills` 冲突）

**重要**：现有 `knowledge_skills: dict | None` 字段已被 `lesson.py` 的 `retrieve_skills()` 使用——存"按教学法动态生成的 per-KP 微技能文本"，`dict[str, str]`，默认开启，并有 `/toggle-knowledge-skills` API 和前端卡片。**不能复用**。KC skill 要另立一个字段。

在 `src/agent/state.py` 的 `LearningState` 中新增：

```python
# KC skills loaded from kc_skills.json (orthogonal to pedagogy skills)
kc_skills: dict | None          # {kp_name: KCSkill.model_dump()} 即 dict[str, dict]
use_kc_skills: bool             # 是否激活 KC skill resolver + 注入
```

同步在 `src/api/chat.py` 的 `_DEFAULT_STATE` 中加入这两个键的默认值（`None` 和从 env 读取的布尔）。

- **键**：图谱节点 `name`（如 "乘法原理"、"染色计数问题"）。以 name 而非 route 为键，是因为下游消费者已拿到 `kp_name`，一次命中，省去二次查表
- **值**：`KCSkill.model_dump()` 的结果，即 `dict`。**不存 Pydantic 对象**——这样 `src/api/store.py` 的 `json.dumps(..., default=str)` 可以直接处理，无需在 `_deserialize_state` 里做特殊恢复
- **未命中的节点不写键**（不填 `None`）——下游用 `dict.get(name)` 判断
- **开关语义**：`use_kc_skills=True` 时 resolver 才跑；`.env` 的 `USE_KC_SKILLS=false` 让默认 state 里 `use_kc_skills=False`，整条链路静默

### session 持久化

因为 state 里存的是纯 dict（不是 Pydantic 对象），`src/api/store.py` 现有的 `_serialize_state`（走 JSON else 分支）和 `_deserialize_state`（直接保留为 dict）**零改动**即可工作。刷新页面时 `GET /api/chat/{id}/state` 回传的 state 自动带上 `kc_skills`。

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

与现有节点一致用**同步 `def`**（LangGraph 调用者是同步 `graph.invoke`），返回 dict patch：

```python
def kc_skill_resolver_node(state: LearningState) -> dict:
    if not state.get("use_kc_skills"):
        return {}
    graph = state.get("knowledge_graph")
    if not graph:
        return {}

    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])

    resolved: dict[str, dict] = {}   # kp_name -> KCSkill.model_dump()
    unresolved: list[str] = []

    # Step 1: 规则匹配
    for node in nodes:
        name = node.name if hasattr(node, "name") else node["name"]
        hit = resolve_by_leaf(name)   # returns KCSkill | None
        if hit is not None:
            resolved[name] = hit.model_dump()
        else:
            unresolved.append(name)

    # Step 2: LLM 兜底（一次调用，批量处理）
    # 候选列表为空（JSON 缺失或全空）时直接跳过，避免浪费一次 LLM 调用
    if unresolved and leaf_index():
        try:
            llm_map = _llm_resolve(unresolved, list(leaf_index().keys()))
            kc = get_kc_skills()
            for name, route in llm_map.items():
                if route and route in kc:
                    resolved[name] = kc[route].model_dump()
        except Exception as e:
            logger.warning("KC skill LLM resolver failed: %s", e)
            # 仅保留规则匹配的结果，不抛

    return {"kc_skills": resolved}
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
- `kc_skills.json` 缺失 → loader 返回 `{}` → resolver 所有节点都 unresolved → `leaf_index()` 为空，Step 2 跳过 → `{"kc_skills": {}}`
- 图谱为空 → resolver 返回 `{}`（state 不变）
- LLM 异常 → 保留规则结果，warning 日志
- `use_kc_skills=False` → 返回 `{}`（state 不变，`kc_skills` 保持 `None`）

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

放在 `src/prompts/loader.py`（保持模板加载统一入口）。输入是**纯 dict**（与 state 里存的一致），不是 Pydantic：

```python
def render_kc_skill_block(kc_skill: dict | None) -> str:
    """kc_skill is the dict form (KCSkill.model_dump()) or None."""
    if not kc_skill:
        return ""
    return render_prompt("kc_skill_block", kc_skill=kc_skill)
```

下游节点统一调这个 helper，返回字符串塞进模板上下文的 `kc_skill_block` 变量。

### 消费节点改动（4 处，模式一致）

每个节点在已经拿到 `knowledge_point_name` 的位置加 3 行：

```python
kc_skill = (state.get("kc_skills") or {}).get(kp_name)
kc_skill_block = render_kc_skill_block(kc_skill)
# ... render_prompt(..., kc_skill_block=kc_skill_block)
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

现有 `lesson.html` 已经在 `h2` 之后注入一种 pedagogy-derived 的 "inline-ks-card"（读 `state.knowledge_skills`）。**新增**一张不同的 "kc-skill-card" 读 `state.kc_skills`，与其并列但样式区分（不同 summary 图标与颜色），避免学生误以为是同一张卡。

在 `static/lesson.html` 渲染 lesson 段落后、对 `h2` 做 `forEach` 注入的循环中，**额外**判断 `state.kc_skills` 并为每个 KP name 注入一张：

```javascript
const kcSkills = state.kc_skills || {};
// ... 在同一个 h2s.forEach 里
if (Object.keys(kcSkills).length > 0) {
  const matchedKey = Object.keys(kcSkills).find(function(k) {
    return headingText.indexOf(k) !== -1 || k.indexOf(headingText) !== -1;
  });
  if (matchedKey) {
    const kc = kcSkills[matchedKey];
    const card = document.createElement('div');
    card.innerHTML =
      '<details class="kc-skill-card">' +
        '<summary>📘 出题考察提示：' + escapeHtml(matchedKey) + '</summary>' +
        '<div class="kc-skill-card-body">' +
          '<p><strong>考察重点：</strong>' + escapeHtml(kc['考察重点'] || '') + '</p>' +
          '<p><strong>常见易错点：</strong>' + escapeHtml(kc['易错点'] || '') + '</p>' +
          '<p><strong>难度提示：</strong>' + escapeHtml(kc['难度提示'] || '') + '</p>' +
        '</div>' +
      '</details>';
    h2.parentNode.insertBefore(card, h2.nextSibling);
  }
}
```

- `<details>` 原生折叠，不引入 JS 库
- 所有字段通过 `escapeHtml()` 注入（遵守仓库 XSS 纪律）——三个字段都是 LLM 生成的中文文本，无需 markdown
- `state.kc_skills` 空 / 该 KP 无匹配 → 不挂载任何卡
- 卡片默认折叠，学生点开才展示——避免"剧透答案"
- 新增 CSS 类 `.kc-skill-card` 和 `.kc-skill-card-body`（蓝紫色系，区别于橙色 inline-ks-card）

## 开关与发布

- `.env` 增加 `USE_KC_SKILLS=true`（默认 true）
- `src/api/chat.py` 的 `_DEFAULT_STATE` 中 `use_kc_skills` 的默认值从 `os.environ.get("USE_KC_SKILLS", "true").lower() == "true"` 派生（在文件顶部读一次）
- 关闭路径：`USE_KC_SKILLS=false` → `_DEFAULT_STATE["use_kc_skills"] = False` → 新 session 的 resolver 短路 → `state["kc_skills"]` 保持 `None` → 下游 `render_kc_skill_block(None) == ""` → prompt 模板 `{{ kc_skill_block }}` 为空字符串 → 全链路无感降级

## 测试

- `tests/test_kc_loader.py`
  - 正常加载 JSON；
  - `normalize` 的边界（空白/标点/大小写）；
  - 精确匹配 vs 包含匹配；
  - 多候选时最长 route 优先，段数相同按字典序；
  - JSON 缺失 / 损坏 → 返回 `{}` 不抛。
- `tests/test_kc_skill_resolver.py`
  - 图谱全部节点命中规则 → LLM 不调用；
  - 部分节点走 LLM 兜底（mock Instructor）；
  - LLM 抛错 → 仅保留规则结果，state 不变坏；
  - `use_kc_skills=False` → 节点短路，返回 `{}`；
  - `kc_skills.json` 为空（`leaf_index()` 空）且有 unresolved → LLM 也不调用。
- `tests/test_prompts.py` 扩展 `kc_skill_block`
  - 非空 dict → 断言渲染结果含 "考察重点"；
  - None / 空 dict → 断言返回空字符串。
- `tests/test_skills.py` 不动（pedagogy 逻辑未变）
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
