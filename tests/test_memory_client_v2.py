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


# ----------------------------------------------------------------------
# fetch_snapshot
# ----------------------------------------------------------------------
def test_fetch_snapshot_happy_path() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.get.return_value = MagicMock(
            status_code=200, json=lambda: {"snapshot_md": "## hello"},
        )
        mk.return_value = client
        out = memory_client.fetch_snapshot("u1")
    assert out == "## hello"


def test_fetch_snapshot_http_error_returns_empty_and_logs() -> None:
    from src import memory_client

    calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: calls.append(kw)):
        client = MagicMock()
        client.get.side_effect = httpx.ConnectError("down")
        mk.return_value = client
        out = memory_client.fetch_snapshot("u1", correlation_id="cid")
    assert out == ""
    assert calls and calls[0]["op"] == "fetch_snapshot"
    assert calls[0]["correlation_id"] == "cid"


def test_fetch_snapshot_non_200_returns_empty_and_logs() -> None:
    from src import memory_client

    calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: calls.append(kw)):
        client = MagicMock()
        client.get.return_value = MagicMock(status_code=503, text="oops")
        mk.return_value = client
        out = memory_client.fetch_snapshot("u1")
    assert out == ""
    assert calls and calls[0]["status"].startswith("http_")


# ----------------------------------------------------------------------
# fetch_active_notes
# ----------------------------------------------------------------------
def test_fetch_active_notes_happy_path() -> None:
    from src import memory_client

    rows = [
        {"id": 1, "category": "misconception", "content": "x",
         "content_hash": "h1", "created_at": 1, "updated_at": 1},
    ]
    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.get.return_value = MagicMock(
            status_code=200, json=lambda: {"notes": rows},
        )
        mk.return_value = client
        out = memory_client.fetch_active_notes("u1", "misconception")
    assert out == rows
    # content_hash must be present — reviewer needs it for hash-dedup
    assert "content_hash" in out[0]


def test_fetch_active_notes_failure_returns_empty_list() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error"):
        client = MagicMock()
        client.get.side_effect = httpx.HTTPError("x")
        mk.return_value = client
        assert memory_client.fetch_active_notes("u1", "preference") == []


# ----------------------------------------------------------------------
# fetch_neighbors
# ----------------------------------------------------------------------
def test_fetch_neighbors_happy_path() -> None:
    from src import memory_client

    n = [{"id": 5, "content": "x", "sim_score": 0.9, "age_days": 2.0}]
    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.get.return_value = MagicMock(
            status_code=200, json=lambda: {"neighbors": n},
        )
        mk.return_value = client
        out = memory_client.fetch_neighbors(
            "u1", category="misconception", query_text="z", top_k=5,
        )
    # Verify URL + params
    args, kwargs = client.get.call_args
    assert args[0] == "/notes/u1/neighbors"
    assert kwargs["params"]["category"] == "misconception"
    assert kwargs["params"]["query_text"] == "z"
    assert kwargs["params"]["top_k"] == 5
    assert out == n


def test_fetch_neighbors_failure_returns_empty() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error"):
        client = MagicMock()
        client.get.side_effect = Exception("nope")
        mk.return_value = client
        assert memory_client.fetch_neighbors("u1", "goal", "q") == []


# ----------------------------------------------------------------------
# get_note
# ----------------------------------------------------------------------
def test_get_note_happy_path() -> None:
    from src import memory_client

    body = {
        "id": 7, "category": "misconception", "content": "x",
        "content_hash": "h", "created_at": 1, "updated_at": 1,
        "user_id": "u1", "archived": False,
        "source_session_id": "s", "source_event": "submit-quiz",
    }
    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.get.return_value = MagicMock(status_code=200, json=lambda: body)
        mk.return_value = client
        out = memory_client.get_note(7)
    assert out == body
    assert client.get.call_args[0][0] == "/notes/by-id/7"


def test_get_note_404_returns_none_no_error_log() -> None:
    """404 is a normal not-found; do NOT spam op_errors with these."""
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.get.return_value = MagicMock(status_code=404)
        mk.return_value = client
        assert memory_client.get_note(7) is None
    assert log_calls == []


def test_get_note_5xx_returns_none_and_logs() -> None:
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.get.return_value = MagicMock(status_code=503, text="x")
        mk.return_value = client
        assert memory_client.get_note(7) is None
    assert log_calls and log_calls[0]["op"] == "get_note"


def test_fetch_active_notes_timeout_reports_timeout_status() -> None:
    """Timeout must be distinguishable from generic 5xx for ops triage."""
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.get.side_effect = httpx.ReadTimeout("slow")
        mk.return_value = client
        out = memory_client.fetch_active_notes("u1", "misconception")
    assert out == []
    assert log_calls and log_calls[0]["status"] == "timeout"


