# Review Flow PR-3 Implementation Plan — Frontend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the user-visible "先复习一下" entry point in `static/index.html`. When the backend reports `has_history=true` (only possible when `ENABLE_REVIEW_FLOW=true`), show a banner button. Clicking it opens an inline modal that runs through the review questions and shows a result card. Reuses the existing `escapeHtml` / DOM-based modal style from the codebase. No new HTML page.

**Architecture:** Frontend-only PR. Two file edits + one manual browser smoke. No backend changes. The `has_history` flag from `/state` (PR-2 Task 8) gates the banner; modal is inline in `index.html` (no `review.html` per spec §12). Modal renders one question at a time with the same A/B/C/D pattern as `diagnosis.html::buildQuizUI`, but self-contained — no navigation, no LangGraph state changes.

**Tech Stack:** Vanilla JS, Tailwind, fetch API, DOMPurify (already loaded by `api.js`)

**Spec reference:** `docs/superpowers/specs/2026-04-30-review-flow-spec.md` §8, §9 (#14-15), §10 (PR-3)

**Branch:** `demo+memory` (continues PR-2 chain at `26fe6df`)

---

## Architecture Decisions Locked in This Plan

1. **Inline modal, not a new page.** Spec §12 explicitly excludes `review.html`. Pattern: copy the `lesson-format-overlay` modal style (`index.html:908+`) — full-screen overlay with internal flex container.
2. **Banner placement: top of the chat area, above the input.** Visible when (a) a session exists AND (b) `state.has_history === true`. Auto-hides once the user starts a review or after submission.
3. **Modal renders one question at a time.** Mirrors `diagnosis.html` UX. Local state: `questions`, `currentIndex`, `answers`, `selectedOption`. Submit on the last question.
4. **Result card is the same modal**, just with different content swapped in. User clicks "继续学习" → modal closes → user back on `index.html` to pick a topic.
5. **No frontend gate.** Backend gate (`ENABLE_REVIEW_FLOW=false`) makes `/state` return `has_history=false`, banner never shows. Single source of truth.
6. **XSS hygiene strict.** All `innerHTML` writes use `escapeHtml`/`safeMarkdown`. The directive text rendered in the result card is escaped (LLM-generated content).

---

## File Structure

| File | Role | Touched in |
|---|---|---|
| `static/api.js` | Add `startReview()` and `submitReview()` | Task 1 |
| `static/index.html` | Banner + modal markup + flow JS | Task 2 |
| (manual) | Browser smoke test against running services | Task 3 |

---

## Task 1: API client methods

**Files:**
- Modify: `static/api.js` (append two functions)

- [ ] **Step 1: Append `startReview` and `submitReview` to `static/api.js`**

After the existing `getSessionState` function (around line 135), append:

```javascript
// Review flow (PR-3)
async function startReview(sessionId) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/start-review`, {
    method: 'POST',
  });
  if (resp.status === 404) {
    const body = await resp.json().catch(() => ({}));
    return { ok: false, reason: body.detail || 'not-available' };
  }
  if (!resp.ok) throw new Error(`start-review failed: ${resp.status}`);
  const data = await resp.json();
  return { ok: true, questions: data.questions };
}

