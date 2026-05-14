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
    """Classify user intent and route based on session_mode.

    chat mode  → always route to chat; if learn_topic detected, stash
                 pending_cta_topic so chat_node can attach a CTA card.
    learning mode → existing routing (chat / graph / quiz).
    """
    # If already in quiz step, treat as answer
    if state.get("mode") == "quiz":
        return {"mode": "quiz"}

    session_mode = state.get("session_mode", "chat")
    track_key = "chat_messages" if session_mode == "chat" else "messages"
    track = state.get(track_key, [])
    if not track:
        return {"mode": "chat"}

    last_msg = track[-1]
    content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

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
    except Exception:
        logger.exception("Intent classification failed, falling back to chat")
        return {"mode": "chat"}

    updates: dict = {}

    # Skill matching (unchanged)
    if result.skill:
        skill_meta = get_skill(result.skill)
        if skill_meta:
            updates["active_skill"] = skill_meta.name
            updates["active_skill_content"] = dict(skill_meta.sections)
            logger.info("Skill matched: %s", skill_meta.name)
        else:
            logger.warning("LLM returned unknown skill: %s", result.skill)

    session_mode = state.get("session_mode", "chat")

    if session_mode == "chat":
        # Force chat routing. If the user expressed learning intent, surface
        # it via CTA in chat_node — do NOT enter the pipeline.
        updates["mode"] = "chat"
        if result.intent == "learn_topic" and result.topic:
            updates["pending_cta_topic"] = result.topic
        return updates

    # session_mode == "learning" — full routing, no GATE.
    if result.intent == "learn_topic" and result.topic:
        updates["mode"] = "graph"
        updates["topic"] = result.topic
    elif result.intent == "start_diagnosis":
        updates["mode"] = "quiz"
    else:
        updates["mode"] = "chat"

    return updates
