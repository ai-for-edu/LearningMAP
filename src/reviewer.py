"""LLM-driven memory reviewer.

Spec §6 (`docs/superpowers/specs/2026-05-08-hybrid-agent-memory-design.md`).

Entry point ``run_memory_review`` is added in a later task. This module
keeps every helper pure and unit-testable in isolation: ``_hash``,
``_is_injection``, ``_collect_extract_input`` have no I/O.

LLM calls (``_extract_candidates``, ``_consolidate``) are isolated to two
functions that take an instructor-wrapped client as an argument so tests
can swap in a MagicMock without monkey-patching globals.
"""

from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def _hash(text: str) -> str:
    """16-hex-char SHA-256 prefix of normalized content.

    Normalization mirrors the server's ``_content_hash``: lowercase + strip.
    The two MUST stay in sync — the UNIQUE partial index added in PR-1
    fix #1 uses the server-side normalization, and a divergent client
    hash would let dedup misses sneak past the in-Python set only to
    fail at the database layer.
    """
    return hashlib.sha256(text.lower().strip().encode("utf-8")).hexdigest()[:16]


_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)ignore (previous|all|the above) instructions"),
    re.compile(r"(?i)disregard (the )?(system|prior) (prompt|message)"),
    re.compile(r"(?i)you are now"),
    re.compile(r"<\|.*?\|>"),
    re.compile(r"(?i)system\s*:"),
]


def _is_injection(content: str) -> bool:
    return any(p.search(content) for p in _INJECTION_PATTERNS)