async function submitReview(sessionId, answers) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/submit-review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  });
  if (!resp.ok) throw new Error(`submit-review failed: ${resp.status}`);
  const data = await resp.json();
  return data.review_result;
}
```

Notes:
- `startReview` returns `{ok: true, questions}` on success and `{ok: false, reason}` on graceful 404 (review disabled, no history, no candidates). Throws only on unexpected errors.
- `submitReview` returns the `review_result` object directly. Any non-2xx is surfaced as a thrown error so the modal can show a retry message.

- [ ] **Step 2: Smoke-test in Node REPL (no browser needed)**

The functions can't run outside a browser (need `fetch` with same-origin), so just `node --check` the file:

```bash
node --check static/api.js && echo "syntax ok"
```

Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add static/api.js
git commit -m "$(cat <<'EOF'
feat(api-js): startReview / submitReview 客户端方法

review-flow PR-3: 接 PR-2 后端两个端点. startReview 把 404 (gate off /
no history) 转成 {ok: false, reason}, 让前端能区分 "正常没历史" 和
"真的失败"; submitReview 直接返回 review_result.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Banner + modal in index.html

**Files:**
- Modify: `static/index.html`

This is the bulk of PR-3. Three sub-changes:

### 2A: Banner element

Find the chat area in `index.html`. The chat container is around the `topNavSkillPill` line and the send-input area. Look for `<main>` or `.content-area` near where the chat history is rendered.

Actually the simplest and least invasive insertion: just before the chat history div, add a placeholder `<div id="reviewBanner">` that's empty by default. Render into it from JS when `has_history === true`.

Add the banner placeholder somewhere near the top of the chat content:

```html
<div id="reviewBanner" style="display:none;"></div>
```

(Exact insertion: find the `<main>` or chat area opening; place this as the FIRST child. The implementer must `grep` for the chat area markup to find the spot — it'll be near where messages are rendered.)

### 2B: Modal markup

Add at the END of `<body>`, just before the closing `</body>` tag, a `<div id="reviewModalRoot"></div>` placeholder. The modal will be rendered into this dynamically by JS so the modal's HTML lives in JS as a template string.

### 2C: JS logic — bulk

In the existing `<script>` block (after the imports/state declarations), add:

```javascript
// ────────────────────────────────────────────────────────────────
// Review flow (PR-3)
// ────────────────────────────────────────────────────────────────
let _reviewQuestions = [];
let _reviewCurrentIdx = 0;
let _reviewAnswers = [];
let _reviewSelectedOption = -1;

async function maybeShowReviewBanner() {
  // Called after session state is loaded. Shows the banner only when
  // backend reports has_history (which requires ENABLE_REVIEW_FLOW=true).
  const sid = currentSessionId;
  if (!sid) return;
  let state;
  try {
    state = await getSessionState(sid);
  } catch (e) {
    return;
  }
  if (!state.has_history) return;

  const banner = document.getElementById('reviewBanner');
  if (!banner) return;
  banner.innerHTML = `
    <div style="margin:16px 0;padding:14px 18px;border:1px solid var(--accent);border-radius:12px;background:var(--bg-tinted);display:flex;align-items:center;gap:12px;">
      <span class="material-symbols-outlined" style="color:var(--accent);font-size:20px;">history_edu</span>
      <div style="flex:1;">
        <div style="font-size:13px;font-weight:600;color:var(--text);">${escapeHtml('您有过往的学习记录')}</div>
        <div style="font-size:11px;color:var(--text-3);margin-top:2px;">${escapeHtml('先做一次复习, 帮 AI 安排本次的学习重点')}</div>
      </div>
      <button id="startReviewBtn" style="padding:8px 16px;border-radius:99px;border:none;background:var(--accent);color:white;font-size:12px;font-weight:600;cursor:pointer;">
        开始复习
      </button>
    </div>`;
  banner.style.display = 'block';
  document.getElementById('startReviewBtn').addEventListener('click', openReviewModal);
}

async function openReviewModal() {
  const sid = currentSessionId;
  if (!sid) return;
  // Show loading modal
  renderReviewModal(`
    <div style="text-align:center;padding:40px;">
      <svg class="animate-spin" style="width:32px;height:32px;color:var(--accent);margin:0 auto 16px;display:block;" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" style="opacity:0.25"></circle>
        <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" style="opacity:0.75"></path>
      </svg>
      <div style="font-size:13px;color:var(--text-3);">正在生成复习题…</div>
    </div>`);

  let result;
  try {
    result = await startReview(sid);
  } catch (e) {
    renderReviewModal(`
      <div style="padding:40px;text-align:center;">
        <div style="font-size:13px;color:#dc2626;margin-bottom:16px;">${escapeHtml('复习生成失败, 请稍后再试')}</div>
        <button onclick="closeReviewModal()" style="padding:8px 20px;border-radius:99px;border:1px solid var(--border);background:white;cursor:pointer;">关闭</button>
      </div>`);
    return;
  }
  if (!result.ok) {
    renderReviewModal(`
      <div style="padding:40px;text-align:center;">
        <div style="font-size:13px;color:var(--text-3);margin-bottom:16px;">${escapeHtml(result.reason || '当前没有可复习的内容')}</div>
        <button onclick="closeReviewModal()" style="padding:8px 20px;border-radius:99px;border:1px solid var(--border);background:white;cursor:pointer;">关闭</button>
      </div>`);
    return;
  }

  _reviewQuestions = result.questions;
  _reviewCurrentIdx = 0;
  _reviewAnswers = new Array(_reviewQuestions.length).fill(-1);
  _reviewSelectedOption = -1;
  renderReviewQuestion();
}

