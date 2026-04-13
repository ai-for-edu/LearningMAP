from __future__ import annotations

import logging
from pydantic import BaseModel
from src.agent.state import LearningState
from src.llm.client import get_llm_client, get_model_name
from src.prompts.loader import render_prompt
from src.skills.loader import get_skill, list_skills

logger = logging.getLogger(__name__)


class IntentResult(BaseModel):
    intent: str  # "learn_topic" | "start_diagnosis" | "answer" | "chat"
    topic: str | None = None
    skill: str | None = None  # matched skill name, or null


def intent_node(state: LearningState) -> dict:
    """Classify user intent and match skill from the latest message."""
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

    # Build skill summaries for intent prompt
    available_skills = list_skills()

    prompt = render_prompt(
        "intent",
        messages=[{"role": "user", "content": content}],
        available_skills=available_skills,
    )

    try:
        client = get_llm_client()
        result = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            response_model=IntentResult,
        )

        # Build return state updates
        updates: dict = {}

        # Handle skill matching
        matched_skill_name = result.skill
        if matched_skill_name:
            skill_meta = get_skill(matched_skill_name)
            if skill_meta:
                updates["active_skill"] = skill_meta.name
                updates["active_skill_content"] = skill_meta.sections
                logger.info("Skill matched: %s", skill_meta.name)
            else:
                logger.warning("LLM returned unknown skill: %s", matched_skill_name)

        # Handle intent routing
        if result.intent == "learn_topic" and result.topic:
            updates["mode"] = "graph"
            updates["topic"] = result.topic
        elif result.intent == "start_diagnosis":
            updates["mode"] = "quiz"
        else:
            updates["mode"] = "chat"

        return updates
    except Exception:
        logger.exception("Intent classification failed, falling back to chat")
        return {"mode": "chat"}
