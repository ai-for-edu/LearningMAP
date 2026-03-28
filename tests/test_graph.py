from src.agent.graph import build_graph


def test_build_graph_compiles():
    graph = build_graph()
    assert graph is not None


def test_graph_has_expected_nodes():
    graph = build_graph()
    node_names = set(graph.nodes.keys())
    expected = {"intent", "chat", "knowledge_graph", "quiz", "grading", "diagnosis", "lesson"}
    assert expected.issubset(node_names)
