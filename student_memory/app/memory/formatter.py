"""Format ranked retrieval results into a prompt-ready ``context_text``.

Determinism matters: the demo branch's chat node will splice this string
directly into a system prompt, so the format must be stable across calls.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.memory.reranker import ScoredCandidate

EMPTY_CONTEXT = "(no relevant past learning records)"


def format_context(results: Sequence[ScoredCandidate]) -> str:
    """Render the top-k results as a numbered, prompt-ready block.

    Returns a stable fallback string when ``results`` is empty so the
    caller can splice the value unconditionally without conditional
    logic on its own side.
    """
    if not results:
        return EMPTY_CONTEXT
    lines = ["Relevant past learning records:"]
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. {r.candidate.retrieval_text}")
    return "\n".join(lines)
