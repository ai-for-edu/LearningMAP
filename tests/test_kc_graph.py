from __future__ import annotations

import json

import pytest

from src.skills import kc_loader
from src.skills.kc_graph import _extract_subgraph, _rule_match_route, resolve_kc_graph


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


def test_subgraph_includes_match_as_core():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    cores = [n for n in g.nodes if n.level == "core"]
    assert len(cores) == 1
    assert cores[0].id == "拓展思维----计数模块----加乘原理----乘法原理"
    assert cores[0].name == "乘法原理"


def test_subgraph_size_within_budget():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    assert 1 <= len(g.nodes) <= 10


def test_subgraph_ancestors_capped_at_2():
    g = _extract_subgraph(
        "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
        max_nodes=10,
    )
    prereqs = [n for n in g.nodes if n.level == "prerequisite"]
    assert len(prereqs) <= 2


def test_subgraph_includes_descendants_when_present():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    ext_ids = {n.id for n in g.nodes if n.level == "extension"}
    assert (
        "拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型"
        in ext_ids
    )


def test_leaf_match_falls_back_to_uncle_nodes(tmp_path):
    """Leaf with no descendants and no siblings — uncles are pulled in to reach ≥3 nodes."""
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    sample = {
        # Route is 3 segments deep so only 1 ancestor is selectable (grandparent
        # is the root and is skipped). With core+1 ancestor = 2 nodes, the
        # leaf-degradation step must pull an uncle to reach _LEAF_MIN_NODES (3).
        "知识点----A----叶子": {
            "route": "知识点----A----叶子",
            "leaf": "叶子",
            "skill": {
                "description": "d",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        "知识点----B----另一个孩子": {
            "route": "知识点----B----另一个孩子",
            "leaf": "另一个孩子",
            "skill": {
                "description": "d",
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
    g = _extract_subgraph("知识点----A----叶子", max_nodes=10)
    assert len(g.nodes) >= 3
    ext_ids = {n.id for n in g.nodes if n.level == "extension"}
    # Uncle "知识点----B" (sibling of parent 知识点----A under root 知识点) is reached.
    assert "知识点----B" in ext_ids


def test_edges_only_for_parent_child():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    node_ids = {n.id for n in g.nodes}
    tree = kc_loader._get_tree()
    for edge in g.edges:
        assert edge.source in node_ids
        assert edge.target in node_ids
        assert tree.parent_of[edge.target] == edge.source
        assert edge.relation == "prerequisite"


def test_node_id_is_route_and_name_is_last_segment():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    for n in g.nodes:
        assert n.name == n.id.split("----")[-1]


def test_node_description_uses_skill_when_registered():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    core = [n for n in g.nodes if n.level == "core"][0]
    assert core.description == "乘法原理 desc"


def test_node_description_falls_back_for_synthetic():
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    # 计数模块 is synthetic (no standalone entry in the test data)
    synthetic = [n for n in g.nodes if n.id == "拓展思维----计数模块"]
    if synthetic:
        assert synthetic[0].description == "计数模块"


def test_leaf_degradation_stops_at_leaf_min_nodes(tmp_path):
    """When r* is a leaf with many uncles available, only fill uncles until ≥ _LEAF_MIN_NODES (3)."""
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    sample = {
        "知识点----A----只有一个孩子----叶子": {
            "route": "知识点----A----只有一个孩子----叶子",
            "leaf": "叶子",
            "skill": {
                "description": "d",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        "知识点----A----分支1": {
            "route": "知识点----A----分支1",
            "leaf": "分支1",
            "skill": {
                "description": "d",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        "知识点----A----分支2": {
            "route": "知识点----A----分支2",
            "leaf": "分支2",
            "skill": {
                "description": "d",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        "知识点----A----分支3": {
            "route": "知识点----A----分支3",
            "leaf": "分支3",
            "skill": {
                "description": "d",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        },
        "知识点----A----分支4": {
            "route": "知识点----A----分支4",
            "leaf": "分支4",
            "skill": {
                "description": "d",
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
    g = _extract_subgraph("知识点----A----只有一个孩子----叶子", max_nodes=10)
    # core (1) + ancestor "只有一个孩子" (1) + at most one uncle to reach 3 nodes total.
    # NOT all 4 uncles even though max_nodes=10 leaves room.
    assert len(g.nodes) == 3


def test_subgraph_with_max_nodes_one():
    """max_nodes=1 returns just the core."""
    g = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=1)
    assert len(g.nodes) == 1
    assert g.nodes[0].level == "core"


def test_leaf_with_many_siblings_uses_full_budget(tmp_path):
    """Leaf match + many siblings: sibling reservation should NOT block the last sibling slot."""
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    kc_loader._TREE_CACHE = {}
    sample = {
        f"知识点----A----叶{i}": {
            "route": f"知识点----A----叶{i}",
            "leaf": f"叶{i}",
            "skill": {
                "description": "d",
                "考察重点": "x",
                "考察形式": "x",
                "易错点": "x",
                "难度提示": "x",
            },
        }
        for i in range(15)
    }
    p = tmp_path / "kc_skills.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    kc_loader.load_kc_skills(p)
    g = _extract_subgraph("知识点----A----叶0", max_nodes=10)
    # Core (1) + ancestor "A" (1) + 8 siblings = 10. No reserved descendant slot since r* is a leaf.
    assert len(g.nodes) == 10


def test_edges_order_is_deterministic():
    """Edge order must be stable across runs for snapshot/serialization stability."""
    g1 = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    g2 = _extract_subgraph("拓展思维----计数模块----加乘原理----乘法原理", max_nodes=10)
    assert [(e.source, e.target) for e in g1.edges] == [
        (e.source, e.target) for e in g2.edges
    ]


def test_resolve_returns_graph_on_match():
    g = resolve_kc_graph("乘法原理", max_nodes=10)
    assert g is not None
    cores = [n for n in g.nodes if n.level == "core"]
    assert cores[0].name == "乘法原理"


def test_resolve_preserves_user_topic_string():
    """KnowledgeGraph.topic is the raw user input, not the matched route's last segment."""
    g = resolve_kc_graph("我想学乘法原理", max_nodes=10)
    assert g is not None
    assert (
        g.topic == "我想学乘法原理"
    )  # passthrough — fails if topic=topic is dropped from _extract_subgraph call
    cores = [n for n in g.nodes if n.level == "core"]
    assert (
        cores[0].name == "乘法原理"
    )  # core still resolved correctly via segment match


def test_resolve_returns_none_on_miss():
    assert resolve_kc_graph("完全无关的话题") is None


def test_resolve_llm_mode_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        resolve_kc_graph("乘法原理", mode="llm")


def test_resolve_unknown_mode_raises_value_error():
    with pytest.raises(ValueError):
        resolve_kc_graph("乘法原理", mode="garbage")  # type: ignore[arg-type]
