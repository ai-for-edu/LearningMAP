"""Offline batch generator for XES3G5M KC skills.

See docs/superpowers/specs/2026-04-23-xes3g5m-kc-skills-design.md for design.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any


def group_questions_by_route(questions: dict) -> dict[str, list[dict]]:
    """Invert questions dict so each KC full-route maps to its questions.

    Each output item is the original question dict enriched with an "id" key
    so downstream logging can record which source questions were sampled.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for qid, q in questions.items():
        item = dict(q)
        item["id"] = qid
        for route in q.get("kc_routes", []):
            groups[route].append(item)
    # preserve insertion order when iterated
    return dict(groups)


def sample_questions(items: list[dict], cap: int = 12) -> list[dict]:
    """Deterministic stratified sample by `type`.

    Rules:
    - if len(items) <= cap: return sorted by id (all items)
    - else: allocate cap slots across types proportionally (floor),
      distribute the remainder to the largest groups (ties broken by type
      name for determinism), then within each group take a uniform-step
      slice of the id-sorted items.
    """
    def sort_key(item: dict) -> tuple:
        """Sort by id, trying numeric sort first, falling back to string."""
        item_id = item["id"]
        try:
            return (0, int(item_id))
        except ValueError:
            return (1, item_id)

    if len(items) <= cap:
        return sorted(items, key=sort_key)

    # bucket by type, sort each bucket by id for determinism
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        buckets[item["type"]].append(item)
    for t in buckets:
        buckets[t].sort(key=sort_key)

    total = len(items)
    # floor quota per type
    quotas: dict[str, int] = {t: (cap * len(buckets[t])) // total for t in buckets}
    assigned = sum(quotas.values())
    remainder = cap - assigned
    # distribute remainder to largest buckets (tie-break by type name ascending)
    order = sorted(buckets.keys(), key=lambda t: (-len(buckets[t]), t))
    for t in order:
        if remainder == 0:
            break
        quotas[t] += 1
        remainder -= 1

    # uniform-step pick from each bucket
    picked: list[dict] = []
    for t in sorted(buckets.keys()):  # deterministic iteration
        q = quotas[t]
        if q == 0:
            continue
        bucket = buckets[t]
        step = len(bucket) // q
        picked.extend(bucket[i * step] for i in range(q))

    return sorted(picked, key=sort_key)


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to `path` atomically: dump to .tmp then os.replace.

    Guarantees that `path` is either the old contents or the new contents,
    never partially-written.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_manifest(path: Path) -> dict:
    """Load manifest or return an empty skeleton if the file is missing."""
    path = Path(path)
    if not path.exists():
        return {"version": 1, "completed_routes": [], "failed": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict) -> None:
    """Atomic write for the manifest."""
    atomic_write_json(path, manifest)
