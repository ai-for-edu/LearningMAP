from src.llm.schemas import KnowledgeNode, KnowledgeEdge, KnowledgeGraph, QuizQuestion, DiagnosisBreakdown, DiagnosisResult


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
