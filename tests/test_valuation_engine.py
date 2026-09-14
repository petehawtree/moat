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

from moat.valuation.engine import (
    annual_pe,
    dcf_scenario,
    ev_ebit,
    fcf_yield,
    margin_of_safety,
    owner_earnings,
    pe_historical_range,
)


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


# ---------------------------------------------------------------------
# margin_of_safety
# ---------------------------------------------------------------------

def test_margin_of_safety_normal_case():
    """(100 - 80) / 100 = 0.20 — the ordinary case, unchanged by the guard."""
    assert margin_of_safety(intrinsic_value_low=100.0, current_price=80.0) == pytest.approx(0.20)


def test_margin_of_safety_negative_intrinsic_value_does_not_report_positive():
    """The bug this guards: a bear case worth less than nothing must never
    come back looking safe. (-50 - 10) / -50 = 1.2 under the naive formula
    — a wildly *positive*-looking margin for the least-safe possible case.
    Must be None, not 1.2 or any other number."""
    result = margin_of_safety(intrinsic_value_low=-50.0, current_price=10.0)
    assert result is None
    assert result != pytest.approx(1.2)  # the sign-flipped value the old formula produced


def test_margin_of_safety_zero_intrinsic_value_is_none_not_a_crash():
    assert margin_of_safety(intrinsic_value_low=0.0, current_price=10.0) is None


def test_margin_of_safety_overpriced_case_is_a_real_negative_number():
    """Price above even the low end of the range is a real, meaningful
    negative margin (overpriced) — NOT the guarded undefined case. Only a
    non-positive intrinsic_value_low is undefined; a non-positive *result*
    from a positive intrinsic_value_low is ordinary and must still be a
    float. (100 - 120) / 100 = -0.20."""
    result = margin_of_safety(intrinsic_value_low=100.0, current_price=120.0)
    assert result == pytest.approx(-0.20)


def test_dcf_scenario_bear_base_bull_ordering():
    """Higher growth and higher terminal growth at the same discount rate
    should produce a strictly higher present value — the three scenarios
    should not come out equal or inverted."""
    base_series = [100.0, 110.0, 120.0]
    bear = dcf_scenario(base_series, growth_rate=0.02, discount_rate=0.095, terminal_growth=0.015)
    base = dcf_scenario(base_series, growth_rate=0.05, discount_rate=0.095, terminal_growth=0.025)
    bull = dcf_scenario(base_series, growth_rate=0.08, discount_rate=0.095, terminal_growth=0.03)
    assert bear < base < bull


# ---------------------------------------------------------------------
# fcf_yield
# ---------------------------------------------------------------------

def test_fcf_yield_normal_case():
    assert fcf_yield(free_cash_flow=50_000_000, market_cap=1_000_000_000) == pytest.approx(0.05)


def test_fcf_yield_negative_fcf_is_a_real_negative_number():
    """Burning cash produces a real negative yield — not sign-ambiguous
    (market cap, the denominator, is never negative for a real company),
    so unlike margin_of_safety/ev_ebit there's nothing to guard here."""
    assert fcf_yield(free_cash_flow=-20_000_000, market_cap=1_000_000_000) == pytest.approx(-0.02)


def test_fcf_yield_none_fcf_propagates_as_none():
    """A company whose capex was unavailable at ingest has free_cash_flow
    = None (§A13, never substituted with OCF) — must stay None here."""
    assert fcf_yield(free_cash_flow=None, market_cap=1_000_000_000) is None


def test_fcf_yield_non_positive_market_cap_is_none():
    assert fcf_yield(free_cash_flow=50, market_cap=0) is None
    assert fcf_yield(free_cash_flow=50, market_cap=-100) is None


# ---------------------------------------------------------------------
# ev_ebit
# ---------------------------------------------------------------------

def test_ev_ebit_normal_case():
    """EV = 800 + 200 - 100 = 900; 900 / 100 = 9.0."""
    result = ev_ebit(market_cap=800.0, total_debt=200.0, cash_and_equiv=100.0, operating_income=100.0)
    assert result == pytest.approx(9.0)


def test_ev_ebit_missing_debt_and_cash_default_to_zero():
    """No debt/cash reported means neither contributes to EV — the ordinary
    convention, not a guess (unlike owner_earnings' D&A/capex, which must
    never default). EV = 800 + 0 - 0 = 800; 800 / 100 = 8.0."""
    result = ev_ebit(market_cap=800.0, total_debt=None, cash_and_equiv=None, operating_income=100.0)
    assert result == pytest.approx(8.0)


def test_ev_ebit_negative_operating_income_is_none_not_a_misleading_multiple():
    """A company losing money operationally must not get a small/negative
    multiple that could read as 'cheap' — same sign-flip family as
    margin_of_safety's guard."""
    assert ev_ebit(market_cap=800.0, total_debt=200.0, cash_and_equiv=100.0, operating_income=-50.0) is None


def test_ev_ebit_zero_operating_income_is_none():
    assert ev_ebit(market_cap=800.0, total_debt=200.0, cash_and_equiv=100.0, operating_income=0.0) is None


def test_ev_ebit_non_positive_market_cap_is_none():
    assert ev_ebit(market_cap=0.0, total_debt=200.0, cash_and_equiv=100.0, operating_income=100.0) is None


# ---------------------------------------------------------------------
# annual_pe / pe_historical_range
# ---------------------------------------------------------------------

def test_annual_pe_normal_case():
    assert annual_pe(price=150.0, eps_diluted=5.0) == pytest.approx(30.0)


def test_annual_pe_loss_making_year_is_none_not_a_negative_multiple():
    """A negative P/E doesn't mean 'cheap' — it means no P/E is defined
    for that year. Letting it into a range would corrupt the min/max with
    a number that isn't comparable to the others."""
    assert annual_pe(price=150.0, eps_diluted=-2.0) is None


def test_annual_pe_zero_eps_is_none():
    assert annual_pe(price=150.0, eps_diluted=0.0) is None


def test_pe_historical_range_full_history():
    historical = [15.0, 18.0, 22.0, 20.0, 25.0, 19.0]  # 6 years, >= MIN_PE_RANGE_YEARS
    result = pe_historical_range(historical, current_pe=21.0)
    assert result == {"current": 21.0, "low": 15.0, "high": 25.0, "years_covered": 6, "low_confidence": False}


def test_pe_historical_range_filters_loss_making_years_before_computing_range():
    """None entries (loss-making years) must not become 0.0 in the range —
    they're excluded from both the min/max and the years_covered count."""
    historical = [15.0, None, 25.0, None]
    result = pe_historical_range(historical, current_pe=20.0)
    assert result["low"] == 15.0
    assert result["high"] == 25.0
    assert result["years_covered"] == 2


def test_pe_historical_range_thin_history_flagged_not_silently_truncated():
    """A recently-listed company with 2 years of usable P/E still gets a
    real range back — just flagged low_confidence, not hidden or padded
    to look like a full 5-10yr range."""
    result = pe_historical_range([18.0, 22.0], current_pe=20.0)
    assert result["low"] == 18.0
    assert result["high"] == 22.0
    assert result["years_covered"] == 2
    assert result["low_confidence"] is True


def test_pe_historical_range_zero_usable_years():
    result = pe_historical_range([None, None], current_pe=20.0)
    assert result == {"current": 20.0, "low": None, "high": None, "years_covered": 0, "low_confidence": True}