# ----------------------------------------------------------------------
# write_note
# ----------------------------------------------------------------------
def test_write_note_happy_path_returns_id() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"note_id": 42, "active_count_in_category": 3},
        )
        mk.return_value = client
        nid = memory_client.write_note(
            "u1", "misconception", "误把 A 当 B",
            "s1", "submit-quiz",
        )
    assert nid == 42
    body = client.post.call_args[1]["json"]
    assert body["user_id"] == "u1"
    assert body["category"] == "misconception"
    assert body["content"] == "误把 A 当 B"
    assert body["source_session_id"] == "s1"
    assert body["source_event"] == "submit-quiz"


def test_write_note_409_duplicate_returns_none_no_op_error() -> None:
    """409 = race-hash-conflict; server already wrote REJECT history.
    Client returns None and does NOT additionally log an op_error."""
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.post.return_value = MagicMock(status_code=409, text="dup")
        mk.return_value = client
        out = memory_client.write_note(
            "u1", "goal", "x", "s", "submit-quiz",
        )
    assert out is None
    assert log_calls == []


def test_write_note_422_returns_none_and_logs() -> None:
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.post.return_value = MagicMock(status_code=422, text="invalid")
        mk.return_value = client
        out = memory_client.write_note(
            "u1", "goal", "x", "s", "submit-quiz",
        )
    assert out is None
    assert log_calls and log_calls[0]["op"] == "write_note"


def test_write_note_5xx_returns_none_and_logs() -> None:
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.post.side_effect = httpx.ReadTimeout("slow")
        mk.return_value = client
        assert memory_client.write_note(
            "u1", "goal", "x", "s", "submit-quiz",
        ) is None
    assert log_calls and log_calls[0]["status"] in ("timeout", "http_5xx")


# ----------------------------------------------------------------------
# update_note
# ----------------------------------------------------------------------
def test_update_note_happy_path_returns_true() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.patch.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": 7, "category": "preference",
                          "content": "v2", "content_hash": "h",
                          "created_at": 1, "updated_at": 2},
        )
        mk.return_value = client
        ok = memory_client.update_note(7, "v2")
    assert ok is True
    assert client.patch.call_args[0][0] == "/notes/by-id/7"


def test_update_note_404_returns_false_no_log() -> None:
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.patch.return_value = MagicMock(status_code=404)
        mk.return_value = client
        ok = memory_client.update_note(7, "v2")
    assert ok is False
    assert log_calls == []


def test_update_note_409_returns_false() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error"):
        client = MagicMock()
        client.patch.return_value = MagicMock(status_code=409, text="conflict")
        mk.return_value = client
        ok = memory_client.update_note(7, "v2")
    assert ok is False


# ----------------------------------------------------------------------
# archive_note
# ----------------------------------------------------------------------
def test_archive_note_happy_path() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk:
        client = MagicMock()
        client.delete.return_value = MagicMock(
            status_code=200,
            json=lambda: {"note_id": 7, "archived": True},
        )
        mk.return_value = client
        ok = memory_client.archive_note(7)
    assert ok is True
    assert client.delete.call_args[0][0] == "/notes/by-id/7"


def test_archive_note_404_returns_false() -> None:
    from src import memory_client

    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error"):
        client = MagicMock()
        client.delete.return_value = MagicMock(status_code=404)
        mk.return_value = client
        assert memory_client.archive_note(7) is False


def test_update_note_400_logs_returns_false() -> None:
    """A non-silent 4xx (e.g. 400 bad request) is logged and returns False."""
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.patch.return_value = MagicMock(status_code=400, text="bad")
        mk.return_value = client
        ok = memory_client.update_note(7, "v2")
    assert ok is False
    assert log_calls and log_calls[0]["op"] == "update_note"
    assert log_calls[0]["status"] == "http_4xx"


def test_archive_note_400_logs_returns_false() -> None:
    """A non-silent 4xx (e.g. 400 bad request) is logged and returns False."""
    from src import memory_client

    log_calls: list[dict] = []
    with patch("src.memory_client._get_client") as mk, \
         patch.object(memory_client, "log_error", side_effect=lambda **kw: log_calls.append(kw)):
        client = MagicMock()
        client.delete.return_value = MagicMock(status_code=400, text="bad")
        mk.return_value = client
        ok = memory_client.archive_note(7)
    assert ok is False
    assert log_calls and log_calls[0]["op"] == "archive_note"
    assert log_calls[0]["status"] == "http_4xx"
