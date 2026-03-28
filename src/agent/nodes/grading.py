from __future__ import annotations

import logging
from src.agent.state import LearningState
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

# Map letter answers to indices
ANSWER_MAP = {"a": 0, "b": 1, "c": 2, "d": 3, "0": 0, "1": 1, "2": 2, "3": 3}


def grading_node(state: LearningState) -> dict:
    """Grade student's answer to current question."""
    messages = state.get("messages", [])
    questions = state.get("questions", [])
    current_idx = state.get("current_question_index", 0)
    answers = list(state.get("answers", []))

    if current_idx >= len(questions):
        return {"mode": "result"}

    # Parse student answer from last message
    last_msg = messages[-1]
    content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    student_answer = content.strip().lower()

    answer_idx = ANSWER_MAP.get(student_answer, -1)
    if answer_idx == -1:
        return {"messages": [AIMessage(content="请输入 A、B、C 或 D 来作答。")], "mode": "quiz"}

    # Check correctness
    q = questions[current_idx]
    correct_idx = q.answer if hasattr(q, "answer") else q["answer"]
    is_correct = answer_idx == correct_idx

    answers.append(answer_idx)
    next_idx = current_idx + 1

    if is_correct:
        feedback = "✓ 回答正确！"
    else:
        correct_letter = chr(65 + correct_idx)
        feedback = f"✗ 回答错误，正确答案是 {correct_letter}。"

    # Check if more questions
    if next_idx >= len(questions):
        return {
            "answers": answers,
            "current_question_index": next_idx,
            "mode": "result",
            "messages": [AIMessage(content=f"{feedback}\n\n所有题目作答完毕，正在生成诊断结果...")],
        }

    return {
        "answers": answers,
        "current_question_index": next_idx,
        "mode": "quiz",
        "messages": [AIMessage(content=feedback)],
    }
