from __future__ import annotations

import logging
from collections import defaultdict
from src.agent.state import LearningState
from src.llm.schemas import DiagnosisBreakdown, DiagnosisResult
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


def diagnosis_node(state: LearningState) -> dict:
    """Compute diagnosis results with skip logic."""
    questions = state.get("questions", [])
    answers = state.get("answers", [])
    graph = state.get("knowledge_graph")

    # Tally per knowledge point
    point_results: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})
    for i, q in enumerate(questions):
        kp = q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
        correct_idx = q.answer if hasattr(q, "answer") else q["answer"]
        point_results[kp]["total"] += 1
        if i < len(answers) and answers[i] == correct_idx:
            point_results[kp]["correct"] += 1

    # Determine mastered/weak
    mastered_points = set()
    weak_points = set()
    for kp, res in point_results.items():
        if res["correct"] >= res["total"] * 0.6:
            mastered_points.add(kp)
        else:
            weak_points.add(kp)

    # Skip logic: if mastered high-level node, skip its prerequisites
    skipped_points = set()
    if graph:
        edges = graph.edges if hasattr(graph, "edges") else graph.get("edges", [])
        nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        node_names = {(n.id if hasattr(n, "id") else n["id"]): (n.name if hasattr(n, "name") else n["name"]) for n in nodes}

        for edge in edges:
            source = edge.source if hasattr(edge, "source") else edge["source"]
            target = edge.target if hasattr(edge, "target") else edge["target"]
            target_name = node_names.get(target, "")
            source_name = node_names.get(source, "")
            if target_name in mastered_points and source_name not in point_results:
                skipped_points.add(source_name)

    # Build breakdown
    breakdown = []
    total_correct = sum(r["correct"] for r in point_results.values())
    total_questions = sum(r["total"] for r in point_results.values())

    for kp in point_results:
        r = point_results[kp]
        if kp in mastered_points:
            breakdown.append(DiagnosisBreakdown(name=kp, status="mastered", detail=f"{r['correct']}/{r['total']} 已掌握"))
        else:
            breakdown.append(DiagnosisBreakdown(name=kp, status="weak", detail=f"{r['correct']}/{r['total']} 需复习"))

    for kp in skipped_points:
        breakdown.append(DiagnosisBreakdown(name=kp, status="skipped", detail="已跳过"))

    score = round(total_correct / max(total_questions, 1) * 100)
    result = DiagnosisResult(
        score=score,
        total=total_questions,
        correct=total_correct,
        incorrect=total_questions - total_correct,
        breakdown=breakdown,
    )

    weak_names = ", ".join(weak_points) if weak_points else "无"
    reply = f"诊断完成！正确率 {score}%（{total_correct}/{total_questions}）\n\n薄弱知识点：{weak_names}"

    return {
        "diagnosis_result": result,
        "mode": "result",
        "messages": [AIMessage(content=reply)],
    }
