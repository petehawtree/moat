"""Offline tests for the Sprint 2 sector-relative quant screen.

Covers: percentile direction-awareness (higher-better vs lower-better
metrics), the missing-sector/too-small-peer-group fallback to floor-only,
and an end-to-end run_screen -> run_quality pass against synthetic
fundamentals in a temp DB.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.db.connection import get_connection, init_db
from moat.quality.quality_score import compute_quality_score, run_quality
from moat.screen.quant_screen import METRICS, _compute_raw_metrics, compute_sector_percentile, run_screen


def test_percentile_higher_better_ranks_top_value_100():
    peers = {"AAA": 10.0, "BBB": 20.0, "CCC": 30.0, "DDD": 40.0, "EEE": 50.0}
    pct = compute_sector_percentile("EEE", "roic", "Tech", {"Tech": peers})
    assert pct == 100.0
    pct_worst = compute_sector_percentile("AAA", "roic", "Tech", {"Tech": peers})
    assert pct_worst == 20.0


def test_percentile_lower_better_inverts_ranking():
    # 'debt' is lower-is-better: the smallest value should rank highest.
    peers = {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0, "DDD": 4.0, "EEE": 5.0}
    pct = compute_sector_percentile("AAA", "debt", "Utilities", {"Utilities": peers})
    assert pct == 100.0
    pct_worst = compute_sector_percentile("EEE", "debt", "Utilities", {"Utilities": peers})
    assert pct_worst == 20.0


def test_percentile_none_when_sector_missing():
    assert compute_sector_percentile("AAA", "roic", None, {}) is None


def test_percentile_none_when_peer_group_too_small():
    # MIN_SECTOR_PEER_GROUP is 5; 3 peers isn't enough to rank against.
    peers = {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0}
    assert compute_sector_percentile("AAA", "roic", "Energy", {"Energy": peers}) is None


def test_compute_quality_score_is_pct_of_metrics_passed():
    rows = [{"overall_pass": 1}, {"overall_pass": 1}, {"overall_pass": 0}, {"overall_pass": 0}]
    assert compute_quality_score(rows) == 50.0


def test_compute_quality_score_rejects_empty_input():
    try:
        compute_quality_score([])
        assert False, "expected ValueError"
    except ValueError:
        pass


def _fundamentals_row(ticker, fiscal_year, **overrides):
    row = {
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "period_end_date": f"{fiscal_year}-12-31",
        "revenue": 1000.0,
        "eps_diluted": 2.0,
        "net_income": 100.0,
        "operating_income": 200.0,
        "operating_margin": 0.20,
        "gross_margin": 0.50,
        "roic": 0.10,
        "roe": 0.10,
        "free_cash_flow": 100.0,
        "capex": 50.0,
        "total_debt": 100.0,
        "cash_and_equiv": 50.0,
        "shares_diluted": 100.0,
        "source": "sec_edgar",
        "confidence": "high",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(overrides)
    return row


def _insert_company(conn, ticker, sector):
    conn.execute(
        """
        INSERT INTO companies (ticker, name, sector, universe, is_active, added_date)
        VALUES (?, ?, ?, 'sp500', 1, '2026-01-01')
        """,
        (ticker, ticker, sector),
    )


def _insert_fundamentals(conn, rows):
    conn.executemany(
        """
        INSERT INTO fundamentals_annual (
            ticker, fiscal_year, period_end_date, revenue, eps_diluted, net_income,
            operating_income, operating_margin, gross_margin, roic, roe, free_cash_flow,
            capex, total_debt, cash_and_equiv, shares_diluted, source, confidence, retrieved_at
        ) VALUES (
            :ticker, :fiscal_year, :period_end_date, :revenue, :eps_diluted, :net_income,
            :operating_income, :operating_margin, :gross_margin, :roic, :roe, :free_cash_flow,
            :capex, :total_debt, :cash_and_equiv, :shares_diluted, :source, :confidence, :retrieved_at
        )
        """,
        rows,
    )


def test_share_dilution_ignores_stock_split():
    """Regression test for the WMT case found running Sprint 2 against real
    data: shares_diluted jumps 2.85B -> 8.42B between our stored FY2021 and
    FY2022 rows, an artifact of how 10-Ks restate comparative history
    around a split (see _detect_split_factors — the jump lands ~2 years
    before Walmart's real Feb 2024 split, not at it). Whatever year it
    lands in, split-adjusted the true multi-year trend (heavy buybacks
    both before and after) should read as negative CAGR (share count
    shrinking), not the ~14%/yr a naive CAGR across the jump would show.
    """
    # Plain dicts stand in for sqlite3.Row here — both support `row["col"]`.
    history = [
        _fundamentals_row("SPLITCO", 2021, shares_diluted=2_847_000_000, eps_diluted=4.75),
        _fundamentals_row("SPLITCO", 2022, shares_diluted=8_415_000_000, eps_diluted=1.62),  # 3-for-1 split
        _fundamentals_row("SPLITCO", 2026, shares_diluted=8_022_000_000, eps_diluted=2.73),  # buybacks resumed
    ]
    values, extra = _compute_raw_metrics(history)

    # Split-adjusted: 2.847B (2021, on today's post-split basis: *3 = ~8.54B)
    # down to 8.022B (2026) is a mild decline, not the ~14%/yr a naive
    # unadjusted CAGR across the split boundary would report.
    assert values["share_dilution"] is not None
    assert values["share_dilution"] < 0.0

    # EPS should be split-adjusted too: 2021's 4.75 / 3 ~= 1.58, close to
    # 2022's actual post-split 1.62 — not a cliff, so the growth floor
    # shouldn't be failed purely on the split artifact.
    assert extra["eps_first"] < 2.0  # split-adjusted basis, not the raw 4.75


def test_run_screen_end_to_end(tmp_path):
    db_path = tmp_path / "test_moat.db"
    init_db(db_path=db_path)
    conn = get_connection(db_path=db_path)

    # 5 Tech peers so sector-relative comparisons are actually computable
    # (MIN_SECTOR_PEER_GROUP = 5). STRONG is the best performer on every
    # metric; WEAK has negative FCF/ROIC (fails the absolute floor outright).
    sector = "Information Technology"
    tickers = ["STRONG", "MID1", "MID2", "MID3", "WEAK"]
    for t in tickers:
        _insert_company(conn, t, sector)
    # One extra company with no sector at all — exercises the floor-only fallback.
    _insert_company(conn, "NOSECTOR", None)

    rows = []
    for i, t in enumerate(tickers):
        roic = 0.30 - i * 0.05  # STRONG=0.30 ... WEAK=0.10, still all >0 except override below
        rows.append(_fundamentals_row(t, 2023, roic=roic, roe=roic))
    # Make WEAK fail the absolute floor outright (negative FCF).
    weak_row = next(r for r in rows if r["ticker"] == "WEAK")
    weak_row["free_cash_flow"] = -50.0
    rows.append(_fundamentals_row("NOSECTOR", 2023, roic=0.25, roe=0.25))
    _insert_fundamentals(conn, rows)
    conn.commit()

    run_id = "20260101T000000Z"
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, started_at, status) VALUES (?, ?, 'running')",
        (run_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    run_screen(run_id, conn)

    n_rows = conn.execute("SELECT COUNT(*) AS n FROM quant_scores WHERE run_id = ?", (run_id,)).fetchone()["n"]
    assert n_rows == len(tickers + ["NOSECTOR"]) * len(METRICS)

    strong_roic = conn.execute(
        "SELECT * FROM quant_scores WHERE run_id = ? AND ticker = 'STRONG' AND metric = 'roic'", (run_id,)
    ).fetchone()
    assert strong_roic["absolute_floor_pass"] == 1
    assert strong_roic["sector_percentile"] == 100.0  # best in its 5-company peer group
    assert strong_roic["sector_relative_pass"] == 1
    assert strong_roic["overall_pass"] == 1

    weak_fcf = conn.execute(
        "SELECT * FROM quant_scores WHERE run_id = ? AND ticker = 'WEAK' AND metric = 'free_cash_flow'", (run_id,)
    ).fetchone()
    assert weak_fcf["absolute_floor_pass"] == 0  # negative FCF margin, disqualifying regardless of sector
    assert weak_fcf["overall_pass"] == 0

    nosector_roic = conn.execute(
        "SELECT * FROM quant_scores WHERE run_id = ? AND ticker = 'NOSECTOR' AND metric = 'roic'", (run_id,)
    ).fetchone()
    assert nosector_roic["sector_percentile"] is None
    assert nosector_roic["sector_relative_pass"] is None
    assert nosector_roic["overall_pass"] == 1  # falls back to floor-only: roic > 0 passes

    run_quality(run_id, conn)
    strong_quality = conn.execute(
        "SELECT * FROM quality_scores WHERE run_id = ? AND ticker = 'STRONG'", (run_id,)
    ).fetchone()
    weak_quality = conn.execute(
        "SELECT * FROM quality_scores WHERE run_id = ? AND ticker = 'WEAK'", (run_id,)
    ).fetchone()
    assert strong_quality["composite_score"] > weak_quality["composite_score"]

    conn.close()