function renderReviewModal(innerHtml) {
  let root = document.getElementById('reviewModalRoot');
  root.innerHTML = `
    <div class="review-overlay" style="position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:1000;display:flex;align-items:center;justify-content:center;padding:24px;">
      <div style="background:white;border-radius:16px;width:100%;max-width:520px;max-height:90vh;overflow-y:auto;">
        ${innerHtml}
      </div>
    </div>`;
  // Click outside to close
  root.querySelector('.review-overlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeReviewModal();
  });
}

function closeReviewModal() {
  document.getElementById('reviewModalRoot').innerHTML = '';
}

function renderReviewQuestion() {
  const q = _reviewQuestions[_reviewCurrentIdx];
  const total = _reviewQuestions.length;
  const isLast = _reviewCurrentIdx === total - 1;
  const letters = ['A', 'B', 'C', 'D'];

  const optionsHtml = q.options.map((opt, idx) => `
    <button class="review-option-btn" data-idx="${idx}" style="display:flex;align-items:center;gap:12px;width:100%;padding:14px 16px;margin:8px 0;border-radius:10px;border:1px solid ${_reviewSelectedOption === idx ? 'var(--accent)' : 'var(--border)'};background:${_reviewSelectedOption === idx ? 'var(--bg-tinted)' : 'white'};cursor:pointer;text-align:left;">
      <span style="font-weight:700;color:var(--accent);">${letters[idx]}</span>
      <span style="font-size:13px;color:var(--text);">${escapeHtml(opt)}</span>
    </button>
  `).join('');

  renderReviewModal(`
    <div style="padding:24px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <div style="font-size:11px;color:var(--text-3);">复习 · 第 ${_reviewCurrentIdx + 1} / ${total} 题</div>
        <button onclick="closeReviewModal()" style="font-size:18px;color:var(--text-3);background:none;border:none;cursor:pointer;">✕</button>
      </div>
      <div style="font-size:11px;color:var(--accent);margin-bottom:8px;">${escapeHtml(q.knowledge_point)}</div>
      <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:16px;">${escapeHtml(q.question)}</div>
      ${optionsHtml}
      <div style="display:flex;justify-content:flex-end;margin-top:16px;">
        <button id="reviewNextBtn" disabled style="padding:8px 20px;border-radius:99px;border:none;background:var(--accent);color:white;font-size:12px;font-weight:600;cursor:pointer;opacity:0.4;">
          ${isLast ? '提交' : '下一题 →'}
        </button>
      </div>
    </div>`);

  // Wire up option clicks (delegation safer than inline onclick for XSS).
  document.querySelectorAll('.review-option-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      _reviewSelectedOption = parseInt(btn.dataset.idx, 10);
      _reviewAnswers[_reviewCurrentIdx] = _reviewSelectedOption;
      renderReviewQuestion();
    });
  });

  // Enable next/submit when an option is picked.
  if (_reviewSelectedOption !== -1) {
    const nextBtn = document.getElementById('reviewNextBtn');
    nextBtn.disabled = false;
    nextBtn.style.opacity = '1';
    nextBtn.addEventListener('click', () => {
      if (isLast) {
        submitReviewFlow();
      } else {
        _reviewCurrentIdx += 1;
        _reviewSelectedOption = _reviewAnswers[_reviewCurrentIdx] ?? -1;
        renderReviewQuestion();
      }
    });
  }
}

