# Hero Landing State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the gray-sentence empty state on brand-new sessions in `static/index.html` with a centered hero (outlined book icon + serif time-of-day greeting + floated input + four starter chips). Hero disappears as soon as the session is no longer fresh (first message sent or mode toggled) and does not return until the user clicks **New Chat**.

**Architecture:** A single `body.is-fresh-landing` class drives the visual switch. A `#heroLanding` section is added inside `<main id="mainContent">`. When the class is active, JS moves the existing `#bottomBar` element into the hero so the input flows naturally inside a flex column — the textarea, send button, `/skill` popup, and all existing handlers stay attached to the same DOM nodes throughout, so no event re-wiring or input duplication. Four wire-up points (hydration, `handleSend`, `_handleModeToggleClick`, `renderEmptyState`) toggle the flag.

**Tech Stack:** Plain HTML + CSS + vanilla JS inside the existing `static/index.html` (no build step). Material Symbols Outlined (already loaded) for the icon. EB Garamond from Google Fonts for the headline, with Georgia fallback. No new files, no Python changes, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-02-hero-landing-design.md`

---

## File Structure

This is a single-file change:

- Modify: `static/index.html` — the only file touched by tasks 1–5.
- Append: `development_log.md` — one entry in task 7.

No new files, no changes to `static/sidebar.js`, `static/api.js`, or anything under `src/`.

Within `static/index.html`, edits land in five regions:
1. `<head>` — add EB Garamond font link.
2. Style block (`<style>` ... `</style>`) — add hero CSS rules.
3. `<main id="mainContent">` — add `#heroLanding` markup before `#chatArea`.
4. `<script>` section near `renderEmptyState` (line 691) — add the hero JS helpers.
5. Four targeted edits in existing functions for wire-up.

This file is already 1676 lines and project convention puts everything in `index.html`. We follow that convention rather than splitting CSS/JS out.

---

## Verification Notes

This is a UI change and the project has no frontend test runner. Each task ends with a manual smoke check using the dev server (`./run` per project convention) plus a browser at the local URL the server prints. Use Chrome DevTools to:
- Confirm DOM additions (`document.getElementById('heroLanding')`)
- Call functions from the console (`enterFreshLanding()`, `exitFreshLanding()`)
- Inspect computed styles to verify CSS rules apply.

Run the dev server once at the start of work and leave it running across tasks (Tailwind CDN and vis-network are loaded from CDNs, so no rebuild is needed when editing the static file).

If `./run` is not appropriate for your environment, use whatever serves `static/` locally; the spec confirms project convention is `./run`.

---

## Task 1: Add EB Garamond font link

**Why:** The hero headline uses a serif at ~58px. Inter (already loaded) is sans. EB Garamond from Google Fonts gives a literary feel that matches the reference mockup; Georgia is the system fallback.

**Files:**
- Modify: `static/index.html` (in `<head>`, near line 11–12 alongside the existing Inter and Material Symbols `<link>` tags)

- [ ] **Step 1: Read current `<head>` to locate exact insertion point**

Run: `grep -n "fonts.googleapis.com" static/index.html`
Expected output:
```
11:<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
12:<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
```

- [ ] **Step 2: Insert the EB Garamond link immediately after the Inter link**

Edit `static/index.html`. After the line:
```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
```
add:
```html
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;500;600&display=swap" rel="stylesheet"/>
```

- [ ] **Step 3: Verify in browser**

Reload the running dev server in the browser. Open DevTools → Network → filter "font". Confirm an `EB Garamond` font request fires and returns 200. Open DevTools → Console — no new errors.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): load EB Garamond for upcoming hero serif headline

EOF
)"
```

---

## Task 2: Add `#heroLanding` HTML scaffolding (hidden)

**Why:** Set up the DOM container the JS helpers will populate. Hidden by default so the live app is unaffected by this task alone.

**Files:**
- Modify: `static/index.html` (inside `<main id="mainContent">` near line 305, immediately before `<div id="chatArea">`)

- [ ] **Step 1: Locate insertion point**

Run: `grep -n 'id="mainContent"\|id="chatArea"' static/index.html`
Expected:
```
304:<main id="mainContent">
305:  <div id="chatArea">
```

