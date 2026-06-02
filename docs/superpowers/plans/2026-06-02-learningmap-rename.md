# LearningMAP Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace product brand `OpenAILearning` → `LearningMAP` and assistant persona `EduAI` → `Merlin` (or `LearningMAP` for brand surfaces) across 7 files on the `main` branch — pure string substitution, no logic change, one atomic commit.

**Architecture:** File-by-file `Edit` operations (no `sed` / bulk replace) to avoid collateral damage; each file is verified independently with a scoped grep before moving on; final atomic commit only after all 7 files are clean and verifications pass.

**Tech Stack:** Static HTML pages under `static/`, FastAPI backend at `run/serve.py`, package managed by `uv`, no frontend build step (edits land directly in the served files).

**Spec:** [`docs/superpowers/specs/2026-06-02-learningmap-rename-design.md`](../specs/2026-06-02-learningmap-rename-design.md)

---

## File Structure

No new files, no removed files. Seven files modified:

| File | Edits | What changes |
|---|---|---|
| `static/index.html` | 9 | `<title>`, topnav title, sidebar logo, 6× `ai-name` chat bubble labels |
| `static/lesson.html` | 2 | `<title>`, sidebar logo |
| `static/diagnosis.html` | 2 | `<title>`, sidebar logo |
| `static/results.html` | 2 | `<title>`, sidebar logo |
| `static/slides.html` | 2 | `<title>`, sidebar logo |
| `README.md` | 3 | H1 title, prose mention, directory-tree label |
| `run/serve.py` | 1 | FastAPI app `title=` kwarg |

Out-of-scope (do NOT touch): `development_log.md`, `pyproject.toml`, `openail.egg-info/`, all `openail_*` localStorage keys in `static/sidebar.js` and `static/index.html`, any favicon / manifest / og: tag, the `zh` branch.

---

## Task 1: Establish pre-flight grep baseline

**Files:** none modified — read-only sanity check before any Edit.

- [ ] **Step 1: Run the in-scope grep to confirm baseline hits exist**

Run: `git grep -nE "OpenAILearning|EduAI" -- 'static/*.html' 'README.md' 'run/serve.py'`

Expected output: a non-empty list, specifically including (line numbers may shift if file changed since spec written; presence is what matters):
- `static/index.html` — at least 9 lines (1× `<title>EduAI`, 1× `topNavTitle`, 1× `sidebar-logo-text OpenAILearning`, 6× `ai-name EduAI`)
- `static/lesson.html` — 2 lines
- `static/diagnosis.html` — 2 lines
- `static/results.html` — 2 lines
- `static/slides.html` — 2 lines
- `README.md` — 3 lines
- `run/serve.py` — 1 line

If a file shows fewer hits than expected, STOP and re-read the spec — the file may have been changed between spec writing and execution. Do not proceed.

- [ ] **Step 2: Run the out-of-scope grep to record what MUST still match after we're done**

Run: `git grep -nE "OpenAILearning|openail" -- '*.toml' 'openail.egg-info/'`

Expected: at least `pyproject.toml:6:name = "openail"` and several lines from `openail.egg-info/`. Record this list (paste into your scratchpad) — verification Task 9 will re-run this grep and the output must be identical to this baseline.

- [ ] **Step 3: Record `git status` baseline**

Run: `git status`

Expected: clean working tree OR only pre-existing modifications (e.g., `development_log.md`, other in-progress files). Note any files already modified so they can be distinguished from rename-introduced changes at commit time.

---

## Task 2: Rename `static/index.html` (9 edits)

**Files:**
- Modify: `static/index.html` (lines ~6, ~278, ~293, and 6× `ai-name` lines around 680/799/1000/1108/1184/1309)

- [ ] **Step 1: Edit the `<title>` (brand surface, 1 edit)**

Use `Edit` tool with the surrounding `<meta>`/`<script>` markup as context so it's unambiguous:

```
old_string: <meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>EduAI</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>

new_string: <meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>LearningMAP</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
```

- [ ] **Step 2: Edit the top-nav title (brand surface, 1 edit)**

