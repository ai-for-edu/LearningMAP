---
name: XSS discipline for innerHTML writes
description: All innerHTML writes in static/*.html must escape user-sourced or LLM-sourced data
type: feedback
---

All `innerHTML` writes in `static/*.html` and `static/*.js` MUST route user/LLM-sourced strings through `escapeHtml()` or `DOMPurify.sanitize()` (for markdown via `safeMarkdown`).

**Why:** Documented in CLAUDE.md as a project invariant. Quiz questions, lesson content, knowledge-graph node names, and skill descriptions all originate from LLM output and could contain HTML/script payloads. The team has chosen XSS-by-construction: escape at the boundary, not on display.

**How to apply:**
- When reviewing a `*.innerHTML = ...` template literal, trace every interpolated `${expr}` to its source. If it could be user-typed or LLM-generated, it must be wrapped in `escapeHtml()`.
- `textContent` assignments (e.g. `el.textContent = q.question`) are inherently safe — no escape needed.
- Static template literals with no `${}` interpolation are safe.
- `marked` + `DOMPurify` are loaded on pages that render markdown (index.html, lesson.html); `safeMarkdown()` falls back to `escapeHtml()` if either lib is missing.