- [ ] **Step 2: Insert `#heroLanding` between `<main id="mainContent">` and `<div id="chatArea">`**

Replace:
```html
<main id="mainContent">
  <div id="chatArea">
```
with:
```html
<main id="mainContent">
  <section id="heroLanding" aria-label="Start a new session" style="display:none;">
    <div class="hero-inner">
      <div class="hero-headline-row">
        <span class="hero-mark material-symbols-outlined" aria-hidden="true">auto_stories</span>
        <h1 class="hero-headline" id="heroHeadline"></h1>
      </div>
      <!-- #bottomBar gets appended here at runtime when is-fresh-landing is active -->
    </div>
  </section>
  <div id="chatArea">
```

The inline `style="display:none;"` is temporary — Task 3 replaces it with a CSS rule. We keep it inline for this task so the intermediate commit doesn't change visible behaviour.

- [ ] **Step 3: Verify**

Reload the browser. DevTools → Console:
```js
document.getElementById('heroLanding')
```
Expected: returns the `<section>` element (not `null`). The page layout is unchanged (hero is `display:none`).

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): add hidden #heroLanding scaffold inside #mainContent

EOF
)"
```

---

## Task 3: Add hero CSS rules

**Why:** Make the hero visually correct under `body.is-fresh-landing` so the JS in Task 4 has something to reveal. Also drop the inline `display:none` from Task 2 — the CSS rule takes over.

**Files:**
- Modify: `static/index.html` (style block; add rules near the end of the existing style block, just before `</style>` around line 272)
- Modify: `static/index.html` (the `<section id="heroLanding" ... style="display:none;">` line — drop the inline style)

- [ ] **Step 1: Locate the end of the style block**

Run: `grep -n "^</style>" static/index.html`
Expected: a single line number like `272:</style>`.

- [ ] **Step 2: Append the hero CSS just before `</style>`**

Insert this block immediately before the `</style>` line:
```css
/* ── Hero Landing ────────────────────────────────────────────────────────── */
#heroLanding { display: none; }

body.is-fresh-landing #chatArea,
body.is-fresh-landing #reviewBanner { display: none; }

body.is-fresh-landing #heroLanding {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 32px;
  width: 100%;
}

.hero-inner {
  display: flex;
  flex-direction: column;
  gap: 28px;
  width: 100%;
  max-width: 720px;
}

.hero-headline-row {
  display: flex;
  align-items: center;
  gap: 18px;
}

.hero-mark.material-symbols-outlined {
  font-size: 48px;
  color: var(--text);
  font-variation-settings: 'wght' 300, 'FILL' 0;
  flex-shrink: 0;
}

.hero-headline {
  font-family: 'EB Garamond', 'Source Serif Pro', Georgia, 'Times New Roman', serif;
  font-size: clamp(34px, 5vw, 58px);
  font-weight: 500;
  line-height: 1.1;
  letter-spacing: -0.012em;
  color: var(--text);
  margin: 0;
}

body.is-fresh-landing #bottomBar {
  position: static;
  left: auto; right: auto; bottom: auto;
  background: transparent;
  border-top: none;
  padding: 0;
  width: 100%;
}

body.is-fresh-landing #bottomBar .hint-row {
  margin-top: 12px;
  justify-content: flex-start;
}
```

- [ ] **Step 3: Remove the inline `display:none` from the `#heroLanding` tag**

Find:
```html
<section id="heroLanding" aria-label="Start a new session" style="display:none;">
```
Replace with:
```html
<section id="heroLanding" aria-label="Start a new session">
```

The new CSS rule `#heroLanding { display: none; }` keeps the hero hidden by default.

- [ ] **Step 4: Smoke-test the CSS in the browser**

Reload the page. The visible layout should be unchanged (hero still hidden). Then in DevTools Console run:
```js
document.body.classList.add('is-fresh-landing')
```
Expected behaviour:
- `#chatArea` and `#reviewBanner` disappear.
- `#heroLanding` becomes visible: a centered flex column with the outlined open-book icon on the left of a (currently empty) headline.
- The `#bottomBar` does **not** yet relocate (the JS DOM-move in Task 4 handles that) — it stays pinned to the bottom. That is expected at this intermediate stage.

