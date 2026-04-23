from __future__ import annotations

from run.build_kc_skills import (
    group_questions_by_route,
    sample_questions,
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
    questions = dict([
        _q("0", [route_a]),
        _q("1", [route_a, route_b]),
        _q("2", [route_b]),
    ])
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
    items = (
        [{"id": f"fill-{i}", "type": "填空"} for i in range(10)]
        + [{"id": f"choice-{i}", "type": "单选"} for i in range(5)]
    )
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
    items = (
        [{"id": f"fill-{i}", "type": "填空"} for i in range(7)]
        + [{"id": f"choice-{i}", "type": "单选"} for i in range(4)]
    )
    result = sample_questions(items, cap=5)
    types = [s["type"] for s in result]
    assert types.count("填空") == 4
    assert types.count("单选") == 1
