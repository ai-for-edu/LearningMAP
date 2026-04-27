from __future__ import annotations

import json

import pytest

from src.skills import kc_loader
from src.skills.kc_graph import _rule_match_route


@pytest.fixture(autouse=True)
def reset_kc_state(tmp_path):
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    sample = {
        # Two routes share segment "乘法原理": one as terminal, one as ancestor of deeper descendant.
        "拓展思维----计数模块----加乘原理----乘法原理": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "乘法原理 desc",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
            "leaf": "乘法原理里的其他类型",
            "skill": {
                "description": "其他类型 desc",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        # One-char segment to test Tier 2 guard
        "知识点----几何----线": {
            "route": "知识点----几何----线",
            "leaf": "线",
            "skill": {
                "description": "线 desc",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        # Containment match candidate
        "拓展思维----组合----抽屉原理": {
            "route": "拓展思维----组合----抽屉原理",
            "leaf": "抽屉原理",
            "skill": {
                "description": "抽屉 desc",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}


def test_rule_match_returns_prefix_not_descendant():
    """topic exactly equals an intermediate segment → return prefix, not deeper descendant."""
    route = _rule_match_route("乘法原理")
    assert route == "拓展思维----计数模块----加乘原理----乘法原理"


def test_rule_match_containment():
    route = _rule_match_route("抽屉原理")
    assert route == "拓展思维----组合----抽屉原理"


def test_rule_match_short_segment_exact_only():
    # topic="线性方程" must NOT match the geometry segment "线"
    assert _rule_match_route("线性方程") is None
    # but exact "线" still matches
    assert _rule_match_route("线") == "知识点----几何----线"


def test_rule_match_returns_none_on_unrelated():
    assert _rule_match_route("完全不相关的词") is None


def test_rule_match_empty_topic():
    assert _rule_match_route("") is None
