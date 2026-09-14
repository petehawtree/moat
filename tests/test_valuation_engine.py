"""Tests for the Owner Earnings + DCF core (Sprint 4 V2).

All expected values below are computed independently of the implementation
(by hand or with a plain calculator), not by running the code and copying
its output — same standard as §A12's regression tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.valuation.engine import dcf_scenario, owner_earnings


# ---------------------------------------------------------------------
# owner_earnings
# ---------------------------------------------------------------------

def test_owner_earnings_capital_light_company():
    """Low D&A/capex relative to net income (a software-style business).
    1,000 + 60 - 40 - 5 = 1,015 (millions).
    """
    row = {
        "net_income": 1_000_000_000,
        "depreciation_amortization": 60_000_000,
        "capex": 40_000_000,
        "working_capital_change": 5_000_000,
    }
    assert owner_earnings(row) == 1_015_000_000


def test_owner_earnings_capital_intensive_company_with_nwc_source_of_cash():
    """High D&A/capex relative to net income (a railroad/utility-style
    business), and a NEGATIVE working_capital_change — operating capital
    *decreased*, a source rather than a use of cash, so subtracting it
    ADDS to owner earnings: 500 + 400 - 450 - (-20) = 470 (millions).
    """
    row = {
        "net_income": 500_000_000,
        "depreciation_amortization": 400_000_000,
        "capex": 450_000_000,
        "working_capital_change": -20_000_000,
    }
    assert owner_earnings(row) == 470_000_000


def test_owner_earnings_real_ko_fy2025_filing():
    """Frozen real figures from Coca-Cola's FY2025 10-K (CIK 0000021344,
    accession 0001628280-26-010047, as-filed) — same "immortalize a real
    company's numbers as a regression fixture" convention as §A12's
    TKO/CRWV/ALAB dilution checks. Also the fixture that hand-verified
    working_capital_change's sign convention (see owner_earnings'
    docstring): subtracting the $7,208M figure reconstructs reported
    operating cash flow ($7,408M) to within $459M, plausible for
    unmodeled non-cash reconciling items; adding it would overshoot by
    roughly $14bn.
    13,107 + 1,050 - 2,112 - 7,208 = 4,837 (millions).
    """
    row = {
        "net_income": 13_107_000_000,
        "depreciation_amortization": 1_050_000_000,
        "capex": 2_112_000_000,
        "working_capital_change": 7_208_000_000,
    }
    assert owner_earnings(row) == 4_837_000_000


def test_owner_earnings_missing_net_income_returns_none():
    row = {"net_income": None, "depreciation_amortization": 60, "capex": 40, "working_capital_change": 5}
    assert owner_earnings(row) is None


def test_owner_earnings_missing_da_returns_none():
    """A missing D&A tag is an extraction gap, not a confirmed zero — must
    not silently compute as if D&A were 0 (§A16.2: 5/91 real companies hit
    this)."""
    row = {"net_income": 1_000, "depreciation_amortization": None, "capex": 40, "working_capital_change": 5}
    assert owner_earnings(row) is None


def test_owner_earnings_missing_capex_returns_none():
    """A missing capex figure must not default to 0 — that would make a
    capital-intensive company look like it has no maintenance spend at
    all, the same class of bug §A13 fixed for free_cash_flow."""
    row = {"net_income": 1_000, "depreciation_amortization": 60, "capex": None, "working_capital_change": 5}
    assert owner_earnings(row) is None


def test_owner_earnings_missing_nwc_defaults_to_zero():
    """Unlike D&A/capex, a missing working_capital_change IS treated as
    zero — that's the documented ingest-time decision behind
    quality_flags='nwc_unavailable_treated_as_zero' (§A16.2), not a new
    guess made here. 1,000 + 60 - 40 - 0 = 1,020."""
    row = {"net_income": 1_000, "depreciation_amortization": 60, "capex": 40, "working_capital_change": None}
    assert owner_earnings(row) == 1_020


# ---------------------------------------------------------------------
# dcf_scenario
# ---------------------------------------------------------------------

def test_dcf_scenario_flat_perpetuity_matches_textbook_formula():
    """growth_rate = terminal_growth = 0 collapses the two-stage DCF to a
    flat perpetuity, which has a closed-form answer independent of this
    implementation: PV = C / discount_rate = 100 / 0.10 = 1,000, regardless
    of how many explicit projection years the split falls on."""
    result = dcf_scenario(
        owner_earnings_series=[100.0],
        growth_rate=0.0,
        discount_rate=0.10,
        terminal_growth=0.0,
        projection_years=1,
    )
    assert result == pytest.approx(1000.0)


def test_dcf_scenario_two_year_hand_computed():
    """base = avg([100,100,100]) = 100. growth = discount = 10%, terminal = 2%.
    Year 1: 100*1.10=110, PV = 110/1.10 = 100.0
    Year 2: 110*1.10=121, PV = 121/1.10^2 = 100.0
    pv_explicit = 200.0
    terminal_value = 121*1.02/(0.10-0.02) = 123.42/0.08 = 1542.75
    pv_terminal = 1542.75/1.10^2 = 1542.75/1.21 = 1275.0
    total = 200.0 + 1275.0 = 1475.0
    """
    result = dcf_scenario(
        owner_earnings_series=[100.0, 100.0, 100.0],
        growth_rate=0.10,
        discount_rate=0.10,
        terminal_growth=0.02,
        projection_years=2,
    )
    assert result == pytest.approx(1475.0)


def test_dcf_scenario_averages_the_trailing_series_not_just_the_last_year():
    """A noisy last year shouldn't dominate the base — series [50, 150]
    averages to 100, same base (and thus same result) as a flat [100, 100]
    series under identical assumptions."""
    noisy = dcf_scenario([50.0, 150.0], growth_rate=0.0, discount_rate=0.10, terminal_growth=0.0, projection_years=1)
    flat = dcf_scenario([100.0, 100.0], growth_rate=0.0, discount_rate=0.10, terminal_growth=0.0, projection_years=1)
    assert noisy == pytest.approx(flat)


def test_dcf_scenario_terminal_growth_must_be_below_discount_rate():
    with pytest.raises(ValueError):
        dcf_scenario([100.0], growth_rate=0.0, discount_rate=0.09, terminal_growth=0.09, projection_years=1)


def test_dcf_scenario_empty_series_raises():
    with pytest.raises(ValueError):
        dcf_scenario([], growth_rate=0.05, discount_rate=0.10, terminal_growth=0.02)


def test_dcf_scenario_bear_base_bull_ordering():
    """Higher growth and higher terminal growth at the same discount rate
    should produce a strictly higher present value — the three scenarios
    should not come out equal or inverted."""
    base_series = [100.0, 110.0, 120.0]
    bear = dcf_scenario(base_series, growth_rate=0.02, discount_rate=0.095, terminal_growth=0.015)
    base = dcf_scenario(base_series, growth_rate=0.05, discount_rate=0.095, terminal_growth=0.025)
    bull = dcf_scenario(base_series, growth_rate=0.08, discount_rate=0.095, terminal_growth=0.03)
    assert bear < base < bull