async function submitReviewFlow() {
  const sid = currentSessionId;
  if (!sid) return;
  renderReviewModal(`
    <div style="text-align:center;padding:40px;">
      <svg class="animate-spin" style="width:32px;height:32px;color:var(--accent);margin:0 auto 16px;display:block;" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" style="opacity:0.25"></circle>
        <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" style="opacity:0.75"></path>
      </svg>
      <div style="font-size:13px;color:var(--text-3);">正在评分…</div>
    </div>`);

  let result;
  try {
    result = await submitReview(sid, _reviewAnswers);
  } catch (e) {
    renderReviewModal(`
      <div style="padding:40px;text-align:center;">
        <div style="font-size:13px;color:#dc2626;margin-bottom:16px;">${escapeHtml('提交失败, 请稍后再试')}</div>
        <button onclick="closeReviewModal()" style="padding:8px 20px;border-radius:99px;border:1px solid var(--border);background:white;cursor:pointer;">关闭</button>
      </div>`);
    return;
  }

  const weakHtml = (result.weak_kps || []).map(k => `<li style="font-size:12px;color:#dc2626;">${escapeHtml(k)}</li>`).join('');
  const strongHtml = (result.strong_kps || []).map(k => `<li style="font-size:12px;color:#059669;">${escapeHtml(k)}</li>`).join('');
  const scorePct = Math.round((result.score || 0) * 100);

  renderReviewModal(`
    <div style="padding:24px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <div style="font-size:13px;font-weight:700;color:var(--text);">复习完成</div>
        <button onclick="closeReviewModal()" style="font-size:18px;color:var(--text-3);background:none;border:none;cursor:pointer;">✕</button>
      </div>
      <div style="text-align:center;font-size:22px;font-weight:700;color:var(--accent);margin:8px 0 16px;">${scorePct}%</div>
      <div style="display:flex;gap:16px;">
        <div style="flex:1;">
          <div style="font-size:11px;font-weight:600;color:#dc2626;margin-bottom:6px;">仍需巩固</div>
          <ul style="margin:0;padding-left:16px;">${weakHtml || '<li style="font-size:12px;color:var(--text-3);">无</li>'}</ul>
        </div>
        <div style="flex:1;">
          <div style="font-size:11px;font-weight:600;color:#059669;margin-bottom:6px;">已掌握</div>
          <ul style="margin:0;padding-left:16px;">${strongHtml || '<li style="font-size:12px;color:var(--text-3);">无</li>'}</ul>
        </div>
      </div>
      <div style="margin-top:16px;padding:12px;background:var(--bg-tinted);border-radius:8px;font-size:12px;color:var(--text-3);">
        ${escapeHtml(result.focus_directive || '')}
      </div>
      <div style="display:flex;justify-content:flex-end;margin-top:16px;">
        <button onclick="closeReviewModal(); document.getElementById('reviewBanner').style.display='none';" style="padding:8px 20px;border-radius:99px;border:none;background:var(--accent);color:white;font-size:12px;font-weight:600;cursor:pointer;">
          继续学习
        </button>
      </div>
    </div>`);
}
```

### 2D: Hook `maybeShowReviewBanner` into the existing session-load flow

Find where `currentSessionId` gets set and the initial state is rendered (look for `loadInitialState`, `initSession`, or similar in the existing `index.html` JS). Add a call to `maybeShowReviewBanner()` after the session is established.

Implementation hint for the implementer: `grep -n "currentSessionId\s*=\|getSessionState" static/index.html` to find the right spot. Likely in a function that runs on `DOMContentLoaded` or after the user creates a session via the sidebar.

If unclear where to hook, place a `maybeShowReviewBanner()` call inside `DOMContentLoaded` after session_id is determined; if no session exists, the function early-returns harmlessly.

### 2E: Verify

- [ ] **Step 1: `node --check`**

```bash
node --check static/index.html 2>&1 || echo "(html doesn't parse with node — that's expected; we test via browser)"
```

(Node can't `--check` HTML; just lint the JS by extracting it. Skip if too involved — Task 3 covers the actual smoke test.)

- [ ] **Step 2: Sanity-check the file is still valid HTML**

```bash
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('static/index.html').read()); print('html ok')"
```

Expected: `html ok` (no error). The Python HTMLParser is lenient; this just catches grossly malformed markup.

- [ ] **Step 3: Run any frontend-related tests**

```bash
uv run python -m pytest tests/ -q -k "html or static or frontend"
```

Expected: 0 collected (no tests for static files in this project) — that's fine.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(frontend): "先复习一下" 入口 + 内联复习模态

review-flow PR-3 前端: 当后端 /state 返回 has_history=true (需要后端
ENABLE_REVIEW_FLOW=true) 时, index.html 顶部显示 banner. 点击后弹出
内联模态, 单题展示 + 4 选 1 + 上一题/下一题/提交; 提交后展示薄弱/已
掌握两栏 + focus_directive 文本卡; 点 "继续学习" 关闭模态.

不新增 review.html (per spec §12). XSS hygiene: 所有 innerHTML 写入用
escapeHtml. 后端 gate=off 时 has_history=false → banner 不渲染.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Manual browser smoke test

**Goal:** verify in a real browser that the full PR-1 → PR-2 → PR-3 stack works end-to-end. **No code changes; this is operator instructions.**

This task should be performed by the human user (or by an implementer subagent if it has Playwright/browser tooling — most setups don't, so default to "instructions to user").

### Steps for the human

- [ ] **Step 1: Start the memory service**

```bash
cd student_memory && uv run uvicorn app.main:app --port 8090
```

Wait for `INFO: Application startup complete.`

- [ ] **Step 2: Start the main app with `ENABLE_REVIEW_FLOW=true`**

In another terminal:

```bash
cd /Users/zhangyiming/code/OpenAILearning && ENABLE_REVIEW_FLOW=true uv run python run/serve.py
```

Wait for `Uvicorn running on http://127.0.0.1:8080`.

