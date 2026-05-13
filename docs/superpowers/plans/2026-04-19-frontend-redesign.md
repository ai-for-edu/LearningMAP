# Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle all 5 static HTML pages to a warm-cream / orange-accent Claude-style design system, with sidebar, step nav bar, and 3-column lesson layout — zero JS changes.

**Architecture:** Each page keeps its own `<style>` block (no shared CSS file). CSS custom properties (`--bg`, `--accent`, etc.) are defined on `:root` in every file. Tailwind CDN stays for utility classes but the custom color/font config is replaced with the new tokens. All JavaScript logic, `api.js`, `sidebar.js`, and backend code are untouched.

**Tech Stack:** Vanilla HTML/CSS, Tailwind CDN (utility classes only), Inter font from Google Fonts, Material Symbols icons (retained), vis.js (retained)

---

## File Structure

| File | Change type |
|------|-------------|
| `static/index.html` | Replace `<style>` + Tailwind config; restyle sidebar, topnav, chat area, input bar |
| `static/diagnosis.html` | Replace `<style>` + Tailwind config; add sidebar + step nav; restyle graph canvas + quiz cards |
| `static/lesson.html` | Replace `<style>` + Tailwind config; restyle 3-col layout, TOC, inline KS cards, right panel |
| `static/slides.html` | Replace `<style>` + Tailwind config; add sidebar + step nav; restyle slide canvas shell |
| `static/results.html` | Replace `<style>` + Tailwind config; restyle score card, breakdown cards, CTA |

---

## Shared CSS tokens (reference block — copy into each page's `:root`)

```css
:root {
  --bg: #FAF9F7;
  --bg-sidebar: #F0EDE8;
  --bg-doc: #FDFCFA;
  --border: #EAE6E0;
  --border-input: #E0DDD8;
  --accent: #CF6833;
  --accent-light: #FFF3EC;
  --accent-border: #F5D5BF;
  --accent-dark: #9E4A2A;
  --text: #1a1a1a;
  --text-2: #4a4a4a;
  --text-3: #888;
  --text-4: #B0A898;
  --user-bubble: #EDE9E3;
  --error: #DC2626;
  --success: #059669;
}
```

---

## Task 1: Restyle `static/index.html`

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Replace the Tailwind config + `<style>` block**

Replace the entire `<script id="tailwind-config">` block and the `<style>` block (lines ~13–126) with:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<script id="tailwind-config">
tailwind.config = {
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'sans-serif'] },
    },
  },
}
</script>
<style>
:root {
  --bg: #FAF9F7;
  --bg-sidebar: #F0EDE8;
  --border: #EAE6E0;
  --border-input: #E0DDD8;
  --accent: #CF6833;
  --accent-light: #FFF3EC;
  --accent-border: #F5D5BF;
  --accent-dark: #9E4A2A;
  --text: #1a1a1a;
  --text-2: #4a4a4a;
  --text-3: #888;
  --text-4: #B0A898;
  --user-bubble: #EDE9E3;
  --error: #DC2626;
  --success: #059669;
}
* { box-sizing: border-box; }
body { background: var(--bg); font-family: 'Inter', sans-serif; color: var(--text); margin: 0; }

/* ── Sidebar ── */
#sidebar {
  width: 210px; height: 100vh; position: fixed; left: 0; top: 0;
  background: var(--bg-sidebar); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; padding: 16px 12px; gap: 4px;
  z-index: 40; transition: width 0.2s;
}
#sidebar.collapsed { width: 0; padding: 0; overflow: hidden; }
.sidebar-logo { display: flex; align-items: center; gap: 8px; padding: 6px 8px; margin-bottom: 8px; }
.sidebar-logo-icon {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 15px; font-weight: 800;
}
.sidebar-logo-text { font-size: 14px; font-weight: 800; color: var(--text); letter-spacing: -.02em; }
.new-session-btn {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border: 1.5px dashed #D4CFC8; border-radius: 8px; background: transparent;
  font-size: 13px; font-weight: 500; color: var(--text-3); cursor: pointer;
  margin-bottom: 12px; transition: all .15s; width: 100%;
}
.new-session-btn:hover { background: white; border-color: #bbb; }
.sidebar-section-label {
  font-size: 10px; font-weight: 600; color: var(--text-4);
  letter-spacing: .06em; text-transform: uppercase;
  padding: 0 8px; margin: 8px 0 4px;
}
.session-item {
  display: flex; align-items: center; gap: 8px; padding: 7px 10px;
  border-radius: 7px; font-size: 13px; color: var(--text-3); cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: background .1s;
}
.session-item:hover { background: #E8E4DE; }
.session-item.active {
  background: white; color: var(--text); font-weight: 500;
  box-shadow: 0 1px 3px rgba(0,0,0,.07);
}
.session-dot { width: 6px; height: 6px; border-radius: 50%; background: #D4CFC8; flex-shrink: 0; }
.session-dot.orange { background: var(--accent); }
.sidebar-status-tag {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--accent-light); border: 1px solid var(--accent-border);
  color: var(--accent-dark); font-size: 10px; font-weight: 600;
  padding: 2px 8px; border-radius: 99px; margin-left: auto; flex-shrink: 0;
}

/* ── Top Nav ── */
#topNav {
  height: 50px; position: fixed; top: 0; left: 210px; right: 0; z-index: 30;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
  transition: left 0.2s;
}
#sidebar.collapsed ~ #topNav { left: 0; }
.topnav-left { display: flex; align-items: center; gap: 8px; }
.topnav-title { font-size: 14px; font-weight: 600; color: var(--text); }
.topnav-skill-pill {
  display: flex; align-items: center; gap: 5px;
  background: var(--accent-light); border: 1px solid var(--accent-border);
  color: var(--accent-dark); font-size: 11px; font-weight: 600;
  padding: 4px 10px; border-radius: 99px; cursor: pointer;
}
.topnav-skill-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }

/* ── Main Content ── */
#mainContent {
  margin-left: 210px; padding-top: 50px; min-height: 100vh;
  display: flex; flex-direction: column; transition: margin-left 0.2s;
}
#sidebar.collapsed ~ #mainContent { margin-left: 0; }

/* ── Home state ── */
#homeState { display: flex; flex-direction: column; align-items: center; justify-content: center; flex: 1; padding: 60px 20px; }
.home-greeting { font-size: 26px; font-weight: 800; color: var(--text); margin-bottom: 8px; }
.home-sub { font-size: 13px; color: var(--text-3); margin-bottom: 24px; }
.home-input-wrap { width: 100%; max-width: 520px; }
.home-hint-chips { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; justify-content: center; }
.hint-chip {
  font-size: 11px; color: var(--text-4); background: var(--bg-sidebar);
  border-radius: 99px; padding: 4px 12px; cursor: pointer; border: none;
}
.hint-chip:hover { background: #E8E4DE; color: var(--text-3); }
.recent-sessions-title { font-size: 11px; font-weight: 600; color: var(--text-4); text-transform: uppercase; letter-spacing: .06em; margin: 28px 0 10px; width: 100%; max-width: 520px; }
.recent-session-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; width: 100%; max-width: 520px; }
.recent-session-card {
  background: white; border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; cursor: pointer; transition: box-shadow .15s;
}
.recent-session-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.07); }
.recent-session-topic { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.recent-session-meta { font-size: 11px; color: var(--text-4); display: flex; align-items: center; gap: 6px; }

/* ── Chat Area ── */
#chatArea { flex: 1; padding: 24px 32px 120px; display: flex; flex-direction: column; gap: 20px; max-width: 760px; width: 100%; margin: 0 auto; }
.user-msg { display: flex; justify-content: flex-end; }
.user-bubble {
  background: var(--user-bubble); border-radius: 18px 18px 4px 18px;
  padding: 10px 16px; max-width: 70%; font-size: 13.5px; color: var(--text-2); line-height: 1.55;
}
.ai-msg { display: flex; gap: 12px; }
.ai-avatar {
  width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0; margin-top: 2px;
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 13px; font-weight: 700;
}
.ai-content { flex: 1; }
.ai-name { font-size: 11px; font-weight: 600; color: var(--text-4); margin-bottom: 5px; letter-spacing: .03em; text-transform: uppercase; }
.ai-text { font-size: 13.5px; color: var(--text-2); line-height: 1.65; }
.ai-text strong { color: var(--text); font-weight: 600; }

/* Knowledge graph card */
.kg-card { background: white; border: 1px solid var(--border); border-radius: 14px; overflow: hidden; margin-top: 8px; }
.kg-header { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
.kg-title { font-size: 13px; font-weight: 600; color: var(--text); }
.kg-badge { font-size: 11px; color: var(--accent); background: var(--accent-light); padding: 2px 8px; border-radius: 99px; font-weight: 600; border: 1px solid var(--accent-border); }
.kg-footer { padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; }
.kg-legend { display: flex; gap: 12px; }
.kg-legend-item { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text-3); }
.kg-legend-dot { width: 8px; height: 8px; border-radius: 50%; }
.kg-start-btn {
  background: var(--accent); color: white;
  font-size: 12px; font-weight: 600; padding: 6px 16px;
  border-radius: 99px; border: none; cursor: pointer; transition: background .15s;
}
.kg-start-btn:hover { background: var(--accent-dark); }

/* Diagnosis result card (rendered inline in chat) */
.diag-card { background: white; border: 1px solid var(--border); border-radius: 14px; overflow: hidden; margin-top: 8px; padding: 16px; }

/* ── Input Bar ── */
#bottomBar {
  position: fixed; bottom: 0; left: 210px; right: 0; z-index: 30;
  padding: 12px 20px 16px; background: var(--bg);
  border-top: 1px solid var(--border); transition: left 0.2s;
}
#sidebar.collapsed ~ #bottomBar { left: 0; }
.input-outer { max-width: 720px; margin: 0 auto; position: relative; }
.input-wrap {
  background: white; border: 1.5px solid var(--border-input); border-radius: 14px;
  padding: 10px 14px; display: flex; align-items: flex-end; gap: 8px;
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
}
.input-wrap:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(207,104,51,.1); }
#chatInput {
  flex: 1; font-size: 13px; color: var(--text); font-family: 'Inter', sans-serif;
  border: none; outline: none; resize: none; background: transparent; min-height: 34px; max-height: 160px;
}
#chatInput::placeholder { color: var(--text-4); }
.send-btn {
  width: 34px; height: 34px; background: var(--accent); border-radius: 10px; border: none;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; flex-shrink: 0; color: white; transition: background .15s;
}
.send-btn:hover { background: var(--accent-dark); }
.hint-row { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; max-width: 720px; margin-left: auto; margin-right: auto; }

