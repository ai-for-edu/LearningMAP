# PPT 幻灯片教案生成 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 新增 PPT 格式教案，参考 OpenMAIC 的 slide 生成系统，用户可选择 Markdown 或 PPT 格式

**Architecture:** LLM 生成 PPT 元素 JSON（文本、LaTeX、形状、线条），前端用 HTML/CSS absolute 定位渲染，与 OpenMAIC 一致。两阶段生成：先大纲再逐页内容。

**Tech Stack:** Jinja2 prompt 模板, OpenAI API (instructor), KaTeX CDN, HTML/CSS 渲染

---

## Task 1: PPT 元素 Pydantic 模型

**Files:**
- Create: `src/llm/slide_schemas.py`
- Test: `tests/test_slide_schemas.py`

**Step 1: 写测试**

```python
# tests/test_slide_schemas.py
from src.llm.slide_schemas import (
    SlideData, TextElement, LatexElement, ShapeElement, LineElement, SlideOutline
)


def test_text_element():
    el = TextElement(
        id="text_001", type="text", left=60, top=80, width=880, height=76,
        content='<p style="font-size:32px;"><strong>Title</strong></p>',
        defaultFontName="", defaultColor="#333333",
    )
    assert el.type == "text"
    assert el.width == 880


def test_latex_element():
    el = LatexElement(
        id="latex_001", type="latex", left=100, top=200, width=300, height=120,
        latex="a^2 + b^2 = c^2", color="#000000",
    )
    assert el.latex == "a^2 + b^2 = c^2"


def test_shape_element():
    el = ShapeElement(
        id="shape_001", type="shape", left=60, top=200, width=400, height=100,
        path="M 0 0 L 1 0 L 1 1 L 0 1 Z", viewBox=[1, 1],
        fill="#5b9bd5", fixedRatio=False,
    )
    assert el.fill == "#5b9bd5"


def test_slide_data():
    slide = SlideData(
        background={"type": "solid", "color": "#ffffff"},
        elements=[
            TextElement(id="t1", type="text", left=60, top=80, width=880, height=76,
                        content="<p>Title</p>", defaultFontName="", defaultColor="#333"),
        ],
    )
    assert len(slide.elements) == 1


def test_slide_outline():
    outline = SlideOutline(
        title="勾股定理概念", description="介绍勾股定理的定义", keyPoints=["a²+b²=c²", "直角三角形"],
    )
    assert outline.title == "勾股定理概念"
```

**Step 2: 运行测试，确认失败**

Run: `cd /Users/wentaohu/project/openail && uv run python -m pytest tests/test_slide_schemas.py -v`

**Step 3: 实现模型**

```python
# src/llm/slide_schemas.py
"""Pydantic models for PPT slide elements, matching OpenMAIC's schema."""
from __future__ import annotations

from typing import Literal, Union
from pydantic import BaseModel


class TextElement(BaseModel):
    id: str
    type: Literal["text"]
    left: float
    top: float
    width: float
    height: float
    content: str  # HTML content
    defaultFontName: str = ""
    defaultColor: str = "#333333"
    rotate: float = 0
    lineHeight: float = 1.5
    opacity: float = 1
    fill: str | None = None


class LatexElement(BaseModel):
    id: str
    type: Literal["latex"]
    left: float
    top: float
    width: float
    height: float
    latex: str
    color: str = "#000000"
    align: str = "center"


class ShapeElement(BaseModel):
    id: str
    type: Literal["shape"]
    left: float
    top: float
    width: float
    height: float
    path: str  # SVG path
    viewBox: list[float]
    fill: str = "#5b9bd5"
    fixedRatio: bool = False


class LineElement(BaseModel):
    id: str
    type: Literal["line"]
    left: float
    top: float
    width: float  # stroke thickness, NOT length
    start: list[float]
    end: list[float]
    style: str = "solid"
    color: str = "#333333"
    points: list[str] = ["", ""]


class ImageElement(BaseModel):
    id: str
    type: Literal["image"]
    left: float
    top: float
    width: float
    height: float
    src: str
    fixedRatio: bool = True


PPTElement = Union[TextElement, LatexElement, ShapeElement, LineElement, ImageElement]


class SlideData(BaseModel):
    background: dict | None = None
    elements: list[PPTElement] = []


class SlideOutline(BaseModel):
    title: str
    description: str
    keyPoints: list[str]
```