```
old_string: <span class="topnav-title" id="topNavTitle">EduAI</span>
new_string: <span class="topnav-title" id="topNavTitle">LearningMAP</span>
```

(This string is unique in the file — no extra context needed.)

- [ ] **Step 3: Edit the sidebar logo text (brand surface, 1 edit)**

```
old_string: <div class="sidebar-logo-icon"><span class="material-symbols-outlined" style="font-size:18px;">school</span></div>
    <span class="sidebar-logo-text">OpenAILearning</span>

new_string: <div class="sidebar-logo-icon"><span class="material-symbols-outlined" style="font-size:18px;">school</span></div>
    <span class="sidebar-logo-text">LearningMAP</span>
```

(The two-line context disambiguates this from the same `sidebar-logo-text` pattern in other static pages — though those are different files so collision is impossible anyway; the context just makes the intent obvious.)

- [ ] **Step 4: Edit all 6 chat-bubble `ai-name` labels in one shot (persona, 6 edits via `replace_all`)**

All 6 occurrences are byte-identical (`<div class="ai-name">EduAI</div>`) and all 6 must become the same new string. Use `Edit` with `replace_all: true`:

```
old_string: <div class="ai-name">EduAI</div>
new_string: <div class="ai-name">Merlin</div>
replace_all: true
```

This avoids the per-call uniqueness problem the spec's Risks section calls out.

- [ ] **Step 5: Verify no residual hits in this file**

Run: `grep -nE "OpenAILearning|EduAI" static/index.html`

Expected output: **(empty — zero matches)**.

If any line still matches, STOP. Likely cause: an `EduAI` occurrence outside the `ai-name` pattern that `replace_all` didn't catch — re-read the file around the unexpected match and add a targeted `Edit` for it.

- [ ] **Step 6: Spot-check that `Merlin` is now present at the expected count**

Run: `grep -c "<div class=\"ai-name\">Merlin</div>" static/index.html`

Expected output: `6`

If the count is anything other than 6, something went wrong — investigate before proceeding.

---

## Task 3: Rename `static/lesson.html` (2 edits)

**Files:**
- Modify: `static/lesson.html` (lines ~6, ~345)

- [ ] **Step 1: Edit the `<title>`**

```
old_string: <title>OpenAILearning - Lesson</title>
new_string: <title>LearningMAP - Lesson</title>
```

- [ ] **Step 2: Edit the sidebar logo text**

```
old_string: <span class="sidebar-logo-text">OpenAILearning</span>
new_string: <span class="sidebar-logo-text">LearningMAP</span>
```

- [ ] **Step 3: Verify no residual hits in this file**

Run: `grep -nE "OpenAILearning|EduAI" static/lesson.html`

Expected output: **(empty)**.

---

## Task 4: Rename `static/diagnosis.html` (2 edits)

**Files:**
- Modify: `static/diagnosis.html` (lines ~6, ~148)

- [ ] **Step 1: Edit the `<title>`**

```
old_string: <title>OpenAILearning - Diagnosis</title>
new_string: <title>LearningMAP - Diagnosis</title>
```

- [ ] **Step 2: Edit the sidebar logo text**

```
old_string: <span class="sidebar-logo-text">OpenAILearning</span>
new_string: <span class="sidebar-logo-text">LearningMAP</span>
```

- [ ] **Step 3: Verify no residual hits in this file**

Run: `grep -nE "OpenAILearning|EduAI" static/diagnosis.html`

Expected output: **(empty)**.

---

## Task 5: Rename `static/results.html` (2 edits)

**Files:**
- Modify: `static/results.html` (lines ~5, ~140)

- [ ] **Step 1: Edit the `<title>`**

```
old_string: <title>OpenAILearning - Results</title>
new_string: <title>LearningMAP - Results</title>
```

- [ ] **Step 2: Edit the sidebar logo text**

```
old_string: <span class="sidebar-logo-text">OpenAILearning</span>
new_string: <span class="sidebar-logo-text">LearningMAP</span>
```

- [ ] **Step 3: Verify no residual hits in this file**

Run: `grep -nE "OpenAILearning|EduAI" static/results.html`

