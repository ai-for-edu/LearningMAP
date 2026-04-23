"""Classba API HTTP adapter with an OpenAI-compatible surface.

Usage::

    client = ClassbaClient.from_env()
    # or:
    client = ClassbaClient(auth_token="...", agent_name="Skiller")

    skill = client.chat.completions.create(
        model="any-value",          # ignored; Classba agent is pre-configured
        messages=[{"role": "user", "content": prompt}],
        response_model=KCSkill,
    )

The class is **synchronous** (uses ``http.client``) so that callers can wrap
it in ``asyncio.to_thread``.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
from typing import Any, TypeVar
from urllib.parse import urlparse

from src.utils.json_repair import parse_json_response

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class ClassbaAPIError(RuntimeError):
    """Raised when the Classba API returns a non-zero code or bad data."""

    def __init__(self, code: int, msg: str, data: str) -> None:
        self.code = code
        self.msg = msg
        self.data = data
        super().__init__(f"ClassbaAPIError(code={code}): {msg} — data={data!r:.200}")


# ---------------------------------------------------------------------------
# Inner namespace classes (mirror OpenAI SDK shape)
# ---------------------------------------------------------------------------


class _Completions:
    def __init__(self, client: ClassbaClient) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        response_model: type[_T],
        max_tokens: int | None = None,
    ) -> _T:
        """POST to Classba and return a validated ``response_model`` instance.

        Parameters
        ----------
        model:
            Ignored (Classba agent is pre-configured upstream). Logged at DEBUG.
        messages:
            ``[{"role": "user", "content": "<prompt>"}]``. Only the first
            message's content is used.
        response_model:
            A Pydantic model class; the parsed JSON dict is validated against it.
        max_tokens:
            Ignored. Logged at DEBUG.
        """
        logger.debug(
            "ClassbaClient.create: model=%r max_tokens=%r (both ignored by Classba)",
            model,
            max_tokens,
        )
        prompt: str = messages[0]["content"]
        return self._client._call(prompt=prompt, response_model=response_model)


class _ChatNamespace:
    def __init__(self, client: ClassbaClient) -> None:
        self.completions = _Completions(client)


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


class ClassbaClient:
    """Synchronous HTTP client for the Classba LLM-agent API.

    Exposes ``client.chat.completions.create(...)`` to match the interface
    expected by ``run/build_kc_skills.py::generate_kc_skill``.
    """

    def __init__(
        self,
        *,
        host: str = "llmaiadmin.classba.cn",
        auth_token: str,
        agent_name: str = "Skiller",
        timeout: float = 60.0,
    ) -> None:
        self.host = host
        self.auth_token = auth_token
        self.agent_name = agent_name
        self.timeout = timeout
        self.chat = _ChatNamespace(self)

    @classmethod
    def from_env(cls) -> ClassbaClient:
        """Construct a ``ClassbaClient`` from environment variables.

        Required
        --------
        ``CLASSBA_AUTH``
            Raw auth token (no ``Bearer `` prefix).

        Optional
        --------
        ``CLASSBA_API_URL``
            Full URL or bare host (default ``llmaiadmin.classba.cn``).  Scheme
            and port are stripped; only the hostname is kept.
        ``CLASSBA_AGENT_NAME``
            Agent name (default ``"Skiller"``).
        """
        auth_token = os.environ.get("CLASSBA_AUTH")
        if not auth_token:
            raise RuntimeError(
                "CLASSBA_AUTH environment variable is required but not set."
            )

        raw_url = os.environ.get("CLASSBA_API_URL", "")
        if raw_url:
            parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
            host = parsed.hostname or raw_url
        else:
            host = "llmaiadmin.classba.cn"

        agent_name = os.environ.get("CLASSBA_AGENT_NAME", "Skiller")

        return cls(host=host, auth_token=auth_token, agent_name=agent_name)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _call(self, *, prompt: str, response_model: type[_T]) -> _T:
        """Execute the HTTP round-trip; return a validated model instance."""
        payload: dict[str, Any] = {
            "name": self.agent_name,
            "inputs": {
                "stream": False,
                "msg": prompt,
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers = {
            "Authorization": self.auth_token,
            "Content-Type": "application/json",
        }

        conn = http.client.HTTPSConnection(self.host, timeout=self.timeout)
        try:
            conn.request("POST", "/api/chat/call", body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
        finally:
            conn.close()

        envelope = json.loads(raw)
        code: int = envelope.get("code", -1)
        msg: str = envelope.get("msg", "")
        data: str = envelope.get("data", "")

        if code != 0:
            raise ClassbaAPIError(code, msg, data)

        parsed = parse_json_response(data)
        if parsed is None:
            raise ClassbaAPIError(-1, "JSON parse failed", data)

        return response_model.model_validate(parsed)
