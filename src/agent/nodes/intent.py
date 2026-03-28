from __future__ import annotations

import logging
from pydantic import BaseModel
from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.prompts.loader import render_prompt

logger = logging.getLogger(__name__)


class IntentResult(BaseModel):
    intent: str  # "learn_topic" | "start_diagnosis" | "answer" | "chat"
    topic: str | None = None


def intent_node(state: LearningState) -> dict:
    """Classify user intent from the latest message."""
    # If already in quiz mode, treat as answer
    if state.get("mode") == "quiz":
        return {"mode": "quiz"}

    messages = state.get("messages", [])
    if not messages:
        return {"mode": "chat"}

    last_msg = messages[-1]
    if hasattr(last_msg, "content"):
        content = last_msg.content
    else:
        content = str(last_msg)

    prompt = render_prompt("intent", messages=[{"role": "user", "content": content}])

    try:
        client = get_llm_client()
        result = client.messages.create(
            model=get_model_name(),
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            response_model=IntentResult,
        )

        if result.intent == "learn_topic" and result.topic:
            return {"mode": "graph", "topic": result.topic}
        elif result.intent == "start_diagnosis":
            return {"mode": "quiz"}
        else:
            return {"mode": "chat"}
    except Exception:
        logger.exception("Intent classification failed, falling back to chat")
        return {"mode": "chat"}
