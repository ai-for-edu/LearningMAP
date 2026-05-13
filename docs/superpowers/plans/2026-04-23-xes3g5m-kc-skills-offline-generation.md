# XES3G5M 知识点 Skill 离线生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `XES3G5M/metadata/questions.json` 中的每一条 KC 全路径离线生成基于真实题库的结构化 skill（考察重点 / 考察形式 / 易错点 / 难度提示），产出 `kc_skills.json` 供后续 app 作为 prompt context。

**Architecture:** 独立离线脚本 `run/build_kc_skills.py` 使用 asyncio.Semaphore + asyncio.to_thread 包裹同步 Instructor 客户端做并发 LLM 调用；所有 LLM 输出走 Pydantic `KCSkill` 做结构化解析；断点续跑通过 manifest + 原子写主文件实现。**不**修改任何 LangGraph 节点或现有运行时代码。

**Tech Stack:** Python 3.11 · asyncio · Instructor + OpenAI SDK (`src/llm/client.py`) · Jinja2 prompts (`src/prompts/loader.py`) · Pydantic v2 · pytest

**Spec:** `docs/superpowers/specs/2026-04-23-xes3g5m-kc-skills-design.md`

---

## File Structure

| 文件 | 作用 | 状态 |
|---|---|---|
| `src/llm/schemas.py` | 新增 `KCSkill` Pydantic 模型（4 个中文字段） | Modify |
| `src/prompts/kc_skill_from_bank.j2` | 新 prompt 模板：输入 route + 样本题，指令 LLM 填 4 字段 | Create |
| `run/build_kc_skills.py` | CLI 入口 + 纯函数 helpers + asyncio 编排 + manifest 管理 | Create |
| `tests/test_kc_skills_build.py` | 单元测试：分组、抽样、manifest、原子写、fake LLM | Create |
| `XES3G5M/metadata/kc_skills.json` | 主产出（~1.5 MB），入 git | 脚本产出 |
| `XES3G5M/metadata/kc_skills.manifest.json` | 进度 / 失败记录，入 git | 脚本产出 |

**边界划分（DRY/SoC）：**

- `src/llm/schemas.py` 只负责契约，零业务逻辑
- `src/prompts/kc_skill_from_bank.j2` 只负责 prompt 渲染，无 Python
- `run/build_kc_skills.py` 同时承载纯函数（`group_questions_by_route` / `sample_questions` / `atomic_write_json` / `load_manifest` / `save_manifest` / `generate_kc_skill`）与 CLI 编排；保持单文件是因为它们共用小数据结构，拆文件会造成 import 噪音，且脚本整体 <300 行
- `tests/test_kc_skills_build.py` 针对上述纯函数；LLM 调用用 fake client 注入，不打真实 API

---

## Task 1: 新增 `KCSkill` Pydantic 模型

**Files:**
- Modify: `src/llm/schemas.py`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schemas.py`:

```python
from src.llm.schemas import KCSkill


def test_kc_skill_schema():
    skill = KCSkill(
        考察重点="理解乘法原理：分步计数中各步方案数相乘得到总方案数。",
        考察形式="以填空题为主，题干给出若干类对象和选择步骤，要求计算方案数。",
        易错点="学生常把分步（乘）和分类（加）混淆，导致用加法代替乘法。",
        难度提示="数字量级 2-5，步骤数 2-4 步，偶见带限制条件的情境。",
    )
    assert skill.考察重点.startswith("理解")
    assert "填空" in skill.考察形式
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_schemas.py::test_kc_skill_schema -v`
Expected: FAIL with `ImportError: cannot import name 'KCSkill' from 'src.llm.schemas'`

- [ ] **Step 3: Add the schema**

Append to `src/llm/schemas.py`:

```python
class KCSkill(BaseModel):
    """LLM-generated, question-bank-grounded context for one knowledge component."""

    考察重点: str = Field(..., description="核心概念 / 子技能 / 思维点，80-150 字")
    考察形式: str = Field(..., description="题型分布 + 常见题干模式 + 情境套路，80-150 字")
    易错点: str = Field(..., description="从 analysis 归纳的高频错误，50-120 字")
    难度提示: str = Field(..., description="数字量级 / 嵌套深度 / 多步性，50-100 字")
```

Also add `Field` to the import at the top of the file: change `from pydantic import BaseModel` to `from pydantic import BaseModel, Field`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_schemas.py::test_kc_skill_schema -v`
Expected: PASS

- [ ] **Step 5: Run full schema suite to catch regressions**

Run: `uv run python -m pytest tests/test_schemas.py -v`
Expected: all tests pass.

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/llm/schemas.py tests/test_schemas.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/llm/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): add KCSkill for XES3G5M KC skill generation"
```

---

## Task 2: Prompt 模板 `kc_skill_from_bank.j2`

**Files:**
- Create: `src/prompts/kc_skill_from_bank.j2`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompts.py`:

