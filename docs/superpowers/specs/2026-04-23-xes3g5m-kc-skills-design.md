# XES3G5M 知识点 Skill 离线生成 — 设计文档

**日期**：2026-04-23
**分支**：`demo+memory`
**状态**：设计阶段，未开始实现

## 1. 目标与非目标

### 目标

针对 `XES3G5M/metadata/questions.json` 中的每一个**知识点（KC）**，基于真实题库用 LLM 离线生成一份结构化的 **"KC skill"**，内容包含：

- **考察重点**：此 KC 被考察的核心概念 / 子技能 / 思维点
- **考察形式**：题型分布（填空 / 单选）、常见题干模式、情境套路
- **易错点**：从题目 `analysis` 中归纳的学生高频错误
- **难度提示**：数字量级、嵌套深度、多步性等难度线索

产出物是一个索引化的 JSON 文件，供后续（不在本期实现范围内）接入 app 时作为 prompt context 注入给 LLM，帮助 LLM 更准确地：

1. 在**诊断阶段**按 KC 出题
2. 在**个性化课程阶段**结合考察形式讲解

### 非目标（本次 out of scope）

- **不修改** LangGraph 节点（`src/agent/nodes/*.py`）
- **不修改** 现有的 `src/prompts/knowledge_skill.j2` 和 `lesson.py:generate_knowledge_skill()`（它们在运行时生成教学法片段，和这次的 KC skill 定位不同）
- **不涉及** 把 XES3G5M 题库接入 app 的出题流程（下期工作）
- **不训练** 任何 KT / embedding 模型，embedding 目录 (`XES3G5M/metadata/embeddings/`) 本期不使用

## 2. 输入数据

| 路径 | 作用 |
|---|---|
| `XES3G5M/metadata/questions.json` | 7652 题，每题字段 `content` / `options` / `answer` / `analysis` / `type` / `kc_routes` |
| `XES3G5M/metadata/kc_routes_map.json` | 1175 个 KC id → leaf 名，仅作辅助元数据（不是主 key） |

### 关键数据观察

- 题目 `kc_routes` 字段是**全路径**字符串数组，例如 `拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型`，一道题可挂多个 KC
- 题库实际出现 **1240 条 distinct 全路径**，而非 1175 — 因为同一 leaf 名会同时挂在 `拓展思维` 和 `知识点` 两棵父树下
- **339 个 leaf 名存在碰撞**（出现在多条全路径中），因此 skill 的 key **必须用全路径字符串**，不能用 leaf 名，也不能用 `kc_routes_map.json` 的整数 id
- 每 KC 题数分布：min=1, median=3, max=138, mean=8.3；有 381 个 KC 只有 1 题，473 个 KC ≥ 5 题

## 3. 总体设计

### 3.1 架构

一个独立的离线批处理脚本，**不嵌入 FastAPI / LangGraph**：

```
run/build_kc_skills.py                 # 入口：asyncio 并发批处理
├── reads: XES3G5M/metadata/questions.json
├── uses:  src/llm/client.py           (Instructor + OpenAI SDK)
├── uses:  src/prompts/kc_skill_from_bank.j2   (新 prompt)
├── uses:  src/llm/schemas.py::KCSkill         (新 Pydantic 模型)
├── writes: XES3G5M/metadata/kc_skills.json          (主产出)
└── writes: XES3G5M/metadata/kc_skills.manifest.json (断点续跑进度 + 失败记录)
```

### 3.2 数据流

```
questions.json
  → group_by(full_route)          # 1240 组
  → for each route:
      sample_questions(route, cap=12)
      render_prompt(route, samples) → kc_skill_from_bank.j2
      llm.chat.completions.create(response_model=KCSkill)   # 结构化
      write entry to kc_skills.json
      update manifest
  → done
```

### 3.3 组件拆解

单元按"一个文件做一件事"的原则：

- **`run/build_kc_skills.py`** — CLI 入口 & 编排。负责加载数据、分组、并发调度、manifest 管理、原子写回
- **`src/prompts/kc_skill_from_bank.j2`** — Jinja2 模板。入参 `route`、`sample_questions`（list of dict：content/type/options/answer/analysis），输出一段结构化指令让 LLM 填 4 字段
- **`src/llm/schemas.py::KCSkill`** — Pydantic 模型，4 个字符串字段，用于 Instructor 结构化输出
- **`src/llm/client.py`** — 已有，复用不改

每个单元 **有独立职责**：编排、模板、契约、LLM 调用；**依赖清晰**：脚本依赖后三者，后三者互不依赖。

## 4. 详细规格

### 4.1 `KCSkill` Pydantic 模型

新增到 `src/llm/schemas.py`（已有 `QuizQuestion` / `KnowledgeGraph` 等，风格对齐）：

```python
class KCSkill(BaseModel):
    """LLM-generated, question-bank-grounded context for one knowledge component."""
    考察重点: str = Field(..., description="核心概念 / 子技能 / 思维点，80-150 字")
    考察形式: str = Field(..., description="题型分布 + 常见题干模式 + 情境套路，80-150 字")
    易错点:   str = Field(..., description="从 analysis 归纳的高频错误，50-120 字")
    难度提示: str = Field(..., description="数字量级 / 嵌套深度 / 多步性，50-100 字")
```

