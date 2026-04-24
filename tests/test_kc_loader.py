from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.llm.schemas import KCSkill
from src.skills import kc_loader


@pytest.fixture(autouse=True)
def reset_kc_loader_state():
    """Reset module-level caches before each test."""
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None


def _sample_json(tmp_path: Path) -> Path:
    data = {
        "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
            "leaf": "乘法原理里的其他类型",
            "skill": {
                "description": "desc A",
                "考察重点": "重点 A",
                "考察形式": "形式 A",
                "易错点": "易错 A",
                "难度提示": "难度 A",
            },
        },
        "拓展思维----计数模块----加乘原理----乘法原理": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "desc B",
                "考察重点": "重点 B",
                "考察形式": "形式 B",
                "易错点": "易错 B",
                "难度提示": "难度 B",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_kc_skills_parses_every_entry(tmp_path: Path):
    path = _sample_json(tmp_path)
    skills = kc_loader.load_kc_skills(path)
    assert len(skills) == 2
    short_route = "拓展思维----计数模块----加乘原理----乘法原理"
    assert short_route in skills
    assert isinstance(skills[short_route], KCSkill)
    assert skills[short_route].考察重点 == "重点 B"


def test_load_missing_file_returns_empty_no_raise(tmp_path: Path):
    skills = kc_loader.load_kc_skills(tmp_path / "does-not-exist.json")
    assert skills == {}


def test_load_invalid_json_returns_empty(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json {", encoding="utf-8")
    skills = kc_loader.load_kc_skills(bad)
    assert skills == {}


def test_leaf_index_is_inverted_and_normalized(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    index = kc_loader.leaf_index()
    # leaf names are normalized (lowercased + punctuation/whitespace stripped)
    assert kc_loader.normalize("乘法原理") in index
    assert kc_loader.normalize("乘法原理里的其他类型") in index


def test_resolve_by_leaf_exact_match(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    hit = kc_loader.resolve_by_leaf("乘法原理里的其他类型")
    assert hit is not None
    assert hit.考察重点 == "重点 A"


def test_resolve_by_leaf_containment(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    # figure-node name is shorter and contained in leaf name → leaf side wins
    hit = kc_loader.resolve_by_leaf("乘法原理")
    assert hit is not None
    assert hit.考察重点 == "重点 B"  # exact match to 乘法原理 leaf, deeper route wins


def test_resolve_by_leaf_prefers_longer_route_on_containment(tmp_path: Path):
    # Both leaves contain "乘法原理"; longer route (more segments) should win
    data = {
        "根----乘法原理": {
            "route": "根----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "d", "考察重点": "短 route",
                "考察形式": "f", "易错点": "e", "难度提示": "l",
            },
        },
        "根----分支----乘法原理": {
            "route": "根----分支----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "d", "考察重点": "长 route",
                "考察形式": "f", "易错点": "e", "难度提示": "l",
            },
        },
    }
    p = tmp_path / "dup.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    hit = kc_loader.resolve_by_leaf("乘法原理")
    assert hit is not None
    assert hit.考察重点 == "长 route"


def test_resolve_by_leaf_no_match_returns_none(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    assert kc_loader.resolve_by_leaf("完全无关的知识点") is None


def test_normalize_strips_whitespace_and_punctuation():
    assert kc_loader.normalize("  乘法 原理 ") == kc_loader.normalize("乘法原理")
    assert kc_loader.normalize("ABC") == "abc"
    assert kc_loader.normalize("A,B.C!") == "abc"
