---
name: Frontend restyle initiative (Material → Claude warm cream/orange)
description: Multi-page redesign on demo+memory branch replacing Material Design tokens with CSS custom-property design system shared across static pages
type: project
---

5-page redesign migrating `static/*.html` from Tailwind/Material Design (blue #005da7 primary, Manrope/Inter fonts, surface-container-* tokens) to a warm cream/orange Claude-style palette driven by CSS custom properties on `:root`.

**Why:** Visual consistency with a new product identity ("EduAI"); the old Material tokens were inconsistently applied across pages. The design system lives inline in each HTML head — no shared stylesheet — so each page duplicates the `:root` token block.

**How to apply:**
- The canonical token set lives in `static/index.html` (Task 1, completed). All other pages must mirror its `:root` variables exactly (`--bg`, `--bg-sidebar`, `--border`, `--accent` #CF6833, `--accent-dark` #9E4A2A, `--text*`, `--success`, `--error`).
- Each page has: 210px fixed sidebar (`#sidebar`), 50px top nav (`#topNav`, `left:210px`), main content `margin-left:210px`. Some pages add a 44px `#stepNav` between top nav and content (top:50px) — diagnosis/lesson/slides/results.
- Shared script: `sidebar.js?v=2` populates `#session-list`. NOTE as of 633bea4 sidebar.js still uses old Tailwind classes (`bg-primary-fixed/40`, `text-primary`, `font-headline`, etc.) — these will not render correctly under the new design system. Flag this on every restyled page review until sidebar.js is migrated (likely a separate task).
- Task order in TaskList: 1=index (done), 2=diagnosis, 3=lesson, 4=slides, 5=results.
- The `#_legacyProgress` hidden div pattern is used to keep old `getElementById('progressText'/'progressBar')` calls non-null without ripping out the JS — acceptable transitional pattern, but worth tracking for cleanup.
