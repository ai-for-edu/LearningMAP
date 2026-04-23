from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path

from src.llm.schemas import KCSkill
from run.build_kc_skills import (
    atomic_write_json,
    build_leaf_to_ids,
    generate_kc_skill,
    group_questions_by_route,
    load_manifest,
    lookup_kc_ids_for_route,
    retry_with_backoff,
    run_build,
    sample_questions,
    save_manifest,
)


def _q(qid: str, kc_routes: list[str], qtype: str = "填空") -> tuple[str, dict]:
    return qid, {
        "content": f"content-{qid}",
        "options": {},
        "answer": [f"a-{qid}"],
        "analysis": f"ana-{qid}",
        "type": qtype,
        "kc_routes": kc_routes,
    }


def test_group_questions_by_route_flattens_multi_route_questions():
    route_a = "root----A"
    route_b = "root----B"
    questions = dict(
        [
            _q("0", [route_a]),
            _q("1", [route_a, route_b]),
            _q("2", [route_b]),
        ]
    )
    groups = group_questions_by_route(questions)
    assert set(groups.keys()) == {route_a, route_b}
    assert [s["id"] for s in groups[route_a]] == ["0", "1"]
    assert [s["id"] for s in groups[route_b]] == ["1", "2"]
    # each sample carries its id so downstream tooling can record it
    assert groups[route_a][0]["content"] == "content-0"


def test_sample_questions_returns_all_when_under_cap():
    items = [{"id": str(i), "type": "填空"} for i in range(5)]
    result = sample_questions(items, cap=12)
    assert [s["id"] for s in result] == ["0", "1", "2", "3", "4"]


def test_sample_questions_stratifies_by_type_when_over_cap():
    # 10 填空 + 5 单选, cap=6 → ratio 2:1 → 4 填空 + 2 单选
    items = [{"id": f"fill-{i}", "type": "填空"} for i in range(10)] + [
        {"id": f"choice-{i}", "type": "单选"} for i in range(5)
    ]
    result = sample_questions(items, cap=6)
    assert len(result) == 6
    types = [s["type"] for s in result]
    assert types.count("填空") == 4
    assert types.count("单选") == 2


def test_sample_questions_is_deterministic():
    items = [{"id": str(i), "type": "填空"} for i in range(30)]
    a = sample_questions(items, cap=12)
    b = sample_questions(items, cap=12)
    assert [s["id"] for s in a] == [s["id"] for s in b]


def test_sample_questions_uses_uniform_step_within_group():
    # 20 items of one type, cap=4 → step = 20//4 = 5 → ids 0,5,10,15
    items = [{"id": str(i), "type": "填空"} for i in range(20)]
    result = sample_questions(items, cap=4)
    assert [s["id"] for s in result] == ["0", "5", "10", "15"]


def test_sample_questions_distributes_remainder_to_largest_group():
    # 7 填空 + 4 单选 = 11, cap=5 → floor 5*7/11 = 3, floor 5*4/11 = 1, remainder 1 → to 填空 (larger)
    items = [{"id": f"fill-{i}", "type": "填空"} for i in range(7)] + [
        {"id": f"choice-{i}", "type": "单选"} for i in range(4)
    ]
    result = sample_questions(items, cap=5)
    types = [s["type"] for s in result]
    assert types.count("填空") == 4
    assert types.count("单选") == 1


def test_atomic_write_json_creates_file_with_utf8(tmp_path: Path):
    target = tmp_path / "out.json"
    data = {"路径": {"skill": {"考察重点": "测试中文"}}}
    atomic_write_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data
    # no leftover .tmp file
    assert not (tmp_path / "out.json.tmp").exists()


def test_atomic_write_json_preserves_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"before": 1})

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"after": 2})

    # main file unchanged
    assert json.loads(target.read_text(encoding="utf-8")) == {"before": 1}


def test_load_manifest_returns_empty_when_missing(tmp_path: Path):
    m = load_manifest(tmp_path / "nope.json")
    assert m == {"version": 1, "completed_routes": [], "failed": []}