/* ── Skill popup (retained but restyled) ── */
.skill-popup {
  position: absolute; bottom: 100%; left: 0; right: 0; margin-bottom: 8px;
  max-height: 360px; overflow-y: auto; background: white;
  border: 1px solid var(--border); border-radius: 14px;
  box-shadow: 0 8px 32px rgba(0,0,0,.1); z-index: 200;
}
.skill-item { padding: 12px 16px; display: flex; align-items: flex-start; gap: 10px; }
.skill-item:hover { background: #F7F5F2; }
.skill-item:not(:last-child) { border-bottom: 1px solid var(--border); }
.skill-item-main { flex: 1; cursor: pointer; }
.skill-item-actions { display: flex; gap: 2px; flex-shrink: 0; padding-top: 2px; }
.skill-action-btn { width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; border-radius: 6px; border: none; background: transparent; cursor: pointer; color: var(--text-4); transition: all .15s; }
.skill-action-btn:hover { background: var(--border); color: var(--text-3); }

/* ── Skill modal (retained but restyled) ── */
.skill-modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.3); display: flex; align-items: center; justify-content: center; z-index: 300; }
.skill-modal { background: white; border-radius: 14px; padding: 0; max-width: 560px; width: 90%; max-height: 80vh; display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0,0,0,.2); }
.skill-modal-header { padding: 20px 24px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
.skill-modal-body { padding: 20px 24px; overflow-y: auto; flex: 1; color: var(--text-2); }

/* ── Prose / Markdown ── */
.prose h3 { font-weight: 700; margin: 1rem 0 0.5rem; color: var(--text); }
.prose p { margin: 0.5rem 0; }
.prose ul, .prose ol { padding-left: 1.5rem; margin: 0.5rem 0; }
.prose li { margin: 0.25rem 0; }
.prose strong { color: var(--text); }
.prose code { background: var(--bg-sidebar); padding: .1rem .35rem; border-radius: .25rem; font-size: .85em; }
.prose blockquote { border-left: 3px solid var(--accent); padding-left: 1rem; color: var(--text-3); margin: .5rem 0; }

/* ── Message actions ── */
.msg-actions { display: flex; gap: 4px; margin-top: 6px; opacity: 0; transition: opacity .15s; }
.msg-wrapper:hover .msg-actions { opacity: 1; }
.msg-action { border: none; background: transparent; cursor: pointer; padding: 4px; color: var(--text-4); transition: color .15s; }
.msg-action:hover { color: var(--text-3); }

/* ── Streaming cursor ── */
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
.stream-cursor { display: inline-block; width: 2px; height: 1em; background: var(--accent); margin-left: 2px; vertical-align: text-bottom; animation: blink .8s step-end infinite; }

/* ── Sidebar collapse button ── */
#collapseBtn {
  position: absolute; top: 50%; transform: translateY(-50%); right: -12px;
  width: 24px; height: 48px; background: white; border: 1px solid var(--border);
  border-radius: 0 6px 6px 0; display: flex; align-items: center; justify-content: center;
  cursor: pointer; z-index: 50; box-shadow: 1px 0 4px rgba(0,0,0,.06);
}
#collapseBtn:hover { background: var(--bg-sidebar); }

/* ── no-scrollbar ── */
.no-scrollbar::-webkit-scrollbar { display: none; }
.no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
</style>
```

- [ ] **Step 2: Remove old Manrope font link; replace with Inter-only**

In the `<head>`, replace:
```html
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&amp;family=Inter:wght@400;500;600&amp;display=swap" rel="stylesheet"/>
```
with:
```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
```
(Note: the Inter link is already included inside the new style block above — remove any duplicate.)

- [ ] **Step 3: Replace the `<body>` opening tag and top nav**

Replace:
```html
<body class="bg-surface font-body text-on-surface selection:bg-primary-fixed">
<!-- Top Navigation Bar -->
<nav class="fixed top-0 w-full z-50 bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border-b border-slate-100 dark:border-slate-800 flex justify-between items-center px-6 py-3">
<div class="flex items-center gap-8">
<span class="text-xl font-extrabold tracking-tighter text-blue-800 dark:text-blue-300">OpenAILearning</span>
</div>
<div class="flex items-center gap-4"></div>
</nav>
```
with:
```html
<body>
<nav id="topNav">
  <div class="topnav-left">
    <span class="topnav-title" id="topNavTitle">EduAI</span>
    <span id="topNavSkillPill" class="topnav-skill-pill" style="display:none;" onclick="showSkillModal(_activeSkillInfo?.name)">
      <span class="topnav-skill-dot"></span>
      <span id="topNavSkillName"></span>
    </span>
  </div>
  <div style="display:flex;align-items:center;gap:8px;" id="topNavActions"></div>
</nav>
```

- [ ] **Step 4: Replace the `<aside>` sidebar**

Replace:
```html
<aside id="sidebar" class="h-screen w-64 fixed left-0 top-0 pt-20 bg-white dark:bg-slate-950 flex flex-col p-4 border-r border-slate-100 dark:border-slate-800 z-40 transition-all duration-300">
<button onclick="createNewSession()" class="w-full flex items-center justify-center gap-2 bg-primary text-white py-3 px-4 rounded-xl font-headline font-bold text-sm shadow-sm hover:shadow-md hover:bg-primary-container transition-all mb-4">
<span class="material-symbols-outlined text-lg">add</span>
<span>New Session</span>
</button>
<div class="flex-grow overflow-y-auto space-y-1" id="session-list"></div>
<!-- Collapse handle -->
<button onclick="toggleSidebar()" id="collapseBtn" class="absolute top-1/2 -translate-y-1/2 -right-4 w-8 h-16 bg-white border border-slate-200 rounded-r-lg flex items-center justify-center shadow-sm hover:bg-slate-50 transition-colors z-50">
  <span class="material-symbols-outlined text-sm text-slate-400" id="collapseIcon">chevron_left</span>
</button>
</aside>
```
with:
```html
<aside id="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon">E</div>
    <span class="sidebar-logo-text">EduAI</span>
  </div>
  <button class="new-session-btn" onclick="createNewSession()">
    <span class="material-symbols-outlined" style="font-size:16px;">add</span>
    新对话
  </button>
  <div style="flex:1;overflow-y:auto;" id="session-list"></div>
  <button onclick="toggleSidebar()" id="collapseBtn">
    <span class="material-symbols-outlined" style="font-size:14px;color:var(--text-4);" id="collapseIcon">chevron_left</span>
  </button>
</aside>
```

- [ ] **Step 5: Replace `<main>` and `#bottomBar`**

Replace:
```html
<main id="mainContent" class="ml-64 pt-24 pb-32 px-6 min-h-screen flex flex-col transition-all duration-300">
<div class="max-w-4xl mx-auto w-full">
<!-- Chat History (dynamic) -->
<div class="flex flex-col gap-12" id="chatHistory"></div>
</main>
```
with:
```html
<main id="mainContent">
  <div id="homeState" style="display:none;">
    <h1 class="home-greeting">今天想学什么？</h1>
    <p class="home-sub">选择一个主题开始你的个性化学习之旅</p>
    <div class="home-input-wrap">
      <div class="input-wrap">
        <textarea id="homeInput" class="chatInput-inner" rows="1" style="flex:1;font-size:13px;font-family:'Inter',sans-serif;border:none;outline:none;resize:none;background:transparent;min-height:34px;" placeholder="输入学习主题，例如：我想学习勾股定理…"></textarea>
        <button class="send-btn" onclick="handleHomeSend()">
          <span class="material-symbols-outlined" style="font-size:18px;">arrow_upward</span>
        </button>
      </div>
      <div class="home-hint-chips" id="homeHintChips"></div>
    </div>
    <p class="recent-sessions-title">最近会话</p>
    <div class="recent-session-grid" id="recentSessionsGrid"></div>
  </div>
  <div id="chatArea" style="display:none;">
    <div id="chatHistory"></div>
  </div>
</main>
```

