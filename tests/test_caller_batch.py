"""Tests for retrieve_batch() (moat/analysis/caller.py) — Sprint 3.1 item 4.

No test previously covered this at all (sprint-3-plan.md's deferred backlog,
item 4). These are offline: the Anthropic client is a stand-in object, not a
real API call.
"""
from types import SimpleNamespace

from moat.analysis.caller import CallResult, retrieve_batch


def _partial(ticker, custom_id, accession="0001-24-000001"):
    return CallResult(
        ticker=ticker,
        accession=accession,
        model_id="claude-sonnet-4-6",
        stop_reason="pending",
        content_blocks=[],
        document_map={0: 1},
        usage={},
        prompt_sha256="abc123",
        protocol_version="v1",
        cost_estimate=0.0,
        is_batch=True,
        batch_id="batch_1",
        custom_id=custom_id,
    )


class _FakeBlock:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


def _fake_message(stop_reason="end_turn", input_tokens=1000, output_tokens=200):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        content=[_FakeBlock({"type": "text", "text": "CLAIM: x"})],
    )


def _fake_client(items):
    """items: list of (custom_id, result_namespace)."""
    results = [
        SimpleNamespace(custom_id=cid, result=result) for cid, result in items
    ]
    return SimpleNamespace(
        messages=SimpleNamespace(
            batches=SimpleNamespace(results=lambda batch_id: results)
        )
    )


def test_retrieve_batch_hydrates_succeeded_item():
    partials = {"AAPL": _partial("AAPL", "AAPL_abc123")}
    client = _fake_client([
        ("AAPL_abc123", SimpleNamespace(type="succeeded", message=_fake_message())),
    ])

    hydrated = retrieve_batch(client, "batch_1", partials)

    result = hydrated["AAPL"]
    assert result.stop_reason == "end_turn"
    assert result.usage["input_tokens"] == 1000
    assert result.content_blocks == [{"type": "text", "text": "CLAIM: x"}]
    assert result.cost_estimate > 0
    # Identity carried over from the partial, unchanged.
    assert result.accession == "0001-24-000001"
    assert result.custom_id == "AAPL_abc123"


def test_retrieve_batch_maps_error_result_to_api_error():
    partials = {"MSFT": _partial("MSFT", "MSFT_def456")}
    client = _fake_client([
        ("MSFT_def456", SimpleNamespace(type="error", message=None)),
    ])

    hydrated = retrieve_batch(client, "batch_1", partials)

    assert hydrated["MSFT"].stop_reason == "api_error"
    assert hydrated["MSFT"].content_blocks == []


def test_retrieve_batch_maps_refusal():
    partials = {"NVDA": _partial("NVDA", "NVDA_ghi789")}
    client = _fake_client([
        ("NVDA_ghi789", SimpleNamespace(type="succeeded", message=_fake_message(stop_reason="refusal"))),
    ])

    hydrated = retrieve_batch(client, "batch_1", partials)

    assert hydrated["NVDA"].stop_reason == "refusal"
    assert hydrated["NVDA"].content_blocks == []


def test_retrieve_batch_leaves_missing_items_pending():
    """A custom_id not present in the results iterator (batch not fully ended
    yet, or a lookup miss) must not be silently dropped or fabricated —
    the partial is returned unchanged, still 'pending'."""
    partials = {
        "AAPL": _partial("AAPL", "AAPL_abc123"),
        "KO":   _partial("KO", "KO_xyz999"),
    }
    client = _fake_client([
        ("AAPL_abc123", SimpleNamespace(type="succeeded", message=_fake_message())),
        # KO's custom_id never appears.
    ])

    hydrated = retrieve_batch(client, "batch_1", partials)

    assert hydrated["AAPL"].stop_reason == "end_turn"
    assert hydrated["KO"].stop_reason == "pending"


def test_retrieve_batch_ignores_unknown_custom_id():
    """A result for a custom_id not in `partials` (e.g. from a different run
    sharing a batch_id namespace) must be skipped, not raise."""
    partials = {"AAPL": _partial("AAPL", "AAPL_abc123")}
    client = _fake_client([
        ("SOMETHING_ELSE", SimpleNamespace(type="succeeded", message=_fake_message())),
    ])

    hydrated = retrieve_batch(client, "batch_1", partials)

    assert hydrated["AAPL"].stop_reason == "pending"
    assert set(hydrated) == {"AAPL"}