Expected output: **(empty)**.

Note: this file also contains `<link rel="icon" href="favicon.ico"/>` — leave it alone (Scope (out) #7 in spec).

---

## Task 6: Rename `static/slides.html` (2 edits)

**Files:**
- Modify: `static/slides.html` (lines ~6, ~131)

- [ ] **Step 1: Edit the `<title>`**

```
old_string: <title>OpenAILearning - Slides</title>
new_string: <title>LearningMAP - Slides</title>
```

- [ ] **Step 2: Edit the sidebar logo text**

```
old_string: <span class="sidebar-logo-text">OpenAILearning</span>
new_string: <span class="sidebar-logo-text">LearningMAP</span>
```

- [ ] **Step 3: Verify no residual hits in this file**

Run: `grep -nE "OpenAILearning|EduAI" static/slides.html`

Expected output: **(empty)**.

---

## Task 7: Rename `README.md` (3 edits)

**Files:**
- Modify: `README.md` (lines ~1, ~33, ~182)

- [ ] **Step 1: Edit the H1 title**

```
old_string: # OpenAILearning
new_string: # LearningMAP
```

- [ ] **Step 2: Edit the prose mention**

```
old_string: OpenAILearning is a full-stack tutoring demo. The frontend is a set of static pages; the backend is a FastAPI service built on a **LangGraph** state machine. Every HTTP request drives one single-round graph execution; branches are selected by an `intent` node:
new_string: LearningMAP is a full-stack tutoring demo. The frontend is a set of static pages; the backend is a FastAPI service built on a **LangGraph** state machine. Every HTTP request drives one single-round graph execution; branches are selected by an `intent` node:
```

- [ ] **Step 3: Edit the directory-tree label**

Run `grep -n "^OpenAILearning/" README.md` first to confirm the exact context (likely the start of a code-fenced tree block). Then:

```
old_string: OpenAILearning/
new_string: LearningMAP/
```

If `grep` shows multiple matches (e.g., the string appears inside other prose), add 1-2 lines of surrounding context to the `old_string` to disambiguate.

- [ ] **Step 4: Verify no residual hits in this file**

Run: `grep -nE "OpenAILearning|EduAI" README.md`

Expected output: **(empty)**.

---

## Task 8: Rename `run/serve.py` (1 edit)

**Files:**
- Modify: `run/serve.py` (line ~19)

- [ ] **Step 1: Edit the FastAPI app title**

```
old_string: app = FastAPI(title="OpenAILearning API", version="0.1.0")
new_string: app = FastAPI(title="LearningMAP API", version="0.1.0")
```

- [ ] **Step 2: Verify no residual hits in this file**

Run: `grep -nE "OpenAILearning|EduAI" run/serve.py`

Expected output: **(empty)**.

Note: do NOT change the lowercase `openail` package name anywhere — that's Scope (out) #2 in spec.

---

## Task 9: Repo-wide grep verification (Spec A1 + A2)

**Files:** none modified — verification only.

- [ ] **Step 1: A1 — in-scope grep returns zero hits**

Run: `git grep -nE "OpenAILearning|EduAI" -- 'static/*.html' 'README.md' 'run/serve.py'`

Expected output: **(empty — zero lines).**

If any line matches, identify the file, return to its task above, and fix.

- [ ] **Step 2: A2 — out-of-scope grep still matches expected lines**

Run: `git grep -nE "OpenAILearning|openail" -- '*.toml' 'openail.egg-info/'`

Expected output: identical to the baseline recorded in Task 1, Step 2 (at minimum: `pyproject.toml:6:name = "openail"` plus several `openail.egg-info/...` lines).

If the output differs from baseline, we accidentally edited an out-of-scope file — STOP and investigate `git diff` before continuing.

- [ ] **Step 3: Broad sanity grep — catch anything missed**

Run: `git grep -nE "OpenAILearning|EduAI"`

Expected output: zero lines.

Why: the spec's A1 grep deliberately scopes to known files. This broader grep catches surprises (e.g., a `.json`, `.svg`, hidden config file we forgot about). If anything else matches, surface it to the user before committing — don't silently auto-fix, because it may be intentional history (e.g., this spec file itself in `docs/superpowers/specs/` mentions both strings repeatedly; if any path under `docs/superpowers/` shows up here that's expected and fine).

If matches appear OUTSIDE `docs/superpowers/`, STOP and flag to user.

---

## Task 10: Visual verification of `static/index.html` (Spec A3)

**Files:** none modified — manual browser check.

This task produces a human checklist; an automated executor should run Step 1 then surface the checklist to the user.

- [ ] **Step 1: Source-level grep confirmation of post-rename strings**

Run these and confirm each expected count:

```bash
grep -c "<title>LearningMAP</title>" static/index.html   # expect: 1
grep -c "topNavTitle\">LearningMAP" static/index.html    # expect: 1
grep -c "sidebar-logo-text\">LearningMAP" static/index.html # expect: 1
grep -c "<div class=\"ai-name\">Merlin</div>" static/index.html # expect: 6
```

If any count is wrong, STOP and fix.

- [ ] **Step 2: Manual browser check (handoff to user)**

Open `static/index.html` in a browser (drag-and-drop into Chrome works; no server needed for source-level rendering, but to exercise the JS chat paths you need the running backend — see Task 12 for startup, then return here).

Verify in the rendered page:
- Browser tab title reads **LearningMAP**
- Top-nav bar reads **LearningMAP**
- Sidebar logo text reads **LearningMAP**
- Six chat-bubble paths each render the assistant label as **MERLIN** (uppercase is intentional — `.ai-name { text-transform: uppercase; }` is unchanged):
  1. Send a fresh message → check the streaming response label
  2. Reload the page → history-replay AI bubbles show MERLIN
  3. During streaming, the thinking placeholder bubble shows MERLIN
  4. Trigger a diagnosis flow → diagnosis-results card shows MERLIN
  5. Trigger a knowledge-graph flow → KG card shows MERLIN
  6. Any other AI response path encountered → shows MERLIN

If any path still shows `EduAI`, capture the page state and add a fix-up task — there was an `ai-name` template path the rename missed.

---

## Task 11: Visual verification of the 4 secondary static pages (Spec A4)

**Files:** none modified — automated source check + manual browser check.

- [ ] **Step 1: Source-level grep confirmation**

Run:

```bash
grep -c "<title>LearningMAP - Lesson</title>" static/lesson.html       # expect: 1
grep -c "sidebar-logo-text\">LearningMAP" static/lesson.html           # expect: 1
grep -c "<title>LearningMAP - Diagnosis</title>" static/diagnosis.html # expect: 1
grep -c "sidebar-logo-text\">LearningMAP" static/diagnosis.html        # expect: 1
grep -c "<title>LearningMAP - Results</title>" static/results.html     # expect: 1
grep -c "sidebar-logo-text\">LearningMAP" static/results.html          # expect: 1
grep -c "<title>LearningMAP - Slides</title>" static/slides.html       # expect: 1
grep -c "sidebar-logo-text\">LearningMAP" static/slides.html           # expect: 1
```

All 8 counts must be `1`.

- [ ] **Step 2: Manual browser check (handoff to user)**

Open each page (these typically need the running backend serving them on `http://localhost:8080/<page>` — start it per Task 12 first). Verify:
- `lesson.html`: tab = `LearningMAP - Lesson`, sidebar = LearningMAP
- `diagnosis.html`: tab = `LearningMAP - Diagnosis`, sidebar = LearningMAP
- `results.html`: tab = `LearningMAP - Results`, sidebar = LearningMAP
- `slides.html`: tab = `LearningMAP - Slides`, sidebar = LearningMAP

---

## Task 12: Backend verification (Spec A5)

**Files:** none modified — start server and curl.

- [ ] **Step 1: Start the backend in the background**

Run: `uv run python run/serve.py` (start it in a background shell so subsequent steps can run; the server binds `0.0.0.0:8080` and runs uvicorn internally per `run/serve.py` lines 45-47).

Wait ~3 seconds for boot.

- [ ] **Step 2: Verify OpenAPI `info.title` via `/openapi.json` (authoritative source)**

Run: `curl -s http://localhost:8080/openapi.json | python -c "import sys, json; print(json.load(sys.stdin)['info']['title'])"`

Expected output: `LearningMAP API`

- [ ] **Step 3: Verify Swagger UI page title (browser-rendered)**

Run: `curl -s http://localhost:8080/docs | grep -oE "<title>[^<]+</title>"`

Expected output: `<title>LearningMAP API - Swagger UI</title>`

- [ ] **Step 4: Stop the background server**

Kill the background shell from Step 1.

---

## Task 13: Single atomic commit (Spec A6)

**Files:** all 7 files staged together.

- [ ] **Step 1: Inspect `git status` and `git diff --stat` to confirm exactly the 7 in-scope files changed**

Run: `git status`

Expected: the 7 in-scope files in "Changes not staged for commit" (plus any pre-existing modifications recorded in Task 1 Step 3, which should NOT be staged).

Run: `git diff --stat -- static/index.html static/lesson.html static/diagnosis.html static/results.html static/slides.html README.md run/serve.py`

Expected: 7 files listed with line counts (`static/index.html` ~9 lines, others 2-3 lines each).

If any unexpected file appears modified (e.g., `static/sidebar.js` — would mean we touched an `openail_*` key by mistake), STOP and investigate.

- [ ] **Step 2: Stage the 7 files explicitly (do NOT use `git add -A` / `git add .`)**

Run:

```bash
git add static/index.html static/lesson.html static/diagnosis.html static/results.html static/slides.html README.md run/serve.py
```

- [ ] **Step 3: Verify staged content**

Run: `git diff --cached --stat`

Expected: same 7 files as Step 1; no extras.

- [ ] **Step 4: Create the atomic commit**

Run:

```bash
git commit -m "$(cat <<'EOF'
chore(brand): rename OpenAILearning → LearningMAP, EduAI → Merlin (frontend + docs)

Product brand: OpenAILearning → LearningMAP across 5 static pages,
README.md, and FastAPI app title in run/serve.py.

Assistant persona: EduAI → LearningMAP for brand surfaces
(<title>, topnav) and EduAI → Merlin for the 6 chat-bubble ai-name
labels (visual rendering remains uppercase MERLIN, intentional).

Out-of-scope (deliberately unchanged):
  - zh branch (separate cycle)
  - /workspace/OpenAILearning RunPod deploy path and its log entry
  - openail Python package name and openail.egg-info/
  - openail_* localStorage keys (would orphan user sessions)
  - favicon/manifest/og: metadata

Spec: docs/superpowers/specs/2026-06-02-learningmap-rename-design.md
Plan: docs/superpowers/plans/2026-06-02-learningmap-rename.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Verify the commit looks right**

Run: `git log -1 --stat`

Expected output: exactly one new commit on `main`, touching exactly the 7 in-scope files, with the message above.

If extra files appear in the commit, run `git reset HEAD~1`, un-stage the extras, and redo the commit.

- [ ] **Step 6: Final repo-wide A1 grep on the committed tree**

Run: `git grep -nE "OpenAILearning|EduAI" -- 'static/*.html' 'README.md' 'run/serve.py'`

Expected output: **(empty).**

Done.

---

## Out-of-scope guard rails (DO NOT TOUCH during execution)

If during any task you find yourself about to edit one of these, STOP — it is explicitly out of scope per Spec § Scope (out):

1. The `zh` branch — separate cycle
2. `pyproject.toml` `name = "openail"` and `openail.egg-info/`
3. `/workspace/OpenAILearning` references in `development_log.md` (and the actual server path)
4. Git history / commit messages
5. `openail_sessions`, `openail_current_session`, `openail_v4`, `openail_examples_v2`, `openail_v3_clean` in `static/sidebar.js` or `static/index.html` (these would orphan user browser sessions)
6. `<link rel="icon" href="favicon.ico"/>` in `static/results.html` (leave as-is; no asset exists, no new asset added)
7. Any new file creation (this rename creates zero new files)
