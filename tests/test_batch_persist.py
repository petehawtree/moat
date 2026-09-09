"""Tests for the batch workflow's persistence half (Sprint 3.1, item 2/4).

submit_batch() (caller.py) was previously a dead end: nothing persisted its
partial results or retrieved them. These tests exercise the fix end-to-end
at the DB layer — submit_and_persist_batch()'s pending frames,
run_batch_retrieval()'s resumable retrieval, and persist_result()'s new
api_error case — using the same minimal-schema fixture test_analysis_persist
already established, plus a fake batches client (no real API calls).
"""
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from moat.analysis.caller import CallResult
from moat.analysis.persist import (
    _load_pending_batch,
    _write_pending_batch_attempts,
    find_ticker_bundle,
    persist_result,
    run_batch_retrieval,
    submit_and_persist_batch,
)
from tests.test_analysis_persist import _insert_doc, _make_db


def _partial(ticker, custom_id, accession="0000-00", batch_id="batch_1"):
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
        batch_id=batch_id,
        custom_id=custom_id,
    )


# ---------------------------------------------------------------------------
# find_ticker_bundle — amendment fallback (confirmed HIGH finding, judge
# report 20260909-131716: the batch precheck had no fallback at all, unlike
# run_analysis(); both now share _resolve_sections_with_amendment_fallback())
# ---------------------------------------------------------------------------

def test_find_ticker_bundle_falls_back_to_original_10k(tmp_path):
    conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO filings (accession_number, ticker, form_type, filing_date, "
        "period_of_report, local_path) VALUES (?,?,?,?,?,?)",
        ("0000-02-000002", "TST", "10-K/A", "2024-04-20", "2023-12-31", "amendment.htm"),
    )
    conn.execute(
        "INSERT INTO filings (accession_number, ticker, form_type, filing_date, "
        "period_of_report, local_path) VALUES (?,?,?,?,?,?)",
        ("0000-01-000001", "TST", "10-K", "2024-02-15", "2023-12-31", "original.htm"),
    )
    conn.commit()

    amendment_path = tmp_path / "amendment.htm"
    amendment_path.write_text("<html><body>Part III only, nothing else here.</body></html>")
    conn.execute(
        "UPDATE filings SET local_path = ? WHERE accession_number = '0000-02-000002'",
        (str(amendment_path),),
    )
    conn.commit()

    for fdi, section_id in enumerate(("item_1", "item_1a", "item_7"), start=1):
        path = tmp_path / f"{section_id}.txt"
        path.write_text(f"{section_id} content, plenty of words to be non-trivial.")
        _insert_doc(
            conn, fdi, hashlib.sha256(path.read_bytes()).hexdigest(), path,
            section_id=section_id, accession="0000-01-000001",
        )
    conn.commit()

    with patch("moat.analysis.persist.NORM_VERSION", "v1"), \
         patch("moat.analysis.caller.NORM_VERSION", "v1"):
        info = find_ticker_bundle("TST", "claude-sonnet-4-6", conn)

    assert "error" not in info
    assert info["accession"] == "0000-01-000001"  # fell back to the original, not the amendment
    assert "content" in info


def test_find_ticker_bundle_reports_extraction_failed_with_no_fallback_available(tmp_path):
    conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO filings (accession_number, ticker, form_type, filing_date, "
        "period_of_report, local_path) VALUES (?,?,?,?,?,?)",
        ("0000-02-000002", "TST", "10-K/A", "2024-04-20", "2023-12-31", str(tmp_path / "amendment.htm")),
    )
    (tmp_path / "amendment.htm").write_text("<html><body>Part III only.</body></html>")
    conn.commit()

    with patch("moat.analysis.persist.NORM_VERSION", "v1"), \
         patch("moat.analysis.caller.NORM_VERSION", "v1"):
        info = find_ticker_bundle("TST", "claude-sonnet-4-6", conn)

    assert info["error"].startswith("extraction_failed:")


# ---------------------------------------------------------------------------
# _write_pending_batch_attempts / submit_and_persist_batch
# ---------------------------------------------------------------------------

