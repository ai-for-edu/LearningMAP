from __future__ import annotations

from src.agent.state import LearningState
from src.llm.client import get_raw_client, get_model_name
from langchain_core.messages import AIMessage


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

    client = get_raw_client()
    response = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=1024,
        messages=api_messages,
    )

    reply = response.choices[0].message.content
    return {"messages": [AIMessage(content=reply)], "mode": "chat"}