```python
def test_render_kc_skill_from_bank_prompt():
    samples = [
        {
            "type": "填空",
            "content": "学校有舞蹈、唱歌、围棋、绘画四种兴趣班……一共有多少种不同的报名方法？",
            "options": {},
            "answer": "$$24$$",
            "analysis": "小宇先选，有 4 种……一共有 4×3×2=24（种）",
        },
        {
            "type": "单选",
            "content": "书架上有 2 本英语书、4 本语文书……",
            "options": {"A": "6", "B": "8", "C": "14", "D": "26"},
            "answer": "D",
            "analysis": "分三类：2×4+2×3+4×3=26。",
        },
    ]
    result = render_prompt(
        "kc_skill_from_bank",
        route="拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
        samples=samples,
        sample_count=2,
        total_count=42,
    )
    assert "拓展思维----计数模块----加乘原理" in result
    assert "考察重点" in result
    assert "考察形式" in result
    assert "易错点" in result
    assert "难度提示" in result
    assert "题目 1" in result and "题目 2" in result
    assert "填空" in result
    # both answers must appear
    assert "24" in result
    assert "26" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_prompts.py::test_render_kc_skill_from_bank_prompt -v`
Expected: FAIL with `jinja2.exceptions.TemplateNotFound: kc_skill_from_bank.j2`.

- [ ] **Step 3: Create the template**

Create `src/prompts/kc_skill_from_bank.j2`:

```jinja
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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_prompts.py::test_render_kc_skill_from_bank_prompt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/prompts/kc_skill_from_bank.j2 tests/test_prompts.py
git commit -m "feat(prompts): add kc_skill_from_bank template for XES3G5M KC skills"
```

---

## Task 3: 分组与采样纯函数（TDD）

**Files:**
- Create: `run/build_kc_skills.py` (first time — will add more in later tasks)
- Create: `tests/test_kc_skills_build.py`

- [ ] **Step 1: Write failing tests for grouping and sampling**

Create `tests/test_kc_skills_build.py`:

```python
from __future__ import annotations

import pytest

from run.build_kc_skills import (
    group_questions_by_route,
    sample_questions,
)


def _q(qid: str, kc_routes: list[str], qtype: str = "填空") -> tuple[str, dict]:
    return qid, {
        "content": f"content-{qid}",
        "options": {},
        "answer": [f"a-{qid}"],
        "analysis": f"ana-{qid}",
        "type": qtype,
        "kc_routes": kc_routes,
    }


def test_group_questions_by_route_flattens_multi_route_questions():
    route_a = "root----A"
    route_b = "root----B"
    questions = dict([
        _q("0", [route_a]),
        _q("1", [route_a, route_b]),
        _q("2", [route_b]),
    ])
    groups = group_questions_by_route(questions)
    assert set(groups.keys()) == {route_a, route_b}
    assert [s["id"] for s in groups[route_a]] == ["0", "1"]
    assert [s["id"] for s in groups[route_b]] == ["1", "2"]
    # each sample carries its id so downstream tooling can record it
    assert groups[route_a][0]["content"] == "content-0"


def test_sample_questions_returns_all_when_under_cap():
    items = [{"id": str(i), "type": "填空"} for i in range(5)]
    result = sample_questions(items, cap=12)
    assert [s["id"] for s in result] == ["0", "1", "2", "3", "4"]


def test_sample_questions_stratifies_by_type_when_over_cap():
    # 10 填空 + 5 单选, cap=6 → ratio 2:1 → 4 填空 + 2 单选
    items = (
        [{"id": f"fill-{i}", "type": "填空"} for i in range(10)]
        + [{"id": f"choice-{i}", "type": "单选"} for i in range(5)]
    )
    result = sample_questions(items, cap=6)
    assert len(result) == 6
    types = [s["type"] for s in result]
    assert types.count("填空") == 4
    assert types.count("单选") == 2


def test_sample_questions_is_deterministic():
    items = [{"id": str(i), "type": "填空"} for i in range(30)]
    a = sample_questions(items, cap=12)
    b = sample_questions(items, cap=12)
    assert [s["id"] for s in a] == [s["id"] for s in b]


def test_sample_questions_uses_uniform_step_within_group():
    # 20 items of one type, cap=4 → step = 20//4 = 5 → ids 0,5,10,15
    items = [{"id": str(i), "type": "填空"} for i in range(20)]
    result = sample_questions(items, cap=4)
    assert [s["id"] for s in result] == ["0", "5", "10", "15"]


def test_sample_questions_distributes_remainder_to_largest_group():
    # 7 填空 + 4 单选 = 11, cap=5 → floor 5*7/11 = 3, floor 5*4/11 = 1, remainder 1 → to 填空 (larger)
    items = (
        [{"id": f"fill-{i}", "type": "填空"} for i in range(7)]
        + [{"id": f"choice-{i}", "type": "单选"} for i in range(4)]
    )
    result = sample_questions(items, cap=5)
    types = [s["type"] for s in result]
    assert types.count("填空") == 4
    assert types.count("单选") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'run.build_kc_skills'`.

