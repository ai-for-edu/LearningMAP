import os
import pytest
from src.llm.client import get_llm_client


def test_get_llm_client_returns_client():
    # Will use ANTHROPIC_API_KEY from .env if available, otherwise skip
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your-key-here":
        pytest.skip("No API key configured")
    client = get_llm_client()
    assert client is not None


def test_get_llm_client_with_explicit_key():
    client = get_llm_client(api_key="test-key")
    assert client is not None
