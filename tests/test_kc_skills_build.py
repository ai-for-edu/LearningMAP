from __future__ import annotations

import json
import pytest
from pathlib import Path

from src.llm.schemas import KCSkill
from run.build_kc_skills import (
    atomic_write_json,
    generate_kc_skill,
    group_questions_by_route,
    load_manifest,
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
