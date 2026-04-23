"""Offline batch generator for XES3G5M KC skills.

See docs/superpowers/specs/2026-04-23-xes3g5m-kc-skills-design.md for design.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from src.llm.schemas import KCSkill
from src.prompts.loader import render_prompt

logger = logging.getLogger(__name__)
T = TypeVar("T")


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


def generate_kc_skill(
    *,
    route: str,
    samples: list[dict],
    total_count: int,
    client,
    model: str,
) -> KCSkill:
    """Render the prompt and call the LLM with Instructor response_model=KCSkill.

    `client` must be an instructor-wrapped client (or fake with the same
    .chat.completions.create(..., response_model=...) contract).
    """
    prompt = render_prompt(
        "kc_skill_from_bank",
        route=route,
        samples=samples,
        sample_count=len(samples),
        total_count=total_count,
    )
    return client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        response_model=KCSkill,
    )


def build_leaf_to_ids(kc_routes_map: dict[str, str]) -> dict[str, list[int]]:
    """Invert kc_routes_map.json (id -> leaf_name) to leaf_name -> [ids].

    Values in the source map are globally unique leaf names in the XES3G5M
    dataset, so lists usually have length 1. Built as list to be safe.
    """
    result: dict[str, list[int]] = {}
    for k, v in kc_routes_map.items():
        result.setdefault(v, []).append(int(k))
    return result


def lookup_kc_ids_for_route(route: str, leaf_to_ids: dict[str, list[int]]) -> list[int]:
    """Return the KC ids for the leaf of `route`, or [] if unknown."""
    leaf = route.rsplit("----", 1)[-1]
    return list(leaf_to_ids.get(leaf, []))


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
) -> T:
    """Call `fn()` up to `attempts` times with exponential backoff (2s/4s/8s)."""
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 — propagate after exhaustion
            last_exc = exc
            if i == attempts - 1:
                break
            delay = base_delay * (2**i)
            logger.warning("attempt %d/%d failed: %s — retrying in %.1fs", i + 1, attempts, exc, delay)
            if delay > 0:
                await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _process_one_route(
    *,
    route: str,
    all_route_items: list[dict],
    leaf_to_ids: dict[str, list[int]],
    client,
    model: str,
    cap: int,
    retry_base_delay: float,
) -> dict:
    """Run sampling + LLM for a single route. Returns the output entry on
    success; raises on final failure (caller records the failure)."""
    samples = sample_questions(all_route_items, cap=cap)

    async def _call():
        return await asyncio.to_thread(
            generate_kc_skill,
            route=route,
            samples=samples,
            total_count=len(all_route_items),
            client=client,
            model=model,
        )

    skill = await retry_with_backoff(_call, attempts=3, base_delay=retry_base_delay)
    leaf = route.rsplit("----", 1)[-1]
    return {
        "route": route,
        "leaf": leaf,
        "kc_ids": lookup_kc_ids_for_route(route, leaf_to_ids),
        "question_count": len(all_route_items),
        "sampled_count": len(samples),
        "sampled_question_ids": [s["id"] for s in samples],
        "skill": skill.model_dump(),
        "source_model": model,
        "generated_at": _now_iso(),
    }


async def run_build(
    *,
    questions_path: Path,
    kc_routes_map_path: Path,
    out_path: Path,
    manifest_path: Path,
    client,
    model: str,
    concurrency: int = 8,
    cap: int = 12,
    limit: int | None = None,
    only_module: str | None = None,
    resume: bool = True,
    retry_base_delay: float = 2.0,
) -> None:
    """Main orchestration: load data → fan out routes under a semaphore →
    write each successful result to `out_path` atomically; record failures
    in `manifest_path`."""
    questions = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    kc_routes_map = json.loads(Path(kc_routes_map_path).read_text(encoding="utf-8"))
    leaf_to_ids = build_leaf_to_ids(kc_routes_map)
    groups = group_questions_by_route(questions)

    if only_module:
        groups = {
            r: items for r, items in groups.items()
            if len(r.split("----")) >= 2 and r.split("----")[1] == only_module
        }

    manifest = load_manifest(manifest_path) if resume else {
        "version": 1, "completed_routes": [], "failed": [],
    }
    manifest.setdefault("completed_routes", [])
    manifest.setdefault("failed", [])
    manifest["model"] = model
    manifest.setdefault("started_at", _now_iso())

    completed_set = set(manifest["completed_routes"])
    todo_routes = [r for r in groups.keys() if r not in completed_set]
    if limit is not None:
        todo_routes = todo_routes[:limit]

    output = json.loads(Path(out_path).read_text(encoding="utf-8")) if Path(out_path).exists() else {}
    output_lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)
    total = len(todo_routes)

    async def _worker(idx: int, route: str) -> None:
        async with sem:
            try:
                entry = await _process_one_route(
                    route=route,
                    all_route_items=groups[route],
                    leaf_to_ids=leaf_to_ids,
                    client=client,
                    model=model,
                    cap=cap,
                    retry_base_delay=retry_base_delay,
                )
            except Exception as exc:  # noqa: BLE001
                async with output_lock:
                    manifest["failed"].append({
                        "route": route,
                        "error": str(exc),
                        "attempts": 3,
                        "last_at": _now_iso(),
                    })
                    save_manifest(manifest_path, manifest)
                print(f"[{idx}/{total}] {route} ✗ {exc}", file=sys.stderr)
                return
            async with output_lock:
                output[route] = entry
                manifest["completed_routes"].append(route)
                atomic_write_json(out_path, output)
                save_manifest(manifest_path, manifest)
            print(f"[{idx}/{total}] {route} ✓", file=sys.stderr)

    await asyncio.gather(*[_worker(i + 1, r) for i, r in enumerate(todo_routes)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline KC-skill generator for XES3G5M")
    parser.add_argument("--backend", choices=["classba", "openai"], default="classba",
                        help="LLM backend to use (default: classba)")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="only process the first N routes")
    parser.add_argument("--only-module", type=str, default=None, help="filter by route's 2nd segment, e.g. 计数模块")
    parser.add_argument("--no-resume", action="store_true", help="overwrite manifest instead of resuming")
    parser.add_argument("--questions", type=Path, default=Path("XES3G5M/metadata/questions.json"))
    parser.add_argument("--kc-map", type=Path, default=Path("XES3G5M/metadata/kc_routes_map.json"))
    parser.add_argument("--out", type=Path, default=Path("XES3G5M/metadata/kc_skills.json"))
    parser.add_argument("--manifest", type=Path, default=Path("XES3G5M/metadata/kc_skills.manifest.json"))
    args = parser.parse_args(argv)

    if args.backend == "classba":
        from run.classba_client import ClassbaClient

        client = ClassbaClient.from_env()
        model = client.agent_name
    else:
        from src.llm.client import get_llm_client, get_model_name

        client = get_llm_client()
        model = get_model_name()

    asyncio.run(run_build(
        questions_path=args.questions,
        kc_routes_map_path=args.kc_map,
        out_path=args.out,
        manifest_path=args.manifest,
        client=client,
        model=model,
        concurrency=args.concurrency,
        limit=args.limit,
        only_module=args.only_module,
        resume=not args.no_resume,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
