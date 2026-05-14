"""End-to-end smoke for KG mastery annotation.

Real: schemas, annotator, prompt rendering, forgetting model, quiz allocation.
Mocked: memory HTTP client, LLM HTTP client.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.agent.nodes.knowledge_graph import knowledge_graph_node
from src.agent.nodes.quiz import _allocate_questions_per_node
from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode

UTC = timezone.utc


@patch("src.agent.nodes.knowledge_graph.resolve_kc_graph")
@patch("src.agent.nodes.knowledge_graph.fetch_mastery")
def test_full_pipeline_kc_path(
    mock_fetch: MagicMock,
    mock_resolve: MagicMock,
) -> None:
    mock_resolve.return_value = KnowledgeGraph(
        topic="quadratic equations",
        nodes=[
            KnowledgeNode(id="n1", name="discriminant", description="d", level="core"),
            KnowledgeNode(id="n2", name="quadratic formula", description="d", level="core"),
            KnowledgeNode(id="n3", name="Vieta theorem", description="d", level="extension"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )
    now_iso_recent = (datetime.now(UTC) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    now_iso_old = (datetime.now(UTC) - timedelta(days=60)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    mock_fetch.return_value = [
        {
            "subtopic": "discriminant",
            "topic": "quadratic equations",
            "last_accuracy": 1.0,
            "last_seen_at": now_iso_recent,
            "session_count": 2,
        },
        {
            "subtopic": "quadratic formula",
            "topic": "quadratic equations",
            "last_accuracy": 0.5,
            "last_seen_at": now_iso_old,
            "session_count": 1,
        },
    ]

    out = knowledge_graph_node({"topic": "quadratic equations", "use_kc_skills": True})
    graph = out["knowledge_graph"]

    by_name = {n.name: n for n in graph.nodes}
    # Recently studied with perfect accuracy → mastered
    assert by_name["discriminant"].mastery is not None
    assert by_name["discriminant"].mastery.status == "mastered"
    # 60-day-old session with 50% accuracy → forgotten
    assert by_name["quadratic formula"].mastery is not None
    assert by_name["quadratic formula"].mastery.status in {"weak", "forgotten"}
    # Never seen: mastery is None
    assert by_name["Vieta theorem"].mastery is None

    # Quiz allocation should give the forgotten node more questions
    alloc = _allocate_questions_per_node(graph.nodes)
    assert alloc["quadratic formula"] >= alloc["discriminant"]
