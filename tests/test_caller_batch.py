"""Tests for retrieve_batch() and submit_batch() (moat/analysis/caller.py) —
Sprint 3.1 item 4.

No test previously covered either at all (sprint-3-plan.md's deferred
backlog, item 4). These are offline: the Anthropic client is a stand-in
object, not a real API call.
"""
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from moat.analysis.caller import CallResult, retrieve_batch, submit_batch
from tests.test_analysis_persist import _insert_doc, _make_db


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


# ---------------------------------------------------------------------------
# submit_batch — real request construction against the installed SDK
#
# Every batch test before this mocked submit_batch (or caller.submit_batch)
# itself, so nothing ever exercised its actual request-building against the
# real anthropic package -- which is exactly how a real SDK-version mismatch
# (anthropic.types.MessageCreateParamsNonStreaming, removed from the
# top-level anthropic.types module by the installed SDK, 0.121.0) went
# uncaught until item 6's first real run. This mocks only the network call
# (client.messages.batches.create); everything up to it is the real code.
# ---------------------------------------------------------------------------

def test_submit_batch_builds_real_request_shape(tmp_path):
    conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO filings (accession_number, ticker, form_type, filing_date, "
        "period_of_report, local_path) VALUES ('0001-24-000001','TST','10-K','2024-02-15','2023-12-31','x.htm')"
    )
    for fdi, section_id in enumerate(("item_1", "item_1a", "item_7"), start=1):
        path = tmp_path / f"{section_id}.txt"
        path.write_text(f"{section_id} content, plenty of words to be non-trivial.")
        _insert_doc(
            conn, fdi, hashlib.sha256(path.read_bytes()).hexdigest(), path,
            section_id=section_id, accession="0001-24-000001",
        )
    conn.commit()

    client = MagicMock()
    client.messages.batches.create.return_value = SimpleNamespace(id="batch_real_1")

    with patch("moat.analysis.caller.NORM_VERSION", "v1"):
        batch_id, partials = submit_batch(client, ["TST"], conn)

    assert batch_id == "batch_real_1"
    assert set(partials) == {"TST"}

    # This is the real request dict submit_batch() built and handed to the
    # SDK -- the AttributeError happened building exactly this, before the
    # network call. Confirms the shape client.messages.batches.create's
    # real signature expects: a list of {"custom_id", "params"} dicts,
    # params carrying model/max_tokens/system/messages directly (no SDK
    # TypedDict construction needed at runtime).
    sent_requests = list(client.messages.batches.create.call_args.kwargs["requests"])
    assert len(sent_requests) == 1
    req = sent_requests[0]
    assert set(req) == {"custom_id", "params"}
    assert req["custom_id"] == partials["TST"].custom_id
    params = req["params"]
    assert params["model"]
    assert params["max_tokens"] == 64_000
    assert isinstance(params["system"], str)
    assert len(params["messages"]) == 1
    assert params["messages"][0]["role"] == "user"
    assert isinstance(params["messages"][0]["content"], list)  # the document+text content blocks
