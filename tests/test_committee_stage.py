"""Tests for run_committee_stage()'s universe selection, default exclusion
(C8) and cost-cap halting (Sprint 5) — same lightweight in-memory-schema,
mocked-dependency convention as test_run_pipeline_exclude.py.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

from scripts.run_pipeline import run_committee_stage


def _conn_with(ai_tickers, valuation_tickers, passed_screen_tickers):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE pipeline_runs (run_id TEXT, started_at TEXT, status TEXT);
        CREATE TABLE quality_scores (run_id TEXT, ticker TEXT, passed_screen INTEGER);
        CREATE TABLE valuations (run_id TEXT, ticker TEXT);
        CREATE TABLE ai_analysis (ticker TEXT, is_current INTEGER);
        """
    )
    conn.execute("INSERT INTO pipeline_runs VALUES ('quality_run', '2026-01-01', 'complete')")
    conn.execute("INSERT INTO pipeline_runs VALUES ('valuation_run', '2026-01-02', 'complete')")
    for t in passed_screen_tickers:
        conn.execute("INSERT INTO quality_scores VALUES ('quality_run', ?, 1)", (t,))
    for t in valuation_tickers:
        conn.execute("INSERT INTO valuations VALUES ('valuation_run', ?)", (t,))
    for t in ai_tickers:
        conn.execute("INSERT INTO ai_analysis VALUES (?, 1)", (t,))
    conn.commit()
    return conn


def _run(conn, **kwargs):
    calls = []

    def fake_run_committee(ticker, run_id, valuation_run_id, quality_run_id, conn_, client, model_id=None, dry_run=False, cost_cap_remaining=None):
        calls.append(ticker)
        return {"ticker": ticker, "outcome": "persisted", "cost_estimate": 1.0, "overall_score": 80.0, "status": "Investigate"}

    with patch("moat.committee.committee.run_committee", side_effect=fake_run_committee), \
         patch("moat.config.ANTHROPIC_API_KEY", "dummy-key-not-used"):
        run_committee_stage(conn, "committee_run", **kwargs)
    return calls


def test_universe_is_intersection_of_ai_analysis_and_valuations():
    """AAPL/KO have both; JPM has ai_analysis but no valuation (like the
    real JPM/KO pilot-leftover case); GOOGL has a valuation but no current
    ai_analysis (like the real §A18 case) — neither should run."""
    conn = _conn_with(
        ai_tickers=["AAPL", "KO", "JPM"],
        valuation_tickers=["AAPL", "KO", "GOOGL"],
        passed_screen_tickers=["AAPL", "KO", "JPM", "GOOGL"],
    )
    calls = _run(conn, include_excluded=True)
    assert set(calls) == {"AAPL", "KO"}


def test_default_excludes_known_bad_tickers_without_a_flag():
    """C8: AMT (REIT-invalid-metrics) is excluded by default even though
    it has both a current ai_analysis and a valuation row — no --exclude
    needed."""
    conn = _conn_with(
        ai_tickers=["AAPL", "AMT"],
        valuation_tickers=["AAPL", "AMT"],
        passed_screen_tickers=["AAPL", "AMT"],
    )
    calls = _run(conn)  # include_excluded defaults to False
    assert calls == ["AAPL"]


def test_include_excluded_opts_back_in():
    conn = _conn_with(
        ai_tickers=["AAPL", "AMT"],
        valuation_tickers=["AAPL", "AMT"],
        passed_screen_tickers=["AAPL", "AMT"],
    )
    calls = _run(conn, include_excluded=True)
    assert set(calls) == {"AAPL", "AMT"}


def test_explicit_exclude_tickers_applies_even_with_include_excluded():
    conn = _conn_with(
        ai_tickers=["AAPL", "KO"],
        valuation_tickers=["AAPL", "KO"],
        passed_screen_tickers=["AAPL", "KO"],
    )
    calls = _run(conn, include_excluded=True, exclude_tickers={"KO"})
    assert calls == ["AAPL"]


def test_cost_cap_halts_before_exceeding():
    conn = _conn_with(
        ai_tickers=["AAA", "BBB", "CCC"],
        valuation_tickers=["AAA", "BBB", "CCC"],
        passed_screen_tickers=["AAA", "BBB", "CCC"],
    )
    # fake_run_committee reports $1.00 per ticker; cap of $2.50 should allow
    # exactly 2 tickers (3rd would push accumulated_cost from $2.00 to
    # $3.00, but the check happens *before* each call: $0 < $2.50 -> AAA
    # runs (-> $1.00); $1.00 < $2.50 -> BBB runs (-> $2.00); $2.00 < $2.50
    # -> CCC runs too (the cap is checked before spending, not after) ->
    # $3.00. A 4th ticker would then be halted before it starts.
    conn.execute("INSERT INTO ai_analysis VALUES ('DDD', 1)")
    conn.execute("INSERT INTO valuations VALUES ('valuation_run', 'DDD')")
    conn.execute("INSERT INTO quality_scores VALUES ('quality_run', 'DDD', 1)")
    conn.commit()
    calls = _run(conn, include_excluded=True, cost_cap_usd=2.50)
    assert calls == ["AAA", "BBB", "CCC"]


def test_no_quality_or_valuation_run_raises():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE pipeline_runs (run_id TEXT, started_at TEXT, status TEXT);
        CREATE TABLE quality_scores (run_id TEXT, ticker TEXT, passed_screen INTEGER);
        CREATE TABLE valuations (run_id TEXT, ticker TEXT);
        CREATE TABLE ai_analysis (ticker TEXT, is_current INTEGER);
        """
    )
    import pytest
    with pytest.raises(RuntimeError, match="committee needs"):
        run_committee_stage(conn, "committee_run")


def _run_with_outcomes(conn, outcomes: dict):
    """outcomes: ticker -> result dict overrides; default persisted."""
    calls = []

    def fake_run_committee(ticker, *a, **kw):
        calls.append(ticker)
        return {"ticker": ticker, "outcome": "persisted", "cost_estimate": 0.01,
                "overall_score": 60.0, "status": "Watch", **outcomes.get(ticker, {})}

    with patch("moat.committee.committee.run_committee", side_effect=fake_run_committee), \
         patch("moat.config.ANTHROPIC_API_KEY", "dummy-key-not-used"):
        run_committee_stage(conn, "committee_run", include_excluded=True)
    return calls


_TRANSIENT = {"outcome": "api_error", "transient": True, "cost_estimate": 0.0}
_DATA_ERROR = {"outcome": "api_error", "reason": "missing current ai_analysis type(s)", "cost_estimate": 0.0}


def test_one_transient_failure_skips_that_ticker_and_continues():
    tickers = ["T1", "T2", "T3", "T4"]
    conn = _conn_with(tickers, tickers, tickers)
    calls = _run_with_outcomes(conn, {"T2": _TRANSIENT})
    assert calls == tickers


def test_consecutive_transient_failures_halt_the_stage():
    tickers = ["T1", "T2", "T3", "T4", "T5", "T6"]
    conn = _conn_with(tickers, tickers, tickers)
    calls = _run_with_outcomes(conn, {t: _TRANSIENT for t in ["T2", "T3", "T4"]})
    assert calls == ["T1", "T2", "T3", "T4"]  # stops before T5


def test_data_problem_api_errors_do_not_count_toward_the_halt():
    tickers = ["T1", "T2", "T3", "T4", "T5"]
    conn = _conn_with(tickers, tickers, tickers)
    calls = _run_with_outcomes(conn, {t: _DATA_ERROR for t in ["T1", "T2", "T3", "T4"]})
    assert calls == tickers
