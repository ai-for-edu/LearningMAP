from unittest.mock import patch

from src.agent.graph import build_graph
from src.llm.schemas import KnowledgeGraph, KnowledgeNode


def test_build_graph_compiles():
    graph = build_graph()
    assert graph is not None


def test_graph_has_expected_nodes():
    graph = build_graph()
    node_names = set(graph.nodes.keys())
    expected = {
        "intent",
        "chat",
        "knowledge_graph",
        "kc_skill_resolver",
        "quiz",
        "grading",
        "diagnosis",
        "lesson",
    }
    assert expected.issubset(node_names)


def _fake_llm_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        topic="from-llm",
        nodes=[KnowledgeNode(id="x", name="x", description="d", level="core")],
        edges=[],
    )


def test_knowledge_graph_node_uses_kc_when_topic_matches():
    """Topic matches kc bank → resolve_kc_graph returns a graph; LLM is NOT called."""
    from src.agent.nodes import knowledge_graph as node_mod

    fake_graph = KnowledgeGraph(
        topic="乘法原理",
        nodes=[KnowledgeNode(id="r", name="乘法原理", description="d", level="core")],
        edges=[],
    )

    state = {"topic": "乘法原理", "use_kc_skills": True}
    with (
        patch.object(
            node_mod, "resolve_kc_graph", return_value=fake_graph
        ) as mock_resolve,
        patch.object(node_mod, "_llm_generate_graph") as mock_llm,
        patch.object(node_mod, "fetch_mastery", return_value=[]),
    ):
        result = node_mod.knowledge_graph_node(state)
        mock_resolve.assert_called_once_with("乘法原理", max_nodes=node_mod.MAX_NODES)
        mock_llm.assert_not_called()

    # annotate_graph_with_mastery returns a model_copy, so use equality not identity
    assert result["knowledge_graph"].topic == fake_graph.topic
    assert len(result["knowledge_graph"].nodes) == len(fake_graph.nodes)
    assert result["mode"] == "graph"


def test_knowledge_graph_node_falls_back_to_llm_on_miss():
    from src.agent.nodes import knowledge_graph as node_mod

    state = {"topic": "完全无关的话题", "use_kc_skills": True}
    with (
        patch.object(node_mod, "resolve_kc_graph", return_value=None),
        patch.object(
            node_mod, "_llm_generate_graph", return_value=_fake_llm_graph()
        ) as mock_llm,
        patch.object(node_mod, "fetch_mastery", return_value=[]),
    ):
        result = node_mod.knowledge_graph_node(state)
        mock_llm.assert_called_once()

    assert result["knowledge_graph"].topic == "from-llm"


def test_knowledge_graph_node_skips_kc_when_flag_off():
    from src.agent.nodes import knowledge_graph as node_mod

    state = {"topic": "乘法原理", "use_kc_skills": False}
    with (
        patch.object(node_mod, "resolve_kc_graph") as mock_resolve,
        patch.object(
            node_mod, "_llm_generate_graph", return_value=_fake_llm_graph()
        ) as mock_llm,
        patch.object(node_mod, "fetch_mastery", return_value=[]),
    ):
        node_mod.knowledge_graph_node(state)
        mock_resolve.assert_not_called()
        mock_llm.assert_called_once()


def test_knowledge_graph_node_returns_failure_when_both_paths_miss():
    """When both kc lookup and LLM generation return None, return the failure dict."""
    from src.agent.nodes import knowledge_graph as node_mod

    state = {"topic": "x", "use_kc_skills": True}
    with (
        patch.object(node_mod, "resolve_kc_graph", return_value=None),
        patch.object(node_mod, "_llm_generate_graph", return_value=None),
        patch.object(node_mod, "fetch_mastery", return_value=[]),
    ):
        result = node_mod.knowledge_graph_node(state)

    assert result["mode"] == "chat"
    assert "知识图谱生成失败" in result["messages"][0].content