def test_manifest_roundtrip(tmp_path: Path):
    path = tmp_path / "m.json"
    m = {
        "version": 1,
        "completed_routes": ["r1", "r2"],
        "failed": [{"route": "r3", "error": "boom", "attempts": 3}],
        "model": "deepseek-chat",
    }
    save_manifest(path, m)
    assert load_manifest(path) == m


class _FakeCompletions:
    def __init__(self, result: KCSkill):
        self.result = result
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _FakeChat:
    def __init__(self, result: KCSkill):
        self.completions = _FakeCompletions(result)


class _FakeClient:
    def __init__(self, result: KCSkill):
        self.chat = _FakeChat(result)


def test_generate_kc_skill_renders_prompt_and_uses_schema():
    expected = KCSkill(
        description="when multi-step counting appears, distinguish 分步 from 分类",
        考察重点="乘法原理核心概念",
        考察形式="填空为主",
        易错点="分步分类混淆",
        难度提示="2-4 步",
    )
    client = _FakeClient(expected)
    samples = [
        {
            "id": "0",
            "type": "填空",
            "content": "c0",
            "options": {},
            "answer": "a0",
            "analysis": "x",
        },
    ]
    result = generate_kc_skill(
        route="root----A",
        samples=samples,
        total_count=42,
        client=client,
        model="test-model",
    )
    assert result == expected
    call = client.chat.completions.create.__self__.calls[0]  # type: ignore[attr-defined]
    assert call["model"] == "test-model"
    assert call["response_model"] is KCSkill
    assert "root----A" in call["messages"][0]["content"]
    assert "题目 1" in call["messages"][0]["content"]


def test_build_leaf_to_ids_inverts_map():
    kc_map = {"0": "乘法原理里的其他类型", "1": "加乘原理综合"}
    result = build_leaf_to_ids(kc_map)
    assert result == {"乘法原理里的其他类型": [0], "加乘原理综合": [1]}


def test_lookup_kc_ids_returns_empty_for_unknown_leaf():
    leaf_to_ids = {"乘法原理里的其他类型": [0]}
    route = "拓展思维----计数模块----加乘原理----乘法原理----未登记的 leaf"
    assert lookup_kc_ids_for_route(route, leaf_to_ids) == []


def test_lookup_kc_ids_returns_match_for_known_leaf():
    leaf_to_ids = {"乘法原理里的其他类型": [0]}
    route = "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型"
    assert lookup_kc_ids_for_route(route, leaf_to_ids) == [0]


def test_retry_with_backoff_succeeds_first_try():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        return "ok"

    result = asyncio.run(retry_with_backoff(fn, attempts=3, base_delay=0))
    assert result == "ok"
    assert calls["n"] == 1


def test_retry_with_backoff_retries_then_succeeds():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = asyncio.run(retry_with_backoff(fn, attempts=3, base_delay=0))
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_with_backoff_raises_after_exhaustion():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        asyncio.run(retry_with_backoff(fn, attempts=3, base_delay=0))
    assert calls["n"] == 3


def test_run_build_processes_all_routes_and_writes_outputs(tmp_path, monkeypatch):
    questions = {
        "0": {
            "content": "c0",
            "options": {},
            "answer": ["a0"],
            "analysis": "x",
            "type": "填空",
            "kc_routes": ["root----A"],
        },
        "1": {
            "content": "c1",
            "options": {},
            "answer": ["a1"],
            "analysis": "y",
            "type": "单选",
            "kc_routes": ["root----B"],
        },
    }
    kc_map = {"0": "A", "1": "B"}

    questions_path = tmp_path / "questions.json"
    kc_map_path = tmp_path / "kc_routes_map.json"
    out_path = tmp_path / "kc_skills.json"
    manifest_path = tmp_path / "kc_skills.manifest.json"
    questions_path.write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")
    kc_map_path.write_text(json.dumps(kc_map, ensure_ascii=False), encoding="utf-8")

    fake_skill = KCSkill(
        description="d1", 考察重点="r1", 考察形式="r2", 易错点="r3", 难度提示="r4",
    )
    client = _FakeClient(fake_skill)

    asyncio.run(run_build(
        questions_path=questions_path,
        kc_routes_map_path=kc_map_path,
        out_path=out_path,
        manifest_path=manifest_path,
        client=client,
        model="fake-model",
        concurrency=2,
        limit=None,
        only_module=None,
        resume=False,
    ))

    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert set(out.keys()) == {"root----A", "root----B"}
    entry_a = out["root----A"]
    assert entry_a["route"] == "root----A"
    assert entry_a["leaf"] == "A"
    assert entry_a["kc_ids"] == [0]
    assert entry_a["question_count"] == 1
    assert entry_a["sampled_count"] == 1
    assert entry_a["sampled_question_ids"] == ["0"]
    assert entry_a["skill"] == fake_skill.model_dump()
    assert entry_a["source_model"] == "fake-model"
    assert "generated_at" in entry_a

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest["completed_routes"]) == {"root----A", "root----B"}
    assert manifest["failed"] == []


