from __future__ import annotations

import anthropic
from src.agent.state import LearningState
from src.llm.client import get_model_name
from langchain_core.messages import AIMessage

_raw_client = None


def _get_raw_client() -> anthropic.Anthropic:
    global _raw_client
    if _raw_client is None:
        _raw_client = anthropic.Anthropic()
    return _raw_client


def chat_node(state: LearningState) -> dict:
    """Handle general conversation."""
    messages = state.get("messages", [])

    # Convert to anthropic format
    api_messages = []
    for msg in messages:
        if hasattr(msg, "type"):
            role = "assistant" if msg.type == "ai" else "user"
            api_messages.append({"role": role, "content": msg.content})
        elif isinstance(msg, dict):
            api_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    # Ensure messages alternate and start with user
    if api_messages and api_messages[0]["role"] != "user":
        api_messages = api_messages[1:]

    client = _get_raw_client()
    response = client.messages.create(
        model=get_model_name(),
        max_tokens=1024,
        system="You are a friendly AI learning assistant. Help students with their questions. Respond in the same language as the student.",
        messages=api_messages if api_messages else [{"role": "user", "content": "Hello"}],
    )

    reply = response.content[0].text
    return {"messages": [AIMessage(content=reply)], "mode": "chat"}