- [ ] **Step 3: Create the module with the helpers**

Create `run/build_kc_skills.py`:

```python
"""Offline batch generator for XES3G5M KC skills.

See docs/superpowers/specs/2026-04-23-xes3g5m-kc-skills-design.md for design.
"""

from __future__ import annotations

from collections import defaultdict


def group_questions_by_route(questions: dict) -> dict[str, list[dict]]:
    """Invert questions dict so each KC full-route maps to its questions.

    Each output item is the original question dict enriched with an "id" key
    so downstream logging can record which source questions were sampled.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for qid, q in questions.items():
        item = dict(q)
        item["id"] = qid
        for route in q.get("kc_routes", []):
            groups[route].append(item)
    # preserve insertion order when iterated
    return dict(groups)


def sample_questions(items: list[dict], cap: int = 12) -> list[dict]:
    """Deterministic stratified sample by `type`.

    Rules:
    - if len(items) <= cap: return sorted by id (all items)
    - else: allocate cap slots across types proportionally (floor),
      distribute the remainder to the largest groups (ties broken by type
      name for determinism), then within each group take a uniform-step
      slice of the id-sorted items.
    """
    if len(items) <= cap:
        return sorted(items, key=lambda x: x["id"])

    # bucket by type, sort each bucket by id for determinism
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        buckets[item["type"]].append(item)
    for t in buckets:
        buckets[t].sort(key=lambda x: x["id"])

    total = len(items)
    # floor quota per type
    quotas: dict[str, int] = {t: (cap * len(buckets[t])) // total for t in buckets}
    assigned = sum(quotas.values())
    remainder = cap - assigned
    # distribute remainder to largest buckets (tie-break by type name ascending)
    order = sorted(buckets.keys(), key=lambda t: (-len(buckets[t]), t))
    for t in order:
        if remainder == 0:
            break
        quotas[t] += 1
        remainder -= 1

    # uniform-step pick from each bucket
    picked: list[dict] = []
    for t in sorted(buckets.keys()):  # deterministic iteration
        q = quotas[t]
        if q == 0:
            continue
        bucket = buckets[t]
        step = len(bucket) // q
        picked.extend(bucket[i * step] for i in range(q))

    return picked
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check run/build_kc_skills.py tests/test_kc_skills_build.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add run/build_kc_skills.py tests/test_kc_skills_build.py
git commit -m "feat(kc-skills): add group_questions_by_route and sample_questions helpers"
```

---

## Task 4: Manifest 与原子写（TDD）

**Files:**
- Modify: `run/build_kc_skills.py`
- Modify: `tests/test_kc_skills_build.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kc_skills_build.py`:

```python
import json
from pathlib import Path

from run.build_kc_skills import (
    atomic_write_json,
    load_manifest,
    save_manifest,
)


def test_atomic_write_json_creates_file_with_utf8(tmp_path: Path):
    target = tmp_path / "out.json"
    data = {"路径": {"skill": {"考察重点": "测试中文"}}}
    atomic_write_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data
    # no leftover .tmp file
    assert not (tmp_path / "out.json.tmp").exists()


def test_atomic_write_json_preserves_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"before": 1})

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"after": 2})

    # main file unchanged
    assert json.loads(target.read_text(encoding="utf-8")) == {"before": 1}


def test_load_manifest_returns_empty_when_missing(tmp_path: Path):
    m = load_manifest(tmp_path / "nope.json")
    assert m == {"version": 1, "completed_routes": [], "failed": []}


def test_manifest_roundtrip(tmp_path: Path):
    path = tmp_path / "m.json"
    m = {
        "version": 1,
        "completed_routes": ["r1", "r2"],
        "failed": [{"route": "r3", "error": "boom", "attempts": 3}],
        "model": "deepseek-chat",
    }
    save_manifest(path, m)
    assert load_manifest(path) == m
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v -k "atomic or manifest"`
Expected: FAIL — those symbols don't exist yet.

- [ ] **Step 3: Implement in `run/build_kc_skills.py`**

Append to `run/build_kc_skills.py`:

```python
import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to `path` atomically: dump to .tmp then os.replace.

    Guarantees that `path` is either the old contents or the new contents,
    never partially-written.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_manifest(path: Path) -> dict:
    """Load manifest or return an empty skeleton if the file is missing."""
    path = Path(path)
    if not path.exists():
        return {"version": 1, "completed_routes": [], "failed": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict) -> None:
    """Atomic write for the manifest."""
    atomic_write_json(path, manifest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add run/build_kc_skills.py tests/test_kc_skills_build.py
git commit -m "feat(kc-skills): atomic JSON write and manifest load/save"
```

---

## Task 5: LLM 调用封装（TDD with fake client）

**Files:**
- Modify: `run/build_kc_skills.py`
- Modify: `tests/test_kc_skills_build.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kc_skills_build.py`:

