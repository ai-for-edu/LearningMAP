"""Tests for run/classba_client.py — ClassbaClient HTTP adapter.

All tests mock http.client.HTTPSConnection; no real network calls are made.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from run.classba_client import ClassbaAPIError, ClassbaClient
from src.llm.schemas import KCSkill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_SKILL_DICT = {
    "考察重点": "A",
    "考察形式": "B",
    "易错点": "C",
    "难度提示": "D",
}
_VALID_SKILL = KCSkill(**_VALID_SKILL_DICT)


def _make_http_response(body: dict | str, status: int = 200) -> MagicMock:
    """Build a mock HTTPResponse whose .read() returns JSON bytes."""
    if isinstance(body, dict):
        raw = json.dumps(body, ensure_ascii=False).encode()
    else:
        raw = body.encode()
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = raw
    return resp


def _mock_conn(response: MagicMock):
    """Return a context manager / class mock for HTTPSConnection.

    The mock's __init__ returns the same mock instance when called,
    and getresponse() returns `response`.
    """
    conn = MagicMock()
    conn.getresponse.return_value = response
    conn_cls = MagicMock(return_value=conn)
    return conn_cls, conn


# ---------------------------------------------------------------------------
# Test 1: correct payload shape
# ---------------------------------------------------------------------------


def test_create_sends_correct_payload():
    envelope = {"code": 0, "msg": "", "data": json.dumps(_VALID_SKILL_DICT)}
    resp = _make_http_response(envelope)
    conn_cls, conn = _mock_conn(resp)

    with patch("http.client.HTTPSConnection", conn_cls):
        client = ClassbaClient(auth_token="my-token", agent_name="Skiller")
        client.chat.completions.create(
            model="dummy-model",
            messages=[{"role": "user", "content": "Hello prompt"}],
            response_model=KCSkill,
        )

    # Verify connection opened to correct host
    conn_cls.assert_called_once()
    call_args = conn_cls.call_args
    assert call_args[0][0] == "llmaiadmin.classba.cn"

    # Verify request method and URL
    conn.request.assert_called_once()
    req_args, req_kwargs = conn.request.call_args
    assert req_args[0] == "POST"
    assert req_args[1] == "/api/chat/call"

    # Verify headers
    headers = req_kwargs.get("headers") or req_args[3] if len(req_args) > 3 else req_kwargs["headers"]
    assert headers["Authorization"] == "my-token"
    assert "Bearer" not in headers["Authorization"]
    assert headers["Content-Type"] == "application/json"

    # Verify body payload
    body_bytes = req_kwargs.get("body") or req_args[2]
    payload = json.loads(body_bytes)
    assert payload["name"] == "Skiller"
    assert payload["inputs"]["msg"] == "Hello prompt"
    assert payload["inputs"]["stream"] is False


# ---------------------------------------------------------------------------
# Test 2: valid response → KCSkill instance
# ---------------------------------------------------------------------------


def test_create_parses_valid_response_into_response_model():
    envelope = {"code": 0, "msg": "", "data": json.dumps(_VALID_SKILL_DICT)}
    resp = _make_http_response(envelope)
    conn_cls, _ = _mock_conn(resp)

    with patch("http.client.HTTPSConnection", conn_cls):
        client = ClassbaClient(auth_token="tok")
        result = client.chat.completions.create(
            model="m",
            messages=[{"role": "user", "content": "prompt"}],
            response_model=KCSkill,
        )

    assert isinstance(result, KCSkill)
    assert result == _VALID_SKILL


# ---------------------------------------------------------------------------
# Test 3: JSON wrapped in code fence still parses
# ---------------------------------------------------------------------------


def test_create_extracts_json_from_code_fence():
    fenced_data = f"```json\n{json.dumps(_VALID_SKILL_DICT)}\n```"
    envelope = {"code": 0, "msg": "", "data": fenced_data}
    resp = _make_http_response(envelope)
    conn_cls, _ = _mock_conn(resp)

    with patch("http.client.HTTPSConnection", conn_cls):
        client = ClassbaClient(auth_token="tok")
        result = client.chat.completions.create(
            model="m",
            messages=[{"role": "user", "content": "prompt"}],
            response_model=KCSkill,
        )

    assert isinstance(result, KCSkill)
    assert result == _VALID_SKILL


# ---------------------------------------------------------------------------
# Test 4: non-zero code → ClassbaAPIError
# ---------------------------------------------------------------------------


def test_create_raises_on_nonzero_code():
    envelope = {"code": 500, "msg": "upstream", "data": "boom"}
    resp = _make_http_response(envelope)
    conn_cls, _ = _mock_conn(resp)

    with patch("http.client.HTTPSConnection", conn_cls):
        client = ClassbaClient(auth_token="tok")
        with pytest.raises(ClassbaAPIError) as exc_info:
            client.chat.completions.create(
                model="m",
                messages=[{"role": "user", "content": "prompt"}],
                response_model=KCSkill,
            )

    err = exc_info.value
    assert err.code == 500
    assert "upstream" in str(err)


# ---------------------------------------------------------------------------
# Test 5: unparseable data → ClassbaAPIError mentioning JSON parse failure
# ---------------------------------------------------------------------------


def test_create_raises_on_unparseable_data():
    envelope = {"code": 0, "msg": "", "data": "completely random words, no json"}
    resp = _make_http_response(envelope)
    conn_cls, _ = _mock_conn(resp)

    with patch("http.client.HTTPSConnection", conn_cls):
        client = ClassbaClient(auth_token="tok")
        with pytest.raises(ClassbaAPIError) as exc_info:
            client.chat.completions.create(
                model="m",
                messages=[{"role": "user", "content": "prompt"}],
                response_model=KCSkill,
            )

    err = exc_info.value
    assert "JSON" in str(err) or "parse" in str(err).lower()


# ---------------------------------------------------------------------------
# Test 6: from_env reads env vars correctly
# ---------------------------------------------------------------------------


def test_from_env_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CLASSBA_AUTH", "tok")
    monkeypatch.setenv("CLASSBA_API_URL", "https://example.com")
    # default agent name
    monkeypatch.delenv("CLASSBA_AGENT_NAME", raising=False)

    client = ClassbaClient.from_env()

    assert client.host == "example.com"
    assert client.auth_token == "tok"
    assert client.agent_name == "Skiller"


# ---------------------------------------------------------------------------
# Test 7: from_env raises when CLASSBA_AUTH is missing
# ---------------------------------------------------------------------------


def test_from_env_raises_when_auth_missing(monkeypatch):
    monkeypatch.delenv("CLASSBA_AUTH", raising=False)
    monkeypatch.delenv("CLASSBA_API_URL", raising=False)

    with pytest.raises((RuntimeError, ValueError)):
        ClassbaClient.from_env()


# ---------------------------------------------------------------------------
# Test 8: conn.close() is called even on error path
# ---------------------------------------------------------------------------


def test_create_closes_connection():
    # Use a non-zero code to trigger early error after conn is opened
    envelope = {"code": 500, "msg": "err", "data": ""}
    resp = _make_http_response(envelope)
    conn_cls, conn = _mock_conn(resp)

    with patch("http.client.HTTPSConnection", conn_cls):
        client = ClassbaClient(auth_token="tok")
        with pytest.raises(ClassbaAPIError):
            client.chat.completions.create(
                model="m",
                messages=[{"role": "user", "content": "prompt"}],
                response_model=KCSkill,
            )

    conn.close.assert_called_once()
