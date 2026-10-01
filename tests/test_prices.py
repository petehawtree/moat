"""Tests for price freshness (GitHub #15): staleness helpers, run_for_ticker's
empty-response handling, the valuation-stage stale-price gate, and
valuations.price_date.

Expected values are hand-computed from the 2026 calendar (2026-09-25 is a
Friday), not copied from output.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.db.connection import get_connection, init_db
from moat.ingest import prices
from moat.ingest.prices import is_stale, run_for_ticker, trading_days_behind
from moat.valuation.engine import run_valuation
from scripts.run_pipeline import run_valuation_stage

NOW = "2026-01-01T00:00:00+00:00"


# --- trading_days_behind / is_stale ----------------------------------

@pytest.mark.parametrize(
    "last_close, as_of, expected",
    [
        ("2026-09-25", date(2026, 9, 28), 0),   # Fri close, Mon: weekend only
        ("2026-09-25", date(2026, 10, 1), 3),   # Mon, Tue, Wed
        ("2026-09-25", date(2026, 10, 2), 4),   # Mon-Thu
        ("2026-09-22", date(2026, 9, 23), 0),   # yesterday's close is current
        ("2026-09-25", date(2026, 9, 25), 0),   # same day clamps at 0
        ("2026-09-25", date(2026, 9, 20), 0),   # future close clamps at 0
    ],
)
def test_trading_days_behind(last_close, as_of, expected):
    assert trading_days_behind(last_close, as_of) == expected


def test_is_stale_none_is_stale():
    assert is_stale(None, date(2026, 9, 28)) is True


def test_is_stale_boundary_three_behind_ok_four_stale():
    assert is_stale("2026-09-25", date(2026, 10, 1)) is False  # 3 behind
    assert is_stale("2026-09-25", date(2026, 10, 2)) is True   # 4 behind


def test_is_stale_respects_max_days():
    assert is_stale("2026-09-25", date(2026, 10, 1), max_days=2) is True


# --- run_for_ticker ---------------------------------------------------

def _price_row(d, close=10.0):
    return {"date": d, "close": close, "volume": 1, "source": "yfinance", "retrieved_at": NOW}


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "prices_test.db"
    init_db(db_path=db_path)
    conn = get_connection(db_path=db_path)
    conn.execute(
        "INSERT INTO companies (ticker, name, universe, is_active, added_date) VALUES ('TEST', 'Test Co', 'sp500', 1, ?)",
        (NOW,),
    )
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('run1', ?, 'running')", (NOW,))
    conn.commit()
    yield conn
    conn.close()


def _store(conn, dates, ticker="TEST"):
    for d in dates:
        conn.execute(
            "INSERT INTO price_history (ticker, date, close, source, retrieved_at) VALUES (?, ?, 10.0, 'yfinance', ?)",
            (ticker, d, NOW),
        )
    conn.commit()


def _count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM price_history").fetchone()["n"]


def test_run_for_ticker_empty_with_stored_rows_is_error(db, monkeypatch):
    _store(db, ["2026-09-24"])
    monkeypatch.setattr(prices, "fetch_price_history", lambda t, start=None: [])
    assert run_for_ticker("TEST", db) == (0, "empty yfinance response (start=2026-09-24)")
    assert _count(db) == 1


def test_run_for_ticker_empty_without_stored_rows_is_error(db, monkeypatch):
    monkeypatch.setattr(prices, "fetch_price_history", lambda t, start=None: [])
    assert run_for_ticker("TEST", db) == (0, "empty yfinance response (start=None)")
    assert _count(db) == 0


def test_run_for_ticker_only_inclusive_duplicate_is_up_to_date(db, monkeypatch):
    _store(db, ["2026-09-24"])
    monkeypatch.setattr(prices, "fetch_price_history", lambda t, start=None: [_price_row("2026-09-24")])
    assert run_for_ticker("TEST", db) == (0, None)
    assert _count(db) == 1


def test_run_for_ticker_new_rows_written_excluding_duplicate_day(db, monkeypatch):
    _store(db, ["2026-09-24"])
    fetched = [_price_row("2026-09-24"), _price_row("2026-09-25", 11.0), _price_row("2026-09-28", 12.0)]
    monkeypatch.setattr(prices, "fetch_price_history", lambda t, start=None: fetched)
    assert run_for_ticker("TEST", db) == (2, None)
    dates = [r["date"] for r in db.execute("SELECT date FROM price_history ORDER BY date")]
    assert dates == ["2026-09-24", "2026-09-25", "2026-09-28"]


# --- run_valuation_stage stale-price gate ------------------------------

def _stage_db(last_close):
    """Smallest DB the valuation stage needs, using the real schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((Path(__file__).resolve().parents[1] / "moat/db/schema.sql").read_text())
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, stage_reached, status) VALUES ('q0', ?, 'quality', 'complete')", (NOW,))
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('run1', ?, 'running')", (NOW,))
    conn.execute("INSERT INTO companies (ticker, name, universe, is_active, added_date) VALUES ('TEST', 'Test Co', 'sp500', 1, ?)", (NOW,))
    conn.execute("INSERT INTO quality_scores (run_id, ticker, passed_screen) VALUES ('q0', 'TEST', 1)")
    for fy, revenue, ni in ((2022, 1000.0, 150.0), (2023, 1100.0, 160.0), (2024, 1210.0, 175.0)):
        conn.execute(
            """
            INSERT INTO fundamentals_annual (
                ticker, fiscal_year, period_end_date, revenue, eps_diluted, net_income,
                operating_income, free_cash_flow, operating_cash_flow, capex,
                depreciation_amortization, working_capital_change, total_debt, cash_and_equiv,
                shares_diluted, source, confidence, retrieved_at
            ) VALUES ('TEST', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 5.0, 200.0, 100.0, 20.0, 'sec_edgar', 'high', ?)
            """,
            (fy, f"{fy}-12-31", revenue, ni / 20.0, ni, ni * 1.2, ni * 0.9, ni * 1.1, ni * 0.2, ni * 0.3, NOW),
        )
    for d, close in (("2022-12-30", 50.0), ("2023-12-29", 55.0), ("2024-12-31", 60.0), (last_close, 65.0)):
        conn.execute("INSERT OR REPLACE INTO price_history (ticker, date, close, source, retrieved_at) VALUES ('TEST', ?, ?, 'yfinance', ?)", (d, close, NOW))
    conn.commit()
    return conn