```python
from src.llm.schemas import KCSkill
from run.build_kc_skills import generate_kc_skill


class _FakeCompletions:
    def __init__(self, result: KCSkill):
        self.result = result
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _FakeChat:
    def __init__(self, result: KCSkill):
        self.completions = _FakeCompletions(result)


class _FakeClient:
    def __init__(self, result: KCSkill):
        self.chat = _FakeChat(result)


def test_generate_kc_skill_renders_prompt_and_uses_schema():
    expected = KCSkill(
        考察重点="乘法原理核心概念",
        考察形式="填空为主",
        易错点="分步分类混淆",
        难度提示="2-4 步",
    )
    client = _FakeClient(expected)
    samples = [
        {"id": "0", "type": "填空", "content": "c0", "options": {}, "answer": "a0", "analysis": "x"},
    ]
    result = generate_kc_skill(
        route="root----A",
        samples=samples,
        total_count=42,
        client=client,
        model="test-model",
    )
    assert result == expected
    call = client.chat.completions.create.__self__.calls[0]  # type: ignore[attr-defined]
    assert call["model"] == "test-model"
    assert call["response_model"] is KCSkill
    assert "root----A" in call["messages"][0]["content"]
    assert "题目 1" in call["messages"][0]["content"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run python -m pytest tests/test_kc_skills_build.py::test_generate_kc_skill_renders_prompt_and_uses_schema -v`
Expected: FAIL — `generate_kc_skill` not defined.

- [ ] **Step 3: Implement**

Append to `run/build_kc_skills.py`:

```python
from src.llm.schemas import KCSkill
from src.prompts.loader import render_prompt


def generate_kc_skill(
    *,
    route: str,
    samples: list[dict],
    total_count: int,
    client,
    model: str,
) -> KCSkill:
    """Render the prompt and call the LLM with Instructor response_model=KCSkill.

    `client` must be an instructor-wrapped client (or fake with the same
    .chat.completions.create(..., response_model=...) contract).
    """
    prompt = render_prompt(
        "kc_skill_from_bank",
        route=route,
        samples=samples,
        sample_count=len(samples),
        total_count=total_count,
    )
    return client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        response_model=KCSkill,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_kc_skills_build.py::test_generate_kc_skill_renders_prompt_and_uses_schema -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run/build_kc_skills.py tests/test_kc_skills_build.py
git commit -m "feat(kc-skills): generate_kc_skill LLM call wrapper with KCSkill response model"
```

---

## Task 6: KC id 反查（TDD）

**Files:**
- Modify: `run/build_kc_skills.py`
- Modify: `tests/test_kc_skills_build.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kc_skills_build.py`:

```python
from run.build_kc_skills import build_leaf_to_ids, lookup_kc_ids_for_route


def test_build_leaf_to_ids_inverts_map():
    kc_map = {"0": "乘法原理里的其他类型", "1": "加乘原理综合"}
    result = build_leaf_to_ids(kc_map)
    assert result == {"乘法原理里的其他类型": [0], "加乘原理综合": [1]}


def test_lookup_kc_ids_returns_empty_for_unknown_leaf():
    leaf_to_ids = {"乘法原理里的其他类型": [0]}
    route = "拓展思维----计数模块----加乘原理----乘法原理----未登记的 leaf"
    assert lookup_kc_ids_for_route(route, leaf_to_ids) == []


def test_lookup_kc_ids_returns_match_for_known_leaf():
    leaf_to_ids = {"乘法原理里的其他类型": [0]}
    route = "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型"
    assert lookup_kc_ids_for_route(route, leaf_to_ids) == [0]
```

- [ ] **Step 2: Confirm failure**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v -k "kc_ids or leaf_to_ids"`
Expected: FAIL — symbols missing.

- [ ] **Step 3: Implement**

Append to `run/build_kc_skills.py`:

```python
def build_leaf_to_ids(kc_routes_map: dict[str, str]) -> dict[str, list[int]]:
    """Invert kc_routes_map.json (id -> leaf_name) to leaf_name -> [ids].

    Values in the source map are globally unique leaf names in the XES3G5M
    dataset, so lists usually have length 1. Built as list to be safe.
    """
    result: dict[str, list[int]] = {}
    for k, v in kc_routes_map.items():
        result.setdefault(v, []).append(int(k))
    return result


def lookup_kc_ids_for_route(route: str, leaf_to_ids: dict[str, list[int]]) -> list[int]:
    """Return the KC ids for the leaf of `route`, or [] if unknown."""
    leaf = route.rsplit("----", 1)[-1]
    return list(leaf_to_ids.get(leaf, []))
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add run/build_kc_skills.py tests/test_kc_skills_build.py
git commit -m "feat(kc-skills): build_leaf_to_ids and lookup_kc_ids_for_route"
```

---

## Task 7: 重试装饰器（TDD）

**Files:**
- Modify: `run/build_kc_skills.py`
- Modify: `tests/test_kc_skills_build.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kc_skills_build.py`:

```python
import asyncio

