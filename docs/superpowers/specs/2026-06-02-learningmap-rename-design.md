# LearningMAP rename — frontend brand refresh

**Date:** 2026-06-02
**Branch scope:** `main` only (zh handled in a later cycle)
**Type:** brand/string rename, no logic change

---

## Goal

Unify the product brand across the `main` branch by replacing two legacy names:

- `OpenAILearning` → `LearningMAP` (the product brand)
- `EduAI` → split by location:
  - Brand surfaces (`<title>`, top-nav title) → `LearningMAP`
  - Assistant persona name (`<div class="ai-name">` on chat bubbles) → `Merlin`

`OpenAILearning` is the original project name (still the git repo name and the on-disk RunPod deploy path); `EduAI` was a placeholder assistant brand only used inside `static/index.html`. Going forward the product is `LearningMAP` and the AI assistant character is `Merlin`.

## Why this split (brand vs persona)

The two `EduAI` occurrences at `<title>` and `topnav-title` were doing double duty — historically labelled "EduAI" but functionally the page/app title. The other 6 occurrences (`<div class="ai-name">EduAI</div>`) sit above each AI chat bubble and function as the assistant's display name (like "ChatGPT" appearing above ChatGPT responses).

Conflating these gives a worse outcome regardless of which single value we pick:
- All `LearningMAP` → the assistant speaks "as the product", awkward and impersonal
- All `Merlin` → page/tab/topnav stop matching the rest of the site's brand surface

Splitting brand from persona resolves both — the title bar/topnav consistently say `LearningMAP` (matching every other page), and chat bubbles consistently say `Merlin` (giving the assistant a stable character).

## Scope (in)

Eight files on `main`, all edits via the `Edit` tool (no `sed` / bulk replace, to avoid collateral damage in surrounding markup):

### `static/index.html` (9 edits in one file)

| Line | Current | New |
|---|---|---|
| 6 | `<title>EduAI</title>` | `<title>LearningMAP</title>` |
| 278 | `<span class="topnav-title" id="topNavTitle">EduAI</span>` | `<span class="topnav-title" id="topNavTitle">LearningMAP</span>` |
| 293 | `<span class="sidebar-logo-text">OpenAILearning</span>` | `<span class="sidebar-logo-text">LearningMAP</span>` |
| 680 | `<div class="ai-name">EduAI</div>` | `<div class="ai-name">Merlin</div>` |
| 799 | `<div class="ai-name">EduAI</div>` | `<div class="ai-name">Merlin</div>` |
| 1000 | `<div class="ai-name">EduAI</div>` | `<div class="ai-name">Merlin</div>` |
| 1108 | `<div class="ai-name">EduAI</div>` | `<div class="ai-name">Merlin</div>` |
| 1184 | `<div class="ai-name">EduAI</div>` | `<div class="ai-name">Merlin</div>` |
| 1309 | `<div class="ai-name">EduAI</div>` | `<div class="ai-name">Merlin</div>` |

Note: the 6 `ai-name` occurrences are produced by 6 separate JS template literals (different chat-render paths: initial render, history replay, streaming, diagnosis card, knowledge-graph card, etc.). They must each be edited — a single shared constant does not exist today and introducing one is **out of scope** for this rename.

### `static/lesson.html` (2 edits)
- L6: `<title>OpenAILearning - Lesson</title>` → `<title>LearningMAP - Lesson</title>`
- L345: `<span class="sidebar-logo-text">OpenAILearning</span>` → `<span class="sidebar-logo-text">LearningMAP</span>`

### `static/diagnosis.html` (2 edits)
- L6: `<title>OpenAILearning - Diagnosis</title>` → `<title>LearningMAP - Diagnosis</title>`
- L148: `<span class="sidebar-logo-text">OpenAILearning</span>` → `<span class="sidebar-logo-text">LearningMAP</span>`

### `static/results.html` (2 edits)
- L5: `<title>OpenAILearning - Results</title>` → `<title>LearningMAP - Results</title>`
- L140: `<span class="sidebar-logo-text">OpenAILearning</span>` → `<span class="sidebar-logo-text">LearningMAP</span>`

### `static/slides.html` (2 edits)
- L6: `<title>OpenAILearning - Slides</title>` → `<title>LearningMAP - Slides</title>`
- L131: `<span class="sidebar-logo-text">OpenAILearning</span>` → `<span class="sidebar-logo-text">LearningMAP</span>`

### `README.md` (3 edits)
- L1: `# OpenAILearning` → `# LearningMAP`
- L33: `OpenAILearning is a full-stack tutoring demo.` → `LearningMAP is a full-stack tutoring demo.`
- L182: directory-tree label `OpenAILearning/` → `LearningMAP/`

### `run/serve.py` (1 edit)
- L19: `app = FastAPI(title="OpenAILearning API", version="0.1.0")` → `app = FastAPI(title="LearningMAP API", version="0.1.0")`

