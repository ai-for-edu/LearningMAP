# OpenAILearning 后端实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 构建 FastAPI + LangGraph 后端，实现有状态的对话式 AI 教育助手

**Architecture:** 单个对话 API 入口，LangGraph 状态机管理 chat/诊断/教案流程切换。LLM 调用使用 Anthropic SDK + instructor 结构化输出。SQLite + SQLAlchemy 做数据持久化。

**Tech Stack:** FastAPI, LangGraph, SQLAlchemy 2.0 (async), Anthropic SDK, instructor, Pydantic v2, Jinja2, sse-starlette, aiosqlite

---

## Task 1: 项目初始化 + 依赖安装

**Files:**
- Create: `openail/pyproject.toml`
- Create: `openail/.env`
- Create: `openail/.gitignore`

**Step 1: 创建 pyproject.toml**

```toml
[project]
name = "openail"
version = "0.1.0"
description = "AI Education Assistant Backend"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "langgraph>=0.4.0",
    "langchain-core>=0.3.0",
    "anthropic>=0.42.0",
    "instructor>=1.7.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "aiosqlite>=0.20.0",
    "pydantic>=2.10.0",
    "jinja2>=3.1.0",
    "sse-starlette>=2.0.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
    "ruff>=0.8.0",
]
```

**Step 2: 创建 .env**

```
ANTHROPIC_API_KEY=your-key-here
LLM_MODEL=claude-sonnet-4-20250514
```

**Step 3: 创建 .gitignore**

```
.env
__pycache__/
*.pyc
*.db
.ruff_cache/
```

**Step 4: 安装依赖**

Run: `cd /Users/wentaohu/project/openail && uv init --no-readme && uv add fastapi "uvicorn[standard]" langgraph langchain-core anthropic instructor "sqlalchemy[asyncio]" aiosqlite pydantic jinja2 sse-starlette python-dotenv`

Run: `uv add --dev pytest pytest-asyncio httpx ruff`

**Step 5: Commit**

```bash
git add pyproject.toml .env .gitignore uv.lock
git commit -m "chore: init backend project with dependencies"
```

---

## Task 2: Pydantic Schema + LearningState

**Files:**
- Create: `src/__init__.py`
- Create: `src/llm/__init__.py`
- Create: `src/llm/schemas.py`
- Create: `src/agent/__init__.py`
- Create: `src/agent/state.py`
- Test: `tests/test_schemas.py`

**Step 1: 写测试**

```python
# tests/test_schemas.py
from src.llm.schemas import KnowledgeNode, KnowledgeEdge, KnowledgeGraph, QuizQuestion, DiagnosisBreakdown, DiagnosisResult


def test_knowledge_graph_schema():
    graph = KnowledgeGraph(
        topic="二元一次方程",
        nodes=[
            KnowledgeNode(id="n1", name="一元一次方程", description="基础方程", level="prerequisite"),
            KnowledgeNode(id="n2", name="二元一次方程组", description="核心内容", level="core"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )
    assert len(graph.nodes) == 2
    assert graph.topic == "二元一次方程"


def test_quiz_question_schema():
    q = QuizQuestion(
        id="q1",
        knowledge_point="一元一次方程",
        question="解方程 2x+5=11，x=?",
        options=["x=2", "x=3", "x=4", "x=8"],
        answer=1,
    )
    assert q.options[q.answer] == "x=3"


def test_diagnosis_result_schema():
    result = DiagnosisResult(
        score=70,
        total=10,
        correct=7,
        incorrect=3,
        breakdown=[
            DiagnosisBreakdown(name="一元一次方程", status="mastered", detail="2/2 已掌握"),
            DiagnosisBreakdown(name="不等式", status="weak", detail="1/2 需复习"),
        ],
    )
    assert result.score == 70
    assert result.breakdown[1].status == "weak"
```

**Step 2: 运行测试，确认失败**

Run: `cd /Users/wentaohu/project/openail && python -m pytest tests/test_schemas.py -v`
Expected: FAIL — ModuleNotFoundError

**Step 3: 实现 schemas**

