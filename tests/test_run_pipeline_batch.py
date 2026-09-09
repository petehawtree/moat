"""Tests for scripts/run_pipeline.py's batch submission path (Sprint 3.1).

Confirmed HIGH finding, judge report 20260909-131716: the batch branch
called submit_and_persist_batch()/run_batch_retrieval() without ever
passing or enforcing cost_cap_usd — a batch run could submit past the
approved spend cap with nothing to stop it. These tests prove the fix:
a preflight token-count projection caps how many tickers are ever handed
to submit_and_persist_batch(), and never calls it when it would submit
past the cap.

No real API calls: client.messages.count_tokens and submit_and_persist_batch
are stubbed.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.run_pipeline import _run_batch_submission_and_retrieval


def _fake_client(input_tokens=1000):
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=input_tokens)
    return client


def _fake_bundle(ticker):
    return {
        "reused_from": None,
        "bundle_key": f"bk_{ticker}",
        "accession": "0001-24-000001",
        "prompt_sha": f"sha_{ticker}",
        "content": [{"type": "text", "text": f"content for {ticker}"}],
    }


def test_cap_truncates_submission_before_calling_submit(tmp_path):
    """3 tickers, a cap that only fits 2 (each ticker ~$0.0615 at 1000 input
    tokens on claude-sonnet-4-6 batch pricing) -> only 2 are ever submitted."""
    conn = MagicMock()
    client = _fake_client(input_tokens=1000)

    with patch("moat.analysis.persist.find_ticker_bundle", side_effect=lambda t, m, c: _fake_bundle(t)), \
         patch("moat.analysis.persist.submit_and_persist_batch") as mock_submit:
        mock_submit.return_value = ("batch_1", {"AAPL": 1, "KO": 2})
        _run_batch_submission_and_retrieval(
            client, ["AAPL", "KO", "JPM"], "run1", conn,
            "claude-sonnet-4-6", dry_run=False,
            poll_interval=0.01, poll_timeout=0, cost_cap_usd=0.15,
        )

    assert mock_submit.called
    submitted_tickers = mock_submit.call_args[0][1]
    assert submitted_tickers == ["AAPL", "KO"]  # JPM excluded — would exceed the cap


def test_cap_of_zero_submits_nothing(tmp_path):
    conn = MagicMock()
    client = _fake_client(input_tokens=1000)

    with patch("moat.analysis.persist.find_ticker_bundle", side_effect=lambda t, m, c: _fake_bundle(t)), \
         patch("moat.analysis.persist.submit_and_persist_batch") as mock_submit:
        _run_batch_submission_and_retrieval(
            client, ["AAPL"], "run1", conn,
            "claude-sonnet-4-6", dry_run=False,
            poll_interval=0.01, poll_timeout=0, cost_cap_usd=0.0,
        )

    mock_submit.assert_not_called()


def test_cache_hits_never_count_against_the_cap(tmp_path):
    """A cache hit costs $0 and must not consume any of the projected budget
    or ever reach count_tokens/submission."""
    conn = MagicMock()
    client = _fake_client(input_tokens=1000)

    def bundle(ticker, model_id, conn_):
        if ticker == "AAPL":
            return {"reused_from": "old_run", "bundle_key": "bk", "accession": "acc",
                    "prompt_sha": "sha", "content": []}
        return _fake_bundle(ticker)

    with patch("moat.analysis.persist.find_ticker_bundle", side_effect=bundle), \
         patch("moat.analysis.persist.persist_cache_hit") as mock_cache_hit, \
         patch("moat.analysis.persist.submit_and_persist_batch") as mock_submit:
        mock_submit.return_value = ("batch_1", {"KO": 1})
        _run_batch_submission_and_retrieval(
            client, ["AAPL", "KO"], "run1", conn,
            "claude-sonnet-4-6", dry_run=False,
            poll_interval=0.01, poll_timeout=0, cost_cap_usd=1.0,
        )

    mock_cache_hit.assert_called_once()
    submitted_tickers = mock_submit.call_args[0][1]
    assert submitted_tickers == ["KO"]
    assert client.messages.count_tokens.call_count == 1  # only for KO, not AAPL


def test_dry_run_projects_cost_without_submitting(tmp_path, capsys):
    """--dry-run must still run the free count_tokens() preflight (that's
    the whole point of a cost-establishing dry run) but never call
    submit_and_persist_batch()."""
    conn = MagicMock()
    client = _fake_client(input_tokens=1000)

    with patch("moat.analysis.persist.find_ticker_bundle", side_effect=lambda t, m, c: _fake_bundle(t)), \
         patch("moat.analysis.persist.submit_and_persist_batch") as mock_submit:
        _run_batch_submission_and_retrieval(
            client, ["AAPL", "KO", "JPM"], "run1", conn,
            "claude-sonnet-4-6", dry_run=True,
            poll_interval=0.01, poll_timeout=0, cost_cap_usd=35.0,
        )

    mock_submit.assert_not_called()
    assert client.messages.count_tokens.call_count == 3  # projected for every to-submit ticker
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "projected" in out


def test_dry_run_flags_when_projected_total_exceeds_cap(tmp_path, capsys):
    conn = MagicMock()
    client = _fake_client(input_tokens=1000)  # ~$0.0615/ticker on sonnet batch pricing

    with patch("moat.analysis.persist.find_ticker_bundle", side_effect=lambda t, m, c: _fake_bundle(t)), \
         patch("moat.analysis.persist.submit_and_persist_batch") as mock_submit:
        _run_batch_submission_and_retrieval(
            client, ["AAPL", "KO", "JPM"], "run1", conn,
            "claude-sonnet-4-6", dry_run=True,
            poll_interval=0.01, poll_timeout=0, cost_cap_usd=0.1,
        )

    mock_submit.assert_not_called()
    out = capsys.readouterr().out
    assert "exceeds the cap" in out