Then clean up:
```js
document.body.classList.remove('is-fresh-landing')
```
The original layout returns.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): hero landing CSS — centered serif greeting layout

EOF
)"
```

---

## Task 4: Add the hero JS helpers

**Why:** Add the state flag, the greeting computation, the chip renderer, and the `enter/exit/maybeEnter` controllers. None of these are called yet — that happens in Task 5 — but each can be exercised manually from the DevTools console to verify it works before wiring it up.

**Files:**
- Modify: `static/index.html` (insert the new functions immediately above `function renderEmptyState(mode)` at line 691)

- [ ] **Step 1: Locate the insertion anchor**

Run: `grep -n "^function renderEmptyState" static/index.html`
Expected: `691:function renderEmptyState(mode) {`

- [ ] **Step 2: Insert the JS helpers immediately above `function renderEmptyState(mode)`**

Add this block as new lines immediately before the existing `function renderEmptyState(mode) {` line:
```js
// ─── Hero landing (fresh-session homepage) ────────────────────────────────
let _isFreshLanding = false;

function computeHeroGreeting() {
  const h = new Date().getHours();
  if (h >= 6  && h < 11) return 'Morning — what shall we learn today?';
  if (h >= 11 && h < 17) return 'Afternoon — what shall we learn next?';
  if (h >= 17 && h < 23) return 'Evening — what shall we learn tonight?';
  return "Late night — still curious? Let's learn.";
}

function renderHeroStarterChips() {
  const chips = [
    { label: '✨ Photosynthesis',                 fill: 'Photosynthesis' },
    { label: '✨ Linear algebra basics',          fill: 'Linear algebra basics' },
    { label: '🔍 Diagnose what I already know',   fill: 'Diagnose what I already know' },
    { label: '/skill  Pick a teaching method',    fill: '/skill ' },
  ];
  const slot = document.getElementById('suggestBtns');
  slot.innerHTML = chips.map((c, i) =>
    `<button class="hint-chip" data-hero-chip="${i}">${escapeHtml(c.label)}</button>`
  ).join('');
  slot.querySelectorAll('[data-hero-chip]').forEach(btn => {
    btn.addEventListener('click', () => {
      const c = chips[Number(btn.dataset.heroChip)];
      const input = document.getElementById('chatInput');
      input.value = c.fill;
      input.focus();
      if (c.fill.startsWith('/skill')) handleInputChange({ target: input });
    });
  });
}

function enterFreshLanding() {
  if (_isFreshLanding) return;
  _isFreshLanding = true;
  document.body.classList.add('is-fresh-landing');
  document.getElementById('heroHeadline').textContent = computeHeroGreeting();
  const hero = document.querySelector('#heroLanding .hero-inner');
  const bar  = document.getElementById('bottomBar');
  if (bar.parentElement !== hero) hero.appendChild(bar);
  renderHeroStarterChips();
  document.getElementById('chatInput').focus();
}

function exitFreshLanding() {
  if (!_isFreshLanding) return;
  _isFreshLanding = false;
  document.body.classList.remove('is-fresh-landing');
  const bar = document.getElementById('bottomBar');
  if (bar.parentElement !== document.body) document.body.appendChild(bar);
  document.getElementById('suggestBtns').innerHTML = '';
}

function maybeEnterFreshLanding() {
  if (_chatMessages.length === 0 &&
      _learningMessages.length === 0 &&
      _learningArtifactsEmpty()) {
    enterFreshLanding();
  } else {
    exitFreshLanding();
  }
}
```

- [ ] **Step 3: Verify in the browser**

Reload. The visible app is unchanged (no helper is called automatically yet). Then in the DevTools Console run:
```js
enterFreshLanding()
```
Expected:
- The hero shows: outlined `auto_stories` icon + a serif greeting matching the current local time bucket.
- The input bar is now inside the hero (use Elements panel: `#bottomBar` is a child of `#heroLanding > .hero-inner`).
- Four chips appear in the chip row: `✨ Photosynthesis`, `✨ Linear algebra basics`, `🔍 Diagnose what I already know`, `/skill  Pick a teaching method`.

Click each chip and confirm:
- The first three populate the input with the matching text.
- The fourth populates with `/skill ` and the skill popup opens (it is the existing popup wired via `handleInputChange`).

Then run:
```js
exitFreshLanding()
```
Expected: hero hides, `#bottomBar` returns as a direct child of `<body>` (Elements panel shows it as a sibling of `<main id="mainContent">` again), chip row is empty, input bar returns to its bottom-pinned position.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): hero landing JS — greeting, chips, enter/exit controllers

EOF
)"
```

---

## Task 5: Wire up the four touch points

**Why:** Connect the helpers to the lifecycle so the hero actually appears on fresh sessions and dismisses on the right user actions.

**Files:**
- Modify: `static/index.html` — four targeted edits.

- [ ] **Step 1: Add the early-return in `renderEmptyState(mode)`**

Find (around line 691):
```js
function renderEmptyState(mode) {
  const text = mode === 'chat'
    ? "Let's start a conversation — I can chat about any topic, or recommend a subject for structured learning."
    : 'Enter a topic you\'d like to learn and start structured study.';
  document.getElementById('chatHistory').innerHTML =
    `<div style="color:var(--text-3);font-size:13px;padding:24px;text-align:center;">${escapeHtml(text)}</div>`;
}
```
Replace with:
```js
function renderEmptyState(mode) {
  if (_isFreshLanding) return;
  const text = mode === 'chat'
    ? "Let's start a conversation — I can chat about any topic, or recommend a subject for structured learning."
    : 'Enter a topic you\'d like to learn and start structured study.';
  document.getElementById('chatHistory').innerHTML =
    `<div style="color:var(--text-3);font-size:13px;padding:24px;text-align:center;">${escapeHtml(text)}</div>`;
}
```

- [ ] **Step 2: Add `exitFreshLanding()` at the top of `handleSend()`**

Find (around line 933):
```js
async function handleSend(text) {
```
Insert `exitFreshLanding();` as the first statement of the function body. The result should be:
```js
async function handleSend(text) {
  exitFreshLanding();
```
(Then the existing body continues unchanged.)

- [ ] **Step 3: Add `exitFreshLanding()` after the guards in `_handleModeToggleClick`**

Find (line 383):
```js
async function _handleModeToggleClick(targetMode) {
  if (_sendInFlight) return;
  if (targetMode === _sessionMode) return;
  const buttons = document.querySelectorAll('#modeToggle button');
```
Replace with:
```js
async function _handleModeToggleClick(targetMode) {
  if (_sendInFlight) return;
  if (targetMode === _sessionMode) return;
  exitFreshLanding();
  const buttons = document.querySelectorAll('#modeToggle button');
```

`exitFreshLanding()` runs **after** the early-return guards so that clicking the already-active mode is a true no-op (the hero stays).

- [ ] **Step 4: Add `maybeEnterFreshLanding()` after hydration's `renderActiveView`**

Find (around line 1441–1442):
```js
  _renderModeToggle(state.session_mode || 'chat');
  renderActiveView(_sessionMode);
```
Replace with:
```js
  _renderModeToggle(state.session_mode || 'chat');
  renderActiveView(_sessionMode);
  maybeEnterFreshLanding();
```

- [ ] **Step 5: Smoke-test the wire-up**

Reload the dev server. The page is currently a fresh session (or click **New Chat** to make one). Expected:
- Hero appears automatically on load — outlined book icon, serif greeting (current time bucket), centered input, four starter chips.

Click a chip → input fills. Then type/edit if needed and press Enter or click send. Expected:
- Hero dismisses, input snaps back to bottom-pinned position, the user message renders in `#chatHistory`, AI reply streams in.

Click **New Chat** in the sidebar → page reloads to a new session → hero appears again.

From the hero state (new session, no message sent), click the **Learning** mode toggle in the top nav. Expected:
- Hero dismisses immediately.
- The small gray *"Enter a topic you'd like to learn…"* sentence appears (from `renderEmptyState('learning')`).

Click back to **Chat**. Expected:
- Hero does **not** reappear; the small gray *"Let's start a conversation…"* sentence shows instead. Only **New Chat** can bring the hero back.

From a hero state, click the **Chat** toggle (the currently-active mode). Expected:
- Nothing changes; the hero stays. The mode-toggle early-return guard correctly prevents a false dismissal.

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): wire hero landing into hydration / send / mode-toggle / empty-state

Hero shows automatically on brand-new sessions and dismisses on the
first message send or on an actual mode change. Clicking the already-
active mode keeps the hero. After dismissal, the hero only returns on
New Chat (which reloads the page).

EOF
)"
```

---

## Task 6: Full manual regression checklist

**Why:** Walk the test-plan items the spec listed, in one pass, to catch any breakage from earlier tasks (font, layout, JS) before declaring done.

**Files:** none — this task is verification only.

- [ ] **Step 1: Load fresh session — hero appears, greeting matches local time**

In a fresh browser tab, load the app. Confirm:
- Hero is visible, centered, with the outlined `auto_stories` icon and a serif headline.
- Headline matches your current local hour: 06–10 *"Morning — what shall we learn today?"*, 11–16 *"Afternoon — what shall we learn next?"*, 17–22 *"Evening — what shall we learn tonight?"*, 23–05 *"Late night — still curious? Let's learn."*
- Four starter chips below the input.

- [ ] **Step 2: Send a message — hero collapses**

Type any text and press Enter. Confirm:
- Hero disappears.
- Input snaps back to bottom-pinned.
- User message + streaming AI reply render normally.

- [ ] **Step 3: New Chat — hero returns**

Click **New Chat** in the sidebar. Confirm:
- Page reloads; hero reappears.

- [ ] **Step 4: From hero, click Learning — hero dismisses, gray text shows**

On a fresh hero, click the **Learning** toggle. Confirm:
- Hero disappears.
- Small gray "Enter a topic you'd like to learn…" appears in the chat area.

- [ ] **Step 5: Click Chat — gray sentence remains, hero does NOT return**

Confirm:
- Small gray "Let's start a conversation…" replaces the learning gray text.
- Hero stays dismissed.

- [ ] **Step 6: Click each starter chip**

Open another fresh session via **New Chat**. Click each of the four chips in turn. Confirm:
- `✨ Photosynthesis` → input = `Photosynthesis`.
- `✨ Linear algebra basics` → input = `Linear algebra basics`.
- `🔍 Diagnose what I already know` → input = `Diagnose what I already know`.
- `/skill  Pick a teaching method` → input = `/skill ` and the skill popup opens above the input.

- [ ] **Step 7: Collapse sidebar — hero stays centered**

On a fresh hero, click the sidebar collapse arrow. Confirm:
- Hero re-centers in the new wider viewport area with no horizontal jank beyond the existing 0.2s transition.

- [ ] **Step 8: Time-of-day buckets**

In DevTools Console, mock `Date.prototype.getHours` and re-trigger the greeting:
```js
const _orig = Date.prototype.getHours;
Date.prototype.getHours = () => 7;
document.getElementById('heroHeadline').textContent = computeHeroGreeting();
// → "Morning — what shall we learn today?"
Date.prototype.getHours = () => 13;
document.getElementById('heroHeadline').textContent = computeHeroGreeting();
// → "Afternoon — what shall we learn next?"
Date.prototype.getHours = () => 20;
document.getElementById('heroHeadline').textContent = computeHeroGreeting();
// → "Evening — what shall we learn tonight?"
Date.prototype.getHours = () => 2;
document.getElementById('heroHeadline').textContent = computeHeroGreeting();
// → "Late night — still curious? Let's learn."
Date.prototype.getHours = _orig;
```
Confirm each substitution sets the expected headline.

- [ ] **Step 9: Block Google Fonts — Georgia fallback still readable**

DevTools → Network → block URL pattern `fonts.googleapis.com`. Hard-reload. Confirm:
- Headline renders in Georgia (or another system serif). Layout intact.

Unblock the pattern after the check.

- [ ] **Step 10: Existing session does NOT show hero**

Send at least one message in a session, then reload the page (same URL with the same `?session_id=`). Confirm:
- Hero does **not** appear; chat history renders normally.
- Then switch to **Learning** mode. Confirm the existing learning-mode behaviour is unchanged (artifacts, gray sentence if empty, etc.).

- [ ] **Step 11: Note any failures**

If anything above fails, do NOT proceed to Task 7. Diagnose the regression, fix it in a follow-up commit (referencing the failing checklist item in the message), and re-run the affected checks.

---

## Task 7: Append dev-log entry

**Why:** Project convention (see `development_log.md`) is a short dated bullet describing each user-visible change. This is the final task; it ends the implementation.

**Files:**
- Modify: `development_log.md` (append a new dated section at the end)

- [ ] **Step 1: Read the last dated entry to match style**

Run: `tail -5 development_log.md`

- [ ] **Step 2: Append a new section dated 2026-06-02 (or today, if later)**

Append to the end of `development_log.md`:
```markdown

## 2026-06-02
- 首页空状态换成 hero landing：仅在「全新会话」上显示，居中放 outlined `auto_stories` 图标 + 时段问候（Morning / Afternoon / Evening / Late night），输入框上浮到屏幕中央，下面挂 4 个 starter chips（2 个主题示例 + 「Diagnose what I already know」+ `/skill` 入口）。
- 通过 `body.is-fresh-landing` + 运行时把 `#bottomBar` 临时挪进 `#heroLanding` 里实现：textarea / send / `/skill` 弹窗都是同一个 DOM，事件无须重接。发首条消息或切 mode 即收起，且只有 New Chat 能让 hero 再回来。
```

(If today's date already has a section in `development_log.md`, append the bullets to that section instead of creating a duplicate header.)

- [ ] **Step 3: Verify and commit**

Run: `tail -10 development_log.md` and confirm the entry reads cleanly.

```bash
git add development_log.md
git commit -m "$(cat <<'EOF'
docs(log): hero landing state for fresh sessions

EOF
)"
```

---

## Self-review (run after writing this plan)

**Spec coverage check** (spec is `docs/superpowers/specs/2026-06-02-hero-landing-design.md`):

- *Architecture — DOM additions:* Task 2.
- *Architecture — CSS additions:* Task 3.
- *Architecture — Font:* Task 1.
- *Architecture — JS additions:* Task 4.
- *Wire-up: hydration completion:* Task 5 step 4.
- *Wire-up: `handleSend()` entry:* Task 5 step 2.
- *Wire-up: `_handleModeToggleClick` after guards:* Task 5 step 3.
- *Wire-up: `renderEmptyState` early return:* Task 5 step 1.
- *Greeting computation (`computeHeroGreeting`):* Task 4 + Task 6 step 8.
- *Starter chips (4, with the `/skill` popup behaviour):* Task 4 + Task 6 step 6.
- *Edge case — sidebar collapsed:* Task 6 step 7.
- *Edge case — font load fallback:* Task 6 step 9.
- *Edge case — existing session does not show hero:* Task 6 step 10.
- *Edge case — skill popup positioning:* covered implicitly by Task 4 step 3 + Task 6 step 6 (the popup opens above the input in the hero).
- *Edge case — programmatic sends (`Study ${topic}`):* covered by Task 5 step 2 (all programmatic sends go through `handleSend`).
- *Edge case — `reviewBanner` race:* covered by Task 3 CSS (`body.is-fresh-landing #reviewBanner { display: none; }`).

No spec requirement lacks a task.

**Placeholder scan:** No "TBD" / "TODO" / "implement later" / "fill in details" / "add appropriate error handling" / "similar to Task N" appear in the plan. Every code step shows the actual code.

**Type / name consistency:** `_isFreshLanding`, `enterFreshLanding`, `exitFreshLanding`, `maybeEnterFreshLanding`, `computeHeroGreeting`, `renderHeroStarterChips`, `#heroLanding`, `.hero-inner`, `.hero-headline-row`, `.hero-mark`, `.hero-headline`, `#heroHeadline` — every identifier is used consistently across tasks.