Replace:
```html
<div id="bottomBar" class="fixed bottom-0 left-64 right-0 p-6 bg-surface/80 backdrop-blur-xl border-t border-slate-100 dark:border-slate-800 transition-all duration-300">
<div class="max-w-[720px] mx-auto relative">
<textarea id="chatInput" onkeydown="handleInputKeydown(event)" oninput="handleInputChange(event)" rows="1" class="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl py-4 px-6 pr-14 focus:ring-2 focus:ring-primary/10 focus:border-primary outline-none transition-all placeholder:text-slate-400 font-body text-on-surface shadow-sm resize-none overflow-hidden" placeholder="输入学习主题，例如：我想学习勾股定理 · 输入 /skill 选择教学方法" style="min-height:56px;max-height:160px;"></textarea>
<div id="skillPopup" class="skill-popup" style="display:none;"></div>
<button onclick="handleSend()" class="absolute right-3 top-1/2 -translate-y-1/2 p-2 bg-primary text-white rounded-xl shadow-sm hover:shadow-md hover:bg-primary-container transition-all">
<span class="material-symbols-outlined text-xl">arrow_upward</span>
</button>
</div>
<div class="max-w-[720px] mx-auto mt-3 flex gap-3 overflow-x-auto no-scrollbar pb-1" id="suggestBtns"></div>
</div>
```
with:
```html
<div id="bottomBar">
  <div class="input-outer">
    <div class="input-wrap">
      <textarea id="chatInput" onkeydown="handleInputKeydown(event)" oninput="handleInputChange(event)" rows="1"
        placeholder="输入学习主题，或 /skill 选择教学方法…"></textarea>
      <div id="skillPopup" class="skill-popup" style="display:none;"></div>
      <button class="send-btn" onclick="handleSend()">
        <span class="material-symbols-outlined" style="font-size:18px;">arrow_upward</span>
      </button>
    </div>
  </div>
  <div class="hint-row no-scrollbar" id="suggestBtns"></div>
</div>
```

- [ ] **Step 6: Update JS that references old Tailwind classes**

Search for class names applied via JS in `static/index.html` that reference old Material Design tokens. Find the `renderMessage` / `appendMessage` / `renderKnowledgeGraph` / `renderDiagnosisResults` functions and update HTML templates emitted by JS.

Find this pattern (user bubble):
```javascript
`<div class="flex justify-end mb-1">
  <div class="bg-surface-container ... rounded-2xl ...">
```
Replace with:
```javascript
`<div class="user-msg">
  <div class="user-bubble">
```

Find the AI message pattern:
```javascript
`<div class="flex gap-3 ...">
  <div class="w-8 h-8 rounded-full bg-primary ...">
```
Replace with:
```javascript
`<div class="ai-msg">
  <div class="ai-avatar">A</div>
  <div class="ai-content">
    <div class="ai-name">EduAI</div>
    <div class="ai-text">
```

Find the knowledge graph card container:
```javascript
`<div class="bg-white rounded-2xl border border-slate-200 ...">
```
Replace outer wrapper with:
```javascript
`<div class="kg-card">
```

Find the `kg-header`, footer, start-diagnosis button and update similarly to use the new class names defined in the CSS.

- [ ] **Step 7: Update `sidebar.js`-generated session item HTML**

`sidebar.js` generates `.session-item` HTML. Check if it uses Tailwind classes directly. If so, the classes will still resolve since Tailwind CDN is retained, but the active state detection should also set `active` class. No changes required if sidebar.js already uses `active` class toggle — verify by reading `sidebar.js` and confirming. (If it uses Tailwind classes for active state color, add a CSS override in the `<style>` block that targets those Tailwind classes for the `.active` case.)

- [ ] **Step 8: Verify in browser**

```bash
uv run python run/serve.py
```

Open `http://localhost:8080`. Check:
- Warm cream background, orange accent
- Sidebar 210px wide, dashed "新对话" button
- Input bar with rounded 14px container, orange send button
- Chat messages: user bubble `#EDE9E3`, AI avatar gradient circle
- Knowledge graph card: white card with orange legend dot

- [ ] **Step 9: Commit**

```bash
git add static/index.html
git commit -m "style: restyle index.html to warm cream / orange Claude design system"
```

---

## Task 2: Restyle `static/diagnosis.html`

**Files:**
- Modify: `static/diagnosis.html`

- [ ] **Step 1: Replace Tailwind config + `<style>` block**

Replace the entire `<script>` tailwind config and `<style>` block (lines ~10–61) with:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<script>
tailwind.config = { theme: { extend: { fontFamily: { sans: ['Inter','sans-serif'] } } } }
</script>
<style>
:root {
  --bg: #FAF9F7; --bg-sidebar: #F0EDE8; --bg-doc: #FDFCFA;
  --border: #EAE6E0; --border-input: #E0DDD8;
  --accent: #CF6833; --accent-light: #FFF3EC; --accent-border: #F5D5BF; --accent-dark: #9E4A2A;
  --text: #1a1a1a; --text-2: #4a4a4a; --text-3: #888; --text-4: #B0A898;
  --error: #DC2626; --success: #059669;
}
* { box-sizing: border-box; }
body { background: var(--bg); font-family: 'Inter', sans-serif; color: var(--text); margin: 0; }

/* ── Sidebar ── */
#sidebar {
  width: 210px; height: 100vh; position: fixed; left: 0; top: 0;
  background: var(--bg-sidebar); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; padding: 16px 12px; gap: 4px; z-index: 40;
}
.sidebar-logo { display: flex; align-items: center; gap: 8px; padding: 6px 8px; margin-bottom: 8px; }
.sidebar-logo-icon {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 15px; font-weight: 800;
}
.sidebar-logo-text { font-size: 14px; font-weight: 800; color: var(--text); letter-spacing: -.02em; }
.new-session-btn {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border: 1.5px dashed #D4CFC8; border-radius: 8px; background: transparent;
  font-size: 13px; font-weight: 500; color: var(--text-3); cursor: pointer;
  margin-bottom: 12px; width: 100%;
}
.new-session-btn:hover { background: white; }
.sidebar-section-label { font-size: 10px; font-weight: 600; color: var(--text-4); letter-spacing: .06em; text-transform: uppercase; padding: 0 8px; margin: 8px 0 4px; }
.session-item { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 7px; font-size: 13px; color: var(--text-3); cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-item:hover { background: #E8E4DE; }
.session-item.active { background: white; color: var(--text); font-weight: 500; box-shadow: 0 1px 3px rgba(0,0,0,.07); }
.session-dot { width: 6px; height: 6px; border-radius: 50%; background: #D4CFC8; flex-shrink: 0; }
.session-dot.orange { background: var(--accent); }

/* ── Top Nav ── */
#topNav {
  height: 50px; position: fixed; top: 0; left: 210px; right: 0; z-index: 30;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
}
.topnav-left { display: flex; align-items: center; gap: 8px; }
.topnav-title { font-size: 14px; font-weight: 600; color: var(--text); }
.topnav-actions { display: flex; align-items: center; gap: 8px; }
.topnav-ghost-btn {
  display: flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 500;
  color: var(--text-3); background: transparent; border: 1px solid var(--border);
  padding: 5px 12px; border-radius: 99px; cursor: pointer; transition: all .15s;
}
.topnav-ghost-btn:hover { background: white; color: var(--text-2); }
.topnav-ghost-btn .material-symbols-outlined { font-size: 15px; }

/* ── Step Nav ── */
#stepNav {
  height: 44px; position: fixed; top: 50px; left: 210px; right: 0; z-index: 29;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 20px; gap: 0;
}
.step-item {
  display: flex; align-items: center; gap: 6px; padding: 0 8px; height: 44px;
  font-size: 13px; font-weight: 500; color: var(--text-4); cursor: default;
  border-bottom: 2px solid transparent; white-space: nowrap;
}
.step-item.done { color: var(--accent); }
.step-item.active { color: var(--text); font-weight: 600; border-bottom-color: var(--accent); }
.step-num {
  width: 20px; height: 20px; border-radius: 50%; background: #E8E4DE; color: var(--text-4);
  display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600;
}
.step-item.done .step-num { background: var(--accent); color: white; }
.step-item.active .step-num { background: var(--accent); color: white; }
.step-sep { color: #D4CFC8; font-size: 14px; padding: 0 4px; }

/* ── Main ── */
#mainContent { margin-left: 210px; padding-top: 94px; min-height: 100vh; padding-bottom: 40px; }
.content-area { max-width: 860px; margin: 0 auto; padding: 24px 24px; }

/* ── Graph canvas card ── */
.graph-card { background: white; border: 1px solid var(--border); border-radius: 14px; overflow: hidden; }
.graph-card-header { padding: 14px 18px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
.graph-card-title { font-size: 14px; font-weight: 600; color: var(--text); }
#graphContainer { min-height: 340px; background: #FAFAF9; }
.graph-card-footer { padding: 12px 18px; display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--border); }
.graph-legend { display: flex; gap: 14px; }
.graph-legend-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--text-3); }
.graph-legend-dot { width: 8px; height: 8px; border-radius: 50%; }
.start-diag-btn {
  background: var(--accent); color: white; font-size: 13px; font-weight: 600;
  padding: 8px 20px; border-radius: 99px; border: none; cursor: pointer; transition: background .15s;
}
.start-diag-btn:hover { background: var(--accent-dark); }

/* ── Quiz progress bar ── */
.quiz-progress-bar { height: 4px; background: var(--border); border-radius: 2px; margin-bottom: 6px; }
.quiz-progress-fill { height: 4px; border-radius: 2px; background: linear-gradient(90deg, var(--accent), var(--accent-dark)); transition: width .3s; }
.quiz-progress-label { font-size: 11px; color: var(--text-4); text-align: right; margin-bottom: 16px; }