### `development_log.md` (1 edit)
- L67: prose mention `/workspace/OpenAILearning` → `/workspace/LearningMAP`. **Description-only**; does NOT change the actual deployment path on the RunPod server.

**Total: 22 edits across 8 files** (9 in `static/index.html`, 2×4 in the other static pages, 3 in `README.md`, 1 in `run/serve.py`, 1 in `development_log.md`).

## Scope (out)

Explicitly NOT changed in this spec:

1. **`zh` branch** — same 8 files exist there with similar pattern plus an `OpenAILearning · 中文版` README heading. Handled in a separate cycle after `main` ships.
2. **Python package name** `openail` in `pyproject.toml` and the `openail.egg-info/` directory — these are backend code identifiers, not user-facing brand surfaces.
3. **RunPod server deploy path** `/workspace/OpenAILearning` (actual filesystem location on the deployment box) — coupled to `nginx` config and persistent volumes. Only the prose description of this path in `development_log.md` changes; the path itself stays put.
4. **Git repository name** `OpenAILearning` (GitHub repo name) — outside the working tree.
5. **Git history** — historical commit messages and prior log entries are not rewritten.

## Approach

1. Single atomic commit on `main`.
2. Edits applied file-by-file via the `Edit` tool (not `sed`) — each replacement uses enough surrounding context to be unambiguous, so an accidental match in unrelated markup is impossible.
3. Commit message: `chore(brand): rename OpenAILearning → LearningMAP, EduAI → Merlin (frontend + docs)`

No new files. No removed files. No code-logic changes.

## Verification (success criteria — all must hold)

A1. **No residual occurrences in the in-scope file set.** This command returns zero lines:
```bash
git grep -nE "OpenAILearning|EduAI" -- 'static/*.html' 'README.md' 'run/serve.py' 'development_log.md'
```

A2. **Out-of-scope occurrences still present.** This command returns at least the expected matches (`pyproject.toml`, `openail.egg-info/`, possibly other historical references) — i.e., we did not over-reach:
```bash
git grep -nE "OpenAILearning|openail" -- '*.toml' 'openail.egg-info/'
```

A3. **Visual check `static/index.html`** (open in a browser):
- Browser tab title: `LearningMAP`
- Top navigation bar: `LearningMAP`
- Sidebar logo text: `LearningMAP`
- All 6 chat-bubble assistant labels: `Merlin` (exercise: send a message, replay session history, trigger streaming, view a diagnosis card, view a knowledge-graph card, view a generic ai message — covers all 6 template paths)

A4. **Visual check other static pages** (open each in a browser):
- `lesson.html`: tab = `LearningMAP - Lesson`, sidebar logo = `LearningMAP`
- `diagnosis.html`: tab = `LearningMAP - Diagnosis`, sidebar logo = `LearningMAP`
- `results.html`: tab = `LearningMAP - Results`, sidebar logo = `LearningMAP`
- `slides.html`: tab = `LearningMAP - Slides`, sidebar logo = `LearningMAP`

A5. **Backend check.** `python run/serve.py` (which internally runs uvicorn on port 8080 via the `if __name__ == "__main__":` block) → open `http://localhost:8080/docs` → OpenAPI page title shows `LearningMAP API`.

A6. **Single commit.** `git log -1 --stat` on `main` shows exactly one new commit touching exactly the 8 files above, no other files modified.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `Edit` `old_string` not unique for one of the 6 identical `ai-name` lines in `index.html` | Each `Edit` call uses a multi-line context (a few lines of surrounding markup unique to that template literal), not just the bare `<div class="ai-name">EduAI</div>` line. If a context still collides, fall back to `replace_all` on the bare line since the desired replacement is identical at all 6 sites. |
| `OpenAILearning` appears somewhere we forgot to grep (e.g., a `.json`, `.svg`, hidden file) | Verification A1 grep is broader than the spec's listed files would catch — but A1 deliberately scopes to in-scope files. As a sanity check, also run a global grep before commit and surface anything unexpected to the user before proceeding. |
| RunPod deploy breaks because nginx / a startup script references the path described in `development_log.md` | The log is documentation only — nothing reads it. The actual server path is unchanged (out-of-scope item 3). Zero deploy risk from this rename. |
| `zh` branch drifts further from `main` and the later zh rename hits more merge conflicts | Acceptable — the bilingual two-branch initiative ([[project_i18n_bilingual_initiative]]) already accepts main/zh drift as the design. The zh rename will be a near-mirror of this spec applied to the zh branch's file copies. |

## What this spec does NOT decide

- Whether `Merlin` gets an icon/avatar change (today the avatar is a generic `A` letter on a colored circle) — kept as-is.
- Whether `<title>` for `index.html` should add a suffix like `LearningMAP - Chat` for symmetry with the other pages — current decision is bare `LearningMAP` (the index is the landing page; no qualifier needed). Revisit if it looks weird in browser tab strip.
- Anything about how to roll the same rename to `zh` (separate spec).