from run.build_kc_skills import retry_with_backoff


def test_retry_with_backoff_succeeds_first_try():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        return "ok"

    result = asyncio.run(retry_with_backoff(fn, attempts=3, base_delay=0))
    assert result == "ok"
    assert calls["n"] == 1


def test_retry_with_backoff_retries_then_succeeds():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = asyncio.run(retry_with_backoff(fn, attempts=3, base_delay=0))
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_with_backoff_raises_after_exhaustion():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        asyncio.run(retry_with_backoff(fn, attempts=3, base_delay=0))
    assert calls["n"] == 3
```

- [ ] **Step 2: Confirm failure**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v -k retry`
Expected: FAIL — `retry_with_backoff` missing.

- [ ] **Step 3: Implement**

Append to `run/build_kc_skills.py`:

```python
import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
) -> T:
    """Call `fn()` up to `attempts` times with exponential backoff (2s/4s/8s)."""
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — propagate after exhaustion
            last_exc = exc
            if i == attempts - 1:
                break
            delay = base_delay * (2**i)
            logger.warning("attempt %d/%d failed: %s — retrying in %.1fs", i + 1, attempts, exc, delay)
            if delay > 0:
                await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add run/build_kc_skills.py tests/test_kc_skills_build.py
git commit -m "feat(kc-skills): async retry_with_backoff helper"
```

---

## Task 8: CLI 入口 + 并发编排（不打真实 API）

This task wires the existing helpers into an asyncio orchestrator with a `main()` entry point. The orchestration itself is tested end-to-end with a fake LLM client; a real API call is deferred to Task 9 (smoke test).

**Files:**
- Modify: `run/build_kc_skills.py`
- Modify: `tests/test_kc_skills_build.py`

- [ ] **Step 1: Write the failing end-to-end orchestration test**

Append to `tests/test_kc_skills_build.py`:

```python
from run.build_kc_skills import run_build


def test_run_build_processes_all_routes_and_writes_outputs(tmp_path, monkeypatch):
    questions = {
        "0": {
            "content": "c0",
            "options": {},
            "answer": ["a0"],
            "analysis": "x",
            "type": "填空",
            "kc_routes": ["root----A"],
        },
        "1": {
            "content": "c1",
            "options": {},
            "answer": ["a1"],
            "analysis": "y",
            "type": "单选",
            "kc_routes": ["root----B"],
        },
    }
    kc_map = {"0": "A", "1": "B"}

    questions_path = tmp_path / "questions.json"
    kc_map_path = tmp_path / "kc_routes_map.json"
    out_path = tmp_path / "kc_skills.json"
    manifest_path = tmp_path / "kc_skills.manifest.json"
    questions_path.write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")
    kc_map_path.write_text(json.dumps(kc_map, ensure_ascii=False), encoding="utf-8")

    fake_skill = KCSkill(
        考察重点="r1", 考察形式="r2", 易错点="r3", 难度提示="r4",
    )
    client = _FakeClient(fake_skill)

    asyncio.run(run_build(
        questions_path=questions_path,
        kc_routes_map_path=kc_map_path,
        out_path=out_path,
        manifest_path=manifest_path,
        client=client,
        model="fake-model",
        concurrency=2,
        limit=None,
        only_module=None,
        resume=False,
    ))

    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert set(out.keys()) == {"root----A", "root----B"}
    entry_a = out["root----A"]
    assert entry_a["route"] == "root----A"
    assert entry_a["leaf"] == "A"
    assert entry_a["kc_ids"] == [0]
    assert entry_a["question_count"] == 1
    assert entry_a["sampled_count"] == 1
    assert entry_a["sampled_question_ids"] == ["0"]
    assert entry_a["skill"] == fake_skill.model_dump()
    assert entry_a["source_model"] == "fake-model"
    assert "generated_at" in entry_a

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest["completed_routes"]) == {"root----A", "root----B"}
    assert manifest["failed"] == []


def test_run_build_records_failures_and_continues(tmp_path):
    questions = {
        "0": {"content": "c0", "options": {}, "answer": ["a0"], "analysis": "x", "type": "填空", "kc_routes": ["root----A"]},
        "1": {"content": "c1", "options": {}, "answer": ["a1"], "analysis": "y", "type": "填空", "kc_routes": ["root----B"]},
    }
    questions_path = tmp_path / "questions.json"
    kc_map_path = tmp_path / "kc_routes_map.json"
    out_path = tmp_path / "out.json"
    manifest_path = tmp_path / "m.json"
    questions_path.write_text(json.dumps(questions), encoding="utf-8")
    kc_map_path.write_text(json.dumps({}), encoding="utf-8")

    class _FailingCompletions:
        def create(self, **_):
            raise RuntimeError("always fails")

    class _FailingForOneRoute:
        """Fail only on route A, succeed on route B."""
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            if "root----A" in prompt:
                raise RuntimeError("fail A")
            return KCSkill(考察重点="ok", 考察形式="ok", 易错点="ok", 难度提示="ok")

    client = _FailingForOneRoute()

    asyncio.run(run_build(
        questions_path=questions_path,
        kc_routes_map_path=kc_map_path,
        out_path=out_path,
        manifest_path=manifest_path,
        client=client,
        model="m",
        concurrency=2,
        limit=None,
        only_module=None,
        resume=False,
        retry_base_delay=0,
    ))

    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert list(out.keys()) == ["root----B"]  # A failed

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["completed_routes"] == ["root----B"]
    failed_routes = [f["route"] for f in manifest["failed"]]
    assert failed_routes == ["root----A"]
    assert manifest["failed"][0]["attempts"] == 3


def test_run_build_resume_skips_completed_routes(tmp_path):
    questions = {
        "0": {"content": "c0", "options": {}, "answer": ["a0"], "analysis": "x", "type": "填空", "kc_routes": ["root----A"]},
    }
    questions_path = tmp_path / "questions.json"
    kc_map_path = tmp_path / "kc_routes_map.json"
    out_path = tmp_path / "out.json"
    manifest_path = tmp_path / "m.json"
    questions_path.write_text(json.dumps(questions), encoding="utf-8")
    kc_map_path.write_text(json.dumps({}), encoding="utf-8")
    # pre-seed manifest + output
    out_path.write_text(json.dumps({"root----A": {"skill": {"pre": "existing"}}}), encoding="utf-8")
    manifest_path.write_text(json.dumps({
        "version": 1,
        "completed_routes": ["root----A"],
        "failed": [],
    }), encoding="utf-8")

    class _ShouldNotCall:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **_):
            raise AssertionError("LLM must not be called for a completed route")

    asyncio.run(run_build(
        questions_path=questions_path,
        kc_routes_map_path=kc_map_path,
        out_path=out_path,
        manifest_path=manifest_path,
        client=_ShouldNotCall(),
        model="m",
        concurrency=1,
        limit=None,
        only_module=None,
        resume=True,
    ))

    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert out == {"root----A": {"skill": {"pre": "existing"}}}
```

