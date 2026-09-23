"""call_persona()'s transient-error retry (2026-09-23: a mid-stream
'overloaded_error' aborted the whole committee stage, twice)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.committee import caller
from moat.committee.caller import PersonaCallFailed, call_persona

_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _midstream_overloaded():
    """The exact shape the SDK raises for an SSE `error` event: a generic
    APIStatusError carrying the stream's own HTTP 200."""
    body = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    return anthropic.APIStatusError(str(body), response=httpx.Response(200, request=_REQ), body=body)


def _bad_request():
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    return anthropic.BadRequestError(str(body), response=httpx.Response(400, request=_REQ), body=body)


def _ok_stream():
    message = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="## VERDICT\nok")],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.return_value = message
    cm.__exit__.return_value = False
    return cm


def _failing_stream(exc):
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.side_effect = exc
    cm.__exit__.return_value = False
    return cm


@pytest.fixture(autouse=True)
def no_sleep():
    with patch.object(caller, "_sleep") as s:
        yield s


def test_midstream_overloaded_is_retried_then_succeeds(no_sleep):
    client = MagicMock()
    client.messages.stream.side_effect = [_failing_stream(_midstream_overloaded()), _ok_stream()]
    result = call_persona(client, "TEST", "quality", "context")
    assert result.stop_reason == "end_turn"
    assert client.messages.stream.call_count == 2
    no_sleep.assert_called_once_with(caller.RETRY_DELAYS_SECONDS[0])


def test_persistent_overload_raises_persona_call_failed_after_all_retries(no_sleep):
    client = MagicMock()
    client.messages.stream.side_effect = [
        _failing_stream(_midstream_overloaded()) for _ in range(len(caller.RETRY_DELAYS_SECONDS) + 1)
    ]
    with pytest.raises(PersonaCallFailed):
        call_persona(client, "TEST", "quality", "context")
    assert client.messages.stream.call_count == len(caller.RETRY_DELAYS_SECONDS) + 1
    assert no_sleep.call_count == len(caller.RETRY_DELAYS_SECONDS)


def test_connection_error_is_retried(no_sleep):
    client = MagicMock()
    client.messages.stream.side_effect = [anthropic.APIConnectionError(request=_REQ), _ok_stream()]
    assert call_persona(client, "TEST", "bear", "context").stop_reason == "end_turn"


def test_non_transient_error_propagates_without_retry(no_sleep):
    """A bad request would fail every company the same way — abort, don't retry."""
    client = MagicMock()
    client.messages.stream.side_effect = [_failing_stream(_bad_request())]
    with pytest.raises(anthropic.BadRequestError):
        call_persona(client, "TEST", "quality", "context")
    assert client.messages.stream.call_count == 1
    no_sleep.assert_not_called()


def test_midstream_connection_reset_is_retried(no_sleep):
    """A connection dropped mid-stream surfaces as a raw httpx.ReadError,
    not an SDK APIError — it aborted the resumed 2026-09-23 run."""
    client = MagicMock()
    client.messages.stream.side_effect = [
        _failing_stream(httpx.ReadError("[Errno 54] Connection reset by peer")), _ok_stream(),
    ]
    assert call_persona(client, "TEST", "valuation", "context").stop_reason == "end_turn"
    assert client.messages.stream.call_count == 2
