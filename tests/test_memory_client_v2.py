"""Unit tests for src.memory_client PR-2 additions.

`v2` because `tests/test_memory_client.py` already exists and covers the
PR-1 surface (retrieve_memory / ingest_session). We add a sibling file
rather than overload the existing one.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx


# ----------------------------------------------------------------------
# log_error
# ----------------------------------------------------------------------
def test_log_error_posts_to_errors_log() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.post.return_value = MagicMock(status_code=200, json=lambda: {"logged": True})
        mk.return_value = client
        memory_client.log_error(
            source="main_app", user_id="u1", session_id="s1",
            correlation_id="corr-1", op="fetch_snapshot",
            status="http_5xx", error_class="HTTPError", error_msg="boom",
        )
        client.post.assert_called_once()
        url = client.post.call_args[0][0]
        assert url == "/errors/log"
        body = client.post.call_args[1]["json"]
        assert body["op"] == "fetch_snapshot"
        assert body["correlation_id"] == "corr-1"
        assert body["error_msg"] == "boom"


def test_log_error_swallows_httpx_failure() -> None:
    """log_error must NEVER raise. cascade protection is mandatory."""
    from src import memory_client

    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.post.side_effect = httpx.ConnectError("nope")
        mk.return_value = client
        # Must not raise — failure-of-failure path
        memory_client.log_error(
            source="main_app", user_id=None, session_id=None,
            correlation_id=None, op="x", status="http_5xx",
            error_class="X", error_msg="y",
        )


def test_log_error_swallows_unexpected_exception() -> None:
    """Even if logger itself fails, log_error stays silent."""
    from src import memory_client

    with patch("src.memory_client._get_client") as mk:
        mk.side_effect = RuntimeError("client init blew up")
        memory_client.log_error(
            source="main_app", user_id=None, session_id=None,
            correlation_id=None, op="x", status="unexpected",
            error_class="RuntimeError", error_msg="y",
        )


def test_log_error_truncates_oversize_fields() -> None:
    """error_msg / payload_excerpt must be truncated to 500 chars by client.

    Server also truncates, but client side reduces wire bytes."""
    from src import memory_client

    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: ARG001
        captured["body"] = json
        return MagicMock(status_code=200, json=lambda: {"logged": True})

    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.post.side_effect = fake_post
        mk.return_value = client
        memory_client.log_error(
            source="main_app", user_id="u", session_id="s",
            correlation_id="c", op="x", status="x",
            error_class="X", error_msg="m" * 1200,
            payload_excerpt="p" * 1200,
        )
    assert len(captured["body"]["error_msg"]) <= 500
    assert len(captured["body"]["payload_excerpt"]) <= 500


def test_log_error_swallows_logger_failure() -> None:
    """Inner try/except must catch logger.error itself raising.

    Cascade protection: even if logger blows up while reporting a failed
    POST, log_error still must not propagate.
    """
    from src import memory_client

    with patch("src.memory_client._get_client") as mk_client, \
         patch("src.memory_client.logger") as mk_logger:
        client = MagicMock()
        client.post.side_effect = RuntimeError("network")
        mk_client.return_value = client
        mk_logger.error.side_effect = RuntimeError("logger broken")

        # Must not raise despite BOTH failures
        memory_client.log_error(
            source="main_app", user_id=None, session_id=None,
            correlation_id=None, op="x", status="x",
            error_class="X", error_msg="y",
        )
