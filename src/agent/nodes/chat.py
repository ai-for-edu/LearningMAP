from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_raw_client, get_model_name
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

_BASE_SYSTEM_PROMPT = "You are a friendly AI learning assistant. Help students with their questions. Respond in the same language as the student."


def _build_context_summary(state: LearningState) -> str:
    """Build a concise summary of the current learning session for the LLM."""
    parts = []

    topic = state.get("topic")
    if topic:
        parts.append(f"学习主题：{topic}")

    # Knowledge graph — node names + levels only
    graph = state.get("knowledge_graph")
    if graph:
        nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        level_map = {"prerequisite": "前置", "core": "核心", "extension": "拓展"}
        node_strs = []
        for n in nodes:
            name = n.name if hasattr(n, "name") else n.get("name", "")
            level = n.level if hasattr(n, "level") else n.get("level", "")
            node_strs.append(f"{name}({level_map.get(level, level)})")
        parts.append(f"知识图谱：{', '.join(node_strs)}")

    # Diagnosis result — score + mastered/weak breakdown
    diagnosis = state.get("diagnosis_result")
    if diagnosis:
        score = diagnosis.score if hasattr(diagnosis, "score") else diagnosis.get("score", 0)
        total = diagnosis.total if hasattr(diagnosis, "total") else diagnosis.get("total", 0)
        correct = diagnosis.correct if hasattr(diagnosis, "correct") else diagnosis.get("correct", 0)
        breakdown = diagnosis.breakdown if hasattr(diagnosis, "breakdown") else diagnosis.get("breakdown", [])

        mastered = []
        weak = []
        for b in breakdown:
            name = b.name if hasattr(b, "name") else b.get("name", "")
            status = b.status if hasattr(b, "status") else b.get("status", "")
            if status == "mastered":
                mastered.append(name)
            elif status == "weak":
                weak.append(name)

        parts.append(f"诊断结果：{score}%（{correct}/{total}）")
        if mastered:
            parts.append(f"  已掌握：{', '.join(mastered)}")
        if weak:
            parts.append(f"  薄弱：{', '.join(weak)}")

    # Lesson status
    lesson = state.get("lesson_content")
    if lesson:
        parts.append("教案：已生成")

    if not parts:
        return ""

    return "## 当前学习上下文\n" + "\n".join(f"- {p}" for p in parts) + "\n\n基于以上上下文回答用户问题。如果用户问到诊断、知识点等，用上下文中的数据回答。"


def chat_node(state: LearningState) -> dict:
    """Handle general conversation with learning context awareness."""
    messages = state.get("messages", [])

    # Build system prompt with context summary
    context = _build_context_summary(state)
    system_prompt = _BASE_SYSTEM_PROMPT
    if context:
        system_prompt += f"\n\n{context}"

    # Convert to OpenAI format
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if hasattr(msg, "type"):
            role = "assistant" if msg.type == "ai" else "user"
            api_messages.append({"role": role, "content": msg.content})
        elif isinstance(msg, dict):
            api_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    try:
        client = get_raw_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=1024,
            messages=api_messages,
        )
        reply = response.choices[0].message.content
    except Exception:
        logger.exception("LLM call failed in chat_node")
        reply = "抱歉，AI 服务暂时不可用，请稍后再试。"
    return {"messages": [AIMessage(content=reply)], "mode": "chat"}