```python
# src/__init__.py
# (empty)

# src/llm/__init__.py
# (empty)

# src/llm/schemas.py
from pydantic import BaseModel


class KnowledgeNode(BaseModel):
    id: str
    name: str
    description: str
    level: str  # "prerequisite" | "core" | "extension"


class KnowledgeEdge(BaseModel):
    source: str
    target: str
    relation: str  # "prerequisite"


class KnowledgeGraph(BaseModel):
    topic: str
    nodes: list[KnowledgeNode]
    edges: list[KnowledgeEdge]


class QuizQuestion(BaseModel):
    id: str
    knowledge_point: str
    question: str
    options: list[str]
    answer: int  # index into options


class DiagnosisBreakdown(BaseModel):
    name: str
    status: str  # "mastered" | "weak" | "skipped"
    detail: str


class DiagnosisResult(BaseModel):
    score: int
    total: int
    correct: int
    incorrect: int
    breakdown: list[DiagnosisBreakdown]
```

**Step 4: 实现 LearningState**

```python
# src/agent/__init__.py
# (empty)

# src/agent/state.py
from __future__ import annotations

import operator
from typing import Annotated

from langgraph.graph import MessagesState
from src.llm.schemas import DiagnosisResult, KnowledgeGraph, QuizQuestion


class LearningState(MessagesState):
    """LangGraph state for the learning agent."""

    mode: str  # "chat" | "graph" | "quiz" | "result" | "lesson"

    # Diagnosis fields
    topic: str | None
    knowledge_graph: KnowledgeGraph | None
    current_question_index: int
    questions: list[QuizQuestion]
    answers: list[int]  # student's selected option indices
    diagnosis_result: DiagnosisResult | None

    # Lesson
    lesson_content: str | None
```

**Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: 3 passed

**Step 6: Commit**

```bash
git add src/ tests/
git commit -m "feat: add Pydantic schemas and LearningState"
```

---

## Task 3: LLM 客户端封装

**Files:**
- Create: `src/llm/client.py`
- Test: `tests/test_llm_client.py`

**Step 1: 写测试**

```python
# tests/test_llm_client.py
import os
import pytest
from src.llm.client import get_llm_client


def test_get_llm_client_returns_client():
    # Will use ANTHROPIC_API_KEY from .env if available, otherwise skip
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your-key-here":
        pytest.skip("No API key configured")
    client = get_llm_client()
    assert client is not None


def test_get_llm_client_with_explicit_key():
    client = get_llm_client(api_key="test-key")
    assert client is not None
```

**Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: FAIL

**Step 3: 实现 LLM client**

```python
# src/llm/client.py
from __future__ import annotations

import os
import logging

import anthropic
import instructor
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_client: instructor.Instructor | None = None


def get_llm_client(api_key: str | None = None) -> instructor.Instructor:
    """Get an instructor-wrapped Anthropic client for structured output."""
    global _client
    if _client is not None and api_key is None:
        return _client

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    base_client = anthropic.Anthropic(api_key=key)
    client = instructor.from_anthropic(base_client)

    if api_key is None:
        _client = client
    return client


def get_model_name() -> str:
    """Get the LLM model name from env."""
    return os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")
```

**Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: PASS (or skip if no API key)

**Step 5: Commit**

```bash
git add src/llm/client.py tests/test_llm_client.py
git commit -m "feat: add LLM client with instructor wrapper"
```

---

## Task 4: Prompt 模板

**Files:**
- Create: `src/prompts/intent.j2`
- Create: `src/prompts/knowledge_graph.j2`
- Create: `src/prompts/quiz_generate.j2`
- Create: `src/prompts/grading.j2`
- Create: `src/prompts/lesson_plan.j2`
- Create: `src/prompts/loader.py`
- Test: `tests/test_prompts.py`

**Step 1: 写测试**

```python
# tests/test_prompts.py
from src.prompts.loader import render_prompt


def test_render_intent_prompt():
    result = render_prompt("intent", messages=[{"role": "user", "content": "我想学二元一次方程"}])
    assert "二元一次方程" in result
    assert len(result) > 50


def test_render_knowledge_graph_prompt():
    result = render_prompt("knowledge_graph", topic="勾股定理", max_nodes=8)
    assert "勾股定理" in result
    assert "8" in result


def test_render_quiz_prompt():
    nodes = [{"name": "一元一次方程", "description": "基础"}]
    result = render_prompt("quiz_generate", knowledge_points=nodes, questions_per_point=2)
    assert "一元一次方程" in result
```

**Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_prompts.py -v`

**Step 3: 实现 prompt 模板和加载器**

```python
# src/prompts/loader.py
from __future__ import annotations

from pathlib import Path
from jinja2 import Environment, FileSystemLoader

_PROMPTS_DIR = Path(__file__).parent
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), keep_trailing_newline=True)


def render_prompt(template_name: str, **kwargs: object) -> str:
    """Render a Jinja2 prompt template."""
    template = _env.get_template(f"{template_name}.j2")
    return template.render(**kwargs)
```

```jinja2
{# src/prompts/intent.j2 #}
You are an intent classifier for an AI learning assistant. Analyze the student's latest message and classify their intent.

Conversation history:
{% for msg in messages %}
{{ msg.role }}: {{ msg.content }}
{% endfor %}

Classify the intent as one of:
- "learn_topic": Student wants to learn a specific topic (extract the topic name)
- "start_diagnosis": Student wants to start or continue the diagnosis quiz
- "answer": Student is answering a quiz question
- "chat": General question or conversation

Respond with JSON: {"intent": "...", "topic": "..." (if learn_topic)}
```

```jinja2
{# src/prompts/knowledge_graph.j2 #}
You are an educational knowledge graph builder. Given a learning topic, generate a knowledge graph of related concepts.

Topic: {{ topic }}
Maximum nodes: {{ max_nodes }}

Generate a knowledge graph with:
1. The core topic and its prerequisite concepts
2. Each node has: id, name, description, level (prerequisite/core/extension)
3. Edges show prerequisite relationships (source → target means source is prerequisite of target)
4. Include 4-{{ max_nodes }} nodes covering prerequisites and core concepts

Return a structured knowledge graph.
```

```jinja2
{# src/prompts/quiz_generate.j2 #}
You are a quiz generator for adaptive learning. Generate multiple-choice questions to diagnose student mastery.

Knowledge points to test:
{% for point in knowledge_points %}
- {{ point.name }}: {{ point.description }}
{% endfor %}

Generate {{ questions_per_point }} questions per knowledge point. Each question should:
1. Test understanding of the specific concept
2. Have exactly 4 options (A/B/C/D)
3. Have exactly one correct answer
4. Be appropriate for middle school level

Return a list of questions.
```

```jinja2
{# src/prompts/grading.j2 #}
You are a quiz grader. Determine if the student's answer is correct.

Question: {{ question.question }}
Options:
{% for opt in question.options %}
{{ loop.index0 }}) {{ opt }}
{% endfor %}
Correct answer index: {{ question.answer }}
Student selected index: {{ student_answer }}

Is the student correct? Respond with JSON: {"correct": true/false, "explanation": "brief explanation"}
```

```jinja2
{# src/prompts/lesson_plan.j2 #}
You are a personalized lesson plan generator. Create a lesson that addresses the student's weak knowledge areas.

Topic: {{ topic }}

Weak areas that need teaching:
{% for point in weak_points %}
- {{ point.name }}: {{ point.description }}
{% endfor %}

Create a comprehensive lesson covering each weak area with:
1. Clear explanation of the concept
2. Key formulas or rules (if applicable)
3. Worked examples with step-by-step solutions
4. Common mistakes to avoid

Use clear, concise language appropriate for a middle school student. Write in Chinese.
```

**Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/prompts/ tests/test_prompts.py
git commit -m "feat: add Jinja2 prompt templates with loader"
```

---

## Task 5: Agent 节点实现

**Files:**
- Create: `src/agent/nodes/__init__.py`
- Create: `src/agent/nodes/intent.py`
- Create: `src/agent/nodes/chat.py`
- Create: `src/agent/nodes/knowledge_graph.py`
- Create: `src/agent/nodes/quiz.py`
- Create: `src/agent/nodes/grading.py`
- Create: `src/agent/nodes/diagnosis.py`
- Create: `src/agent/nodes/lesson.py`

**Step 1: 意图识别节点**

```python
# src/agent/nodes/intent.py
from __future__ import annotations

import logging
from pydantic import BaseModel
from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.prompts.loader import render_prompt

logger = logging.getLogger(__name__)


class IntentResult(BaseModel):
    intent: str  # "learn_topic" | "start_diagnosis" | "answer" | "chat"
    topic: str | None = None


def intent_node(state: LearningState) -> dict:
    """Classify user intent from the latest message."""
    # If already in quiz mode, treat as answer
    if state.get("mode") == "quiz":
        return {"mode": "quiz"}

    messages = state.get("messages", [])
    if not messages:
        return {"mode": "chat"}

    last_msg = messages[-1]
    if hasattr(last_msg, "content"):
        content = last_msg.content
    else:
        content = str(last_msg)

    prompt = render_prompt("intent", messages=[{"role": "user", "content": content}])

    try:
        client = get_llm_client()
        result = client.messages.create(
            model=get_model_name(),
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            response_model=IntentResult,
        )

        if result.intent == "learn_topic" and result.topic:
            return {"mode": "graph", "topic": result.topic}
        elif result.intent == "start_diagnosis":
            return {"mode": "quiz"}
        else:
            return {"mode": "chat"}
    except Exception:
        logger.exception("Intent classification failed, falling back to chat")
        return {"mode": "chat"}
```

**Step 2: 普通对话节点**

```python
# src/agent/nodes/chat.py
from __future__ import annotations

import anthropic
from src.agent.state import LearningState
from src.llm.client import get_model_name
from langchain_core.messages import AIMessage

_raw_client = None


def _get_raw_client() -> anthropic.Anthropic:
    global _raw_client
    if _raw_client is None:
        _raw_client = anthropic.Anthropic()
    return _raw_client


def chat_node(state: LearningState) -> dict:
    """Handle general conversation."""
    messages = state.get("messages", [])

    # Convert to anthropic format
    api_messages = []
    for msg in messages:
        if hasattr(msg, "type"):
            role = "assistant" if msg.type == "ai" else "user"
            api_messages.append({"role": role, "content": msg.content})
        elif isinstance(msg, dict):
            api_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    # Ensure messages alternate and start with user
    if api_messages and api_messages[0]["role"] != "user":
        api_messages = api_messages[1:]

    client = _get_raw_client()
    response = client.messages.create(
        model=get_model_name(),
        max_tokens=1024,
        system="You are a friendly AI learning assistant. Help students with their questions. Respond in the same language as the student.",
        messages=api_messages if api_messages else [{"role": "user", "content": "Hello"}],
    )

    reply = response.content[0].text
    return {"messages": [AIMessage(content=reply)], "mode": "chat"}
```

**Step 3: 知识图谱生成节点**

```python
# src/agent/nodes/knowledge_graph.py
from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import KnowledgeGraph
from src.prompts.loader import render_prompt
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

MAX_NODES = 8


def knowledge_graph_node(state: LearningState) -> dict:
    """Generate knowledge graph for the given topic."""
    topic = state.get("topic", "")
    prompt = render_prompt("knowledge_graph", topic=topic, max_nodes=MAX_NODES)

    client = get_llm_client()
    graph = client.messages.create(
        model=get_model_name(),
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
        response_model=KnowledgeGraph,
    )

    reply = f"我已经为你梳理了「{topic}」的知识图谱，包含 {len(graph.nodes)} 个知识点。准备好了就点击开始诊断！"
    return {
        "messages": [AIMessage(content=reply)],
        "knowledge_graph": graph,
        "mode": "graph",
    }
```

**Step 4: 出题节点**

```python
# src/agent/nodes/quiz.py
from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import QuizQuestion
from src.prompts.loader import render_prompt
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

QUESTIONS_PER_POINT = 2


def quiz_node(state: LearningState) -> dict:
    """Generate quiz questions or serve the next question."""
    questions = state.get("questions", [])
    current_idx = state.get("current_question_index", 0)

    # If no questions generated yet, generate them
    if not questions:
        graph = state.get("knowledge_graph")
        if not graph:
            return {"mode": "chat", "messages": [AIMessage(content="请先选择一个学习主题。")]}

        nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        points = [{"name": n.name if hasattr(n, "name") else n["name"],
                    "description": n.description if hasattr(n, "description") else n["description"]}
                   for n in nodes]

        prompt = render_prompt("quiz_generate", knowledge_points=points, questions_per_point=QUESTIONS_PER_POINT)
        client = get_llm_client()
        generated = client.messages.create(
            model=get_model_name(),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            response_model=list[QuizQuestion],
        )

        return {
            "questions": generated,
            "current_question_index": 0,
            "answers": [],
            "mode": "quiz",
            "messages": [AIMessage(content=f"共 {len(generated)} 道题，开始作答！")],
        }

    # Serve current question
    if current_idx < len(questions):
        q = questions[current_idx]
        q_name = q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
        q_text = q.question if hasattr(q, "question") else q["question"]
        opts = q.options if hasattr(q, "options") else q["options"]
        options_text = "\n".join(f"  {chr(65+i)}) {opt}" for i, opt in enumerate(opts))
        reply = f"[{q_name}] 第 {current_idx+1}/{len(questions)} 题\n\n{q_text}\n\n{options_text}"
        return {"mode": "quiz", "messages": [AIMessage(content=reply)]}

    # All done
    return {"mode": "result"}
```

**Step 5: 判题节点**

```python
# src/agent/nodes/grading.py
from __future__ import annotations

import logging
from src.agent.state import LearningState
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

# Map letter answers to indices
ANSWER_MAP = {"a": 0, "b": 1, "c": 2, "d": 3, "0": 0, "1": 1, "2": 2, "3": 3}


def grading_node(state: LearningState) -> dict:
    """Grade student's answer to current question."""
    messages = state.get("messages", [])
    questions = state.get("questions", [])
    current_idx = state.get("current_question_index", 0)
    answers = list(state.get("answers", []))

    if current_idx >= len(questions):
        return {"mode": "result"}

    # Parse student answer from last message
    last_msg = messages[-1]
    content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    student_answer = content.strip().lower()

    answer_idx = ANSWER_MAP.get(student_answer, -1)
    if answer_idx == -1:
        return {"messages": [AIMessage(content="请输入 A、B、C 或 D 来作答。")], "mode": "quiz"}

    # Check correctness
    q = questions[current_idx]
    correct_idx = q.answer if hasattr(q, "answer") else q["answer"]
    is_correct = answer_idx == correct_idx

    answers.append(answer_idx)
    next_idx = current_idx + 1

    if is_correct:
        feedback = "✓ 回答正确！"
    else:
        correct_letter = chr(65 + correct_idx)
        feedback = f"✗ 回答错误，正确答案是 {correct_letter}。"

    # Check if more questions
    if next_idx >= len(questions):
        return {
            "answers": answers,
            "current_question_index": next_idx,
            "mode": "result",
            "messages": [AIMessage(content=f"{feedback}\n\n所有题目作答完毕，正在生成诊断结果...")],
        }

    return {
        "answers": answers,
        "current_question_index": next_idx,
        "mode": "quiz",
        "messages": [AIMessage(content=feedback)],
    }
```

**Step 6: 诊断结果节点**

```python
# src/agent/nodes/diagnosis.py
from __future__ import annotations

import logging
from collections import defaultdict
from src.agent.state import LearningState
from src.llm.schemas import DiagnosisBreakdown, DiagnosisResult
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


def diagnosis_node(state: LearningState) -> dict:
    """Compute diagnosis results with skip logic."""
    questions = state.get("questions", [])
    answers = state.get("answers", [])
    graph = state.get("knowledge_graph")

    # Tally per knowledge point
    point_results: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})
    for i, q in enumerate(questions):
        kp = q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
        correct_idx = q.answer if hasattr(q, "answer") else q["answer"]
        point_results[kp]["total"] += 1
        if i < len(answers) and answers[i] == correct_idx:
            point_results[kp]["correct"] += 1

    # Determine mastered/weak
    mastered_points = set()
    weak_points = set()
    for kp, res in point_results.items():
        if res["correct"] >= res["total"] * 0.6:
            mastered_points.add(kp)
        else:
            weak_points.add(kp)

    # Skip logic: if mastered high-level node, skip its prerequisites
    skipped_points = set()
    if graph:
        edges = graph.edges if hasattr(graph, "edges") else graph.get("edges", [])
        nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        node_names = {(n.id if hasattr(n, "id") else n["id"]): (n.name if hasattr(n, "name") else n["name"]) for n in nodes}

        for edge in edges:
            source = edge.source if hasattr(edge, "source") else edge["source"]
            target = edge.target if hasattr(edge, "target") else edge["target"]
            target_name = node_names.get(target, "")
            source_name = node_names.get(source, "")
            if target_name in mastered_points and source_name not in point_results:
                skipped_points.add(source_name)

    # Build breakdown
    breakdown = []
    total_correct = sum(r["correct"] for r in point_results.values())
    total_questions = sum(r["total"] for r in point_results.values())

    for kp in point_results:
        r = point_results[kp]
        if kp in mastered_points:
            breakdown.append(DiagnosisBreakdown(name=kp, status="mastered", detail=f"{r['correct']}/{r['total']} 已掌握"))
        else:
            breakdown.append(DiagnosisBreakdown(name=kp, status="weak", detail=f"{r['correct']}/{r['total']} 需复习"))

    for kp in skipped_points:
        breakdown.append(DiagnosisBreakdown(name=kp, status="skipped", detail="已跳过"))

    score = round(total_correct / max(total_questions, 1) * 100)
    result = DiagnosisResult(
        score=score,
        total=total_questions,
        correct=total_correct,
        incorrect=total_questions - total_correct,
        breakdown=breakdown,
    )

    weak_names = ", ".join(weak_points) if weak_points else "无"
    reply = f"诊断完成！正确率 {score}%（{total_correct}/{total_questions}）\n\n薄弱知识点：{weak_names}"

    return {
        "diagnosis_result": result,
        "mode": "result",
        "messages": [AIMessage(content=reply)],
    }
```

**Step 7: 教案生成节点**

```python
# src/agent/nodes/lesson.py
from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_model_name
from src.prompts.loader import render_prompt
from langchain_core.messages import AIMessage
import anthropic

logger = logging.getLogger(__name__)


def lesson_node(state: LearningState) -> dict:
    """Generate personalized lesson for weak points."""
    diagnosis = state.get("diagnosis_result")
    graph = state.get("knowledge_graph")
    topic = state.get("topic", "")

    if not diagnosis:
        return {"mode": "chat", "messages": [AIMessage(content="请先完成诊断。")]}

    # Get weak points
    breakdown = diagnosis.breakdown if hasattr(diagnosis, "breakdown") else diagnosis.get("breakdown", [])
    weak_items = [b for b in breakdown if (b.status if hasattr(b, "status") else b["status"]) == "weak"]

    if not weak_items:
        return {
            "mode": "chat",
            "lesson_content": "",
            "messages": [AIMessage(content="你已经掌握了所有知识点，太棒了！有其他想学的吗？")],
        }

    # Build weak points info
    nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
    node_map = {(n.name if hasattr(n, "name") else n["name"]): n for n in nodes}
    weak_points = []
    for item in weak_items:
        name = item.name if hasattr(item, "name") else item["name"]
        node = node_map.get(name)
        if node:
            desc = node.description if hasattr(node, "description") else node.get("description", "")
            weak_points.append({"name": name, "description": desc})
        else:
            weak_points.append({"name": name, "description": ""})

    prompt = render_prompt("lesson_plan", topic=topic, weak_points=weak_points)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=get_model_name(),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    lesson_text = response.content[0].text
    return {
        "lesson_content": lesson_text,
        "mode": "lesson",
        "messages": [AIMessage(content=lesson_text)],
    }
```

**Step 8: 节点 __init__**

```python
# src/agent/nodes/__init__.py
from src.agent.nodes.intent import intent_node
from src.agent.nodes.chat import chat_node
from src.agent.nodes.knowledge_graph import knowledge_graph_node
from src.agent.nodes.quiz import quiz_node
from src.agent.nodes.grading import grading_node
from src.agent.nodes.diagnosis import diagnosis_node
from src.agent.nodes.lesson import lesson_node

__all__ = [
    "intent_node",
    "chat_node",
    "knowledge_graph_node",
    "quiz_node",
    "grading_node",
    "diagnosis_node",
    "lesson_node",
]
```

**Step 9: Commit**

```bash
git add src/agent/nodes/
git commit -m "feat: implement all agent nodes (intent, chat, graph, quiz, grading, diagnosis, lesson)"
```

---

## Task 6: LangGraph 状态机组装

**Files:**
- Create: `src/agent/graph.py`
- Test: `tests/test_graph.py`

**Step 1: 写测试**

```python
# tests/test_graph.py
from src.agent.graph import build_graph


def test_build_graph_compiles():
    graph = build_graph()
    assert graph is not None


def test_graph_has_expected_nodes():
    graph = build_graph()
    node_names = set(graph.nodes.keys())
    expected = {"intent", "chat", "knowledge_graph", "quiz", "grading", "diagnosis", "lesson"}
    assert expected.issubset(node_names)
```

**Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_graph.py -v`

**Step 3: 实现状态机**

```python
# src/agent/graph.py
from __future__ import annotations

from langgraph.graph import StateGraph, END
from src.agent.state import LearningState
from src.agent.nodes import (
    intent_node,
    chat_node,
    knowledge_graph_node,
    quiz_node,
    grading_node,
    diagnosis_node,
    lesson_node,
)


def _route_after_intent(state: LearningState) -> str:
    """Route based on detected intent / current mode."""
    mode = state.get("mode", "chat")
    if mode == "graph":
        return "knowledge_graph"
    elif mode == "quiz":
        # If questions exist and student sent an answer, go to grading
        questions = state.get("questions", [])
        current_idx = state.get("current_question_index", 0)
        if questions and current_idx < len(questions):
            return "grading"
        return "quiz"
    elif mode == "result":
        return "diagnosis"
    elif mode == "lesson":
        return "lesson"
    return "chat"


def _route_after_grading(state: LearningState) -> str:
    """After grading, either next question or diagnosis."""
    mode = state.get("mode", "quiz")
    if mode == "result":
        return "diagnosis"
    return "quiz"


def _route_after_diagnosis(state: LearningState) -> str:
    """After diagnosis, generate lesson."""
    return "lesson"


def build_graph() -> StateGraph:
    """Build and compile the learning agent graph."""
    builder = StateGraph(LearningState)

    # Add nodes
    builder.add_node("intent", intent_node)
    builder.add_node("chat", chat_node)
    builder.add_node("knowledge_graph", knowledge_graph_node)
    builder.add_node("quiz", quiz_node)
    builder.add_node("grading", grading_node)
    builder.add_node("diagnosis", diagnosis_node)
    builder.add_node("lesson", lesson_node)

    # Entry point
    builder.set_entry_point("intent")

    # Edges from intent
    builder.add_conditional_edges("intent", _route_after_intent, {
        "chat": "chat",
        "knowledge_graph": "knowledge_graph",
        "quiz": "quiz",
        "grading": "grading",
        "diagnosis": "diagnosis",
        "lesson": "lesson",
    })

    # Terminal edges
    builder.add_edge("chat", END)
    builder.add_edge("knowledge_graph", END)
    builder.add_edge("quiz", END)

    # Grading → quiz or diagnosis
    builder.add_conditional_edges("grading", _route_after_grading, {
        "quiz": "quiz",
        "diagnosis": "diagnosis",
    })

    # Diagnosis → lesson
    builder.add_edge("diagnosis", "lesson")

    # Lesson → END (back to chat on next message)
    builder.add_edge("lesson", END)

    return builder.compile()
```

**Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_graph.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/agent/graph.py tests/test_graph.py
git commit -m "feat: assemble LangGraph state machine with routing"
```

---

## Task 7: FastAPI 路由 + SSE

**Files:**
- Create: `src/api/__init__.py`
- Create: `src/api/chat.py`
- Create: `run/serve.py`
- Test: `tests/test_api.py`

**Step 1: 写测试**

```python
# tests/test_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from run.serve import app


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

**Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_api.py -v`

**Step 3: 实现 API**

```python
# src/api/__init__.py
# (empty)

# src/api/chat.py
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from src.agent.graph import build_graph

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory session store (MVP)
_sessions: dict[str, dict[str, Any]] = {}
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    mode: str
    message: str
    data: dict | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Main chat endpoint — all student messages go through here."""
    session_id = req.session_id or str(uuid.uuid4())

    # Get or init session state
    state = _sessions.get(session_id, {
        "messages": [],
        "mode": "chat",
        "topic": None,
        "knowledge_graph": None,
        "current_question_index": 0,
        "questions": [],
        "answers": [],
        "diagnosis_result": None,
        "lesson_content": None,
    })

    # Add user message
    state["messages"] = list(state.get("messages", []))
    state["messages"].append(HumanMessage(content=req.message))

    # Run graph
    graph = _get_graph()
    result = graph.invoke(state)

    # Save state
    _sessions[session_id] = result

    # Extract response
    messages = result.get("messages", [])
    last_ai_msg = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai":
            last_ai_msg = msg.content
            break

    mode = result.get("mode", "chat")

    # Build response data based on mode
    data: dict | None = None
    if mode == "graph" and result.get("knowledge_graph"):
        kg = result["knowledge_graph"]
        data = {"graph": kg.model_dump() if hasattr(kg, "model_dump") else kg}
    elif mode == "quiz" and result.get("questions"):
        questions = result["questions"]
        idx = result.get("current_question_index", 0)
        if idx < len(questions):
            q = questions[idx]
            data = {
                "question": q.model_dump() if hasattr(q, "model_dump") else q,
                "progress": f"{idx+1}/{len(questions)}",
            }
    elif mode == "result" and result.get("diagnosis_result"):
        dr = result["diagnosis_result"]
        data = {"result": dr.model_dump() if hasattr(dr, "model_dump") else dr}
    elif mode == "lesson" and result.get("lesson_content"):
        data = {"lesson": result["lesson_content"]}

    return ChatResponse(
        session_id=session_id,
        mode=mode,
        message=last_ai_msg,
        data=data,
    )


@router.get("/chat/{session_id}/state")
async def get_state(session_id: str) -> dict:
    """Get current session state for frontend UI switching."""
    state = _sessions.get(session_id)
    if not state:
        return {"mode": "chat", "session_id": session_id}

    return {
        "session_id": session_id,
        "mode": state.get("mode", "chat"),
        "topic": state.get("topic"),
        "has_graph": state.get("knowledge_graph") is not None,
        "question_progress": f"{state.get('current_question_index', 0)}/{len(state.get('questions', []))}",
        "has_result": state.get("diagnosis_result") is not None,
        "has_lesson": state.get("lesson_content") is not None,
    }
```

```python
# run/serve.py
from __future__ import annotations

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.chat import router as chat_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="OpenAILearning API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run.serve:app", host="0.0.0.0", port=8000, reload=True)
```

**Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_api.py -v`
Expected: 1 passed

**Step 5: Commit**

```bash
git add src/api/ run/serve.py tests/test_api.py
git commit -m "feat: add FastAPI chat endpoint with LangGraph integration"
```

---

## Task 8: 端到端测试 + 启动验证

**Files:**
- Create: `tests/test_e2e.py`

**Step 1: 写端到端测试（需要 API key）**

```python
# tests/test_e2e.py
import os
import pytest
from httpx import AsyncClient, ASGITransport
from run.serve import app


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("ANTHROPIC_API_KEY", "your-key-here") == "your-key-here",
    reason="No API key configured",
)
async def test_full_chat_flow():
    """Test: greeting → learn topic → get graph."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Greeting
        r1 = await client.post("/api/chat", json={"message": "你好"}, timeout=30)
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["mode"] == "chat"
        session_id = data1["session_id"]

        # Step 2: Learn topic
        r2 = await client.post("/api/chat", json={
            "session_id": session_id,
            "message": "我想学习勾股定理",
        }, timeout=60)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["mode"] == "graph"
        assert data2["data"]["graph"]["topic"] is not None
```

**Step 2: 运行测试**

Run: `python -m pytest tests/test_e2e.py -v -s`
Expected: PASS (if API key set) or SKIP

**Step 3: 手动启动验证**

Run: `cd /Users/wentaohu/project/openail && python run/serve.py`

浏览器打开 `http://localhost:8000/docs` 查看 Swagger UI，手动测试 `/api/chat` 端点。

**Step 4: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: add e2e test for chat flow"
```

---

## 任务依赖关系

```
Task 1 (项目初始化)
  └─► Task 2 (Schema + State)
       └─► Task 3 (LLM Client)
            └─► Task 4 (Prompt 模板)
                 └─► Task 5 (Agent 节点)
                      └─► Task 6 (状态机组装)
                           └─► Task 7 (FastAPI 路由)
                                └─► Task 8 (端到端测试)
```

**预计总耗时：** 8 个 Task，每个 15-30 分钟，总计约 3-4 小时。