中文字段名符合既有 `knowledge_skill.j2` 风格（该模板输出也用"教学切入点"等中文段名）。

### 4.2 Prompt 模板 `kc_skill_from_bank.j2`

结构：

```
你是一位数学教研专家。下面是题库中属于同一个知识点的若干真题，请基于这些真题总结该知识点的考察情况。

## 知识点路径
{{ route }}

## 样本题目（共 {{ sample_count }} 道，从 {{ total_count }} 道中抽取）
{% for q in samples %}
### 题目 {{ loop.index }}（{{ q.type }}）
**题干**：{{ q.content }}
{% if q.options %}**选项**：{{ q.options }}{% endif %}
**答案**：{{ q.answer }}
**解析**：{{ q.analysis }}
{% endfor %}

## 输出要求

严格按照以下 4 个字段结构化输出（字段名用中文，不要额外字段，不要 markdown 标题）：
- 考察重点：概括此知识点考察的核心概念 / 子技能 / 思维点（80-150 字）
- 考察形式：题型分布（填空/单选比例）+ 常见题干模式 + 典型情境（80-150 字）
- 易错点：从解析中提炼学生高频错误（50-120 字）
- 难度提示：数字量级、嵌套深度、多步性等难度线索（50-100 字）

禁止事项：不要复述题目原文；不要加教学建议；不要写"本知识点"这类空话开头。
```

### 4.3 采样策略

对于一条 KC 全路径 `route`，从挂载在该 route 下的所有题中选样：

- **题数 ≤ 12**：全选
- **题数 > 12**：按 `type` 分层抽样，确定性可复现。具体算法：
    1. 将 route 下所有题按 `type` 分组；对每组按题目 id 升序排序
    2. 按各 type 在全集中的比例分配名额（向下取整，余数按组大小降序分配），总计 12 席
    3. 每组在其内部做**均匀步长抽样**（`samples[i] = sorted[i * step]`，`step = len // quota`），保证跨 id 范围分布而非只取前几道

不按难度分层（题库没标注难度）。不去重（题干重复由数据本身保证）。

**理由**：12 题 × 典型一题 300 字 ≈ 4k input tokens，加 prompt overhead 稳在 5k 以内，远低于 32k context 上限；同时 12 题足以覆盖同一 KC 的形式多样性（观察到 max 138 题的 KC 其实形式高度重复）。

### 4.4 输出文件：`kc_skills.json`

单 JSON 索引文件，顶层是 dict，key 为全路径：

```json
{
  "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型": {
    "route": "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
    "leaf": "乘法原理里的其他类型",
    "kc_ids": [0],
    "question_count": 42,
    "sampled_count": 12,
    "sampled_question_ids": ["0", "17", "93", "..."],
    "skill": {
      "考察重点": "...",
      "考察形式": "...",
      "易错点":   "...",
      "难度提示": "..."
    },
    "source_model": "deepseek-chat",
    "generated_at": "2026-04-23T10:23:45Z"
  },
  ...
}
```

字段说明：

- `route`：冗余存一份，便于单条记录自足
- `leaf`：leaf 名，方便人工浏览和 downstream 模糊匹配
- `kc_ids`：从 `kc_routes_map.json` 反查得到的整数 id 列表。该 map 的 value（leaf 名）全局唯一，因此通常 `len(kc_ids) == 1`；若 route 的 leaf 未在 map 中（题库中有 leaf 名在 map 里找不到的情况，因为 map 的 1175 项 ⊃ 题库实际用到的 865 leaf），则为 `[]`。**非主 key，仅作元数据**
- `sampled_question_ids`：留痕，便于复现和人工 spot-check
- `source_model` / `generated_at`：版本溯源

**大小估算**：1240 条 × (每条 skill 4 字段 × ~120 字 + 元数据) ≈ 1.5 MB。内存全加载无问题。

### 4.5 Manifest 文件：`kc_skills.manifest.json`

```json
{
  "version": 1,
  "started_at": "2026-04-23T10:00:00Z",
  "model": "deepseek-chat",
  "completed_routes": ["...", "..."],
  "failed": [
    {"route": "...", "error": "...", "attempts": 3, "last_at": "..."}
  ]
}
```

启动时读 manifest，`completed_routes` 里的 KC 跳过，支持断点续跑。失败 route 进入 `failed` 列表但不阻塞整体；跑完后提示用户重跑失败项。

### 4.6 并发与容错

