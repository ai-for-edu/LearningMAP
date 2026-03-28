from __future__ import annotations

import os
import logging

import openai
import instructor
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_client: instructor.Instructor | None = None
_raw_client: openai.OpenAI | None = None


def _get_base_client(api_key: str | None = None) -> openai.OpenAI:
    """Create an OpenAI-compatible client."""
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", None)
    return openai.OpenAI(api_key=key, base_url=base_url)


def get_llm_client(api_key: str | None = None) -> instructor.Instructor:
    """Get an instructor-wrapped OpenAI client for structured output."""
    global _client
    if _client is not None and api_key is None:
        return _client

    base_client = _get_base_client(api_key)
    client = instructor.from_openai(base_client)

    if api_key is None:
        _client = client
    return client


def get_raw_client() -> openai.OpenAI:
    """Get a raw OpenAI client for non-structured calls."""
    global _raw_client
    if _raw_client is None:
        _raw_client = _get_base_client()
    return _raw_client


def get_model_name() -> str:
    """Get the LLM model name from env."""
    return os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")
