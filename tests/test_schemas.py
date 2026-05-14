import pytest
from src.llm.schemas import (
    KnowledgeNode,
    KnowledgeEdge,
    KnowledgeGraph,
    QuizQuestion,
    DiagnosisBreakdown,
    DiagnosisResult,
    KCSkill,
    MasteryMeta,
)


def test_knowledge_graph_schema():
    graph = KnowledgeGraph(
        topic="system of linear equations",
        nodes=[
            KnowledgeNode(
                id="n1",
                name="linear equations",
                description="fundamental equations",
                level="prerequisite",
            ),
            KnowledgeNode(
                id="n2", name="systems of two linear equations", description="core content", level="core"
            ),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )
    assert len(graph.nodes) == 2
    assert graph.topic == "system of linear equations"


def test_quiz_question_schema():
    q = QuizQuestion(
        id="q1",
        knowledge_point="linear equations",
        question="Solve 2x+5=11, x=?",
        options=["x=2", "x=3", "x=4", "x=8"],
        answer=1,
    )
    assert q.options[q.answer] == "x=3"


def test_diagnosis_result_schema():
    result = DiagnosisResult(
        score=70,
        total=10,
        correct=7,
        incorrect=3,
        breakdown=[
            DiagnosisBreakdown(
                name="linear equations", status="mastered", detail="2/2 mastered"
            ),
            DiagnosisBreakdown(name="inequalities", status="weak", detail="1/2 needs review"),
        ],
    )
    assert result.score == 70
    assert result.breakdown[1].status == "weak"


def test_kc_skill_schema():
    skill = KCSkill(
        description="Use when a student confuses step-based and category-based counting in multi-step problems. Builds on the multiplication principle.",
        考察重点="Understand the multiplication principle: multiply the number of options at each step in step-based counting.",
        考察形式="Primarily fill-in-the-blank questions: the stem gives several types of objects and selection steps, requiring calculation of the number of arrangements.",
        易错点="Students often confuse step-based (multiply) and category-based (add) counting, using addition instead of multiplication.",
        难度提示="Number scale 2-5, steps 2-4, occasionally with constraint conditions.",
    )
    assert skill.考察重点.startswith("Understand")
    assert "fill-in" in skill.考察形式
    assert "Use when" in skill.description or "skill" in skill.description.lower()


def test_review_result_round_trip() -> None:
    from src.llm.schemas import ReviewResult

    r = ReviewResult(
        weak_kps=["distributive law"],
        strong_kps=["associative law", "commutative law"],
        score=0.67,
        focus_directive="The student is still weak on distributive law; prioritize bridging this in the lesson plan.",
    )
    dumped = r.model_dump()
    restored = ReviewResult(**dumped)
    assert restored == r


def test_review_result_score_bounds() -> None:
    import pytest
    from pydantic import ValidationError
    from src.llm.schemas import ReviewResult

    with pytest.raises(ValidationError):
        ReviewResult(weak_kps=[], strong_kps=[], score=1.5, focus_directive="x")


# ---- MasteryMeta + KnowledgeNode.mastery ----


class TestMasteryMeta:
    def test_required_fields(self) -> None:
        m = MasteryMeta(
            last_accuracy=0.8,
            last_seen_at="2026-04-30T10:00:00Z",
            session_count=2,
            retention=0.62,
            status="weak",
            forgetting_model="ebbinghaus_v1",
        )
        assert m.status == "weak"
        assert m.retention == 0.62

    def test_last_accuracy_optional(self) -> None:
        # accuracy can be None for never-tested rows
        m = MasteryMeta(
            last_accuracy=None,
            last_seen_at="2026-04-30T10:00:00Z",
            session_count=1,
            retention=0.0,
            status="unseen",
            forgetting_model="ebbinghaus_v1",
        )
        assert m.last_accuracy is None

    def test_last_seen_at_required_str_rejects_none(self) -> None:
        # Lock in Pydantic v2's rejection of None for a str-typed field —
        # this is the safety net that prevents a NULL DB timestamp from
        # silently becoming the string "None" in storage.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MasteryMeta(
                last_accuracy=0.5,
                last_seen_at=None,  # type: ignore[arg-type]
                session_count=1,
                retention=0.4,
                status="weak",
                forgetting_model="ebbinghaus_v1",
            )


class TestKnowledgeNodeMastery:
    def test_default_mastery_none(self) -> None:
        n = KnowledgeNode(id="n1", name="discriminant", description="...", level="core")
        assert n.mastery is None

    def test_with_mastery(self) -> None:
        n = KnowledgeNode(
            id="n1",
            name="discriminant",
            description="...",
            level="core",
            mastery=MasteryMeta(
                last_accuracy=0.5,
                last_seen_at="2026-04-30T10:00:00Z",
                session_count=1,
                retention=0.4,
                status="weak",
                forgetting_model="ebbinghaus_v1",
            ),
        )
        assert n.mastery.status == "weak"

    def test_round_trip_dict(self) -> None:
        n = KnowledgeNode(
            id="n1",
            name="x",
            description="d",
            level="core",
            mastery=MasteryMeta(
                last_accuracy=0.9,
                last_seen_at="2026-04-30T10:00:00Z",
                session_count=2,
                retention=0.88,
                status="mastered",
                forgetting_model="ebbinghaus_v1",
            ),
        )
        dumped = n.model_dump()
        assert dumped["mastery"]["status"] == "mastered"
        rebuilt = KnowledgeNode.model_validate(dumped)
        assert rebuilt.mastery.status == "mastered"
