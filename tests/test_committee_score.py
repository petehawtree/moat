"""Tests for the PRD §8 weighted score, status assignment, and the
data-confidence rollup (Sprint 5). Expected values computed by hand, same
standard as §A12/test_valuation_engine.py's regression tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.committee.committee import (
    assign_status,
    compute_overall_score,
    roll_up_data_confidence,
)


# ---------------------------------------------------------------------
# compute_overall_score
# ---------------------------------------------------------------------

def test_compute_overall_score_hand_computed_weighted_sum():
    """0.25*80 + 0.20*70 + 0.15*60 + 0.10*50 + 0.25*90 + 0.05*40
    = 20 + 14 + 9 + 5 + 22.5 + 2 = 72.5
    """
    scores = {
        "business_quality_score": 80,
        "competitive_moat_score": 70,
        "financial_strength_score": 60,
        "management_score": 50,
        "valuation_score": 90,
        "risk_score": 40,
    }
    assert compute_overall_score(scores) == pytest.approx(72.5)


def test_compute_overall_score_all_100_is_100():
    scores = {k: 100 for k in (
        "business_quality_score", "competitive_moat_score", "financial_strength_score",
        "management_score", "valuation_score", "risk_score",
    )}
    assert compute_overall_score(scores) == pytest.approx(100.0)


def test_compute_overall_score_missing_component_raises():
    scores = {
        "business_quality_score": 80,
        "competitive_moat_score": 70,
        "financial_strength_score": 60,
        "management_score": 50,
        "valuation_score": 90,
        # risk_score missing
    }
    with pytest.raises(ValueError, match="risk_score"):
        compute_overall_score(scores)


# ---------------------------------------------------------------------
# assign_status
# ---------------------------------------------------------------------

def test_assign_status_high_score_low_severity_investigates():
    assert assign_status(85.0, "low") == "Investigate"


def test_assign_status_high_score_medium_severity_still_investigates():
    assert assign_status(75.0, "medium") == "Investigate"


def test_assign_status_high_score_high_severity_capped_to_watch():
    """A severe bear case caps an otherwise-Investigate score to Watch —
    never independently Rejects a high-scoring company (PRD §7: a
    consolidated assessment, not one persona's unilateral veto)."""
    assert assign_status(90.0, "high") == "Watch"


def test_assign_status_mid_score_watches_regardless_of_severity():
    assert assign_status(60.0, "low") == "Watch"
    assert assign_status(60.0, "high") == "Watch"


def test_assign_status_low_score_rejects_regardless_of_severity():
    """A severe bear case never rescues a low score either."""
    assert assign_status(30.0, "low") == "Reject"
    assert assign_status(30.0, "high") == "Reject"


def test_assign_status_boundary_values_use_threshold_as_pass():
    assert assign_status(70.0, "low") == "Investigate"   # >= INVESTIGATE_THRESHOLD
    assert assign_status(69.99, "low") == "Watch"
    assert assign_status(50.0, "low") == "Watch"          # >= WATCH_THRESHOLD
    assert assign_status(49.99, "low") == "Reject"


def test_assign_status_invalid_severity_raises():
    with pytest.raises(ValueError, match="bear_case_severity"):
        assign_status(80.0, "extreme")


# ---------------------------------------------------------------------
# roll_up_data_confidence
# ---------------------------------------------------------------------

@pytest.fixture
def confidence_db(tmp_path):
    from moat.db.connection import get_connection, init_db

    db_path = tmp_path / "confidence_test.db"
    init_db(db_path=db_path)
    conn = get_connection(db_path=db_path)
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies (ticker, name, universe, is_active, added_date) VALUES (?, ?, ?, 1, ?)",
        ("TEST", "Test Co", "sp500", now),
    )
    conn.commit()
    yield conn
    conn.close()


def _insert_fundamentals_row(conn, ticker, fiscal_year, confidence):
    conn.execute(
        "INSERT INTO fundamentals_annual (ticker, fiscal_year, source, confidence, retrieved_at) "
        "VALUES (?, ?, 'sec_edgar', ?, '2026-01-01T00:00:00+00:00')",
        (ticker, fiscal_year, confidence),
    )
    conn.commit()


def test_roll_up_data_confidence_all_high_is_high(confidence_db):
    conn = confidence_db
    for fy in (2022, 2023, 2024):
        _insert_fundamentals_row(conn, "TEST", fy, "high")
    assert roll_up_data_confidence("TEST", conn) == "high"


def test_roll_up_data_confidence_one_low_row_drags_it_down(confidence_db):
    """Worst tier across ALL rows, not the latest year alone — the DCF/
    screen both draw on a multi-year trailing window (§A16)."""
    conn = confidence_db
    _insert_fundamentals_row(conn, "TEST", 2022, "high")
    _insert_fundamentals_row(conn, "TEST", 2023, "high")
    _insert_fundamentals_row(conn, "TEST", 2024, "low")
    assert roll_up_data_confidence("TEST", conn) == "low"


def test_roll_up_data_confidence_medium_beats_high_but_not_low(confidence_db):
    conn = confidence_db
    _insert_fundamentals_row(conn, "TEST", 2022, "high")
    _insert_fundamentals_row(conn, "TEST", 2023, "medium")
    assert roll_up_data_confidence("TEST", conn) == "medium"


def test_roll_up_data_confidence_no_rows_is_worst_case_not_high(confidence_db):
    conn = confidence_db
    assert roll_up_data_confidence("NOROWS", conn) == "low"
