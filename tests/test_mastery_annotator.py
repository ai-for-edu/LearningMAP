"""Tests for graph-level mastery annotation.

Covers:
- Status thresholds at exact boundaries
- Name normalization match (exact + normalized fallback)
- Unmatched nodes get mastery=None (sentinel for "unseen" without metadata)
- forgetting_model name is propagated
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.agent.mastery_annotator import (
    _compute_status,
    _normalize_name,
    annotate_graph_with_mastery,
)
from src.forgetting.ebbinghaus import EbbinghausV1
from src.llm.schemas import KnowledgeEdge, KnowledgeGraph, KnowledgeNode


UTC = timezone.utc


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def graph() -> KnowledgeGraph:
    return KnowledgeGraph(
        topic="二次方程",
        nodes=[
            KnowledgeNode(id="n1", name="判别式", description="d1", level="core"),
            KnowledgeNode(id="n2", name="求根公式", description="d2", level="core"),
            KnowledgeNode(id="n3", name="配方法", description="d3", level="extension"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )


class TestNormalizeName:
    def test_strips_whitespace(self) -> None:
        assert _normalize_name("  判别式 ") == _normalize_name("判别式")

    def test_lowercases_ascii(self) -> None:
        assert _normalize_name("Quadratic") == _normalize_name("quadratic")

    def test_strips_common_punct(self) -> None:
        assert _normalize_name("判别式（Δ）") == _normalize_name("判别式 Δ")
        assert _normalize_name("a, b, c") == _normalize_name("abc")


class TestComputeStatus:
    @pytest.mark.parametrize(
        "retention,expected",
        [
            (1.00, "mastered"),
            (0.90, "mastered"),
            (0.70, "mastered"),  # boundary
            (0.69999, "weak"),
            (0.50, "weak"),
            (0.30, "weak"),  # boundary
            (0.29999, "forgotten"),
            (0.10, "forgotten"),
            (0.0, "forgotten"),
        ],
    )
    def test_thresholds(self, retention: float, expected: str) -> None:
        assert _compute_status(retention) == expected


class TestAnnotateGraphWithMastery:
    def test_empty_mastery_leaves_all_nodes_unseen(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        out = annotate_graph_with_mastery(
            graph,
            mastery_rows=[],
            model=EbbinghausV1(),
            now=now,
        )
        for node in out.nodes:
            assert node.mastery is None  # unseen sentinel

    def test_exact_name_match_attaches_mastery(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [
            {
                "subtopic": "判别式",
                "topic": "二次方程",
                "last_accuracy": 1.0,
                "last_seen_at": (now - timedelta(hours=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "session_count": 1,
            }
        ]
        out = annotate_graph_with_mastery(
            graph,
            mastery_rows=rows,
            model=EbbinghausV1(),
            now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is not None
        assert n1.mastery.status == "mastered"
        assert n1.mastery.forgetting_model == "ebbinghaus_v1"
        # other nodes still unseen
        n3 = next(n for n in out.nodes if n.name == "配方法")
        assert n3.mastery is None

    def test_normalized_match(self, graph: KnowledgeGraph, now: datetime) -> None:
        rows = [
            {
                "subtopic": "判别式（Δ）",  # KG has "判别式" → match via normalization
                "topic": "二次方程",
                "last_accuracy": 0.5,
                "last_seen_at": (now - timedelta(days=2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "session_count": 1,
            }
        ]
        out = annotate_graph_with_mastery(
            graph,
            mastery_rows=rows,
            model=EbbinghausV1(),
            now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is not None

    def test_unmatched_row_does_not_crash(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [
            {
                "subtopic": "完全无关的主题",
                "topic": "其他",
                "last_accuracy": 0.9,
                "last_seen_at": (now - timedelta(days=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "session_count": 1,
            }
        ]
        out = annotate_graph_with_mastery(
            graph,
            mastery_rows=rows,
            model=EbbinghausV1(),
            now=now,
        )
        # All graph nodes still unseen
        for node in out.nodes:
            assert node.mastery is None

    def test_malformed_row_skipped(self, graph: KnowledgeGraph, now: datetime) -> None:
        rows = [
            {"subtopic": "判别式"},  # missing required keys
            {
                "subtopic": "求根公式",
                "topic": "二次方程",
                "last_accuracy": 0.95,
                "last_seen_at": (now - timedelta(hours=2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "session_count": 1,
            },
        ]
        out = annotate_graph_with_mastery(
            graph,
            mastery_rows=rows,
            model=EbbinghausV1(),
            now=now,
        )
        # 判别式 row malformed → unseen
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is None
        # 求根公式 row valid → annotated
        n2 = next(n for n in out.nodes if n.name == "求根公式")
        assert n2.mastery is not None

    def test_returns_new_graph_does_not_mutate_input(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        rows = [
            {
                "subtopic": "判别式",
                "topic": "二次方程",
                "last_accuracy": 0.9,
                "last_seen_at": (now - timedelta(hours=2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "session_count": 1,
            }
        ]
        original_n1 = next(n for n in graph.nodes if n.name == "判别式")
        out = annotate_graph_with_mastery(
            graph,
            mastery_rows=rows,
            model=EbbinghausV1(),
            now=now,
        )
        assert original_n1.mastery is None  # original unchanged
        out_n1 = next(n for n in out.nodes if n.name == "判别式")
        assert out_n1.mastery is not None

    def test_matched_row_with_null_accuracy_yields_unseen_status(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        """A mastery row with last_accuracy=None still attaches MasteryMeta,
        but with status='unseen' and retention=0. Distinct from mastery=None
        (truly unmatched)."""
        rows = [
            {
                "subtopic": "判别式",
                "topic": "二次方程",
                "last_accuracy": None,
                "last_seen_at": (now - timedelta(hours=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "session_count": 1,
            }
        ]
        out = annotate_graph_with_mastery(
            graph,
            mastery_rows=rows,
            model=EbbinghausV1(),
            now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is not None  # matched, not unseen-by-absence
        assert n1.mastery.status == "unseen"
        assert n1.mastery.retention == 0.0
        assert n1.mastery.last_accuracy is None

    def test_legacy_polluted_subtopic_matches_by_prefix(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        """Legacy memory rows shaped 'node: long description' should still
        match a clean KG node name. This recovers data from before
        quiz_node started pinning knowledge_point to the KG node name."""
        rows = [{
            "subtopic": "判别式: 讲解二次方程根的判别式 b²-4ac，区分根的情况",
            "topic": "二次方程",
            "last_accuracy": 0.8,
            "last_seen_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_count": 1,
        }]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is not None
        assert n1.mastery.last_accuracy == 0.8

    def test_legacy_chinese_colon_also_matches(
        self, graph: KnowledgeGraph, now: datetime
    ) -> None:
        """Both ASCII colon ': ' and full-width '：' separators recover."""
        rows = [{
            "subtopic": "求根公式：完整推导加用例",
            "topic": "二次方程",
            "last_accuracy": 0.9,
            "last_seen_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_count": 1,
        }]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        n2 = next(n for n in out.nodes if n.name == "求根公式")
        assert n2.mastery is not None
        assert n2.mastery.last_accuracy == 0.9


class TestWeightedAccuracyPreference:
    """Annotator should feed weighted_accuracy (when present) to the
    forgetting model, not last_accuracy. Pinning prevents future
    refactors from silently dropping the time-weighted aggregation."""

    def test_uses_weighted_accuracy_when_present(
        self, graph, now
    ) -> None:
        # Big gap: last_accuracy=0.0 (just answered wrong), but the
        # weighted blend across history is 0.9 → annotator should treat
        # this as mastered, since the user has historically known it.
        rows = [{
            "subtopic": "判别式",
            "topic": "二次方程",
            "last_accuracy": 0.0,
            "weighted_accuracy": 0.9,
            "last_seen_at": (now - timedelta(seconds=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "session_count": 5,
        }]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        # retention ≈ 0.9 × exp(-tiny/S) ≈ 0.9 → mastered
        assert n1.mastery is not None
        assert n1.mastery.status == "mastered"
        # last_accuracy field on MasteryMeta still preserved for UI
        assert n1.mastery.last_accuracy == 0.0

    def test_falls_back_to_last_accuracy_when_weighted_missing(
        self, graph, now
    ) -> None:
        rows = [{
            "subtopic": "判别式",
            "topic": "二次方程",
            "last_accuracy": 0.9,
            # no weighted_accuracy key (legacy memory response)
            "last_seen_at": (now - timedelta(seconds=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "session_count": 1,
        }]
        out = annotate_graph_with_mastery(
            graph, mastery_rows=rows, model=EbbinghausV1(), now=now,
        )
        n1 = next(n for n in out.nodes if n.name == "判别式")
        assert n1.mastery is not None
        assert n1.mastery.status == "mastered"