def test_write_pending_batch_attempts_writes_one_row_per_ticker(tmp_path):
    conn = _make_db(tmp_path)
    partials = {
        "AAPL": _partial("AAPL", "AAPL_abc123"),
        "KO":   _partial("KO", "KO_def456"),
    }
    attempt_ids = _write_pending_batch_attempts("run1", partials, conn)

    assert set(attempt_ids) == {"AAPL", "KO"}
    rows = conn.execute("SELECT ticker, outcome, batch_id, custom_id FROM analysis_attempts").fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r["outcome"] == "pending"
        assert r["batch_id"] == "batch_1"


def test_submit_and_persist_batch_persists_before_returning(tmp_path):
    conn = _make_db(tmp_path)

    fake_partials = {"AAPL": _partial("AAPL", "AAPL_abc123")}
    with patch("moat.analysis.caller.submit_batch", return_value=("batch_1", fake_partials)):
        batch_id, attempt_ids = submit_and_persist_batch(
            client=object(), tickers=["AAPL"], conn=conn, run_id="run1",
        )

    assert batch_id == "batch_1"
    row = conn.execute("SELECT outcome, ticker FROM analysis_attempts WHERE ticker = 'AAPL'").fetchone()
    assert row["outcome"] == "pending"


# ---------------------------------------------------------------------------
# _load_pending_batch
# ---------------------------------------------------------------------------

def test_load_pending_batch_reconstructs_call_results(tmp_path):
    conn = _make_db(tmp_path)
    partials = {"AAPL": _partial("AAPL", "AAPL_abc123", accession="0001-24-000001")}
    _write_pending_batch_attempts("run1", partials, conn)

    loaded = _load_pending_batch(conn, "batch_1")

    assert set(loaded) == {"AAPL"}
    r = loaded["AAPL"]
    assert r.custom_id == "AAPL_abc123"
    assert r.accession == "0001-24-000001"
    assert r.stop_reason == "pending"
    assert r.document_map == {0: 1}


def test_load_pending_batch_excludes_resolved_rows(tmp_path):
    """Only outcome='pending' rows are a batch's remaining work."""
    conn = _make_db(tmp_path)
    partials = {
        "AAPL": _partial("AAPL", "AAPL_abc123"),
        "KO":   _partial("KO", "KO_def456"),
    }
    _write_pending_batch_attempts("run1", partials, conn)
    conn.execute("UPDATE analysis_attempts SET outcome = 'persisted' WHERE ticker = 'AAPL'")
    conn.commit()

    loaded = _load_pending_batch(conn, "batch_1")
    assert set(loaded) == {"KO"}


# ---------------------------------------------------------------------------
# run_batch_retrieval — full round trip + resumability
# ---------------------------------------------------------------------------

def _fake_retrieve_batch_factory(hydrated_by_ticker):
    def _fake(client, batch_id, partials, model_id="claude-sonnet-4-6"):
        return {t: hydrated_by_ticker[t] for t in partials}
    return _fake


