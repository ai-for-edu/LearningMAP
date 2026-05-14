from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.llm.schemas import KCSkill
from src.skills import kc_loader


@pytest.fixture(autouse=True)
def reset_kc_loader_state():
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}


def _sample_json(tmp_path: Path) -> Path:
    data = {
        "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
            "leaf": "乘法原理里的其他类型",
            "skill": {
                "description": "desc A",
                "考察重点": "focus A",
                "考察形式": "format A",
                "易错点": "mistake A",
                "难度提示": "difficulty A",
            },
        },
        "拓展思维----计数模块----加乘原理----乘法原理": {
            "route": "拓展思维----计数模块----加乘原理----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "desc B",
                "考察重点": "focus B",
                "考察形式": "format B",
                "易错点": "mistake B",
                "难度提示": "difficulty B",
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
    assert skills[short_route].考察重点 == "focus B"


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
    assert hit.考察重点 == "focus A"


def test_resolve_by_leaf_containment(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    # figure-node name is shorter and contained in leaf name → leaf side wins
    hit = kc_loader.resolve_by_leaf("乘法原理")
    assert hit is not None
    assert hit.考察重点 == "focus B"  # exact match to 乘法原理 leaf, deeper route wins


def test_resolve_by_leaf_prefers_longer_route_on_containment(tmp_path: Path):
    # Both leaves contain "乘法原理"; longer route (more segments) should win
    data = {
        "根----乘法原理": {
            "route": "根----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "d",
                "考察重点": "short route",
                "考察形式": "f",
                "易错点": "e",
                "难度提示": "l",
            },
        },
        "根----分支----乘法原理": {
            "route": "根----分支----乘法原理",
            "leaf": "乘法原理",
            "skill": {
                "description": "d",
                "考察重点": "long route",
                "考察形式": "f",
                "易错点": "e",
                "难度提示": "l",
            },
        },
    }
    p = tmp_path / "dup.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    hit = kc_loader.resolve_by_leaf("乘法原理")
    assert hit is not None
    assert hit.考察重点 == "long route"


def test_resolve_by_leaf_no_match_returns_none(tmp_path: Path):
    path = _sample_json(tmp_path)
    kc_loader.load_kc_skills(path)
    assert kc_loader.resolve_by_leaf("completely unrelated knowledge point") is None


def test_normalize_strips_whitespace_and_punctuation():
    assert kc_loader.normalize("  乘法 原理 ") == kc_loader.normalize("乘法原理")
    assert kc_loader.normalize("ABC") == "abc"
    assert kc_loader.normalize("A,B.C!") == "abc"


def test_flat_caches_are_full_bank(tmp_path):
    """allowed_roots must NOT filter the flat caches."""
    sample = {
        "知识点----A": {
            "route": "知识点----A",
            "leaf": "A",
            "skill": {
                "description": "d",
                "考察重点": "focus",
                "考察形式": "format",
                "易错点": "mistake",
                "难度提示": "difficulty",
            },
        },
        "课内题型----B": {
            "route": "课内题型----B",
            "leaf": "B",
            "skill": {
                "description": "d",
                "考察重点": "focus",
                "考察形式": "format",
                "易错点": "mistake",
                "难度提示": "difficulty",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    # Even though 课内题型 is excluded from the default tree, flat caches contain everything.
    assert "知识点----A" in kc_loader.get_kc_skills()
    assert "课内题型----B" in kc_loader.get_kc_skills()
    assert kc_loader.normalize("A") in kc_loader.leaf_index()
    assert kc_loader.normalize("B") in kc_loader.leaf_index()


def test_default_tree_built_for_allowed_roots(tmp_path):
    sample = {
        "知识点----A----X": {
            "route": "知识点----A----X",
            "leaf": "X",
            "skill": {
                "description": "dx",
                "考察重点": "focus",
                "考察形式": "format",
                "易错点": "mistake",
                "难度提示": "difficulty",
            },
        },
        "课内题型----B": {
            "route": "课内题型----B",
            "leaf": "B",
            "skill": {
                "description": "db",
                "考察重点": "focus",
                "考察形式": "format",
                "易错点": "mistake",
                "难度提示": "difficulty",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    tree = kc_loader._get_tree()  # default allowed_roots
    # 课内题型 root excluded
    assert "课内题型----B" not in tree.parent_of
    # 知识点 leaf and synthetic prefix present
    assert "知识点----A----X" in tree.parent_of
    assert "知识点----A" in tree.parent_of  # synthetic
    assert "知识点" in tree.parent_of  # root
    assert tree.parent_of["知识点"] is None
    assert tree.parent_of["知识点----A"] == "知识点"
    assert tree.parent_of["知识点----A----X"] == "知识点----A"


def test_synthetic_prefix_node_desc_falls_back_to_leaf_segment(tmp_path):
    sample = {
        "知识点----A----X": {
            "route": "知识点----A----X",
            "leaf": "X",
            "skill": {
                "description": "dx",
                "考察重点": "focus",
                "考察形式": "format",
                "易错点": "mistake",
                "难度提示": "difficulty",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    tree = kc_loader._get_tree()
    assert tree.node_desc["知识点----A----X"] == "dx"  # registered
    assert tree.node_desc["知识点----A"] == "A"  # synthetic, last segment
    assert tree.node_desc["知识点"] == "知识点"  # synthetic root


def test_tree_cache_keyed_by_allowed_roots(tmp_path):
    sample = {
        "知识点----A": {
            "route": "知识点----A",
            "leaf": "A",
            "skill": {
                "description": "d",
                "考察重点": "focus",
                "考察形式": "format",
                "易错点": "mistake",
                "难度提示": "difficulty",
            },
        },
        "拓展思维----C": {
            "route": "拓展思维----C",
            "leaf": "C",
            "skill": {
                "description": "d",
                "考察重点": "focus",
                "考察形式": "format",
                "易错点": "mistake",
                "难度提示": "difficulty",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    only_kp = kc_loader._get_tree(frozenset({"知识点"}))
    default = kc_loader._get_tree()
    assert "拓展思维----C" not in only_kp.parent_of
    assert "拓展思维----C" in default.parent_of
    # Same scope returns same instance (cached)
    again = kc_loader._get_tree(frozenset({"知识点"}))
    assert again is only_kp


def test_parent_child_invariants(tmp_path):
    sample = {
        "知识点----A----X": {
            "route": "知识点----A----X",
            "leaf": "X",
            "skill": {
                "description": "dx",
                "考察重点": "focus-x",
                "考察形式": "format-x",
                "易错点": "mistake-x",
                "难度提示": "difficulty-x",
            },
        },
        "知识点----A----Y": {
            "route": "知识点----A----Y",
            "leaf": "Y",
            "skill": {
                "description": "dy",
                "考察重点": "focus-y",
                "考察形式": "format-y",
                "易错点": "mistake-y",
                "难度提示": "difficulty-y",
            },
        },
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    tree = kc_loader._get_tree()
    # children_of[知识点----A] contains both X and Y
    assert set(tree.children_of["知识点----A"]) == {
        "知识点----A----X",
        "知识点----A----Y",
    }
    # Each child has 知识点----A as parent
    assert tree.parent_of["知识点----A----X"] == "知识点----A"
    assert tree.parent_of["知识点----A----Y"] == "知识点----A"
