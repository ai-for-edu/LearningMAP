"""Quiz weighting: forgotten/weak nodes get more questions, mastered get fewer."""
from __future__ import annotations

from src.agent.nodes.quiz import _allocate_questions_per_node
from src.llm.schemas import KnowledgeNode, MasteryMeta


def _node(name: str, retention: float | None = None, status: str = "unseen") -> KnowledgeNode:
    if retention is None:
        return KnowledgeNode(id=name, name=name, description="d", level="core")
    return KnowledgeNode(
        id=name, name=name, description="d", level="core",
        mastery=MasteryMeta(
            last_accuracy=0.5, last_seen_at="2026-05-01T10:00:00Z",
            session_count=1, retention=retention, status=status,
            forgetting_model="ebbinghaus_v1",
        ),
    )


class TestAllocateQuestionsPerNode:
    def test_no_mastery_uniform(self) -> None:
        nodes = [_node("a"), _node("b"), _node("c")]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        # Without mastery info, every node gets the base count
        assert alloc == {"a": 2, "b": 2, "c": 2}

    def test_forgotten_gets_more_than_mastered(self) -> None:
        nodes = [
            _node("forgot", retention=0.05, status="forgotten"),
            _node("solid", retention=0.95, status="mastered"),
        ]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        assert alloc["forgot"] > alloc["solid"]

    def test_min_one_per_node(self) -> None:
        # Even a fully-mastered node should still get at least 1 question
        # (acts as a spot-check)
        nodes = [_node("m", retention=1.0, status="mastered")]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        assert alloc["m"] >= 1

    def test_total_count_close_to_base_times_n(self) -> None:
        nodes = [
            _node("a", retention=0.1, status="forgotten"),
            _node("b", retention=0.5, status="weak"),
            _node("c", retention=0.9, status="mastered"),
        ]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        # Total budget is preserved (within ±N rounding)
        assert sum(alloc.values()) == 2 * 3 or abs(sum(alloc.values()) - 6) <= 1

    def test_unseen_node_gets_baseline_weight(self) -> None:
        # Unseen nodes get a moderate priority — between forgotten and mastered
        nodes = [
            _node("unseen"),
            _node("mastered_one", retention=0.95, status="mastered"),
        ]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        assert alloc["unseen"] >= alloc["mastered_one"]
