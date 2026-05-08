"""Annotate a KnowledgeGraph with per-node mastery metadata.

Three-stage match between KG nodes and memory mastery rows:
1. exact name match (case- and whitespace-sensitive)
2. normalized name match — strip whitespace, lowercase ASCII, drop
   common punctuation. Catches LLM drift like "判别式 (Δ)" vs "判别式".
3. prefix-before-colon match — recovers legacy memory rows where the
   subtopic was stored as "节点名: 完整描述" because an older quiz LLM
   appended the description into QuizQuestion.knowledge_point. Modern
   quiz_node pins this field to the KG node name, but old data stays.

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
#
# 4-bucket scheme: any retention above _MASTERED_MIN counts as mastered
# (loose by design — see brainstorming notes; users need to see green to
# feel progress). Below _WEAK_MIN, the material is effectively forgotten.

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


def _split_prefix(name: str) -> str | None:
    """Return the part of `name` before the first ':' / '：' separator.

    Used to recover legacy memory rows where the subtopic was stored as
    "node: long description" — match the prefix to today's KG node.
    Returns None if no separator is present (avoids matching everything).
    """
    if not isinstance(name, str):
        return None
    for sep in (": ", "：", ":"):
        if sep in name:
            head = name.split(sep, 1)[0].strip()
            return head or None
    return None


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
    by_prefix_norm: dict[str, dict[str, Any]] = {}
    for row in mastery_rows:
        sub = row.get("subtopic")
        if not isinstance(sub, str) or not sub:
            continue
        by_exact.setdefault(sub, row)
        by_norm.setdefault(_normalize_name(sub), row)
        prefix = _split_prefix(sub)
        if prefix:
            by_prefix_norm.setdefault(_normalize_name(prefix), row)

    new_nodes: list[KnowledgeNode] = []
    for node in graph.nodes:
        node_norm = _normalize_name(node.name)
        row = (
            by_exact.get(node.name)
            or by_norm.get(node_norm)
            or by_prefix_norm.get(node_norm)
        )
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
    # Prefer the time-weighted accuracy when memory provides it.
    # Falls back to last_accuracy (most-recent-session) for older payloads
    # or when only one session exists. The forgetting model still applies
    # exp(-t/S) on top: weighted_accuracy answers "how well did the
    # student know it across sessions", retention curve answers "how
    # much has decayed since the last touch".
    accuracy_val = row.get("weighted_accuracy")
    if accuracy_val is None:
        accuracy_val = row.get("last_accuracy")
    if accuracy_val is not None:
        try:
            accuracy_val = float(accuracy_val)
        except (TypeError, ValueError):
            logger.warning(
                "non-numeric accuracy in mastery row, treating as unseen: %r",
                row,
            )
            accuracy_val = None

    # Keep last_accuracy on MasteryMeta for UI/debug visibility, separate
    # from what we feed the forgetting model.
    last_accuracy_for_meta = row.get("last_accuracy")
    if last_accuracy_for_meta is not None:
        try:
            last_accuracy_for_meta = float(last_accuracy_for_meta)
        except (TypeError, ValueError):
            last_accuracy_for_meta = None

    retention = model.retention(
        last_seen=last_seen,
        last_accuracy=accuracy_val,
        session_count=session_count,
        now=now,
    )
    status = "unseen" if accuracy_val is None else _compute_status(retention)
    return MasteryMeta(
        last_accuracy=last_accuracy_for_meta,
        last_seen_at=last_seen_str,
        session_count=session_count,
        retention=retention,
        status=status,
        forgetting_model=model.name,
    )