- [ ] **Step 2: Confirm failure**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v -k run_build`
Expected: FAIL — `run_build` missing.

- [ ] **Step 3: Implement `run_build` and `main`**

Append to `run/build_kc_skills.py`:

```python
import argparse
import sys
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _process_one_route(
    *,
    route: str,
    all_route_items: list[dict],
    leaf_to_ids: dict[str, list[int]],
    client,
    model: str,
    cap: int,
    retry_base_delay: float,
) -> dict:
    """Run sampling + LLM for a single route. Returns the output entry on
    success; raises on final failure (caller records the failure)."""
    samples = sample_questions(all_route_items, cap=cap)

    async def _call():
        return await asyncio.to_thread(
            generate_kc_skill,
            route=route,
            samples=samples,
            total_count=len(all_route_items),
            client=client,
            model=model,
        )

    skill = await retry_with_backoff(_call, attempts=3, base_delay=retry_base_delay)
    leaf = route.rsplit("----", 1)[-1]
    return {
        "route": route,
        "leaf": leaf,
        "kc_ids": lookup_kc_ids_for_route(route, leaf_to_ids),
        "question_count": len(all_route_items),
        "sampled_count": len(samples),
        "sampled_question_ids": [s["id"] for s in samples],
        "skill": skill.model_dump(),
        "source_model": model,
        "generated_at": _now_iso(),
    }