- **asyncio** + `asyncio.Semaphore(CONCURRENCY)`，默认 `CONCURRENCY=8`
- 每次 LLM 调用封装在 `asyncio.to_thread`（Instructor 是同步客户端，这点和现有 `chat.py` 等节点一致）
- **重试**：单条失败最多 3 次（指数退避 2s / 4s / 8s）；三次都失败则记入 `failed`
- **写回**：主产出 `kc_skills.json` 保留整份 dict 在内存中；每完成一条时拿 `asyncio.Lock`，把 dict 序列化到临时文件 `kc_skills.json.tmp`，然后 `os.replace` 原子替换正式文件。全量重写而非 append — 1.5 MB 文件每次 ~几 ms，避免 JSON 追加带来的格式复杂度。manifest 同理
- **进度**：stderr 打印 `[325/1240] 知识点----应用题----... ✓` 这样的行，便于目测

### 4.7 CLI 参数

```
python run/build_kc_skills.py [--concurrency N] [--limit N] [--only-module MODULE] [--resume]
```

- `--limit N`：只跑前 N 条 route（用于冒烟测试）
- `--only-module`：按 route 第二段过滤，如 `--only-module 计数模块` 只跑计数相关 KC（也用于冒烟）
- `--resume`：默认行为（manifest 存在即续跑）；显式 `--no-resume` 则从头覆盖
- `--concurrency`：默认 8

**首次使用方式**：先 `--limit 5` 冒烟，看输出质量；再 `--only-module 计数模块` 跑一个模块；满意后全量跑。

## 5. 错误处理

| 错误类型 | 处理 |
|---|---|
| LLM API 超时 / 5xx | 指数退避重试 ≤ 3 次 |
| Instructor 结构化解析失败 | Instructor 内部已有 max_retries，再外层 1 次；仍失败记入 `failed` |
| 单个 KC 样本为 0 题 | 不应出现（我们是按题库倒推 route），如出现 skip 并 log warning |
| 磁盘写入失败 | 原子写失败直接抛，不污染主文件 |
| 脚本中途被 Ctrl-C | 因每条即写，`kc_skills.json` 保持最后一条完成时的一致状态 |

**不处理**的边界情况（由上游保证）：

- `questions.json` 格式异常 — 题库是已发布的静态数据
- `.env` 缺失 `OPENAI_API_KEY` — 复用 `src/llm/client.py` 的错误提示

## 6. 测试

本期仅有离线脚本，测试以轻量 unit test 为主：

- `tests/test_kc_skills_build.py`：
    - `test_group_questions_by_route` — 给构造的小题库 dict，断言分组正确，全路径作为 key
    - `test_sample_questions_cap` — 超过 cap 时按类型分层抽样，确定性复现
    - `test_sample_questions_small` — 题数 ≤ cap 时全选
    - `test_manifest_resume_skips_completed` — manifest 有条目则跳过
    - `test_atomic_write_preserves_on_failure` — mock os.replace 失败，断言主文件未变
- **不测试** LLM 真实调用（造 fake client 注入，断言被调用次数和传参）
- **人工验收**：`--limit 5` 跑完后，眼验 5 条输出对于 4 字段是否合理、字数是否在范围内

## 7. 成本与时间估算

- 1240 条 × 平均 5k input + 500 output tokens
- 按 DeepSeek-chat 价格（input ¥1/M, output ¥2/M）：≈ ¥6–10 总成本
- 8 并发下：1240 × 3s / 8 ≈ 8 分钟（乐观）；含重试和慢响应保守 20 分钟

## 8. 后续工作（下期，不在本次实现范围内）

给接入者留的契约：

```python
from pathlib import Path
import json
KC_SKILLS = json.load(open("XES3G5M/metadata/kc_skills.json", encoding="utf-8"))

def lookup_kc_skill(route: str) -> dict | None:
    """Returns {'考察重点': ..., '考察形式': ..., '易错点': ..., '难度提示': ...} or None"""
    entry = KC_SKILLS.get(route)
    return entry["skill"] if entry else None
```

接入点提示（供下期参考，本次不做）：

- `src/agent/nodes/quiz.py` 生成题目时，若 state 指定 KC route，从 `KC_SKILLS` 查表，将 skill 4 字段作为 few-shot 指引拼入 `quiz_generate.j2`
- `src/agent/nodes/lesson.py:generate_knowledge_skill()` 可改为先查 `KC_SKILLS`，命中则跳过运行时 LLM 调用（省钱且质量更稳）；未命中退回原逻辑
- 需要做 route 匹配：动态 `knowledge_graph` 的 KC name 是 leaf 级中文短语；需要一个 "leaf name → candidate routes" 的模糊匹配层，命中多条时取题量最大的那条

## 9. 交付清单

本次 PR 应包含：

1. `src/llm/schemas.py` — 新增 `KCSkill` 类
2. `src/prompts/kc_skill_from_bank.j2` — 新 prompt
3. `run/build_kc_skills.py` — 离线脚本（CLI + asyncio 并发）
4. `tests/test_kc_skills_build.py` — unit tests
5. `XES3G5M/metadata/kc_skills.json` — 全量产出（约 1.5 MB，入 git）
6. `XES3G5M/metadata/kc_skills.manifest.json` — 进度日志（入 git，便于下次增量）

**不产出**：下期的 app 接入代码，仅在本文档 §8 留出契约。
