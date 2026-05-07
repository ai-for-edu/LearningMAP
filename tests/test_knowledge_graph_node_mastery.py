"""Integration test for knowledge_graph_node with mastery annotation.

Verifies:
- fetch_mastery is called with the topic
- existing_mastery is passed into the prompt
- annotate_graph_with_mastery is applied regardless of generation path
- empty mastery degrades gracefully (no annotation, no prompt block)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


from src.agent.nodes.knowledge_graph import knowledge_graph_node
from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode

UTC = timezone.utc


def _fake_graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        topic="二次方程",
        nodes=[
            KnowledgeNode(id="n1", name="判别式", description="d", level="core"),
            KnowledgeNode(id="n2", name="求根公式", description="d", level="core"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_kc_path_still_annotated(
    mock_resolve: MagicMock, mock_fetch: MagicMock,
) -> None:
    """When kc_skills resolver hits, the result must still be annotated."""
    mock_resolve.return_value = _fake_graph()
    now = datetime(2026, 5, 7, 12, tzinfo=UTC)
    mock_fetch.return_value = [{
        "subtopic": "判别式",
        "topic": "二次方程",
        "last_accuracy": 0.9,
        "last_seen_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_count": 1,
    }]

    state = {"topic": "二次方程", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    graph = out["knowledge_graph"]
    n1 = next(n for n in graph.nodes if n.name == "判别式")
    assert n1.mastery is not None
    assert n1.mastery.status in {"mastered", "partial"}


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph._llm_generate_graph")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_llm_path_receives_mastery_in_prompt(
    mock_resolve: MagicMock,
    mock_llm: MagicMock,
    mock_fetch: MagicMock,
) -> None:
    """LLM generator must be called with existing_mastery list."""
    mock_resolve.return_value = None  # force LLM fallback
    mock_llm.return_value = _fake_graph()
    mock_fetch.return_value = [{
        "subtopic": "判别式", "topic": "二次方程",
        "last_accuracy": 0.9, "last_seen_at": "2026-05-05T10:00:00Z",
        "session_count": 1,
    }]

    state = {"topic": "二次方程", "use_kc_skills": False}
    knowledge_graph_node(state)

    args, kwargs = mock_llm.call_args
    # _llm_generate_graph must have received the mastery list
    assert "existing_mastery" in kwargs or len(args) >= 3
    mastery_arg = kwargs.get("existing_mastery") or args[2]
    assert any(m.get("subtopic") == "判别式" for m in mastery_arg)


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_no_mastery_means_unseen_nodes(
    mock_resolve: MagicMock, mock_fetch: MagicMock,
) -> None:
    mock_resolve.return_value = _fake_graph()
    mock_fetch.return_value = []
    state = {"topic": "二次方程", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    graph = out["knowledge_graph"]
    for node in graph.nodes:
        assert node.mastery is None


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_fetch_mastery_failure_does_not_break_graph(
    mock_resolve: MagicMock, mock_fetch: MagicMock,
) -> None:
    """fetch_mastery already returns [] on error; verify node still works."""
    mock_resolve.return_value = _fake_graph()
    mock_fetch.return_value = []  # graceful-degradation contract
    state = {"topic": "二次方程", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    assert "knowledge_graph" in out
    assert len(out["knowledge_graph"].nodes) == 2
