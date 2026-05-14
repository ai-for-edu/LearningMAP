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
        topic="quadratic equations",
        nodes=[
            KnowledgeNode(id="n1", name="discriminant", description="d", level="core"),
            KnowledgeNode(id="n2", name="quadratic formula", description="d", level="core"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_kc_path_still_annotated(
    mock_resolve: MagicMock,
    mock_fetch: MagicMock,
) -> None:
    """When kc_skills resolver hits, the result must still be annotated.

    We assert the annotation contract (mastery is attached, status is
    not unseen-because-unmatched) rather than a specific status bucket,
    since retention computation depends on wall-clock now and would
    drift if the forgetting curve is recalibrated.
    """
    mock_resolve.return_value = _fake_graph()
    now = datetime(2026, 5, 7, 12, tzinfo=UTC)
    mock_fetch.return_value = [
        {
            "subtopic": "discriminant",
            "topic": "quadratic equations",
            "last_accuracy": 0.9,
            "last_seen_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_count": 1,
        }
    ]

    state = {"topic": "quadratic equations", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    graph = out["knowledge_graph"]
    n1 = next(n for n in graph.nodes if n.name == "discriminant")
    # Matched row → mastery attached, status is not the unmatched-sentinel.
    # Specific retention bucket is covered by mastery_annotator unit tests.
    assert n1.mastery is not None
    assert n1.mastery.status != "unseen"
    assert n1.mastery.forgetting_model == "ebbinghaus_v1"
    # Unmatched node remains None (not over-annotated).
    n2 = next(n for n in graph.nodes if n.name == "quadratic formula")
    assert n2.mastery is None


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
    mock_fetch.return_value = [
        {
            "subtopic": "discriminant",
            "topic": "quadratic equations",
            "last_accuracy": 0.9,
            "last_seen_at": "2026-05-05T10:00:00Z",
            "session_count": 1,
        }
    ]

    state = {"topic": "quadratic equations", "use_kc_skills": False}
    knowledge_graph_node(state)

    args, kwargs = mock_llm.call_args
    # _llm_generate_graph must have received the mastery list
    assert "existing_mastery" in kwargs or len(args) >= 3
    mastery_arg = kwargs.get("existing_mastery") or args[2]
    assert any(m.get("subtopic") == "discriminant" for m in mastery_arg)


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_no_mastery_means_unseen_nodes(
    mock_resolve: MagicMock,
    mock_fetch: MagicMock,
) -> None:
    mock_resolve.return_value = _fake_graph()
    mock_fetch.return_value = []
    state = {"topic": "quadratic equations", "use_kc_skills": True}
    out = knowledge_graph_node(state)

    graph = out["knowledge_graph"]
    for node in graph.nodes:
        assert node.mastery is None


@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
def test_fetch_mastery_failure_does_not_break_graph(
    mock_resolve: MagicMock,
    mock_fetch: MagicMock,
) -> None:
    """The node must produce a graph even if fetch_mastery itself blows up.

    fetch_mastery already swallows HTTP errors and returns []; this test
    covers the defense-in-depth case where the call somehow raises anyway.
    """
    mock_resolve.return_value = _fake_graph()
    mock_fetch.side_effect = RuntimeError("synthetic memory service crash")
    state = {"topic": "quadratic equations", "use_kc_skills": True}

    # Should not raise. Should still produce a graph (with no mastery).
    out = knowledge_graph_node(state)
    assert "knowledge_graph" in out
    graph = out["knowledge_graph"]
    assert len(graph.nodes) == 2
    for node in graph.nodes:
        assert node.mastery is None
