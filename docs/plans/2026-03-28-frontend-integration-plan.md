# 前端对接后端 API 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将现有纯静态 HTML 前端对接 FastAPI 后端，用真实 LLM 数据替换硬编码的 session-data.js

**Architecture:** FastAPI 托管 static/ 目录下的前端文件。每个页面通过 `static/api.js` 调后端 API，页面间用 URL 参数 `?session_id=xxx` 传递会话。

**Tech Stack:** FastAPI StaticFiles, vanilla JS fetch API, 现有 HTML + Tailwind CSS

---

## Task 1: FastAPI 托管静态文件

**Files:**
- Create: `static/` 目录（移动现有 HTML/JS）
- Modify: `run/serve.py`

**Step 1: 移动前端文件到 static/**

```bash
mkdir -p static
mv index.html diagnosis.html results.html lesson.html session-data.js sidebar.js static/
```

**Step 2: 修改 serve.py 挂载静态文件**

在 `run/serve.py` 的 `app.include_router` 之后添加：

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# 放在 app.include_router(chat_router, prefix="/api") 之后
_static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
```

注意：`mount("/", ...)` 必须放在所有路由之后，否则会拦截 API 请求。

**Step 3: 验证**

Run: `cd /Users/wentaohu/project/openail && uv run python run/serve.py`

浏览器打开 `http://localhost:8080` 应该能看到 index.html 页面。
打开 `http://localhost:8080/health` 应该返回 `{"status": "ok"}`。

**Step 4: Commit**

```bash
git add static/ run/serve.py
git commit -m "chore: move frontend to static/ and serve via FastAPI"
```

---

## Task 2: 创建 api.js 公共模块

**Files:**
- Create: `static/api.js`

**Step 1: 实现 api.js**

```javascript
// static/api.js
const API_BASE = '/api';

// 从 URL 参数获取 session_id
function getSessionId() {
  return new URLSearchParams(window.location.search).get('session_id');
}

// 保存 session_id 到 URL（不刷新页面）
function setSessionId(id) {
  const url = new URL(window.location);
  url.searchParams.set('session_id', id);
  window.history.replaceState({}, '', url);
}

// 发送聊天消息
async function sendMessage(message, sessionId) {
  const body = { message };
  if (sessionId) body.session_id = sessionId;

  const resp = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

// 获取会话状态
async function getSessionState(sessionId) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/state`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}
```

**Step 2: Commit**

```bash
git add static/api.js
git commit -m "feat: add api.js frontend helper module"
```

---

## Task 3: 改造 index.html 对接后端

**Files:**
- Modify: `static/index.html`

**Step 1: 引入 api.js**

在 `<script src="sidebar.js"></script>` 后面加：

```html
<script src="api.js"></script>
```

**Step 2: 改造输入框和发送逻辑**

替换底部输入框的 `<input>` 和 `<button>`，给它们加 id 并绑定事件：

- input 加 `id="chatInput"`
- button 加 `id="sendBtn"` 和 `onclick="handleSend()"`
- 建议按钮点击也调用 `handleSend(text)`

**Step 3: 实现 handleSend 和渲染逻辑**

在页面底部的 `<script>` 中替换 `renderSessionContent` 函数为：

```javascript
let currentSessionId = getSessionId();

async function handleSend(text) {
  const input = document.getElementById('chatInput');
  const message = text || input.value.trim();
  if (!message) return;
  input.value = '';

  // 显示用户消息
  const chatHistory = document.getElementById('chatHistory');
  chatHistory.innerHTML += `
    <div class="flex justify-end">
      <div class="bg-surface-container-low px-6 py-4 rounded-3xl rounded-tr-none max-w-[85%] text-on-surface shadow-sm">
        <p class="font-body leading-relaxed">${message}</p>
      </div>
    </div>`;

  // 显示加载中
  chatHistory.innerHTML += `
    <div class="flex gap-4" id="loading">
      <div class="w-8 h-8 rounded-full bg-primary-fixed flex items-center justify-center shrink-0">
        <span class="material-symbols-outlined text-primary text-sm" style="font-variation-settings: 'FILL' 1;">smart_toy</span>
      </div>
      <div class="flex-grow"><p class="font-body text-on-surface-variant">思考中...</p></div>
    </div>`;

  try {
    const data = await sendMessage(message, currentSessionId);
    currentSessionId = data.session_id;
    setSessionId(currentSessionId);

    // 移除加载
    document.getElementById('loading')?.remove();

    if (data.mode === 'graph' && data.data?.graph) {
      renderKnowledgeGraph(data);
    } else {
      // 普通聊天回复
      chatHistory.innerHTML += `
        <div class="flex gap-4">
          <div class="w-8 h-8 rounded-full bg-primary-fixed flex items-center justify-center shrink-0">
            <span class="material-symbols-outlined text-primary text-sm" style="font-variation-settings: 'FILL' 1;">smart_toy</span>
          </div>
          <div class="flex-grow"><p class="font-body text-on-surface-variant leading-relaxed">${data.message}</p></div>
        </div>`;
    }
  } catch (err) {
    document.getElementById('loading')?.remove();
    chatHistory.innerHTML += `<div class="text-error text-sm">请求失败: ${err.message}</div>`;
  }
}

function renderKnowledgeGraph(data) {
  const graph = data.data.graph;
  const chatHistory = document.getElementById('chatHistory');
  const nodesHtml = graph.nodes.map((n, i) => {
    const positions = [
      'top-[20%] left-[25%]', 'top-[20%] right-[25%]',
      'bottom-[20%] left-[25%]', 'bottom-[20%] right-[25%]',
      'top-[50%] left-[10%]', 'top-[50%] right-[10%]',
      'top-[35%] left-[15%]', 'bottom-[35%] right-[15%]'
    ];
    const pos = positions[i % positions.length];
    const color = n.level === 'core' ? 'primary' : 'slate-400';
    return `<div class="absolute ${pos} knowledge-label cursor-default">
      <span class="font-headline font-bold text-xs tracking-wider text-${color}">${n.name}</span>
    </div>`;
  }).join('');

  chatHistory.innerHTML += `
    <div class="flex gap-4">
      <div class="w-8 h-8 rounded-full bg-primary-fixed flex items-center justify-center shrink-0">
        <span class="material-symbols-outlined text-primary text-sm" style="font-variation-settings: 'FILL' 1;">smart_toy</span>
      </div>
      <div class="flex-grow space-y-6">
        <div class="prose prose-slate max-w-none">
          <p class="font-headline text-2xl font-bold tracking-tight text-primary">${graph.topic} 知识图谱</p>
          <p class="font-body text-on-surface-variant leading-relaxed">${data.message}</p>
        </div>
        <div class="relative bg-white rounded-[2.5rem] border border-slate-100 p-12 min-h-[400px] flex items-center justify-center overflow-hidden">
          <div class="relative z-10 w-full h-full flex items-center justify-center">
            <div class="text-center">
              <span class="font-headline font-extrabold text-sm tracking-widest text-primary">${graph.topic}</span>
            </div>
            ${nodesHtml}
          </div>
          <div class="absolute bottom-10 left-1/2 -translate-x-1/2 w-full max-w-xs text-center">
            <a href="diagnosis.html?session_id=${currentSessionId}" class="block w-full bg-secondary text-white py-3 px-8 rounded-full font-headline font-bold tracking-wide shadow-md hover:bg-secondary/90 transition-all text-sm text-center">Start Diagnosis</a>
          </div>
        </div>
      </div>
    </div>`;
}

// 初始化：清空硬编码内容，显示欢迎
document.getElementById('chatHistory').innerHTML = '';
```

**Step 4: 绑定输入框回车和按钮**

修改底部输入框 HTML：

```html
<input id="chatInput" onkeydown="if(event.key==='Enter')handleSend()" class="..." placeholder="输入学习主题，例如：我想学习勾股定理" type="text"/>
<button id="sendBtn" onclick="handleSend()" class="...">
```

**Step 5: 验证**

启动服务，打开 `http://localhost:8080`，输入"我想学习勾股定理"，应该看到从后端返回的知识图谱。

**Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat: connect index.html to backend API"
```

---

## Task 4: 改造 diagnosis.html 对接后端

**Files:**
- Modify: `static/diagnosis.html`

**Step 1: 引入 api.js，去掉 session-data.js**

替换 `<script src="session-data.js"></script>` 为 `<script src="api.js"></script>`

**Step 2: 重写答题逻辑**

替换 `<script>` 中的所有 JS 为：

```javascript
let sessionId = getSessionId();
let totalQuestions = 0;
let currentIndex = 0;

async function initDiagnosis() {
  if (!sessionId) {
    alert('请先从主页选择学习主题');
    window.location.href = '/';
    return;
  }

  // 发送"开始诊断"触发出题
  const data = await sendMessage('开始诊断', sessionId);
  sessionId = data.session_id;

  if (data.mode === 'quiz' && data.data?.question) {
    totalQuestions = parseInt(data.data.progress.split('/')[1]);
    currentIndex = 0;
    renderQuestion(data.data.question, data.data.progress);
  }
}

function renderQuestion(q, progress) {
  const [cur, total] = progress.split('/').map(Number);
  document.getElementById('progressText').textContent = `Question ${cur} / ${total}`;
  document.getElementById('progressBar').style.width = `${(cur / total) * 100}%`;
  document.getElementById('knowledgeTagText').textContent = q.knowledge_point;
  document.getElementById('questionText').textContent = q.question;
  document.getElementById('optA').textContent = q.options[0];
  document.getElementById('optB').textContent = q.options[1];
  document.getElementById('optC').textContent = q.options[2];
  document.getElementById('optD').textContent = q.options[3];

  // Reset selection
  selectedOption = -1;
  document.querySelectorAll('.option-btn').forEach(b => b.classList.remove('selected'));
  document.getElementById('nextBtn').disabled = true;
  document.getElementById('nextBtnText').textContent = cur >= total ? 'Finish' : 'Next Question';
}

let selectedOption = -1;

function selectOption(btn, index) {
  document.querySelectorAll('.option-btn').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
  selectedOption = index;
  document.getElementById('nextBtn').disabled = false;
}

async function nextQuestion() {
  if (selectedOption === -1) return;
  const letter = ['A', 'B', 'C', 'D'][selectedOption];

  document.getElementById('nextBtn').disabled = true;
  const data = await sendMessage(letter, sessionId);

  if (data.mode === 'quiz' && data.data?.question) {
    renderQuestion(data.data.question, data.data.progress);
  } else {
    // 诊断完成（mode 可能是 result 或 lesson）
    document.querySelector('main').innerHTML = `
      <div class="max-w-2xl mx-auto w-full flex flex-col items-center justify-center min-h-[60vh] text-center">
        <span class="material-symbols-outlined text-6xl text-secondary mb-6" style="font-variation-settings: 'FILL' 1;">check_circle</span>
        <p class="font-headline text-3xl font-extrabold text-on-surface mb-3">Diagnosis Complete</p>
        <p class="font-body text-on-surface-variant mb-8">${data.message}</p>
        <a href="results.html?session_id=${sessionId}" class="inline-block bg-primary text-white py-4 px-10 rounded-2xl font-headline font-bold text-sm shadow-md hover:shadow-lg transition-all">
          View Results
        </a>
      </div>`;
    document.getElementById('progressBar').style.width = '100%';
    document.getElementById('progressText').textContent = 'Completed';
  }
}

initDiagnosis();
```

**Step 3: 验证**

从 index.html 生成知识图谱后，点 Start Diagnosis 跳转到 diagnosis.html，应该能看到 LLM 生成的题目并逐题作答。

**Step 4: Commit**

```bash
git add static/diagnosis.html
git commit -m "feat: connect diagnosis.html to backend API"
```

---

## Task 5: 改造 results.html 对接后端

**Files:**
- Modify: `static/results.html`

**Step 1: 引入 api.js，去掉 session-data.js**

替换 `<script src="session-data.js"></script>` 为 `<script src="api.js"></script>`

**Step 2: 重写渲染逻辑**

替换渲染 `<script>` 为：

```javascript
(async function() {
  const sessionId = getSessionId();
  if (!sessionId) {
    window.location.href = '/';
    return;
  }

  const state = await getSessionState(sessionId);
  if (!state.has_result) {
    document.getElementById('resultsContent').innerHTML = '<p class="text-on-surface-variant">暂无诊断结果</p>';
    return;
  }

  // 需要完整的 diagnosis_result 数据，用 sendMessage 触发教案生成
  // 或者扩展 state API 返回完整数据
  // 这里直接发一条消息让后端返回 lesson 模式的数据
  const data = await sendMessage('查看结果', sessionId);

  // 如果后端返回了 result 数据
  const r = data.data?.result;
  if (!r) {
    document.getElementById('resultsContent').innerHTML = `
      <div class="flex gap-4">
        <div class="w-8 h-8 rounded-full bg-primary-fixed flex items-center justify-center shrink-0">
          <span class="material-symbols-outlined text-primary text-sm" style="font-variation-settings: 'FILL' 1;">smart_toy</span>
        </div>
        <div class="flex-grow"><p class="font-body text-on-surface-variant">${data.message}</p></div>
      </div>`;
    return;
  }

  const dashOffset = 326.73 * (1 - r.score / 100);
  const weakNames = r.breakdown.filter(b => b.status === 'weak').map(b => b.name).join(' 和 ');

  const breakdownHtml = r.breakdown.map(item => {
    if (item.status === 'mastered') {
      return `<div class="flex items-center gap-3"><span class="material-symbols-outlined text-secondary text-lg" style="font-variation-settings:'FILL' 1;">check_circle</span><span class="font-body text-sm text-on-surface flex-grow">${item.name}</span><span class="font-label text-xs font-semibold text-secondary bg-secondary/10 px-2.5 py-0.5 rounded-full">${item.detail}</span></div>`;
    } else if (item.status === 'weak') {
      return `<div class="flex items-center gap-3"><span class="material-symbols-outlined text-error text-lg" style="font-variation-settings:'FILL' 1;">cancel</span><span class="font-body text-sm text-on-surface flex-grow">${item.name}</span><span class="font-label text-xs font-semibold text-error bg-error/10 px-2.5 py-0.5 rounded-full">${item.detail}</span></div>`;
    } else {
      return `<div class="flex items-center gap-3"><span class="material-symbols-outlined text-slate-400 text-lg" style="font-variation-settings:'FILL' 1;">radio_button_unchecked</span><span class="font-body text-sm text-on-surface flex-grow">${item.name}</span><span class="font-label text-xs font-semibold text-slate-400 bg-slate-100 px-2.5 py-0.5 rounded-full">${item.detail}</span></div>`;
    }
  }).join('');

  document.getElementById('resultsContent').innerHTML = `
    <div class="flex gap-4">
      <div class="w-8 h-8 rounded-full bg-primary-fixed flex items-center justify-center shrink-0">
        <span class="material-symbols-outlined text-primary text-sm" style="font-variation-settings: 'FILL' 1;">smart_toy</span>
      </div>
      <div class="flex-grow space-y-6">
        <div class="prose prose-slate max-w-none">
          <p class="font-headline text-2xl font-bold tracking-tight text-primary">诊断结果</p>
          <p class="font-body text-on-surface-variant leading-relaxed">${data.message}</p>
        </div>
        <div class="bg-white rounded-3xl border border-slate-100 p-8 shadow-sm">
          <div class="flex items-center gap-8 mb-8">
            <div class="relative w-28 h-28 shrink-0">
              <svg class="w-full h-full -rotate-90" viewBox="0 0 120 120">
                <circle cx="60" cy="60" r="52" fill="none" stroke="#e6e8ea" stroke-width="8"/>
                <circle cx="60" cy="60" r="52" fill="none" stroke="#005da7" stroke-width="8" stroke-linecap="round" stroke-dasharray="326.73" stroke-dashoffset="${dashOffset}"/>
              </svg>
              <div class="absolute inset-0 flex flex-col items-center justify-center">
                <span class="font-headline text-3xl font-extrabold text-primary">${r.score}%</span>
                <span class="font-label text-[10px] text-on-surface-variant uppercase tracking-widest">Accuracy</span>
              </div>
            </div>
            <div class="flex-grow space-y-3">
              <div class="flex items-center justify-between"><span class="font-body text-sm text-on-surface-variant">Total</span><span class="font-headline font-bold text-on-surface">${r.total}</span></div>
              <div class="flex items-center justify-between"><span class="font-body text-sm text-on-surface-variant">Correct</span><span class="font-headline font-bold text-secondary">${r.correct}</span></div>
              <div class="flex items-center justify-between"><span class="font-body text-sm text-on-surface-variant">Incorrect</span><span class="font-headline font-bold text-error">${r.incorrect}</span></div>
            </div>
          </div>
          <div class="space-y-3">
            <p class="font-headline font-bold text-xs text-on-surface-variant uppercase tracking-widest mb-4">Knowledge Breakdown</p>
            ${breakdownHtml}
          </div>
        </div>
        ${weakNames ? `<div class="bg-error/5 rounded-3xl border border-error/10 p-8">
          <div class="flex items-start gap-3">
            <span class="material-symbols-outlined text-error text-xl mt-0.5">warning</span>
            <div>
              <p class="font-headline font-bold text-on-surface mb-1">薄弱知识点</p>
              <p class="font-body text-sm text-on-surface-variant">需要加强 <strong>${weakNames}</strong> 的理解。</p>
            </div>
          </div>
        </div>` : ''}
        <div class="text-center pt-2">
          <a href="lesson.html?session_id=${sessionId}" class="inline-flex items-center gap-2 bg-primary text-white py-4 px-10 rounded-2xl font-headline font-bold text-sm shadow-md hover:shadow-lg hover:bg-primary-container transition-all">
            <span class="material-symbols-outlined text-lg">school</span>Start Learning
          </a>
        </div>
      </div>
    </div>`;
})();
```

**Step 3: Commit**

```bash
git add static/results.html
git commit -m "feat: connect results.html to backend API"
```

---

## Task 6: 改造 lesson.html 对接后端

**Files:**
- Modify: `static/lesson.html`

**Step 1: 引入 api.js，去掉 session-data.js**

**Step 2: 重写渲染逻辑**

lesson.html 需要获取 lesson_content（markdown 文本）。在诊断完成后后端会自动生成教案。用 state API 获取：

```javascript
(async function() {
  const sessionId = getSessionId();
  if (!sessionId) {
    window.location.href = '/';
    return;
  }

  const state = await getSessionState(sessionId);
  if (!state.has_lesson) {
    // 触发教案生成
    const data = await sendMessage('生成教案', sessionId);
    renderLesson(data.message || data.data?.lesson || '教案生成中...');
  } else {
    const data = await sendMessage('查看教案', sessionId);
    renderLesson(data.message || data.data?.lesson || '');
  }
})();

function renderLesson(content) {
  document.getElementById('lessonContent').innerHTML = `
    <div class="flex gap-4">
      <div class="w-8 h-8 rounded-full bg-primary-fixed flex items-center justify-center shrink-0">
        <span class="material-symbols-outlined text-primary text-sm" style="font-variation-settings: 'FILL' 1;">smart_toy</span>
      </div>
      <div class="flex-grow prose prose-slate max-w-none">
        <p class="font-headline text-2xl font-bold tracking-tight text-primary mb-6">个性化教案</p>
        <div class="font-body text-on-surface-variant leading-relaxed whitespace-pre-wrap">${content}</div>
      </div>
    </div>`;
}
```

**Step 3: Commit**

```bash
git add static/lesson.html
git commit -m "feat: connect lesson.html to backend API"
```

---

## Task 7: 扩展后端 state API 返回完整数据

**Files:**
- Modify: `src/api/chat.py`

**Step 1: 扩展 GET /api/chat/{session_id}/state**

当前 state 接口只返回布尔标记，前端需要完整的 diagnosis_result 和 lesson_content。修改 `get_state` 函数：

```python
@router.get("/chat/{session_id}/state")
async def get_state(session_id: str) -> dict:
    """Get current session state with full data for frontend."""
    state = _sessions.get(session_id)
    if not state:
        return {"mode": "chat", "session_id": session_id}

    result = {
        "session_id": session_id,
        "mode": state.get("mode", "chat"),
        "topic": state.get("topic"),
        "has_graph": state.get("knowledge_graph") is not None,
        "question_progress": f"{state.get('current_question_index', 0)}/{len(state.get('questions', []))}",
        "has_result": state.get("diagnosis_result") is not None,
        "has_lesson": state.get("lesson_content") is not None,
    }

    # Include full data when available
    kg = state.get("knowledge_graph")
    if kg:
        result["graph"] = kg.model_dump() if hasattr(kg, "model_dump") else kg

    dr = state.get("diagnosis_result")
    if dr:
        result["diagnosis_result"] = dr.model_dump() if hasattr(dr, "model_dump") else dr

    lc = state.get("lesson_content")
    if lc:
        result["lesson_content"] = lc

    return result
```

**Step 2: 验证**

Run: `uv run python -m pytest tests/test_api.py -v`

**Step 3: Commit**

```bash
git add src/api/chat.py
git commit -m "feat: extend state API to return full session data"
```

---

## Task 8: 端到端验证 + 清理

**Step 1: 清理不再需要的文件**

`static/session-data.js` 在改造完成后可以删除（但先保留作为 fallback 参考）。

**Step 2: 端到端手动测试**

启动服务：`uv run python run/serve.py`

测试流程：
1. 打开 `http://localhost:8080` → 输入"我想学习勾股定理" → 看到知识图谱
2. 点 Start Diagnosis → 跳转 diagnosis.html → 逐题作答
3. 答完 → 跳转 results.html → 看到诊断结果
4. 点 Start Learning → 跳转 lesson.html → 看到教案

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: frontend-backend integration complete"
```

---

## 任务依赖关系

```
Task 1 (静态文件托管)
  └─► Task 2 (api.js)
       ├─► Task 3 (index.html)
       │    └─► Task 4 (diagnosis.html)
       │         └─► Task 5 (results.html)
       │              └─► Task 6 (lesson.html)
       └─► Task 7 (扩展 state API)
            └─► Task 8 (端到端验证)
```