/* ── Question card ── */
.question-card { background: white; border: 1px solid var(--border); border-radius: 14px; padding: 20px 22px; }
.question-label { font-size: 10px; font-weight: 600; color: var(--accent); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }
.question-text { font-size: 15px; font-weight: 600; color: var(--text); line-height: 1.55; margin-bottom: 18px; }
.option-btn {
  display: flex; align-items: center; gap: 12px; width: 100%;
  padding: 12px 14px; border: 1.5px solid var(--border); border-radius: 10px;
  background: white; cursor: pointer; margin-bottom: 10px; transition: all .15s; text-align: left;
}
.option-btn:hover { border-color: var(--accent); background: var(--accent-light); }
.option-btn.selected { border-color: var(--accent); background: var(--accent-light); }
.option-letter {
  width: 26px; height: 26px; border-radius: 50%; background: var(--bg-sidebar); color: var(--text-3);
  display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600;
  flex-shrink: 0;
}
.option-btn.selected .option-letter { background: var(--accent); color: white; }
.option-btn.correct { border-color: var(--success); background: #ECFDF5; }
.option-btn.correct .option-letter { background: var(--success); color: white; }
.option-btn.incorrect { border-color: var(--error); background: #FEF2F2; }
.option-btn.incorrect .option-letter { background: var(--error); color: white; }
.option-text { font-size: 13.5px; color: var(--text-2); }
.quiz-nav { display: flex; align-items: center; justify-content: space-between; margin-top: 20px; }
.quiz-prev-btn {
  font-size: 13px; font-weight: 500; color: var(--text-3);
  background: transparent; border: 1px solid var(--border);
  padding: 8px 18px; border-radius: 99px; cursor: pointer; transition: all .15s;
}
.quiz-prev-btn:hover { background: white; color: var(--text-2); }
.quiz-next-btn {
  font-size: 13px; font-weight: 600; color: white;
  background: var(--accent); border: none;
  padding: 8px 20px; border-radius: 99px; cursor: pointer; transition: background .15s;
}
.quiz-next-btn:hover { background: var(--accent-dark); }

/* ── No-scrollbar ── */
.no-scrollbar::-webkit-scrollbar { display: none; }
.no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
</style>
```

- [ ] **Step 2: Replace `<body>` with new layout shell**

Replace:
```html
<body class="bg-surface font-body text-on-surface selection:bg-primary-fixed">
<!-- Top Navigation Bar -->
<nav class="fixed top-0 w-full z-50 bg-white/80 backdrop-blur-md border-b border-slate-100 flex justify-between items-center px-6 py-3">
  <div class="flex items-center gap-4">
    <span class="text-xl font-extrabold tracking-tighter text-blue-800">OpenAILearning</span>
    <span class="text-slate-300">|</span>
    <span class="font-headline font-bold text-sm text-on-surface-variant">Diagnosis Mode</span>
  </div>
  <div class="flex items-center gap-2">
```
with:
```html
<body>
<!-- Sidebar -->
<aside id="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon">E</div>
    <span class="sidebar-logo-text">EduAI</span>
  </div>
  <button class="new-session-btn" onclick="window.location.href='index.html'">
    <span class="material-symbols-outlined" style="font-size:16px;">add</span>
    新对话
  </button>
  <div style="flex:1;overflow-y:auto;" id="session-list"></div>
</aside>

<!-- Top Nav -->
<nav id="topNav">
  <div class="topnav-left">
    <span class="topnav-title" id="topNavTitle">知识图谱 · 诊断</span>
  </div>
  <div class="topnav-actions">
    <button id="navStopBtn" onclick="stopQuiz()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined">stop_circle</span>停止
    </button>
    <button id="navRegenBtn" onclick="retryQuiz()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined">refresh</span>重试
    </button>
    <a id="navExitBtn" href="index.html" class="topnav-ghost-btn">
      <span class="material-symbols-outlined">close</span>
    </a>
  </div>
</nav>

<!-- Step Nav -->
<div id="stepNav">
  <div class="step-item done" id="step1">
    <span class="step-num">✓</span>话题
  </div>
  <span class="step-sep">›</span>
  <div class="step-item active" id="step2">
    <span class="step-num">2</span>知识图谱
  </div>
  <span class="step-sep">›</span>
  <div class="step-item" id="step3">
    <span class="step-num">3</span>诊断
  </div>
  <span class="step-sep">›</span>
  <div class="step-item" id="step4">
    <span class="step-num">4</span>课程
  </div>
</div>

<!-- Main -->
<div id="mainContent">
  <div class="content-area" id="contentArea">
```

- [ ] **Step 3: Close the new layout divs at the bottom of `<body>`**

Before the first `<script>` tag (after all HTML), add the closing tags:
```html
  </div><!-- /content-area -->
</div><!-- /mainContent -->
```

- [ ] **Step 4: Update quiz option button classes in JS**

Find where quiz options are rendered in JS (the `renderQuestion` or similar function). The `.option-btn`, `.option-letter`, `.option-text` classes are already defined in the new CSS to match. Verify the JS still emits `option-btn`, `option-letter`, `option-text` class names — if so no change needed. If JS uses old Tailwind inline classes like `border-slate-200 rounded-xl`, update to `option-btn`.

- [ ] **Step 5: Update step nav state from JS**

Find the JS that switches between graph view and quiz view. It likely shows/hides sections. Add step state updates:
```javascript
// When entering quiz mode (after "开始诊断"):
document.getElementById('step2').className = 'step-item done';
document.getElementById('step2').querySelector('.step-num').textContent = '✓';
document.getElementById('step3').className = 'step-item active';
document.getElementById('step3').querySelector('.step-num').textContent = '3';
```

- [ ] **Step 6: Add sidebar.js script tag**

After the existing `<script src="api.js...">` tag (or at start of body scripts), add:
```html
<script src="sidebar.js?v=2"></script>
```

- [ ] **Step 7: Verify in browser**

```bash
uv run python run/serve.py
```

Navigate from `index.html` to start a session and go to `diagnosis.html`. Check:
- Sidebar matches index.html sidebar style
- Step nav shows steps with orange active indicator
- Graph canvas inside white card with legend + orange start button
- Quiz: question card white, options with orange hover/selected state
- Progress bar orange gradient

- [ ] **Step 8: Commit**

```bash
git add static/diagnosis.html
git commit -m "style: restyle diagnosis.html — add sidebar, step nav, quiz card design system"
```

---

## Task 3: Restyle `static/lesson.html`

**Files:**
- Modify: `static/lesson.html`

- [ ] **Step 1: Replace Tailwind config + `<style>` block**

Replace the tailwind config `<script>` block and the `<style>` block (lines ~13–~130) with:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<script>
tailwind.config = { theme: { extend: { fontFamily: { sans: ['Inter','sans-serif'] } } } }
</script>
<style>
:root {
  --bg: #FAF9F7; --bg-sidebar: #F0EDE8; --bg-doc: #FDFCFA;
  --border: #EAE6E0; --border-input: #E0DDD8;
  --accent: #CF6833; --accent-light: #FFF3EC; --accent-border: #F5D5BF; --accent-dark: #9E4A2A;
  --text: #1a1a1a; --text-2: #4a4a4a; --text-3: #888; --text-4: #B0A898;
  --error: #DC2626; --success: #059669;
}
* { box-sizing: border-box; }
body { background: var(--bg); font-family: 'Inter', sans-serif; color: var(--text); margin: 0; }

/* ── Sidebar ── */
#sidebar {
  width: 210px; height: 100vh; position: fixed; left: 0; top: 0;
  background: var(--bg-sidebar); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; padding: 16px 12px; gap: 4px; z-index: 40;
}
.sidebar-logo { display: flex; align-items: center; gap: 8px; padding: 6px 8px; margin-bottom: 8px; }
.sidebar-logo-icon {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 15px; font-weight: 800;
}
.sidebar-logo-text { font-size: 14px; font-weight: 800; color: var(--text); letter-spacing: -.02em; }
.new-session-btn {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border: 1.5px dashed #D4CFC8; border-radius: 8px; background: transparent;
  font-size: 13px; font-weight: 500; color: var(--text-3); cursor: pointer;
  margin-bottom: 12px; width: 100%;
}
.new-session-btn:hover { background: white; }
.sidebar-section-label { font-size: 10px; font-weight: 600; color: var(--text-4); letter-spacing: .06em; text-transform: uppercase; padding: 0 8px; margin: 8px 0 4px; }
.session-item { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 7px; font-size: 13px; color: var(--text-3); cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-item:hover { background: #E8E4DE; }
.session-item.active { background: white; color: var(--text); font-weight: 500; box-shadow: 0 1px 3px rgba(0,0,0,.07); }
.session-dot { width: 6px; height: 6px; border-radius: 50%; background: #D4CFC8; flex-shrink: 0; }
.session-dot.orange { background: var(--accent); }

/* ── Top Nav ── */
#topNav {
  height: 50px; position: fixed; top: 0; left: 210px; right: 0; z-index: 30;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
}
.topnav-left { display: flex; align-items: center; gap: 8px; }
.topnav-title { font-size: 14px; font-weight: 600; color: var(--text); }
.topnav-skill-pill {
  display: flex; align-items: center; gap: 5px;
  background: var(--accent-light); border: 1px solid var(--accent-border);
  color: var(--accent-dark); font-size: 11px; font-weight: 600;
  padding: 4px 10px; border-radius: 99px; cursor: pointer;
}
.topnav-actions { display: flex; align-items: center; gap: 8px; }
.topnav-ghost-btn {
  display: flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 500;
  color: var(--text-3); background: transparent; border: 1px solid var(--border);
  padding: 5px 12px; border-radius: 99px; cursor: pointer;
}
.topnav-ghost-btn:hover { background: white; color: var(--text-2); }
.topnav-ghost-btn .material-symbols-outlined { font-size: 15px; }

/* ── Step Nav ── */
#stepNav {
  height: 44px; position: fixed; top: 50px; left: 210px; right: 0; z-index: 29;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 20px; gap: 0;
}
.step-item {
  display: flex; align-items: center; gap: 6px; padding: 0 8px; height: 44px;
  font-size: 13px; font-weight: 500; color: var(--text-4); cursor: default;
  border-bottom: 2px solid transparent; white-space: nowrap;
}
.step-item.done { color: var(--accent); }
.step-item.active { color: var(--text); font-weight: 600; border-bottom-color: var(--accent); }
.step-num {
  width: 20px; height: 20px; border-radius: 50%; background: #E8E4DE; color: var(--text-4);
  display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600;
}
.step-item.done .step-num { background: var(--accent); color: white; }
.step-item.active .step-num { background: var(--accent); color: white; }
.step-sep { color: #D4CFC8; font-size: 14px; padding: 0 4px; }

/* ── Three-column layout ── */
#mainContent {
  margin-left: 210px; padding-top: 94px; height: 100vh; overflow: hidden;
  display: flex;
}

/* TOC column */
#tocPanel {
  width: 190px; flex-shrink: 0; background: var(--bg-doc);
  border-right: 1px solid var(--border); overflow-y: auto;
  padding: 16px 0 80px;
}
.toc-header { font-size: 10px; font-weight: 600; color: var(--text-4); text-transform: uppercase; letter-spacing: .06em; padding: 0 16px; margin-bottom: 8px; }
.toc-item {
  padding: 7px 16px; font-size: 13px; color: var(--text-3); cursor: pointer;
  transition: all .1s; border-left: 3px solid transparent;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.toc-item:hover { color: var(--text-2); background: #F0EDE8; }
.toc-item.active { color: var(--accent); background: var(--accent-light); border-left-color: var(--accent); font-weight: 500; }
.toc-weak-box { margin: 12px 10px 0; background: var(--accent-light); border: 1px solid var(--accent-border); border-radius: 8px; padding: 10px 12px; }
.toc-weak-label { font-size: 10px; font-weight: 600; color: var(--accent); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 4px; }
.toc-weak-item { font-size: 11px; color: var(--accent-dark); line-height: 1.5; }

/* Main document column */
#docPanel {
  flex: 1; overflow-y: auto; padding: 24px 32px 120px;
}

/* Right panel */
#rightPanel {
  width: 200px; flex-shrink: 0; background: var(--bg-doc);
  border-left: 1px solid var(--border); overflow-y: auto;
  padding: 16px 0 80px;
}
.right-panel-section { padding: 0 12px; margin-bottom: 20px; }
.right-panel-title { font-size: 10px; font-weight: 600; color: var(--text-4); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 10px; }
.ks-toggle-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.ks-toggle { width: 32px; height: 18px; border-radius: 9px; background: #D4CFC8; position: relative; cursor: pointer; transition: background .2s; border: none; }
.ks-toggle.on { background: var(--accent); }
.ks-toggle::after { content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: white; transition: left .2s; }
.ks-toggle.on::after { left: 16px; }

/* Knowledge skill cards in right panel */
.ks-card { background: white; border-radius: 8px; margin-bottom: 6px; border: 1px solid var(--border); }
.ks-card summary { padding: 8px 10px; font-size: 12px; font-weight: 600; color: var(--text-2); cursor: pointer; display: flex; align-items: center; gap: 6px; list-style: none; }
.ks-card summary::marker { display: none; }
.ks-card summary::-webkit-details-marker { display: none; }
.ks-card-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); flex-shrink: 0; }
.ks-card-body { padding: 0 10px 10px; font-size: 11px; color: var(--text-3); line-height: 1.5; }

/* Mini graph panel */
#lessonGraph { min-height: 140px; background: #FAFAF9; border-radius: 8px; border: 1px solid var(--border); }
.graph-legend-row { display: flex; gap: 10px; margin-top: 6px; }
.graph-legend-item { display: flex; align-items: center; gap: 4px; font-size: 10px; color: var(--text-4); }
.graph-legend-dot { width: 7px; height: 7px; border-radius: 50%; }

/* ── Lesson document typography ── */
.lesson-title { font-size: 20px; font-weight: 800; color: var(--text); margin-bottom: 6px; }
.lesson-meta { display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--text-4); margin-bottom: 20px; }
.lesson-skill-tag {
  background: var(--accent-light); border: 1px solid var(--accent-border);
  color: var(--accent-dark); font-size: 11px; font-weight: 600;
  padding: 2px 8px; border-radius: 99px;
}
.lesson-section { scroll-margin-top: 110px; }
.lesson-section h2 {
  font-family: 'Inter'; font-weight: 700; font-size: 14px; color: var(--text);
  margin: 20px 0 10px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
}
.lesson-section h3 { font-weight: 700; font-size: 13px; color: var(--accent-dark); margin: 16px 0 8px; }
.lesson-section h4 { font-weight: 600; font-size: 13px; color: var(--text-2); margin: 12px 0 6px; }
.lesson-section p { line-height: 1.75; margin: 8px 0; color: var(--text-2); font-size: 13px; }
.lesson-section ul, .lesson-section ol { margin: 8px 0; padding-left: 1.5rem; color: var(--text-2); font-size: 13px; }
.lesson-section li { margin: 4px 0; line-height: 1.7; }
.lesson-section strong { color: var(--text); font-weight: 600; }
.lesson-section blockquote {
  border-left: 3px solid var(--accent); background: var(--accent-light);
  padding: 10px 14px; margin: 12px 0; border-radius: 0 8px 8px 0;
}
.lesson-section blockquote p { color: var(--text-2); margin: 0; }
.lesson-section code { background: var(--bg-sidebar); padding: .1rem .35rem; border-radius: .25rem; font-size: .85em; color: var(--accent-dark); }
.lesson-section pre { background: #1e293b; color: #e2e8f0; padding: 1.25rem; border-radius: 8px; overflow-x: auto; margin: 12px 0; }
.lesson-section pre code { background: none; color: inherit; padding: 0; }
.lesson-section table { width: 100%; border-collapse: collapse; margin: 12px 0; }
.lesson-section th { background: var(--bg-sidebar); font-weight: 700; text-align: left; padding: 8px 10px; border-bottom: 2px solid var(--border); font-size: 12px; }
.lesson-section td { padding: 8px 10px; border-bottom: 1px solid var(--border); font-size: 12px; }
.lesson-section hr { border: none; border-top: 1px solid var(--border); margin: 20px 0; }

/* Inline knowledge skill cards (before each h2) */
.inline-ks-card {
  background: #F0FDF4; border: 1.5px dashed #86EFAC; border-radius: 8px;
  margin-bottom: 10px;
}
.inline-ks-card summary {
  padding: 10px 14px; font-size: 12px; font-weight: 600; color: #15803D;
  cursor: pointer; display: flex; align-items: center; gap: 6px; list-style: none;
}
.inline-ks-card summary::marker, .inline-ks-card summary::-webkit-details-marker { display: none; }
.inline-ks-card-body { padding: 0 14px 12px; font-size: 11px; color: #15803D; line-height: 1.55; }

/* Pedagogy skill card */
.pedagogy-skill-card {
  background: var(--accent-light); border: 1.5px solid var(--accent-border);
  border-radius: 10px; margin-bottom: 16px;
}
.pedagogy-skill-card summary {
  padding: 12px 16px; font-size: 13px; font-weight: 600; color: var(--accent-dark);
  cursor: pointer; display: flex; align-items: center; gap: 8px; list-style: none;
}
.pedagogy-skill-card summary::marker, .pedagogy-skill-card summary::-webkit-details-marker { display: none; }
.pedagogy-skill-card-body { padding: 0 16px 14px; font-size: 12px; color: var(--accent-dark); line-height: 1.55; }

/* Formula block */
.formula-block { background: var(--bg-sidebar); border-radius: 8px; padding: 14px; text-align: center; font-weight: 700; margin: 12px 0; }

/* Callout block */
.callout-block { border-left: 3px solid var(--accent); background: var(--accent-light); border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 12px 0; }

/* Finish / nav links at bottom */
.lesson-bottom-nav { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border); }
.lesson-finish-btn {
  background: var(--accent); color: white; font-size: 13px; font-weight: 600;
  padding: 10px 24px; border-radius: 99px; border: none; cursor: pointer; transition: background .15s;
}
.lesson-finish-btn:hover { background: var(--accent-dark); }

/* TOC nav-link compat */
.nav-link { transition: all .15s; }
.nav-link:hover, .nav-link.active { color: var(--accent); border-left-color: var(--accent); }

/* Selection toolbar */
#selectionToolbar { display: none; position: absolute; z-index: 60; animation: fadeIn .12s ease-out; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

/* Highlighted text */
.noted-text { background: #FFF9C4; border-bottom: 2px solid #F59E0B; cursor: pointer; }

/* Sticky bottom chat in lesson */
#lessonBottomBar {
  position: fixed; bottom: 0; left: 210px; right: 0; z-index: 28;
  padding: 10px 14px; background: var(--bg); border-top: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
}
#lessonChatInput {
  flex: 1; font-size: 13px; font-family: 'Inter', sans-serif; color: var(--text);
  border: 1.5px solid var(--border-input); border-radius: 10px; outline: none;
  padding: 8px 12px; background: white;
}
#lessonChatInput:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(207,104,51,.1); }
.lesson-send-btn {
  width: 34px; height: 34px; background: var(--accent); border-radius: 8px; border: none;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; color: white; transition: background .15s;
}
.lesson-send-btn:hover { background: var(--accent-dark); }

.no-scrollbar::-webkit-scrollbar { display: none; }
.no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
</style>
```

- [ ] **Step 2: Replace `<body>` opening and layout structure**

Replace the existing `<body>` tag and everything up to the content area with:

```html
<body>

<!-- Sidebar (left, 210px) -->
<aside id="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon">E</div>
    <span class="sidebar-logo-text">EduAI</span>
  </div>
  <button class="new-session-btn" onclick="window.location.href='index.html'">
    <span class="material-symbols-outlined" style="font-size:16px;">add</span>
    新对话
  </button>
  <div style="flex:1;overflow-y:auto;" id="session-list"></div>
</aside>

<!-- Top Nav -->
<nav id="topNav">
  <div class="topnav-left">
    <span class="topnav-title" id="lessonTitle">课程</span>
    <span id="topNavSkillPill" class="topnav-skill-pill" style="display:none;">
      <span style="width:6px;height:6px;border-radius:50%;background:var(--accent);"></span>
      <span id="topNavSkillName"></span>
    </span>
  </div>
  <div class="topnav-actions">
    <button id="navStopBtn" onclick="stopLesson()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined" style="font-size:15px;">stop_circle</span>停止
    </button>
    <button id="navRegenBtn" onclick="retryLesson()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined" style="font-size:15px;">refresh</span>重试
    </button>
    <button id="navSlidesBtn" onclick="goToSlides()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined" style="font-size:15px;">slideshow</span>转为 PPT
    </button>
    <a href="index.html" class="topnav-ghost-btn">
      <span class="material-symbols-outlined" style="font-size:15px;">close</span>
    </a>
  </div>
</nav>

<!-- Step Nav -->
<div id="stepNav">
  <div class="step-item done">
    <span class="step-num">✓</span>话题
  </div>
  <span class="step-sep">›</span>
  <div class="step-item done">
    <span class="step-num">✓</span>知识图谱
  </div>
  <span class="step-sep">›</span>
  <div class="step-item done">
    <span class="step-num">✓</span>诊断
  </div>
  <span class="step-sep">›</span>
  <div class="step-item active">
    <span class="step-num">4</span>课程
  </div>
</div>

<!-- Three-column content -->
<div id="mainContent">

  <!-- TOC Column -->
  <div id="tocPanel">
    <div class="toc-header">目录</div>
    <div id="tocList"></div>
    <div id="weakPointBox" class="toc-weak-box" style="display:none;">
      <div class="toc-weak-label">📌 薄弱点</div>
      <div id="weakPointList" class="toc-weak-item"></div>
    </div>
  </div>

  <!-- Main Document Column -->
  <div id="docPanel">
    <div id="lessonContent"></div>

    <!-- Finish button area -->
    <div class="lesson-bottom-nav" id="lessonBottomNav" style="display:none;">
      <button class="lesson-finish-btn" id="finishLearningBtn" onclick="finishLearning()" style="display:none;">
        完成学习 →
      </button>
    </div>
  </div>

  <!-- Right Panel -->
  <div id="rightPanel">
    <!-- Knowledge Skills section -->
    <div class="right-panel-section" id="knowledgeSkillsSection">
      <div class="ks-toggle-row">
        <span class="right-panel-title">💡 知识点技能</span>
        <button class="ks-toggle" id="ksToggleBtn" onclick="toggleKnowledgeSkills()"></button>
      </div>
      <div id="knowledgeSkillsPanel"></div>
    </div>
    <!-- Mini Graph section -->
    <div class="right-panel-section">
      <div class="right-panel-title">📊 薄弱点图谱</div>
      <div id="lessonGraph"></div>
      <div class="graph-legend-row">
        <div class="graph-legend-item"><div class="graph-legend-dot" style="background:#FECACA;border:1px solid #DC2626;"></div>薄弱</div>
        <div class="graph-legend-item"><div class="graph-legend-dot" style="background:var(--accent);"></div>核心</div>
      </div>
    </div>
  </div>

</div><!-- /mainContent -->

<!-- Sticky bottom chat input -->
<div id="lessonBottomBar" style="display:none;">
  <input type="text" id="lessonChatInput" placeholder="有问题？输入提问…"
    onkeydown="if(event.key==='Enter') sendLessonChat()"/>
  <button class="lesson-send-btn" onclick="sendLessonChat()">
    <span class="material-symbols-outlined" style="font-size:18px;">arrow_upward</span>
  </button>
</div>
```

- [ ] **Step 3: Update JS-generated lesson content classes**

Find in `lesson.html`'s inline `<script>` where lesson sections are appended via `innerHTML`:

The existing code around line 407–433 inserts inline KS cards as:
```javascript
card.innerHTML = '<details class="my-4 rounded-xl border border-dashed border-green-300 bg-green-50/50 group">...'
```
Replace with:
```javascript
card.innerHTML = `<details class="inline-ks-card">
  <summary>💡 知识点技能：${escapeHtml(ks.name)}</summary>
  <div class="inline-ks-card-body">${safeMarkdown(ks.content)}</div>
</details>`;
```

Find the pedagogy skill card insertion (around the `showPedagogySkill` or similar function):
```javascript
// old: class="... bg-blue-50 border-blue-200 ..."
// new:
skillCard.className = 'pedagogy-skill-card';
```
Or where it uses `innerHTML`:
```javascript
details.innerHTML = `<details class="pedagogy-skill-card">
  <summary>
    <span class="material-symbols-outlined" style="font-size:16px;color:var(--accent);">psychology</span>
    教学法：${escapeHtml(skillName)}
  </summary>
  <div class="pedagogy-skill-card-body">${safeMarkdown(skillBody)}</div>
</details>`;
```

Find where `#knowledgeSkillsPanel` is populated (toggle skill cards in right panel):
```javascript
// old: `<details class="mb-2 rounded-xl bg-white border ..."><summary class="... text-sm font-bold ...">`
// new:
panel.innerHTML = skills.map(s => `
  <details class="ks-card">
    <summary><span class="ks-card-dot"></span>${escapeHtml(s.name)}</summary>
    <div class="ks-card-body">${safeMarkdown(s.content)}</div>
  </details>
`).join('');
```

Find where `ksToggleBtn` state is updated:
```javascript
// Add/remove 'on' class instead of changing colors
ksToggleBtn.classList.toggle('on', isEnabled);
```

Find where TOC items are rendered (populating `#tocList`):
```javascript
// old: `<button class="nav-link ... text-sm ...">`
// new:
tocList.innerHTML = sections.map((s, i) => `
  <div class="toc-item${i===0?' active':''}" data-section="${escapeHtml(s.id)}" onclick="scrollToSection('${escapeHtml(s.id)}')">
    ${escapeHtml(s.title)}
  </div>
`).join('');
```

- [ ] **Step 4: Verify in browser**

```bash
uv run python run/serve.py
```

Navigate to a lesson via `lesson.html?session_id=...`. Check:
- 3-column layout with sidebar + TOC + doc + right panel all visible
- Inline KS cards appear before h2 sections (green dashed border, green text)
- Right panel shows knowledge skills with toggle button
- Mini graph visible in right panel with red weak nodes
- Sticky input at bottom for lesson chat

- [ ] **Step 5: Commit**

```bash
git add static/lesson.html
git commit -m "style: restyle lesson.html — 3-col layout, Claude design tokens, inline KS cards, right panel"
```

---

## Task 4: Restyle `static/slides.html`

**Files:**
- Modify: `static/slides.html`

- [ ] **Step 1: Replace Tailwind config + `<style>` block**

Replace the tailwind config `<script>` and `<style>` block (lines ~14–48) with:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<script>
tailwind.config = { theme: { extend: { fontFamily: { sans: ['Inter','sans-serif'] } } } }
</script>
<style>
:root {
  --bg: #FAF9F7; --bg-sidebar: #F0EDE8;
  --border: #EAE6E0;
  --accent: #CF6833; --accent-light: #FFF3EC; --accent-border: #F5D5BF; --accent-dark: #9E4A2A;
  --text: #1a1a1a; --text-2: #4a4a4a; --text-3: #888; --text-4: #B0A898;
  --error: #DC2626;
}
* { box-sizing: border-box; }
body { background: var(--bg); font-family: 'Inter', sans-serif; color: var(--text); margin: 0; }

/* ── Sidebar ── */
#sidebar {
  width: 210px; height: 100vh; position: fixed; left: 0; top: 0;
  background: var(--bg-sidebar); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; padding: 16px 12px; gap: 4px; z-index: 40;
}
.sidebar-logo { display: flex; align-items: center; gap: 8px; padding: 6px 8px; margin-bottom: 8px; }
.sidebar-logo-icon {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 15px; font-weight: 800;
}
.sidebar-logo-text { font-size: 14px; font-weight: 800; color: var(--text); letter-spacing: -.02em; }
.new-session-btn {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border: 1.5px dashed #D4CFC8; border-radius: 8px; background: transparent;
  font-size: 13px; font-weight: 500; color: var(--text-3); cursor: pointer;
  margin-bottom: 12px; width: 100%;
}
.new-session-btn:hover { background: white; }
.session-item { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 7px; font-size: 13px; color: var(--text-3); cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-item:hover { background: #E8E4DE; }
.session-item.active { background: white; color: var(--text); font-weight: 500; box-shadow: 0 1px 3px rgba(0,0,0,.07); }

/* ── Top Nav ── */
#topNav {
  height: 50px; position: fixed; top: 0; left: 210px; right: 0; z-index: 30;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
}
.topnav-left { display: flex; align-items: center; gap: 8px; }
.topnav-title { font-size: 14px; font-weight: 600; color: var(--text); }
.topnav-actions { display: flex; align-items: center; gap: 8px; }
.topnav-ghost-btn {
  display: flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 500;
  color: var(--text-3); background: transparent; border: 1px solid var(--border);
  padding: 5px 12px; border-radius: 99px; cursor: pointer;
}
.topnav-ghost-btn:hover { background: white; color: var(--text-2); }
.topnav-ghost-btn .material-symbols-outlined { font-size: 15px; }

/* ── Step Nav ── */
#stepNav {
  height: 44px; position: fixed; top: 50px; left: 210px; right: 0; z-index: 29;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 20px; gap: 0;
}
.step-item {
  display: flex; align-items: center; gap: 6px; padding: 0 8px; height: 44px;
  font-size: 13px; font-weight: 500; color: var(--text-4); cursor: default;
  border-bottom: 2px solid transparent; white-space: nowrap;
}
.step-item.done { color: var(--accent); }
.step-item.active { color: var(--text); font-weight: 600; border-bottom-color: var(--accent); }
.step-num {
  width: 20px; height: 20px; border-radius: 50%; background: #E8E4DE; color: var(--text-4);
  display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600;
}
.step-item.done .step-num { background: var(--accent); color: white; }
.step-item.active .step-num { background: var(--accent); color: white; }
.step-sep { color: #D4CFC8; font-size: 14px; padding: 0 4px; }

/* ── Slides area ── */
#mainContent {
  margin-left: 210px; padding-top: 94px; min-height: 100vh;
  display: flex; flex-direction: column; align-items: center; padding-bottom: 40px;
}
.slides-shell { width: 100%; max-width: 1060px; padding: 24px 24px; }
.slide-wrapper { display: flex; justify-content: center; margin-bottom: 16px; }
.slide-canvas {
  width: 1000px; height: 562.5px; position: relative; overflow: hidden;
  border-radius: 14px; box-shadow: 0 4px 24px rgba(0,0,0,.12);
  background: white;
}
.slide-canvas * { box-sizing: border-box; }
.slide-nav-row { display: flex; align-items: center; justify-content: center; gap: 12px; }
.slide-nav-btn {
  font-size: 13px; font-weight: 500; color: var(--text-3);
  background: transparent; border: 1px solid var(--border);
  padding: 7px 18px; border-radius: 99px; cursor: pointer; transition: all .15s;
}
.slide-nav-btn:hover { background: white; color: var(--text-2); }
.slide-dot-row { display: flex; gap: 6px; align-items: center; }
.slide-dot { width: 6px; height: 6px; border-radius: 50%; background: #D4CFC8; cursor: pointer; transition: background .15s; }
.slide-dot.active { background: var(--accent); width: 8px; height: 8px; }
.slide-counter { font-size: 12px; color: var(--text-4); }

/* Status messages */
.slides-status { text-align: center; padding: 60px 20px; }
.slides-status-icon { font-size: 40px; margin-bottom: 12px; }
.slides-status-text { font-size: 14px; color: var(--text-3); }
</style>
```

- [ ] **Step 2: Replace `<body>` layout shell**

Replace:
```html
<body class="bg-surface font-body text-on-surface">

<!-- Top Nav -->
<nav class="fixed top-0 w-full z-50 bg-white/80 backdrop-blur-md border-b border-slate-100 flex justify-between items-center px-6 py-3">
  <div class="flex items-center gap-4">
    <span class="text-xl font-extrabold tracking-tighter text-blue-800">OpenAILearning</span>
    <span class="text-slate-300">|</span>
    <span class="font-headline font-bold text-sm text-on-surface-variant">Slide Mode</span>
  </div>
  <div class="flex items-center gap-2">
```
with:
```html
<body>

<!-- Sidebar -->
<aside id="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon">E</div>
    <span class="sidebar-logo-text">EduAI</span>
  </div>
  <button class="new-session-btn" onclick="window.location.href='index.html'">
    <span class="material-symbols-outlined" style="font-size:16px;">add</span>
    新对话
  </button>
  <div style="flex:1;overflow-y:auto;" id="session-list"></div>
</aside>

<!-- Top Nav -->
<nav id="topNav">
  <div class="topnav-left">
    <span class="topnav-title" id="topNavTitle">课程 PPT</span>
  </div>
  <div class="topnav-actions">
    <button id="navStopBtn" onclick="cancelSlides()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined">stop_circle</span>停止
    </button>
    <button id="navRegenBtn" onclick="regenSlides()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined">refresh</span>重试
    </button>
    <button id="navExportBtn" onclick="exportPptx()" class="topnav-ghost-btn" style="display:none;">
      <span class="material-symbols-outlined">download</span>导出 PPTX
    </button>
    <a href="index.html" class="topnav-ghost-btn">
      <span class="material-symbols-outlined">close</span>
    </a>
  </div>
</nav>

<!-- Step Nav -->
<div id="stepNav">
  <div class="step-item done"><span class="step-num">✓</span>话题</div>
  <span class="step-sep">›</span>
  <div class="step-item done"><span class="step-num">✓</span>知识图谱</div>
  <span class="step-sep">›</span>
  <div class="step-item done"><span class="step-num">✓</span>诊断</div>
  <span class="step-sep">›</span>
  <div class="step-item active"><span class="step-num">4</span>课程</div>
</div>

<!-- Main content -->
<div id="mainContent">
  <div class="slides-shell">
    <div class="slide-wrapper">
      <div id="slideCanvas" class="slide-canvas"></div>
    </div>
    <div class="slide-nav-row">
      <button class="slide-nav-btn" onclick="prevSlide()">← 上一页</button>
      <div class="slide-dot-row" id="slideDots"></div>
      <span class="slide-counter" id="slideCounter"></span>
      <button class="slide-nav-btn" onclick="nextSlide()">下一页 →</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Update old Tailwind class refs in slides JS**

Find where status/loading messages are set via `innerHTML`. Replace old Tailwind classes:
```javascript
// old: `<div class="text-center py-20 text-slate-400">...</div>`
// new:
container.innerHTML = `<div class="slides-status">
  <div class="slides-status-icon">⏳</div>
  <div class="slides-status-text">正在生成幻灯片…</div>
</div>`;
```

Find where `navExportBtn` visibility is toggled and ensure the button ID matches (`navExportBtn`).

- [ ] **Step 4: Verify in browser**

```bash
uv run python run/serve.py
```

Navigate from `lesson.html` to slides. Check:
- Sidebar + step nav present
- Slide canvas centered, white card with rounded corners and shadow
- Prev/Next ghost pill buttons visible
- Dot indicators show orange for current slide

- [ ] **Step 5: Commit**

```bash
git add static/slides.html
git commit -m "style: restyle slides.html — add sidebar, step nav, Claude design tokens"
```

---

## Task 5: Restyle `static/results.html`

**Files:**
- Modify: `static/results.html`

- [ ] **Step 1: Replace Tailwind config + `<style>` block**

Replace the tailwind config `<script>` block and `<style>` block with:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
<script>
tailwind.config = { theme: { extend: { fontFamily: { sans: ['Inter','sans-serif'] } } } }
</script>
<style>
:root {
  --bg: #FAF9F7; --bg-sidebar: #F0EDE8;
  --border: #EAE6E0;
  --accent: #CF6833; --accent-light: #FFF3EC; --accent-border: #F5D5BF; --accent-dark: #9E4A2A;
  --text: #1a1a1a; --text-2: #4a4a4a; --text-3: #888; --text-4: #B0A898;
  --error: #DC2626; --success: #059669;
}
* { box-sizing: border-box; }
body { background: var(--bg); font-family: 'Inter', sans-serif; color: var(--text); margin: 0; }

/* ── Sidebar ── */
#sidebar {
  width: 210px; height: 100vh; position: fixed; left: 0; top: 0;
  background: var(--bg-sidebar); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; padding: 16px 12px; gap: 4px; z-index: 40;
}
.sidebar-logo { display: flex; align-items: center; gap: 8px; padding: 6px 8px; margin-bottom: 8px; }
.sidebar-logo-icon {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  display: flex; align-items: center; justify-content: center;
  color: white; font-size: 15px; font-weight: 800;
}
.sidebar-logo-text { font-size: 14px; font-weight: 800; color: var(--text); letter-spacing: -.02em; }
.new-session-btn {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border: 1.5px dashed #D4CFC8; border-radius: 8px; background: transparent;
  font-size: 13px; font-weight: 500; color: var(--text-3); cursor: pointer;
  margin-bottom: 12px; width: 100%;
}
.new-session-btn:hover { background: white; }
.session-item { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 7px; font-size: 13px; color: var(--text-3); cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-item:hover { background: #E8E4DE; }
.session-item.active { background: white; color: var(--text); font-weight: 500; box-shadow: 0 1px 3px rgba(0,0,0,.07); }

/* ── Top Nav ── */
#topNav {
  height: 50px; position: fixed; top: 0; left: 210px; right: 0; z-index: 30;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
}
.topnav-title { font-size: 14px; font-weight: 600; color: var(--text); }
.topnav-actions { display: flex; align-items: center; gap: 8px; }
.topnav-ghost-btn {
  display: flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 500;
  color: var(--text-3); background: transparent; border: 1px solid var(--border);
  padding: 5px 12px; border-radius: 99px; cursor: pointer;
}
.topnav-ghost-btn:hover { background: white; color: var(--text-2); }
.topnav-ghost-btn .material-symbols-outlined { font-size: 15px; }

/* ── Step Nav ── */
#stepNav {
  height: 44px; position: fixed; top: 50px; left: 210px; right: 0; z-index: 29;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 20px; gap: 0;
}
.step-item {
  display: flex; align-items: center; gap: 6px; padding: 0 8px; height: 44px;
  font-size: 13px; font-weight: 500; color: var(--text-4); cursor: default;
  border-bottom: 2px solid transparent; white-space: nowrap;
}
.step-item.done { color: var(--accent); }
.step-item.active { color: var(--text); font-weight: 600; border-bottom-color: var(--accent); }
.step-num {
  width: 20px; height: 20px; border-radius: 50%; background: #E8E4DE; color: var(--text-4);
  display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600;
}
.step-item.done .step-num { background: var(--accent); color: white; }
.step-sep { color: #D4CFC8; font-size: 14px; padding: 0 4px; }

/* ── Main ── */
#mainContent { margin-left: 210px; padding-top: 94px; min-height: 100vh; padding-bottom: 60px; }
.results-area { max-width: 680px; margin: 0 auto; padding: 28px 24px; }

/* ── Score card ── */
.score-card { background: white; border: 1px solid var(--border); border-radius: 14px; padding: 28px 28px 20px; margin-bottom: 16px; }
.score-num { font-size: 42px; font-weight: 800; color: var(--accent); line-height: 1; }
.score-label { font-size: 13px; color: var(--text-4); margin-top: 2px; }
.score-row { display: flex; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.score-row:last-child { border-bottom: none; }
.score-dot { width: 8px; height: 8px; border-radius: 50%; }

/* ── Knowledge breakdown card ── */
.breakdown-card { background: white; border: 1px solid var(--border); border-radius: 14px; padding: 20px 24px; margin-bottom: 16px; }
.breakdown-title { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 14px; }
.breakdown-item { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.breakdown-item:last-child { border-bottom: none; }
.breakdown-icon {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: 14px;
}
.breakdown-icon.correct { background: #ECFDF5; color: var(--success); }
.breakdown-icon.incorrect { background: #FEF2F2; color: var(--error); }
.breakdown-name { flex: 1; font-size: 13px; color: var(--text-2); }
.breakdown-tag { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 99px; }
.breakdown-tag.mastered { background: #ECFDF5; color: var(--success); }
.breakdown-tag.weak { background: #FEF2F2; color: var(--error); }

/* ── Weak-point callout ── */
.weak-callout { background: var(--accent-light); border-left: 3px solid var(--accent); border-radius: 0 10px 10px 0; padding: 14px 18px; margin-bottom: 20px; }
.weak-callout-title { font-size: 12px; font-weight: 600; color: var(--accent-dark); margin-bottom: 4px; }
.weak-callout-text { font-size: 12px; color: var(--accent-dark); line-height: 1.55; }

/* ── CTA buttons ── */
.cta-row { display: flex; align-items: center; gap: 12px; }
.cta-primary {
  background: var(--accent); color: white; font-size: 13px; font-weight: 600;
  padding: 10px 24px; border-radius: 99px; border: none; cursor: pointer; transition: background .15s;
}
.cta-primary:hover { background: var(--accent-dark); }
.cta-ghost {
  color: var(--text-3); font-size: 13px; font-weight: 500;
  background: transparent; border: 1px solid var(--border);
  padding: 10px 20px; border-radius: 99px; cursor: pointer; transition: all .15s;
}
.cta-ghost:hover { background: white; color: var(--text-2); }
</style>
```

- [ ] **Step 2: Replace `<body>` layout shell**

Replace the `<body>` tag and all HTML up to `#resultsContent` (the existing JS-rendered area) with:

```html
<body>
<!-- Sidebar -->
<aside id="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon">E</div>
    <span class="sidebar-logo-text">EduAI</span>
  </div>
  <button class="new-session-btn" onclick="window.location.href='index.html'">
    <span class="material-symbols-outlined" style="font-size:16px;">add</span>
    新对话
  </button>
  <div style="flex:1;overflow-y:auto;" id="session-list"></div>
</aside>

<!-- Top Nav -->
<nav id="topNav">
  <div style="display:flex;align-items:center;gap:8px;">
    <span class="topnav-title">诊断结果</span>
  </div>
  <div class="topnav-actions">
    <a href="index.html" class="topnav-ghost-btn">
      <span class="material-symbols-outlined">close</span>
    </a>
  </div>
</nav>

<!-- Step Nav — all done -->
<div id="stepNav">
  <div class="step-item done"><span class="step-num">✓</span>话题</div>
  <span class="step-sep">›</span>
  <div class="step-item done"><span class="step-num">✓</span>知识图谱</div>
  <span class="step-sep">›</span>
  <div class="step-item done"><span class="step-num">✓</span>诊断</div>
  <span class="step-sep">›</span>
  <div class="step-item done"><span class="step-num">✓</span>课程</div>
</div>

<!-- Main content -->
<div id="mainContent">
  <div class="results-area" id="resultsContent">
    <!-- Populated by JS -->
  </div>
</div>
```

- [ ] **Step 3: Update JS-generated results HTML**

Find the `renderResults` or equivalent function that populates `#resultsContent`. It currently uses Tailwind classes like `bg-white rounded-2xl`, `text-4xl font-black text-primary`, etc.

Update the score card section:
```javascript
// Score HTML emitted by JS:
html += `<div class="score-card">
  <div style="display:flex;align-items:center;gap:24px;margin-bottom:16px;">
    <div>
      <div class="score-num">${score}%</div>
      <div class="score-label">正确率</div>
    </div>
  </div>
  <div class="score-row">
    <span class="score-dot" style="background:var(--success);"></span>
    <span>正确</span><span style="margin-left:auto;font-weight:600;color:var(--success);">${correct}</span>
  </div>
  <div class="score-row">
    <span class="score-dot" style="background:var(--error);"></span>
    <span>错误</span><span style="margin-left:auto;font-weight:600;color:var(--error);">${wrong}</span>
  </div>
  <div class="score-row">
    <span class="score-dot" style="background:var(--text-4);"></span>
    <span>总题数</span><span style="margin-left:auto;color:var(--text-3);">${total}</span>
  </div>
</div>`;
```

Update the knowledge breakdown section:
```javascript
html += `<div class="breakdown-card">
  <div class="breakdown-title">知识点掌握情况</div>
  ${items.map(item => `
    <div class="breakdown-item">
      <div class="breakdown-icon ${item.mastered ? 'correct' : 'incorrect'}">
        ${item.mastered ? '✓' : '✗'}
      </div>
      <span class="breakdown-name">${escapeHtml(item.name)}</span>
      <span class="breakdown-tag ${item.mastered ? 'mastered' : 'weak'}">
        ${item.mastered ? '已掌握' : '需加强'}
      </span>
    </div>
  `).join('')}
</div>`;
```

Update the weak-point callout:
```javascript
if (weakPoints.length > 0) {
  html += `<div class="weak-callout">
    <div class="weak-callout-title">⚠ 建议重点复习</div>
    <div class="weak-callout-text">${weakPoints.map(w => escapeHtml(w)).join('、')}</div>
  </div>`;
}
```

Update the CTA buttons:
```javascript
html += `<div class="cta-row">
  <button class="cta-primary" onclick="startLesson()">开始课程 →</button>
  <button class="cta-ghost" onclick="window.location.href='index.html'">返回首页</button>
</div>`;
```

Remove any SVG score ring code — replace with the plain `score-num` approach above.

- [ ] **Step 4: Verify in browser**

```bash
uv run python run/serve.py
```

Navigate to results page. Check:
- Sidebar + step nav (all orange ✓)
- Score card: large orange number, row breakdown
- Knowledge breakdown: green/red icons, mastered/weak tags
- Weak-point callout: orange left border
- CTA: orange fill + ghost button

- [ ] **Step 5: Commit**

```bash
git add static/results.html
git commit -m "style: restyle results.html — score card, breakdown, Claude design tokens"
```

---

## Spec coverage self-review

| Spec requirement | Task |
|-----------------|------|
| CSS custom properties on `:root` | All tasks (Step 1 each) |
| Inter font, remove Manrope | All tasks (Step 2 font link) |
| Sidebar 210px, `--bg-sidebar`, dashed new-session btn | All tasks |
| Top nav 50px, title + skill pill | Tasks 1–5 |
| Step nav 44px, 4 states (pending/done/active) | Tasks 2–5 |
| `index.html` home state + chat state | Task 1 |
| Knowledge graph card (white, 14px radius, legend, orange btn) | Task 1 |
| User bubble `--user-bubble`, AI avatar gradient | Task 1 |
| Input bar 14px radius, orange send btn, focus ring | Task 1 |
| `diagnosis.html` graph sub-state + quiz cards | Task 2 |
| Quiz progress bar 4px orange gradient | Task 2 |
| Option buttons orange hover/selected, letter circle | Task 2 |
| `lesson.html` 3-col (TOC 190px / flex:1 / right 200px) | Task 3 |
| TOC active orange, weak-point box | Task 3 |
| Inline KS cards green dashed | Task 3 |
| Right panel: KS list + toggle + mini graph | Task 3 |
| Pedagogy skill card orange | Task 3 |
| `slides.html` shell restyle + sidebar + step nav | Task 4 |
| `results.html` score card + breakdown + CTA | Task 5 |
| All JS logic untouched | Confirmed: only HTML/CSS changed |
| `api.js`, `sidebar.js` untouched | Confirmed: no changes to those files |
