from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_raw_client, get_model_name
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


def chat_node(state: LearningState) -> dict:
    """Handle general conversation."""
    messages = state.get("messages", [])

    # Convert to OpenAI format
    api_messages = [
        {"role": "system", "content": "You are a friendly AI learning assistant. Help students with their questions. Respond in the same language as the student."}
    ]
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
