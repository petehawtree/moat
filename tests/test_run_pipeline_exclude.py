"""Test for run_ai_analysis_stage()'s exclude_tickers filtering (Sprint 3.1).

Operational exclusion for a known-bad screen result (PRD_ADDENDUM.md §A17)
without touching the persisted quality_scores data itself.
"""
import sqlite3
from unittest.mock import MagicMock, patch

from scripts.run_pipeline import run_ai_analysis_stage


def _conn_with_passed_screen(tickers):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE pipeline_runs (
            run_id TEXT, started_at TEXT, completed_at TEXT,
            stage_reached TEXT, status TEXT
        );
        CREATE TABLE quality_scores (
            run_id TEXT, ticker TEXT, passed_screen INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, started_at, stage_reached, status) "
        "VALUES ('run0', '2026-01-01', 'quality', 'complete')"
    )
    for t in tickers:
        conn.execute(
            "INSERT INTO quality_scores (run_id, ticker, passed_screen) VALUES ('run0', ?, 1)",
            (t,),
        )
    conn.commit()
    return conn


def test_exclude_tickers_filters_before_w1_fetch():
    conn = _conn_with_passed_screen(["AAPL", "BADCO", "KO", "BADCO2"])

    w1_calls = []

    def fake_w1_fetch(ticker, conn_, offline=False):
        w1_calls.append(ticker)
        return None, f"offline: no cached filing for {ticker}"

    with patch("moat.ingest.filing_fetcher.run_for_ticker", side_effect=fake_w1_fetch), \
         patch("moat.config.ANTHROPIC_API_KEY", "dummy-key-not-used"):
        run_ai_analysis_stage(
            conn, "run1",
            offline=True, dry_run=True,
            exclude_tickers={"BADCO", "BADCO2"},
        )

    assert set(w1_calls) == {"AAPL", "KO"}


def test_no_exclude_tickers_processes_everything():
    conn = _conn_with_passed_screen(["AAPL", "KO"])
    w1_calls = []

    def fake_w1_fetch(ticker, conn_, offline=False):
        w1_calls.append(ticker)
        return None, f"offline: no cached filing for {ticker}"

    with patch("moat.ingest.filing_fetcher.run_for_ticker", side_effect=fake_w1_fetch), \
         patch("moat.config.ANTHROPIC_API_KEY", "dummy-key-not-used"):
        run_ai_analysis_stage(conn, "run1", offline=True, dry_run=True)

    assert set(w1_calls) == {"AAPL", "KO"}
