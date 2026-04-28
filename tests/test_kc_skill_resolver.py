from __future__ import annotations

from unittest.mock import patch

import pytest

from src.llm.schemas import (
    KCSkill,
    KnowledgeGraph,
    KnowledgeNode,
)
from src.skills import kc_loader


def _skill(mark: str = "重点") -> KCSkill:
    return KCSkill(
        description="d",
        考察重点=f"{mark}-重点",
        考察形式="形式",
        易错点="易错",
        难度提示="难度",
    )


def _graph(names: list[str]) -> KnowledgeGraph:
    nodes = [
        KnowledgeNode(id=str(i), name=n, description="", level="core")
        for i, n in enumerate(names)
    ]
    return KnowledgeGraph(topic="t", nodes=nodes, edges=[])


@pytest.fixture(autouse=True)
def reset_kc_loader():
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None
    yield
    kc_loader._KC_SKILLS = None
    kc_loader._LEAF_INDEX = None


def test_resolver_returns_empty_patch_when_disabled():
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    state = {"use_kc_skills": False, "knowledge_graph": _graph(["乘法原理"])}
    patch_dict = kc_skill_resolver_node(state)
    assert patch_dict == {}


def test_resolver_returns_empty_patch_when_graph_missing():
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    state = {"use_kc_skills": True, "knowledge_graph": None}
    patch_dict = kc_skill_resolver_node(state)
    assert patch_dict == {}


