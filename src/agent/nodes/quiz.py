from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.llm.schemas import QuizQuestion
from src.prompts.loader import render_prompt
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

QUESTIONS_PER_POINT = 2


def quiz_node(state: LearningState) -> dict:
    """Generate quiz questions or serve the next question."""
    questions = state.get("questions", [])
    current_idx = state.get("current_question_index", 0)

    # If no questions generated yet, generate them
    if not questions:
        graph = state.get("knowledge_graph")
        if not graph:
            return {"mode": "chat", "messages": [AIMessage(content="请先选择一个学习主题。")]}

        nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        points = [{"name": n.name if hasattr(n, "name") else n["name"],
                    "description": n.description if hasattr(n, "description") else n["description"]}
                   for n in nodes]

        prompt = render_prompt("quiz_generate", knowledge_points=points, questions_per_point=QUESTIONS_PER_POINT)
        client = get_llm_client()
        generated = client.messages.create(
            model=get_model_name(),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            response_model=list[QuizQuestion],
        )

        return {
            "questions": generated,
            "current_question_index": 0,
            "answers": [],
            "mode": "quiz",
            "messages": [AIMessage(content=f"共 {len(generated)} 道题，开始作答！")],
        }

    # Serve current question
    if current_idx < len(questions):
        q = questions[current_idx]
        q_name = q.knowledge_point if hasattr(q, "knowledge_point") else q["knowledge_point"]
        q_text = q.question if hasattr(q, "question") else q["question"]
        opts = q.options if hasattr(q, "options") else q["options"]
        options_text = "\n".join(f"  {chr(65+i)}) {opt}" for i, opt in enumerate(opts))
        reply = f"[{q_name}] 第 {current_idx+1}/{len(questions)} 题\n\n{q_text}\n\n{options_text}"
        return {"mode": "quiz", "messages": [AIMessage(content=reply)]}

    # All done
    return {"mode": "result"}
