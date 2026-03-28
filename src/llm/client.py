from __future__ import annotations

import os
import logging

import anthropic
import instructor
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_client: instructor.Instructor | None = None


def get_llm_client(api_key: str | None = None) -> instructor.Instructor:
    """Get an instructor-wrapped Anthropic client for structured output."""
    global _client
    if _client is not None and api_key is None:
        return _client

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    base_client = anthropic.Anthropic(api_key=key)
    client = instructor.from_anthropic(base_client)

    if api_key is None:
        _client = client
    return client


def get_model_name() -> str:
    """Get the LLM model name from env."""
    return os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")
