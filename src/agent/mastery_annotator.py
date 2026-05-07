"""Annotate a KnowledgeGraph with per-node mastery metadata.

Two-stage match between KG nodes and memory mastery rows:
1. exact name match (case- and whitespace-sensitive)
2. normalized name match — strip whitespace, lowercase ASCII, drop
   common punctuation. Catches LLM drift like "判别式 (Δ)" vs "判别式".

Anything still unmatched stays as `mastery=None`, which the frontend
renders as "unseen". Malformed rows are skipped (graceful degradation).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from src.forgetting.base import ForgettingModel
from src.llm.schemas import KnowledgeGraph, KnowledgeNode, MasteryMeta

logger = logging.getLogger(__name__)


# ---- thresholds (tunable; pinned by tests) ------------------------

_MASTERED_MIN = 0.70
_WEAK_MIN = 0.30


def _compute_status(retention: float) -> str:
    if retention >= _MASTERED_MIN:
        return "mastered"
    if retention >= _WEAK_MIN:
        return "weak"
    return "forgotten"


# ---- name normalization -------------------------------------------

# Keep only CJK unified ideographs and ASCII alphanumeric [a-z0-9].
# All whitespace, punctuation, symbols, and non-ASCII letters (e.g.
# Greek Δ/δ) are dropped so that "判别式（Δ）" matches "判别式".
_KEEP_RE = re.compile(r"[^一-鿿㐀-䶿a-z0-9豈-﫿]")


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).strip().lower()
    s = _KEEP_RE.sub("", s)
    return s


# ---- timestamp parsing --------------------------------------------


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    # Memory service emits "YYYY-MM-DDTHH:MM:SSZ"; tolerate "+00:00" too.
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1] + "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ---- main ----------------------------------------------------------


def annotate_graph_with_mastery(
    graph: KnowledgeGraph,
    *,
    mastery_rows: list[dict[str, Any]],
    model: ForgettingModel,
    now: datetime,
) -> KnowledgeGraph:
    """Return a new KnowledgeGraph with mastery attached to matching nodes."""
    by_exact: dict[str, dict[str, Any]] = {}
    by_norm: dict[str, dict[str, Any]] = {}
    for row in mastery_rows:
        sub = row.get("subtopic")
        if not isinstance(sub, str) or not sub:
            continue
        by_exact.setdefault(sub, row)
        by_norm.setdefault(_normalize_name(sub), row)

    new_nodes: list[KnowledgeNode] = []
    for node in graph.nodes:
        row = by_exact.get(node.name) or by_norm.get(_normalize_name(node.name))
        meta: MasteryMeta | None = None
        if row is not None:
            meta = _row_to_meta(row, model=model, now=now)
        new_nodes.append(node.model_copy(update={"mastery": meta}))

    return graph.model_copy(update={"nodes": new_nodes})


def _row_to_meta(
    row: dict[str, Any], *, model: ForgettingModel, now: datetime
) -> MasteryMeta | None:
    last_seen_str = row.get("last_seen_at")
    last_seen = _parse_ts(last_seen_str) if isinstance(last_seen_str, str) else None
    if last_seen is None:
        logger.debug("dropping mastery row with bad last_seen_at: %r", row)
        return None
    try:
        session_count = int(row.get("session_count") or 0)
    except (TypeError, ValueError):
        logger.warning("dropping mastery row with bad session_count: %r", row)
        return None
    last_accuracy = row.get("last_accuracy")
    if last_accuracy is not None:
        try:
            last_accuracy = float(last_accuracy)
        except (TypeError, ValueError):
            logger.warning(
                "non-numeric last_accuracy in mastery row, treating as unseen: %r",
                row,
            )
            last_accuracy = None

    retention = model.retention(
        last_seen=last_seen,
        last_accuracy=last_accuracy,
        session_count=session_count,
        now=now,
    )
    status = "unseen" if last_accuracy is None else _compute_status(retention)
    return MasteryMeta(
        last_accuracy=last_accuracy,
        last_seen_at=last_seen_str,
        session_count=session_count,
        retention=retention,
        status=status,
        forgetting_model=model.name,
    )
