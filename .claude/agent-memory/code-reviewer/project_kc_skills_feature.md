---
name: KC Skills Runtime Injection Feature
description: KC skills feature on demo+memory: state fields, kc_loader, resolver node, prompt helpers, 3 consumer nodes, frontend card — reviewed in commit range ec35306..HEAD (11 commits)
type: project
---

KC skills feature (XES3G5M kc_skills.json → lesson/quiz/slides injection) merged into demo+memory branch.

**Why:** Add exam-bank-grounded metadata (考察重点, 易错点, etc.) to personalize quiz generation and lesson content.

**How to apply:** Feature is APPROVED FOR MERGE with one known latent risk: `_llm_resolve` in `kc_skill_resolver.py` dumps the entire kc_skills.json route catalogue into a single LLM prompt. At 5 entries (current test fixture) this is fine; if XES3G5M grows to hundreds of entries, this will exceed context limits. Flag this before scaling the JSON bank.

Key design decisions:
- `kc_skills: dict | None` vs `knowledge_skills: dict | None` — intentionally orthogonal (see state.py comments)
- `use_kc_skills: bool` (not `Optional[bool]`) — safe because resolver guard uses `state.get("use_kc_skills")` which returns None (falsy) when key absent
- `kc_skills` stored as `dict[str, dict]` (plain dicts from KCSkill.model_dump()) — round-trips cleanly through JSON store
- Frontend KC card scoped to `section-1` only — works for current single-section lesson but would miss content in multi-section layouts
