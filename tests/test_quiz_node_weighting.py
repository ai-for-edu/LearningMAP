"""Quiz weighting: forgotten/weak nodes get more questions, mastered get fewer."""

from __future__ import annotations

from src.agent.nodes.quiz import _allocate_questions_per_node
from src.llm.schemas import KnowledgeNode, MasteryMeta


def _node(
    name: str, retention: float | None = None, status: str = "unseen"
) -> KnowledgeNode:
    if retention is None:
        return KnowledgeNode(id=name, name=name, description="d", level="core")
    return KnowledgeNode(
        id=name,
        name=name,
        description="d",
        level="core",
        mastery=MasteryMeta(
            last_accuracy=0.5,
            last_seen_at="2026-05-01T10:00:00Z",
            session_count=1,
            retention=retention,
            status=status,
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

    def test_dominant_node_does_not_overflow_budget(self) -> None:
        # 4 mastered + 1 forgotten: raw allocation gives the forgotten
        # node ~all the budget, but other nodes are pinned at MIN_PER_NODE.
        # Total must still equal base_per_node × N.
        nodes = [_node(f"m{i}", retention=1.0, status="mastered") for i in range(4)] + [
            _node("f1", retention=0.05, status="forgotten"),
        ]
        alloc = _allocate_questions_per_node(nodes, base_per_node=2)
        # 5 nodes × base 2 = 10 total
        assert sum(alloc.values()) == 10
        # All mastered nodes get at least MIN
        for i in range(4):
            assert alloc[f"m{i}"] >= 1
        # Forgotten node gets the bulk
        assert alloc["f1"] >= 6


class TestCoerceNode:
    def test_accepts_knowledge_node(self) -> None:
        from src.agent.nodes.quiz import _coerce_node

        n = KnowledgeNode(id="x", name="x", description="d", level="core")
        assert _coerce_node(n) is n  # passthrough

    def test_accepts_dict(self) -> None:
        from src.agent.nodes.quiz import _coerce_node

        d = {"id": "x", "name": "x", "description": "d", "level": "core"}
        out = _coerce_node(d)
        assert isinstance(out, KnowledgeNode)
        assert out.name == "x"
        assert out.mastery is None

    def test_accepts_dict_with_mastery(self) -> None:
        from src.agent.nodes.quiz import _coerce_node

        d = {
            "id": "x",
            "name": "x",
            "description": "d",
            "level": "core",
            "mastery": {
                "last_accuracy": 0.5,
                "last_seen_at": "2026-05-01T10:00:00Z",
                "session_count": 1,
                "retention": 0.4,
                "status": "weak",
                "forgetting_model": "ebbinghaus_v1",
            },
        }
        out = _coerce_node(d)
        assert out.mastery is not None
        assert out.mastery.status == "weak"


class TestQuizNodePinsKnowledgePoint:
    """quiz_node and quiz_generate_progressive must overwrite the LLM's
    knowledge_point field with the KG node name, so memory ingest groups
    by stable identifiers across sessions."""

    def test_quiz_node_pins_kp_to_node_name(self) -> None:
        from unittest.mock import MagicMock, patch
        from src.agent.nodes.quiz import quiz_node
        from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, QuizQuestion

        graph = KnowledgeGraph(
            topic="二次方程",
            nodes=[KnowledgeNode(id="n1", name="判别式", description="d", level="core")],
            edges=[],
        )
        # Simulate LLM returning a polluted knowledge_point string
        polluted = QuizQuestion(
            id="q1",
            knowledge_point="判别式: 这道题考察判别式的计算与根的判断",
            question="b²-4ac < 0 时？",
            options=["两实根", "一实根", "无实根", "无法判断"],
            answer=2,
        )
        with patch("src.agent.nodes.quiz.get_llm_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = [polluted]
            out = quiz_node({"knowledge_graph": graph, "questions": []})

        # After the pin, the question's knowledge_point must equal the KG node name
        assert out["questions"][0].knowledge_point == "判别式"

    def test_progressive_pins_kp_to_node_name(self) -> None:
        from unittest.mock import MagicMock, patch
        from src.agent.nodes.quiz import quiz_generate_progressive
        from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, QuizQuestion

        graph = KnowledgeGraph(
            topic="二次方程",
            nodes=[KnowledgeNode(id="n1", name="求根公式", description="d", level="core")],
            edges=[],
        )
        polluted = QuizQuestion(
            id="q1",
            knowledge_point="求根公式：完整推导",
            question="x = ?",
            options=["a", "b", "c", "d"],
            answer=0,
        )
        with patch("src.agent.nodes.quiz.get_llm_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = [polluted]
            batches = list(quiz_generate_progressive({"knowledge_graph": graph}))

        assert len(batches) == 1
        assert batches[0][0].knowledge_point == "求根公式"