def test_run_build_records_failures_and_continues(tmp_path):
    questions = {
        "0": {"content": "c0", "options": {}, "answer": ["a0"], "analysis": "x", "type": "填空", "kc_routes": ["root----A"]},
        "1": {"content": "c1", "options": {}, "answer": ["a1"], "analysis": "y", "type": "填空", "kc_routes": ["root----B"]},
    }
    questions_path = tmp_path / "questions.json"
    kc_map_path = tmp_path / "kc_routes_map.json"
    out_path = tmp_path / "out.json"
    manifest_path = tmp_path / "m.json"
    questions_path.write_text(json.dumps(questions), encoding="utf-8")
    kc_map_path.write_text(json.dumps({}), encoding="utf-8")

    class _FailingCompletions:
        def create(self, **_):
            raise RuntimeError("always fails")

    class _FailingForOneRoute:
        """Fail only on route A, succeed on route B."""
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            prompt = kwargs["messages"][0]["content"]
            if "root----A" in prompt:
                raise RuntimeError("fail A")
            return KCSkill(description="ok", 考察重点="ok", 考察形式="ok", 易错点="ok", 难度提示="ok")

    client = _FailingForOneRoute()

    asyncio.run(run_build(
        questions_path=questions_path,
        kc_routes_map_path=kc_map_path,
        out_path=out_path,
        manifest_path=manifest_path,
        client=client,
        model="m",
        concurrency=2,
        limit=None,
        only_module=None,
        resume=False,
        retry_base_delay=0,
    ))

    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert list(out.keys()) == ["root----B"]  # A failed

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["completed_routes"] == ["root----B"]
    failed_routes = [f["route"] for f in manifest["failed"]]
    assert failed_routes == ["root----A"]
    assert manifest["failed"][0]["attempts"] == 3


def test_run_build_resume_skips_completed_routes(tmp_path):
    questions = {
        "0": {"content": "c0", "options": {}, "answer": ["a0"], "analysis": "x", "type": "填空", "kc_routes": ["root----A"]},
    }
    questions_path = tmp_path / "questions.json"
    kc_map_path = tmp_path / "kc_routes_map.json"
    out_path = tmp_path / "out.json"
    manifest_path = tmp_path / "m.json"
    questions_path.write_text(json.dumps(questions), encoding="utf-8")
    kc_map_path.write_text(json.dumps({}), encoding="utf-8")
    # pre-seed manifest + output
    out_path.write_text(json.dumps({"root----A": {"skill": {"pre": "existing"}}}), encoding="utf-8")
    manifest_path.write_text(json.dumps({
        "version": 1,
        "completed_routes": ["root----A"],
        "failed": [],
    }), encoding="utf-8")

    class _ShouldNotCall:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **_):
            raise AssertionError("LLM must not be called for a completed route")

    asyncio.run(run_build(
        questions_path=questions_path,
        kc_routes_map_path=kc_map_path,
        out_path=out_path,
        manifest_path=manifest_path,
        client=_ShouldNotCall(),
        model="m",
        concurrency=1,
        limit=None,
        only_module=None,
        resume=True,
    ))

    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert out == {"root----A": {"skill": {"pre": "existing"}}}