def _valuation_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM valuations").fetchone()["n"]


def test_valuation_stage_stale_price_raises_and_writes_nothing():
    conn = _stage_db("2026-09-22")
    # Tue 09-22 close, as_of Tue 10-06: weekdays 09-23..10-05 = 9 behind -> stale.
    with pytest.raises(RuntimeError, match=r"TEST.*2026-09-22|2026-09-22.*TEST"):
        run_valuation_stage(conn, "run1", as_of=date(2026, 10, 6))
    assert _valuation_count(conn) == 0


def test_valuation_stage_allow_stale_prices_proceeds(capsys):
    conn = _stage_db("2026-09-22")
    run_valuation_stage(conn, "run1", allow_stale_prices=True, as_of=date(2026, 10, 6))
    assert _valuation_count(conn) == 6
    assert "WARNING" in capsys.readouterr().out


def test_valuation_stage_fresh_prices_proceed():
    conn = _stage_db("2026-09-22")
    run_valuation_stage(conn, "run1", as_of=date(2026, 9, 28))  # Wed, Thu, Fri = 3 behind, not stale
    assert _valuation_count(conn) == 6


# --- valuations.price_date ---------------------------------------------

def test_price_date_persisted_on_every_row():
    conn = _stage_db("2026-09-22")
    rows_written, err = run_valuation("TEST", "run1", conn)
    assert err is None and rows_written == 6
    dates = [r["price_date"] for r in conn.execute("SELECT price_date FROM valuations WHERE ticker='TEST'")]
    assert dates == ["2026-09-22"] * 6


# --- migration ---------------------------------------------------------

def test_migration_adds_price_date_to_old_valuations_table(tmp_path):
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(db_path)
    raw.execute(
        """CREATE TABLE valuations (
            run_id TEXT NOT NULL, ticker TEXT NOT NULL, method TEXT NOT NULL, scenario TEXT,
            intrinsic_value_low REAL, intrinsic_value_high REAL, current_price REAL,
            margin_of_safety_pct REAL, key_assumptions TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, ticker, method, scenario))"""
    )
    raw.execute("INSERT INTO valuations (run_id, ticker, method, created_at) VALUES ('r', 'T', 'fcf_yield', 'x')")
    raw.commit()
    raw.close()

    migrated = init_db(db_path=db_path)
    assert "valuations.price_date" in migrated
    conn = get_connection(db_path=db_path)
    assert conn.execute("SELECT price_date FROM valuations").fetchone()["price_date"] is None  # old rows stay NULL
    conn.close()