def test_resolver_rule_match_only_fills_kc_skills():
    """All nodes match via rule; LLM fallback never invoked."""
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {"root----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["root----乘法原理"]}

    with patch.object(node_mod, "_llm_resolve") as mock_llm:
        state = {"use_kc_skills": True, "knowledge_graph": _graph(["乘法原理"])}
        patch_dict = node_mod.kc_skill_resolver_node(state)

    mock_llm.assert_not_called()
    assert "kc_skills" in patch_dict
    assert "乘法原理" in patch_dict["kc_skills"]
    assert patch_dict["kc_skills"]["乘法原理"]["考察重点"] == "A-重点"
    # stored as plain dict, not KCSkill instance
    assert isinstance(patch_dict["kc_skills"]["乘法原理"], dict)


def test_resolver_falls_back_to_llm_for_unmatched_nodes():
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {
        "root----乘法原理": _skill("A"),
        "root----染色计数问题": _skill("B"),
    }
    kc_loader._LEAF_INDEX = {
        kc_loader.normalize("乘法原理"): ["root----乘法原理"],
        kc_loader.normalize("染色计数问题"): ["root----染色计数问题"],
    }

    def fake_llm(unresolved, _candidates):
        # Only "给地图涂色" should be in unresolved — map it to the 染色 route
        return {"给地图涂色": "root----染色计数问题"}

    with patch.object(node_mod, "_llm_resolve", side_effect=fake_llm) as mock_llm:
        state = {
            "use_kc_skills": True,
            "knowledge_graph": _graph(["乘法原理", "给地图涂色"]),
        }
        patch_dict = node_mod.kc_skill_resolver_node(state)

    mock_llm.assert_called_once()
    called_args = mock_llm.call_args
    assert called_args.args[0] == ["给地图涂色"]  # only unresolved node passed
    assert patch_dict["kc_skills"]["乘法原理"]["考察重点"] == "A-重点"
    assert patch_dict["kc_skills"]["给地图涂色"]["考察重点"] == "B-重点"


def test_resolver_llm_error_preserves_rule_results():
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {"root----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["root----乘法原理"]}

    with patch.object(node_mod, "_llm_resolve", side_effect=RuntimeError("llm down")):
        state = {
            "use_kc_skills": True,
            "knowledge_graph": _graph(["乘法原理", "未知节点"]),
        }
        patch_dict = node_mod.kc_skill_resolver_node(state)

    # Rule-matched survives
    assert "乘法原理" in patch_dict["kc_skills"]
    # Unmatched dropped silently
    assert "未知节点" not in patch_dict["kc_skills"]


def test_resolver_skips_llm_when_leaf_index_empty():
    """JSON missing/empty → leaf_index is {} → no LLM call even if unresolved."""
    from src.agent.nodes import kc_skill_resolver as node_mod

    kc_loader._KC_SKILLS = {}
    kc_loader._LEAF_INDEX = {}

    with patch.object(node_mod, "_llm_resolve") as mock_llm:
        state = {"use_kc_skills": True, "knowledge_graph": _graph(["任何节点"])}
        patch_dict = node_mod.kc_skill_resolver_node(state)

    mock_llm.assert_not_called()
    assert patch_dict == {"kc_skills": {}}


def test_resolver_id_fast_path_for_route_id():
    """Route-id node hits _KC_SKILLS directly; resolve_by_leaf is NOT called."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    kc_loader._KC_SKILLS = {"知识点----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["知识点----乘法原理"]}

    nodes = [
        KnowledgeNode(
            id="知识点----乘法原理", name="乘法原理", description="d", level="core"
        )
    ]
    graph = KnowledgeGraph(topic="t", nodes=nodes, edges=[])
    state = {"use_kc_skills": True, "knowledge_graph": graph}

    with patch("src.agent.nodes.kc_skill_resolver.resolve_by_leaf") as mock_leaf:
        result = kc_skill_resolver_node(state)
        mock_leaf.assert_not_called()

    assert "乘法原理" in result["kc_skills"]
    assert result["kc_skills"]["乘法原理"]["考察重点"] == "A-重点"


def test_resolver_skips_synthetic_prefix_silently():
    """Synthetic-prefix node (id has '----' but isn't in _KC_SKILLS) → skipped, no leaf fallback."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    # Real route lives at a different leaf path; synthetic-prefix node has the same tail name but no entry
    kc_loader._KC_SKILLS = {"知识点----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["知识点----乘法原理"]}

    nodes = [
        KnowledgeNode(
            id="知识点----加乘原理----乘法原理",
            name="乘法原理",
            description="d",
            level="core",
        )
    ]
    graph = KnowledgeGraph(topic="t", nodes=nodes, edges=[])
    state = {"use_kc_skills": True, "knowledge_graph": graph}

    with patch("src.agent.nodes.kc_skill_resolver.resolve_by_leaf") as mock_leaf:
        result = kc_skill_resolver_node(state)
        mock_leaf.assert_not_called()

    # No false positive — the synthetic node is silently skipped.
    assert result.get("kc_skills", {}) == {}


def test_resolver_legacy_path_for_non_route_ids():
    """Node id without '----' falls through to existing leaf-name resolver."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    kc_loader._KC_SKILLS = {"root----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["root----乘法原理"]}

    nodes = [KnowledgeNode(id="n1", name="乘法原理", description="d", level="core")]
    graph = KnowledgeGraph(topic="t", nodes=nodes, edges=[])
    state = {"use_kc_skills": True, "knowledge_graph": graph}

    result = kc_skill_resolver_node(state)
    assert "乘法原理" in result["kc_skills"]


def test_resolver_defaults_to_enabled_when_flag_missing():
    """Missing use_kc_skills field defaults to True (matches knowledge_graph_node)."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    kc_loader._KC_SKILLS = {"知识点----乘法原理": _skill("A")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("乘法原理"): ["知识点----乘法原理"]}

    nodes = [
        KnowledgeNode(
            id="知识点----乘法原理", name="乘法原理", description="d", level="core"
        )
    ]
    graph = KnowledgeGraph(topic="t", nodes=nodes, edges=[])
    state = {"knowledge_graph": graph}  # no use_kc_skills key

    result = kc_skill_resolver_node(state)
    assert "乘法原理" in result["kc_skills"]


def test_resolver_skips_bare_root_segment_silently():
    """Topic-matched root segment (id with no '----') is a synthetic tree node and
    must not fall through to leaf-name search — same invariant as '----'-prefix nodes."""
    from src.agent.nodes.kc_skill_resolver import kc_skill_resolver_node

    # Set up a leaf index where "知识点" *would* false-match if leaf search ran.
    kc_loader._KC_SKILLS = {"some----route----知识点": _skill("X")}
    kc_loader._LEAF_INDEX = {kc_loader.normalize("知识点"): ["some----route----知识点"]}

    nodes = [KnowledgeNode(id="知识点", name="知识点", description="d", level="core")]
    graph = KnowledgeGraph(topic="知识点", nodes=nodes, edges=[])
    state = {"use_kc_skills": True, "knowledge_graph": graph}

    with patch("src.agent.nodes.kc_skill_resolver.resolve_by_leaf") as mock_leaf:
        result = kc_skill_resolver_node(state)
        mock_leaf.assert_not_called()

    assert result.get("kc_skills", {}) == {}