- [ ] **Step 3: Verify the gate-off invariant first**

Open `http://localhost:8080` in a browser. The `ENABLE_REVIEW_FLOW=true` setting *should* allow the banner to appear, but only if the user has prior history. For a fresh user, no banner appears yet.

Run a learning round: pick a topic ("二次方程"), do diagnosis, generate a lesson. This populates `student_memory`. Reload the page.

- [ ] **Step 4: Banner should appear after at least one ingested session**

After the lesson is generated (the `ingest_session` async write), reload `index.html`. You should see the "您有过往的学习记录" banner with a "开始复习" button.

If the banner doesn't appear, debug:
- Open DevTools → Network → look at `/api/chat/<sid>/state` response. Confirm `has_history: true`.
- If `has_history: false`, verify `ENABLE_REVIEW_FLOW=true` in the server's environment (check `os.environ.get` at the top of `chat.py` was evaluated at startup).
- If `has_history: true` but no banner, check console for JS errors and confirm `maybeShowReviewBanner` was hooked into the right initial-load function in 2D.

- [ ] **Step 5: Click "开始复习"**

Modal should appear with a loading spinner, then question 1 of N. Each question shows knowledge point label + question text + 4 options.

- [ ] **Step 6: Answer all questions**

Pick options. Click "下一题" to advance. On the last question, button shows "提交".

- [ ] **Step 7: Submit and verify result card**

Modal switches to result view: percentage score, two columns (仍需巩固 / 已掌握), focus_directive text below. Click "继续学习" to close.

- [ ] **Step 8: Verify focus_directive flows to the next lesson**

Pick a new topic and run through diagnosis → lesson. Inspect the lesson content (it should subtly reference the weak KPs from the review). Optionally peek at the prompt sent to the LLM (server logs or by adding a temporary `print(prompt)` in `generate_lesson_per_point`).

- [ ] **Step 9: Verify gate-off graceful behavior**

Stop the server. Restart **without** `ENABLE_REVIEW_FLOW`:

```bash
unset ENABLE_REVIEW_FLOW; uv run python run/serve.py
```

Reload the page. Banner should NOT appear regardless of history. Endpoints should 404.

- [ ] **Step 10: Document smoke-test result**

If everything works, mark Task 3 complete. If anything broke, file a bug pointing to the failing step.

---

## Acceptance Checklist

- [ ] `static/api.js` has `startReview` and `submitReview` (Task 1)
- [ ] `index.html` shows banner only when `state.has_history === true` (Task 2D)
- [ ] Modal renders question + 4 options + nav buttons (Task 2C)
- [ ] Modal renders result card with score / weak / strong / directive (Task 2C)
- [ ] All `innerHTML` writes go through `escapeHtml` (Task 2C — XSS hygiene)
- [ ] Backend `ENABLE_REVIEW_FLOW=false` → banner never appears (smoke Task 3 Step 9)
- [ ] Backend `ENABLE_REVIEW_FLOW=true` + history → banner + full flow works (smoke Task 3 Steps 4-8)
- [ ] No regressions in existing chat / diagnosis / lesson flows (smoke Task 3 Step 8)

---

## Out of Scope for PR-3

- Separate `review.html` page (spec §12 — explicit no)
- Error history / "错题本" (spec §12 — explicit no)
- Spaced-repetition timing UI (spec §12 — explicit no)
- Multi-user account UI (spec §12 — explicit no)
- Browser automation / Playwright tests (no project pattern; manual smoke is sufficient)
- Mobile-specific styling (existing site is desktop-first)
- KC-routing focus_directive integration (still a TODO — tracked in commit `911e862`)
