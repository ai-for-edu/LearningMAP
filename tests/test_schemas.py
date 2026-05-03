from src.llm.schemas import KnowledgeNode, KnowledgeEdge, KnowledgeGraph, QuizQuestion, DiagnosisBreakdown, DiagnosisResult, KCSkill


def test_knowledge_graph_schema():
    graph = KnowledgeGraph(
        topic="二元一次方程",
        nodes=[
            KnowledgeNode(id="n1", name="一元一次方程", description="基础方程", level="prerequisite"),
            KnowledgeNode(id="n2", name="二元一次方程组", description="核心内容", level="core"),
        ],
        edges=[KnowledgeEdge(source="n1", target="n2", relation="prerequisite")],
    )
    assert len(graph.nodes) == 2
    assert graph.topic == "二元一次方程"


def test_quiz_question_schema():
    q = QuizQuestion(
        id="q1",
        knowledge_point="一元一次方程",
        question="解方程 2x+5=11，x=?",
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
            DiagnosisBreakdown(name="一元一次方程", status="mastered", detail="2/2 已掌握"),
            DiagnosisBreakdown(name="不等式", status="weak", detail="1/2 需复习"),
        ],
    )
    assert result.score == 70
    assert result.breakdown[1].status == "weak"


def test_kc_skill_schema():
    skill = KCSkill(
        description="当学生处理多步骤计数问题且出现分步与分类混淆时使用。构建在乘法原理。",
        考察重点="理解乘法原理：分步计数中各步方案数相乘得到总方案数。",
        考察形式="以填空题为主，题干给出若干类对象和选择步骤，要求计算方案数。",
        易错点="学生常把分步（乘）和分类（加）混淆，导致用加法代替乘法。",
        难度提示="数字量级 2-5，步骤数 2-4 步，偶见带限制条件的情境。",
    )
    assert skill.考察重点.startswith("理解")
    assert "填空" in skill.考察形式
    assert "使用" in skill.description or "skill" in skill.description.lower()


def test_review_result_round_trip() -> None:
    from src.llm.schemas import ReviewResult

    r = ReviewResult(
        weak_kps=["分配律"],
        strong_kps=["结合律", "交换律"],
        score=0.67,
        focus_directive="学生在 分配律 上仍薄弱，本次教案应优先衔接此点。",
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