async def run_build(
    *,
    questions_path: Path,
    kc_routes_map_path: Path,
    out_path: Path,
    manifest_path: Path,
    client,
    model: str,
    concurrency: int = 8,
    cap: int = 12,
    limit: int | None = None,
    only_module: str | None = None,
    resume: bool = True,
    retry_base_delay: float = 2.0,
) -> None:
    """Main orchestration: load data → fan out routes under a semaphore →
    write each successful result to `out_path` atomically; record failures
    in `manifest_path`."""
    questions = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    kc_routes_map = json.loads(Path(kc_routes_map_path).read_text(encoding="utf-8"))
    leaf_to_ids = build_leaf_to_ids(kc_routes_map)
    groups = group_questions_by_route(questions)

    if only_module:
        groups = {
            r: items for r, items in groups.items()
            if len(r.split("----")) >= 2 and r.split("----")[1] == only_module
        }

    manifest = load_manifest(manifest_path) if resume else {
        "version": 1, "completed_routes": [], "failed": [],
    }
    manifest.setdefault("completed_routes", [])
    manifest.setdefault("failed", [])
    manifest["model"] = model
    manifest.setdefault("started_at", _now_iso())

    completed_set = set(manifest["completed_routes"])
    todo_routes = [r for r in groups.keys() if r not in completed_set]
    if limit is not None:
        todo_routes = todo_routes[:limit]

    output = json.loads(Path(out_path).read_text(encoding="utf-8")) if Path(out_path).exists() else {}
    output_lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)
    total = len(todo_routes)

    async def _worker(idx: int, route: str) -> None:
        async with sem:
            try:
                entry = await _process_one_route(
                    route=route,
                    all_route_items=groups[route],
                    leaf_to_ids=leaf_to_ids,
                    client=client,
                    model=model,
                    cap=cap,
                    retry_base_delay=retry_base_delay,
                )
            except Exception as exc:  # noqa: BLE001
                async with output_lock:
                    manifest["failed"].append({
                        "route": route,
                        "error": str(exc),
                        "attempts": 3,
                        "last_at": _now_iso(),
                    })
                    save_manifest(manifest_path, manifest)
                print(f"[{idx}/{total}] {route} ✗ {exc}", file=sys.stderr)
                return
            async with output_lock:
                output[route] = entry
                manifest["completed_routes"].append(route)
                atomic_write_json(out_path, output)
                save_manifest(manifest_path, manifest)
            print(f"[{idx}/{total}] {route} ✓", file=sys.stderr)

    await asyncio.gather(*[_worker(i + 1, r) for i, r in enumerate(todo_routes)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline KC-skill generator for XES3G5M")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="only process the first N routes")
    parser.add_argument("--only-module", type=str, default=None, help="filter by route's 2nd segment, e.g. 计数模块")
    parser.add_argument("--no-resume", action="store_true", help="overwrite manifest instead of resuming")
    parser.add_argument("--questions", type=Path, default=Path("XES3G5M/metadata/questions.json"))
    parser.add_argument("--kc-map", type=Path, default=Path("XES3G5M/metadata/kc_routes_map.json"))
    parser.add_argument("--out", type=Path, default=Path("XES3G5M/metadata/kc_skills.json"))
    parser.add_argument("--manifest", type=Path, default=Path("XES3G5M/metadata/kc_skills.manifest.json"))
    args = parser.parse_args(argv)

    from src.llm.client import get_llm_client, get_model_name

    client = get_llm_client()
    model = get_model_name()

    asyncio.run(run_build(
        questions_path=args.questions,
        kc_routes_map_path=args.kc_map,
        out_path=args.out,
        manifest_path=args.manifest,
        client=client,
        model=model,
        concurrency=args.concurrency,
        limit=args.limit,
        only_module=args.only_module,
        resume=not args.no_resume,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all tests in the file**

Run: `uv run python -m pytest tests/test_kc_skills_build.py -v`
Expected: all tests pass (including the three `run_build` tests).

- [ ] **Step 5: Run full test suite to catch collateral damage**

Run: `uv run python -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Lint**

Run: `uv run ruff check run/build_kc_skills.py tests/test_kc_skills_build.py`
Expected: no errors. If ruff flags the bare `except Exception`, keep the `# noqa: BLE001` comment that's already there.

- [ ] **Step 7: Commit**

```bash
git add run/build_kc_skills.py tests/test_kc_skills_build.py
git commit -m "feat(kc-skills): async CLI orchestrator with retry and resume"
```

---

## Task 9: 冒烟测试（`--limit 5` 真实 API 调用）

**Goal:** Verify the script actually talks to the LLM end-to-end before spending the full budget.

- [ ] **Step 1: Confirm .env has credentials**

Run: `test -f .env && grep -q OPENAI_API_KEY .env && echo ok`
Expected: `ok`. If missing, ask the user to populate `.env` before continuing.

- [ ] **Step 2: Dry-run limited generation**

Run: `uv run python run/build_kc_skills.py --limit 5 --concurrency 2 --no-resume`
Expected:
- 5 progress lines on stderr like `[1/5] <route> ✓`
- `XES3G5M/metadata/kc_skills.json` created with exactly 5 entries
- `XES3G5M/metadata/kc_skills.manifest.json` has `completed_routes` of length 5 and `failed == []` (or small number with clear error)

- [ ] **Step 3: Spot-check the output quality manually**

Run: `uv run python -c "import json; d=json.load(open('XES3G5M/metadata/kc_skills.json', encoding='utf-8')); import itertools; [print('---', r, '\n', json.dumps(e['skill'], ensure_ascii=False, indent=2)) for r, e in itertools.islice(d.items(), 5)]"`

Verify by reading the output:
- Each of the 4 fields is populated and in Chinese
- 考察重点 is 80-150 字 (approx, eyeball it)
- 易错点 is 50-120 字
- 难度提示 is 50-100 字
- No markdown headers leaked into values, no "本知识点" openers

If the output looks wrong, adjust `src/prompts/kc_skill_from_bank.j2`, remove the generated files, and re-run Step 2.

- [ ] **Step 4: Delete the smoke-test artifacts so they don't get committed prematurely**

Run: `rm XES3G5M/metadata/kc_skills.json XES3G5M/metadata/kc_skills.manifest.json`
Expected: both files gone.

---

## Task 10: 单模块试跑 `--only-module 计数模块`

**Goal:** One more gated run before full execution, to verify `--only-module` filters correctly and to measure realistic speed/cost on ~100 routes.

- [ ] **Step 1: Run with module filter**

Run: `uv run python run/build_kc_skills.py --only-module 计数模块 --concurrency 8`
Expected: progress lines scroll past; ends with `[N/N] <route> ✓` where N is the number of 计数 KCs (roughly 100).

- [ ] **Step 2: Validate output**

Run: `uv run python -c "import json; d=json.load(open('XES3G5M/metadata/kc_skills.json', encoding='utf-8')); print('routes:', len(d)); print('all 计数:', all('计数' in r for r in d.keys()))"`
Expected: `routes: ~100` and `all 计数: True`.

- [ ] **Step 3: Check manifest for failures**

Run: `uv run python -c "import json; m=json.load(open('XES3G5M/metadata/kc_skills.manifest.json', encoding='utf-8')); print('completed:', len(m['completed_routes']), 'failed:', len(m['failed']))"`
Expected: `failed` should be empty or very small; if > 5% log the errors.

- [ ] **Step 4: Rerun with --no-resume to start fresh for full run**

Run: `rm XES3G5M/metadata/kc_skills.json XES3G5M/metadata/kc_skills.manifest.json`

---

## Task 11: 全量生产运行

**Goal:** Generate all ~1240 KC skills.

- [ ] **Step 1: Full run**

Run: `uv run python run/build_kc_skills.py --concurrency 8 2>&1 | tee /tmp/kc_skills_run.log`
Expected: ~10-20 minutes; last line is approximately `[1240/1240] <route> ✓`.

- [ ] **Step 2: Check for failed routes and retry them**

Run: `uv run python -c "import json; m=json.load(open('XES3G5M/metadata/kc_skills.manifest.json', encoding='utf-8')); print('failed:', [f['route'] for f in m['failed']])"`

- If `failed` is non-empty, remove the failed entries from `completed_routes` (they aren't there — just ensure they are missing from `kc_skills.json`), clear the `failed` list in the manifest, and rerun:

```bash
uv run python -c "
import json, pathlib
p = pathlib.Path('XES3G5M/metadata/kc_skills.manifest.json')
m = json.loads(p.read_text(encoding='utf-8'))
m['failed'] = []
p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
"
uv run python run/build_kc_skills.py --concurrency 4
```

Repeat until `failed == []`.

- [ ] **Step 3: Sanity-check totals**

Run: `uv run python -c "
import json
q = json.load(open('XES3G5M/metadata/questions.json', encoding='utf-8'))
out = json.load(open('XES3G5M/metadata/kc_skills.json', encoding='utf-8'))
distinct_routes = {r for qobj in q.values() for r in qobj.get('kc_routes', [])}
print('distinct routes in bank:', len(distinct_routes))
print('entries in kc_skills.json:', len(out))
print('missing:', sorted(distinct_routes - set(out.keys()))[:5])
"`
Expected: `distinct routes in bank == entries in kc_skills.json`, `missing: []`.

- [ ] **Step 4: Check file size is in range**

Run: `ls -lh XES3G5M/metadata/kc_skills.json`
Expected: 1-2 MB.

---

## Task 12: 提交产出物 & 文档更新

**Files:**
- `XES3G5M/metadata/kc_skills.json`
- `XES3G5M/metadata/kc_skills.manifest.json`

- [ ] **Step 1: Verify the spec directory is clean**

Run: `git status XES3G5M/metadata/`
Expected: two untracked files: `kc_skills.json` and `kc_skills.manifest.json`.

- [ ] **Step 2: Commit outputs**

```bash
git add XES3G5M/metadata/kc_skills.json XES3G5M/metadata/kc_skills.manifest.json
git commit -m "data(xes3g5m): generate KC skills for 1240 routes via offline LLM pass"
```

- [ ] **Step 3: Final sanity check — full test suite one more time**

Run: `uv run python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 4: Verify the commit graph is clean**

Run: `git log --oneline -15`
Expected: feat commits for Tasks 1-8 followed by the data commit from Task 12. No amends, no reverts.

---

## Done Criteria

- ✅ `KCSkill` importable from `src.llm.schemas`
- ✅ `kc_skill_from_bank.j2` renders with correct fields
- ✅ `run/build_kc_skills.py --limit 5` produces 5 valid entries in under 30 s
- ✅ Full run produces 1 entry per distinct route in `questions.json`
- ✅ `tests/test_kc_skills_build.py` + existing test suites all pass
- ✅ `ruff check` clean on all touched files
- ✅ `kc_skills.json` committed to git, size 1-2 MB
- ✅ No modifications to `src/agent/**` or any runtime code path

## Out of Scope (per spec §1)

- ❌ Wiring `kc_skills.json` into quiz / lesson nodes (next iteration)
- ❌ Training KT or embedding models
- ❌ Any changes to `knowledge_skill.j2` or `lesson.generate_knowledge_skill()`