class TestRunBatchRetrieval:
    def test_round_trip_persists_and_clears_pending(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_doc(conn, 1, "doc_sha", tmp_path / "doc.txt", accession="0000-00")
        (tmp_path / "doc.txt").write_text("hello world extra text for context")

        partials = {"AAPL": _partial("AAPL", "AAPL_abc123")}
        _write_pending_batch_attempts("run1", partials, conn)

        # A batch item that resolved with a real (albeit minimal) response —
        # missing three of the four required sections, so it lands as
        # validation_failed rather than persisted; either way it must leave
        # 'pending' behind.
        hydrated = {
            "AAPL": CallResult(
                ticker="AAPL", accession="0000-00", model_id="claude-sonnet-4-6",
                stop_reason="end_turn",
                content_blocks=[{"type": "text", "text": "ok", "citations": None}],
                document_map={0: 1}, usage={"input_tokens": 100, "output_tokens": 50},
                prompt_sha256="abc123", protocol_version="v1", cost_estimate=0.01,
                is_batch=True, batch_id="batch_1", custom_id="AAPL_abc123",
            ),
        }

        with patch("moat.analysis.persist.NORM_VERSION", "v1"), \
             patch("moat.analysis.caller.retrieve_batch", side_effect=_fake_retrieve_batch_factory(hydrated)):
            summary = run_batch_retrieval(object(), "batch_1", "run1", conn)

        assert summary["still_pending"] == 0
        assert summary["outcomes"] == {"validation_failed": 1}

        row = conn.execute(
            "SELECT outcome, custom_id FROM analysis_attempts WHERE ticker = 'AAPL'"
        ).fetchone()
        assert row["outcome"] == "validation_failed"
        assert row["custom_id"] == "AAPL_abc123"  # UPSERTed in place, not a second row
        assert conn.execute("SELECT COUNT(*) FROM analysis_attempts").fetchone()[0] == 1

        # Resumable: a second call finds nothing left pending for this batch.
        with patch("moat.analysis.persist.NORM_VERSION", "v1"), \
             patch("moat.analysis.caller.retrieve_batch", side_effect=_fake_retrieve_batch_factory(hydrated)):
            summary2 = run_batch_retrieval(object(), "batch_1", "run1", conn)
        assert summary2 == {"batch_id": "batch_1", "outcomes": {}, "still_pending": 0}

    def test_api_error_item_persists_as_api_error_not_validation_failed(self, tmp_path):
        conn = _make_db(tmp_path)
        partials = {"KO": _partial("KO", "KO_def456")}
        _write_pending_batch_attempts("run1", partials, conn)

        hydrated = {
            "KO": CallResult(
                ticker="KO", accession="0000-00", model_id="claude-sonnet-4-6",
                stop_reason="api_error", content_blocks=[], document_map={0: 1},
                usage={}, prompt_sha256="abc123", protocol_version="v1",
                cost_estimate=0.0, is_batch=True, batch_id="batch_1", custom_id="KO_def456",
            ),
        }

        with patch("moat.analysis.persist.NORM_VERSION", "v1"), \
             patch("moat.analysis.caller.retrieve_batch", side_effect=_fake_retrieve_batch_factory(hydrated)):
            summary = run_batch_retrieval(object(), "batch_1", "run1", conn)

        assert summary["outcomes"] == {"api_error": 1}
        row = conn.execute("SELECT outcome, failure_reason FROM analysis_attempts WHERE ticker = 'KO'").fetchone()
        assert row["outcome"] == "api_error"
        assert row["failure_reason"] == "batch_item_error"

    def test_item_still_pending_is_left_alone(self, tmp_path):
        """A custom_id the batch hasn't resolved yet must not be persisted as
        anything — it stays 'pending' for a later call."""
        conn = _make_db(tmp_path)
        partials = {"NVDA": _partial("NVDA", "NVDA_ghi789")}
        _write_pending_batch_attempts("run1", partials, conn)

        hydrated = {"NVDA": partials["NVDA"]}  # unchanged: still stop_reason="pending"

        with patch("moat.analysis.persist.NORM_VERSION", "v1"), \
             patch("moat.analysis.caller.retrieve_batch", side_effect=_fake_retrieve_batch_factory(hydrated)):
            summary = run_batch_retrieval(object(), "batch_1", "run1", conn)

        assert summary == {"batch_id": "batch_1", "outcomes": {}, "still_pending": 1}
        row = conn.execute("SELECT outcome FROM analysis_attempts WHERE ticker = 'NVDA'").fetchone()
        assert row["outcome"] == "pending"

    def test_no_pending_rows_is_a_no_op(self, tmp_path):
        conn = _make_db(tmp_path)
        summary = run_batch_retrieval(object(), "nonexistent_batch", "run1", conn)
        assert summary == {"batch_id": "nonexistent_batch", "outcomes": {}, "still_pending": 0}


# ---------------------------------------------------------------------------
# persist_result — batch item error (Sprint 3.1 addition to an existing fn)
# ---------------------------------------------------------------------------

def test_persist_result_api_error_writes_only_attempt(tmp_path):
    conn = _make_db(tmp_path)
    result = CallResult(
        ticker="KO", accession="0000-00", model_id="claude-sonnet-4-6",
        stop_reason="api_error", content_blocks=[], document_map={0: 1},
        usage={}, prompt_sha256="abc123", protocol_version="v1",
        cost_estimate=0.0, is_batch=True, batch_id="batch_1", custom_id="KO_def456",
    )
    with patch("moat.analysis.persist.NORM_VERSION", "v1"):
        attempt_id = persist_result(result, None, "run1", conn)

    assert conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0] == 0
    attempt = conn.execute("SELECT * FROM analysis_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
    assert attempt["outcome"] == "api_error"
    assert attempt["failure_reason"] == "batch_item_error"