**Step 4: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_slide_schemas.py -v`

**Step 5: Commit**

```bash
git add src/llm/slide_schemas.py tests/test_slide_schemas.py
git commit -m "feat: add PPT slide element Pydantic models"
```

---

## Task 2: Prompt 模板（参考 OpenMAIC）

**Files:**
- Create: `src/prompts/slide_outline.j2`
- Create: `src/prompts/slide_content.j2`

**Step 1: 大纲生成 prompt**

```jinja2
{# src/prompts/slide_outline.j2 #}
You are an educational slide planner. Given a topic and weak knowledge points, create a slide outline for a personalized lesson.

Topic: {{ topic }}

{% if weak_points %}
Weak areas to focus on:
{% for point in weak_points %}
- {{ point.name }}: {{ point.description }}
{% endfor %}
{% else %}
Review all knowledge points for consolidation.
{% endif %}

Generate 3-6 slides covering the weak areas. Each slide should have:
- title: slide title
- description: what this slide teaches
- keyPoints: 3-5 key points for this slide

Output pure JSON array. No explanations, no code blocks.

Example: [{"title":"概念介绍","description":"解释核心概念","keyPoints":["要点1","要点2","要点3"]}]

Language: Use Chinese for all content.
```

**Step 2: 单页内容生成 prompt**

将 OpenMAIC 的完整 slide-content system prompt（约 960 行）适配为 Jinja2 模板。保留核心的元素规格、文本高度表、设计规则、Pre-Output Checklist。

创建 `src/prompts/slide_content_system.j2`（system prompt）和 `src/prompts/slide_content_user.j2`（user prompt）。

system prompt 直接复用 OpenMAIC 的规格，但移除 Image/Video/Chart/Table 相关段落（MVP 不支持），只保留 TextElement、LatexElement、ShapeElement、LineElement。

user prompt:

```jinja2
{# src/prompts/slide_content_user.j2 #}
# Generation Requirements

## Scene Information

- **Title**: {{ title }}
- **Description**: {{ description }}
- **Key Points**:
{% for point in key_points %}
  - {{ point }}
{% endfor %}

## Canvas Size

- **Canvas Size**: 1000 × 562.5 px

## Output Requirements

Generate a complete PPT slide for one page.

**Language**: All text content must be in Chinese.

**Must Follow**:
1. Output pure JSON directly, without any explanation
2. Do not wrap with code blocks
3. Ensure JSON format is correct
4. All TextElement height values must come from the height lookup table

**Output Structure**:
{"background":{"type":"solid","color":"#ffffff"},"elements":[...]}
```

**Step 3: Commit**

```bash
git add src/prompts/slide_outline.j2 src/prompts/slide_content_system.j2 src/prompts/slide_content_user.j2
git commit -m "feat: add slide generation prompt templates"
```

---

## Task 3: 幻灯片生成节点

**Files:**
- Create: `src/agent/nodes/slides.py`
- Modify: `src/agent/state.py`
- Modify: `src/api/chat.py`

**Step 1: 扩展 LearningState**

在 `src/agent/state.py` 中添加：

```python
slide_outlines: list | None  # SlideOutline list
slide_contents: list | None  # SlideData list (one per outline)
lesson_format: str | None    # "markdown" | "slides"
```

**Step 2: 实现 slides 节点**

```python
# src/agent/nodes/slides.py
from __future__ import annotations

import json
import logging
from src.llm.client import get_raw_client, get_llm_client, get_model_name
from src.llm.slide_schemas import SlideData, SlideOutline
from src.prompts.loader import render_prompt

logger = logging.getLogger(__name__)


def generate_slide_outlines(state: dict) -> list[SlideOutline]:
    """Generate slide outlines from diagnosis results."""
    topic = state.get("topic", "")
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")

    weak_points = []
    if diagnosis:
        breakdown = diagnosis.breakdown if hasattr(diagnosis, "breakdown") else diagnosis.get("breakdown", [])
        nodes = graph.nodes if graph and hasattr(graph, "nodes") else (graph.get("nodes", []) if graph else [])
        node_map = {(n.name if hasattr(n, "name") else n["name"]): n for n in nodes}
        for b in breakdown:
            status = b.status if hasattr(b, "status") else b.get("status", "")
            name = b.name if hasattr(b, "name") else b.get("name", "")
            if status == "weak":
                node = node_map.get(name)
                desc = (node.description if hasattr(node, "description") else node.get("description", "")) if node else ""
                weak_points.append({"name": name, "description": desc})

    prompt = render_prompt("slide_outline", topic=topic, weak_points=weak_points)

    try:
        client = get_llm_client()
        outlines = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            response_model=list[SlideOutline],
        )
        return outlines
    except Exception:
        logger.exception("Slide outline generation failed")
        return []


def generate_slide_content(outline: SlideOutline) -> SlideData | None:
    """Generate single slide content from outline."""
    system_prompt = render_prompt("slide_content_system")
    user_prompt = render_prompt(
        "slide_content_user",
        title=outline.title,
        description=outline.description,
        key_points=outline.keyPoints,
    )

    try:
        client = get_raw_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content.strip()
        # Parse JSON — LLM may wrap in code blocks
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(content)
        return SlideData(**data)
    except Exception:
        logger.exception("Slide content generation failed for: %s", outline.title)
        return None
```

**Step 3: 新增 API 端点**

在 `src/api/chat.py` 中添加：

```python
@router.post("/chat/{session_id}/generate-slides")
async def generate_slides(session_id: str) -> dict:
    """Generate slide outlines + all slide contents."""
    state = _get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.agent.nodes.slides import generate_slide_outlines, generate_slide_content

    # Stage 1: Generate outlines
    outlines = await asyncio.to_thread(generate_slide_outlines, state)
    if not outlines:
        return {"session_id": session_id, "error": "Failed to generate outlines"}

    # Stage 2: Generate content for each slide
    slides = []
    for outline in outlines:
        slide = await asyncio.to_thread(generate_slide_content, outline)
        if slide:
            slides.append(slide.model_dump())
        else:
            slides.append(None)

    state["slide_outlines"] = [o.model_dump() for o in outlines]
    state["slide_contents"] = slides
    state["lesson_format"] = "slides"
    _save(session_id, state)

    return {
        "session_id": session_id,
        "outlines": [o.model_dump() for o in outlines],
        "slides": slides,
    }
```

**Step 4: Commit**

```bash
git add src/agent/nodes/slides.py src/agent/state.py src/api/chat.py
git commit -m "feat: slide generation node + API endpoint"
```

---

## Task 4: 前端 — slides.html 渲染页面

**Files:**
- Create: `static/slides.html`

**Step 1: 创建 PPT 渲染页面**

页面结构：
- 顶部导航栏（Slide Mode | Back to Chat）
- 中央：1000×562.5 的 slide 容器，CSS 缩放适配屏幕
- 底部：翻页按钮 + 页码 + 缩略图导航
- 每个元素用 `position: absolute` + `left/top/width/height` 定位

核心渲染逻辑：

```javascript
function renderSlide(container, slideData) {
  container.innerHTML = '';

  // Background
  if (slideData.background) {
    container.style.background = slideData.background.color || '#ffffff';
  }

  // Render each element
  for (const el of slideData.elements) {
    const div = document.createElement('div');
    div.style.position = 'absolute';
    div.style.left = el.left + 'px';
    div.style.top = el.top + 'px';
    div.style.width = el.width + 'px';
    div.style.height = el.height + 'px';

    switch (el.type) {
      case 'text':
        div.innerHTML = el.content;
        div.style.padding = '10px';
        div.style.lineHeight = (el.lineHeight || 1.5).toString();
        div.style.color = el.defaultColor || '#333';
        if (el.fill) div.style.backgroundColor = el.fill;
        if (el.opacity !== undefined) div.style.opacity = el.opacity;
        break;

      case 'latex':
        if (typeof katex !== 'undefined') {
          div.innerHTML = katex.renderToString(el.latex, {
            throwOnError: false, displayMode: true
          });
          div.style.display = 'flex';
          div.style.alignItems = 'center';
          div.style.justifyContent = el.align || 'center';
        } else {
          div.textContent = el.latex;
        }
        break;

      case 'shape':
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', `0 0 ${el.viewBox[0]} ${el.viewBox[1]}`);
        svg.style.width = '100%';
        svg.style.height = '100%';
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', el.path);
        path.setAttribute('fill', el.fill || '#5b9bd5');
        svg.appendChild(path);
        div.appendChild(svg);
        break;

      case 'line':
        renderLine(div, el);
        break;
    }

    if (el.rotate) div.style.transform = `rotate(${el.rotate}deg)`;
    container.appendChild(div);
  }
}
```

页面加载时：
1. 调 `GET /api/chat/{id}/state` 检查是否有 slide_contents
2. 没有则调 `POST /api/chat/{id}/generate-slides`（显示蓝色加载圈）
3. 渲染第一页，左右键翻页

**Step 2: Commit**

```bash
git add static/slides.html
git commit -m "feat: add PPT slide viewer page"
```

---

## Task 5: 前端 — 格式选择 + 跳转

**Files:**
- Modify: `static/index.html`
- Modify: `static/lesson.html`

**Step 1: 修改 Start Learning 按钮**

诊断结果中的 "Start Learning" 按钮改为弹窗让用户选择格式：

```javascript
function showLessonFormatChoice(sessionId) {
  const overlay = document.createElement('div');
  overlay.className = 'fixed inset-0 bg-black/30 flex items-center justify-center z-[100]';
  overlay.innerHTML = `
    <div class="bg-white rounded-3xl p-8 max-w-md mx-4 shadow-xl">
      <h3 class="font-headline font-bold text-lg text-on-surface mb-2">选择学习方式</h3>
      <p class="font-body text-sm text-on-surface-variant mb-6">选择教案格式：</p>
      <div class="flex gap-4">
        <button id="chooseMarkdown" class="flex-1 flex flex-col items-center gap-3 p-6 rounded-2xl border-2 border-slate-200 hover:border-primary hover:bg-primary/5 transition-all">
          <span class="material-symbols-outlined text-3xl text-primary">article</span>
          <span class="font-headline font-bold text-sm">Markdown</span>
          <span class="text-xs text-on-surface-variant">文字教案，支持笔记</span>
        </button>
        <button id="chooseSlides" class="flex-1 flex flex-col items-center gap-3 p-6 rounded-2xl border-2 border-slate-200 hover:border-primary hover:bg-primary/5 transition-all">
          <span class="material-symbols-outlined text-3xl text-primary">slideshow</span>
          <span class="font-headline font-bold text-sm">PPT 幻灯片</span>
          <span class="text-xs text-on-surface-variant">演示文稿式教案</span>
        </button>
      </div>
      <button id="cancelChoice" class="w-full mt-4 text-sm text-on-surface-variant hover:bg-slate-100 py-2 rounded-xl">Cancel</button>
    </div>`;
  document.body.appendChild(overlay);

  document.getElementById('chooseMarkdown').onclick = () => {
    overlay.remove();
    window.location.href = 'lesson.html?session_id=' + sessionId;
  };
  document.getElementById('chooseSlides').onclick = () => {
    overlay.remove();
    window.location.href = 'slides.html?session_id=' + sessionId;
  };
  document.getElementById('cancelChoice').onclick = () => overlay.remove();
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
}
```

**Step 2: Commit**

```bash
git add static/index.html static/lesson.html
git commit -m "feat: add lesson format choice (Markdown / PPT)"
```

---

## Task 6: State API + 持久化

**Files:**
- Modify: `src/api/chat.py` (state endpoint)
- Modify: `src/api/store.py` (serialization)

**Step 1: state API 返回 slide 数据**

```python
# In get_state()
if state.get("slide_contents"):
    result["slide_contents"] = state["slide_contents"]
    result["slide_outlines"] = state.get("slide_outlines")
    result["lesson_format"] = state.get("lesson_format")
```

**Step 2: store.py 序列化**

slide_contents 是 list[dict]，已经是 JSON 可序列化的，不需要特殊处理。

**Step 3: Commit**

```bash
git add src/api/chat.py src/api/store.py
git commit -m "feat: persist and serve slide data in state API"
```

---

## Task 7: 测试 + 验证

**Step 1: 运行全部测试**

Run: `uv run python -m pytest tests/ -v`

**Step 2: 端到端手动测试**

1. 打开 http://localhost:8080
2. 输入"我想学勾股定理" → 生成知识图谱
3. Start Diagnosis → 答题 → View Results
4. Start Learning → 选择 PPT → 蓝色加载圈 → 看到幻灯片
5. 左右翻页，检查元素渲染

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: slide generation feature complete"
```

---

## 任务依赖关系

```
Task 1 (Pydantic 模型)
  └─► Task 2 (Prompt 模板)
       └─► Task 3 (生成节点 + API)
            ├─► Task 4 (slides.html 渲染页)
            ├─► Task 5 (格式选择弹窗)
            └─► Task 6 (State API + 持久化)
                 └─► Task 7 (测试验证)
```
