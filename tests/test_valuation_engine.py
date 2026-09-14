"""Tests for the Owner Earnings + DCF core (Sprint 4 V2).

All expected values below are computed independently of the implementation
(by hand or with a plain calculator), not by running the code and copying
its output — same standard as §A12's regression tests.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.valuation.engine import (
    annual_pe,
    dcf_scenario,
    ev_ebit,
    fcf_yield,
    historical_revenue_cagr,
    margin_of_safety,
    owner_earnings,
    pe_historical_range,
    run_valuation,
    scenario_growth_rates,
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


def test_ev_ebit_missing_cash_defaults_to_zero():
    """No cash reported means it contributes nothing to EV — a near-
    universal single XBRL tag with no documented extraction gap, unlike
    total_debt below. EV = 800 + 200 - 0 = 1000; 1000 / 100 = 10.0."""
    result = ev_ebit(market_cap=800.0, total_debt=200.0, cash_and_equiv=None, operating_income=100.0)
    assert result == pytest.approx(10.0)


def test_ev_ebit_missing_debt_is_none_not_defaulted_to_zero():
    """Found by a judge review of this exact function: total_debt IS NULL
    is a known, confirmed extraction gap for 18/91 real companies (§A17),
    not a reliable signal of zero debt — treating it as zero would be the
    same silent-wrong-number substitution owner_earnings() already refuses
    for missing capex/D&A. AMT is the real, confirmed case: stored
    total_debt $3.39bn vs. SEC's real ~$37.2bn — defaulting to 0 for a
    *fully* missing figure would have been even more wrong, not less."""
    assert ev_ebit(market_cap=800.0, total_debt=None, cash_and_equiv=100.0, operating_income=100.0) is None


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


# ---------------------------------------------------------------------
# historical_revenue_cagr / scenario_growth_rates
# ---------------------------------------------------------------------

def test_historical_revenue_cagr_hand_computed():
    """100 -> 133.1 over 3 years: (133.1/100)^(1/3) - 1 = 1.1 - 1 = 0.10 exactly
    (1.1^3 = 1.331)."""
    rows = [
        {"fiscal_year": 2020, "revenue": 100.0},
        {"fiscal_year": 2021, "revenue": 110.0},
        {"fiscal_year": 2023, "revenue": 133.1},
    ]
    assert historical_revenue_cagr(rows) == pytest.approx(0.10)


def test_historical_revenue_cagr_uses_first_and_last_not_a_trend_fit():
    """Documented as endpoint CAGR, not a regression — a noisy middle year
    must not change the result."""
    steady = historical_revenue_cagr([{"fiscal_year": 2020, "revenue": 100.0}, {"fiscal_year": 2022, "revenue": 121.0}])
    noisy_middle = historical_revenue_cagr(
        [{"fiscal_year": 2020, "revenue": 100.0}, {"fiscal_year": 2021, "revenue": 9000.0}, {"fiscal_year": 2022, "revenue": 121.0}]
    )
    assert steady == pytest.approx(noisy_middle)


def test_historical_revenue_cagr_needs_two_points():
    assert historical_revenue_cagr([{"fiscal_year": 2020, "revenue": 100.0}]) is None
    assert historical_revenue_cagr([]) is None


def test_historical_revenue_cagr_ignores_missing_revenue_rows():
    rows = [
        {"fiscal_year": 2020, "revenue": 100.0},
        {"fiscal_year": 2021, "revenue": None},
        {"fiscal_year": 2022, "revenue": 121.0},
    ]
    assert historical_revenue_cagr(rows) == pytest.approx(0.10)


def test_historical_revenue_cagr_negative_ending_revenue_does_not_crash():
    """Regression for a judge-found bug: a negative revenue at the *last*
    point used to pass the old truthy filter and reach
    (negative / positive) ** fractional unguarded — a complex number in
    Python, which then raised TypeError the moment scenario_growth_rates()
    compared it against GROWTH_RATE_FLOOR. Both endpoints are now filtered
    to strictly positive revenue up front; a negative/zero ending point is
    excluded like a missing one, not fed to the math.
    """
    rows = [
        {"fiscal_year": 2020, "revenue": 100.0},
        {"fiscal_year": 2023, "revenue": -10.0},
    ]
    assert historical_revenue_cagr(rows) is None  # fewer than 2 usable points left
    assert scenario_growth_rates(rows) == {"bear": pytest.approx(-0.04), "base": pytest.approx(0.0), "bull": pytest.approx(0.04)}


def test_historical_revenue_cagr_zero_revenue_point_excluded():
    rows = [
        {"fiscal_year": 2020, "revenue": 100.0},
        {"fiscal_year": 2021, "revenue": 0.0},
        {"fiscal_year": 2022, "revenue": 121.0},
    ]
    assert historical_revenue_cagr(rows) == pytest.approx(0.10)  # same as the missing-row case


def test_scenario_growth_rates_hand_computed():
    """cagr=0.10 (from 100 -> 121 over 2 years): bear = 0.10 - 0.04 = 0.06,
    base = 0.10, bull = 0.10 + 0.04 = 0.14."""
    rows = [{"fiscal_year": 2020, "revenue": 100.0}, {"fiscal_year": 2022, "revenue": 121.0}]
    result = scenario_growth_rates(rows)
    assert result["bear"] == pytest.approx(0.06)
    assert result["base"] == pytest.approx(0.10)
    assert result["bull"] == pytest.approx(0.14)


def test_scenario_growth_rates_no_history_defaults_to_flat_zero():
    result = scenario_growth_rates([])
    assert result == {"bear": pytest.approx(-0.04), "base": pytest.approx(0.0), "bull": pytest.approx(0.04)}


def test_scenario_growth_rates_shrinking_company_bull_is_less_negative_than_bear():
    """The additive design's whole point: for a shrinking company (negative
    CAGR), bull must be the LEAST negative scenario, not the most — a
    multiplicative spread gets this backwards (multiplying a negative
    number by a bull multiplier > 1 makes it more negative)."""
    rows = [{"fiscal_year": 2020, "revenue": 100.0}, {"fiscal_year": 2022, "revenue": 81.0}]  # -10%/yr
    result = scenario_growth_rates(rows)
    assert result["bear"] < result["base"] < result["bull"]


def test_scenario_growth_rates_extreme_cagr_is_capped_both_before_and_after_spread():
    """A 50% CAGR is capped to GROWTH_RATE_CEILING (0.20) as the base rate;
    bull (base + 0.04 = 0.24) must be re-capped back down to 0.20, not
    allowed to exceed the ceiling the base rate was just clamped to."""
    rows = [{"fiscal_year": 2020, "revenue": 100.0}, {"fiscal_year": 2021, "revenue": 150.0}]  # +50%/yr
    result = scenario_growth_rates(rows)
    assert result["base"] == pytest.approx(0.20)
    assert result["bull"] == pytest.approx(0.20)
    assert result["bear"] == pytest.approx(0.16)


# ---------------------------------------------------------------------
# run_valuation (integration: real schema, temp DB)
# ---------------------------------------------------------------------

@pytest.fixture
def valuation_db(tmp_path):
    from moat.db.connection import get_connection, init_db

    db_path = tmp_path / "valuation_test.db"
    init_db(db_path=db_path)
    conn = get_connection(db_path=db_path)
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies (ticker, name, universe, is_active, added_date) VALUES (?, ?, ?, 1, ?)",
        ("TEST", "Test Co", "sp500", now),
    )
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, started_at, status) VALUES (?, ?, 'running')",
        ("run1", now),
    )
    for fy, revenue, ni in ((2022, 1000.0, 150.0), (2023, 1100.0, 160.0), (2024, 1210.0, 175.0)):
        conn.execute(
            """
            INSERT INTO fundamentals_annual (
                ticker, fiscal_year, period_end_date, revenue, eps_diluted, net_income,
                operating_income, free_cash_flow, operating_cash_flow, capex,
                depreciation_amortization, working_capital_change, total_debt, cash_and_equiv,
                shares_diluted, source, confidence, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sec_edgar', 'high', ?)
            """,
            (
                "TEST", fy, f"{fy}-12-31", revenue, ni / 20.0, ni,
                ni * 1.2, ni * 0.9, ni * 1.1, ni * 0.2,
                ni * 0.3, 5.0, 200.0, 100.0,
                20.0, now,
            ),
        )
    for date_str, close in (("2022-12-30", 50.0), ("2023-12-29", 55.0), ("2024-12-31", 60.0), ("2026-01-01", 65.0)):
        conn.execute(
            "INSERT INTO price_history (ticker, date, close, source, retrieved_at) VALUES (?, ?, ?, 'yfinance', ?)",
            ("TEST", date_str, close, now),
        )
    conn.commit()
    yield conn
    conn.close()


def test_run_valuation_writes_six_rows(valuation_db):
    conn = valuation_db
    rows_written, error = run_valuation("TEST", "run1", conn)
    assert error is None
    assert rows_written == 6  # 3 DCF scenarios + fcf_yield + ev_ebit + pe_historical

    persisted = conn.execute("SELECT * FROM valuations WHERE run_id = 'run1' AND ticker = 'TEST'").fetchall()
    assert len(persisted) == 6
    methods = {(r["method"], r["scenario"]) for r in persisted}
    assert methods == {
        ("owner_earnings_dcf", "bear"), ("owner_earnings_dcf", "base"), ("owner_earnings_dcf", "bull"),
        ("fcf_yield", None), ("ev_ebit", None), ("pe_historical", None),
    }


def test_run_valuation_dcf_scenarios_are_ordered_and_priced_per_share(valuation_db):
    conn = valuation_db
    run_valuation("TEST", "run1", conn)
    dcf = {
        r["scenario"]: r["intrinsic_value_low"]
        for r in conn.execute(
            "SELECT scenario, intrinsic_value_low FROM valuations WHERE ticker='TEST' AND method='owner_earnings_dcf'"
        )
    }
    assert dcf["bear"] < dcf["base"] < dcf["bull"]
    # Owner earnings here run a few hundred dollars/year on a 20-share
    # company — a per-share intrinsic value in the tens of dollars is the
    # sanity bound that catches a forgotten /shares_diluted division
    # (which would land in the thousands instead).
    for value in dcf.values():
        assert 1.0 < value < 500.0


def test_run_valuation_current_price_is_latest_close(valuation_db):
    conn = valuation_db
    run_valuation("TEST", "run1", conn)
    prices = {r["current_price"] for r in conn.execute("SELECT current_price FROM valuations WHERE ticker='TEST'")}
    assert prices == {65.0}


def test_run_valuation_is_idempotent_per_run_id(valuation_db):
    conn = valuation_db
    first_write, _ = run_valuation("TEST", "run1", conn)
    second_write, _ = run_valuation("TEST", "run1", conn)
    assert first_write == second_write == 6
    count = conn.execute("SELECT COUNT(*) AS n FROM valuations WHERE ticker='TEST' AND run_id='run1'").fetchone()["n"]
    assert count == 6  # upsert, not duplicate rows


def test_run_valuation_missing_shares_diluted_is_an_explicit_error(valuation_db):
    conn = valuation_db
    conn.execute("UPDATE fundamentals_annual SET shares_diluted = NULL WHERE ticker='TEST'")
    conn.commit()
    rows_written, error = run_valuation("TEST", "run1", conn)
    assert rows_written == 0
    assert error is not None and "shares_diluted" in error
    assert conn.execute("SELECT COUNT(*) AS n FROM valuations WHERE ticker='TEST'").fetchone()["n"] == 0


def test_run_valuation_no_fundamentals_is_an_explicit_error(valuation_db):
    conn = valuation_db
    rows_written, error = run_valuation("NOPE", "run1", conn)
    assert rows_written == 0
    assert error == "no fundamentals_annual data"


def test_run_valuation_dcf_ignores_stale_owner_earnings_data_outside_the_recent_window(valuation_db):
    """Regression for a real bug found on live data (NVDA): a company
    whose capex tag is only present in old fiscal years (outside the
    trailing DEFAULT_PROJECTION_YEARS window) and NULL in every recent one
    must report DCF as unavailable, not silently average the old years and
    divide by today's (post-split) share count into a near-zero per-share
    "intrinsic value" against a real price.
    """
    conn = valuation_db
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO companies (ticker, name, universe, is_active, added_date) VALUES (?, ?, ?, 1, ?)",
        ("STALE", "Stale Co", "sp500", now),
    )
    # Old years (outside the trailing 10): full owner_earnings inputs, tiny company.
    for fy in (2005, 2006, 2007):
        conn.execute(
            """
            INSERT INTO fundamentals_annual (
                ticker, fiscal_year, period_end_date, revenue, net_income,
                depreciation_amortization, capex, shares_diluted, source, confidence, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sec_edgar', 'high', ?)
            """,
            ("STALE", fy, f"{fy}-12-31", 100.0, 10.0, 5.0, 3.0, 1000.0, now),
        )
    # Recent 10 years: capex NULL every year (the real NVDA-shaped gap).
    for fy in range(2015, 2025):
        conn.execute(
            """
            INSERT INTO fundamentals_annual (
                ticker, fiscal_year, period_end_date, revenue, net_income,
                depreciation_amortization, capex, shares_diluted, source, confidence, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'sec_edgar', 'high', ?)
            """,
            ("STALE", fy, f"{fy}-12-31", 10000.0, 5000.0, 400.0, 50_000_000.0, now),
        )
    conn.execute(
        "INSERT INTO price_history (ticker, date, close, source, retrieved_at) VALUES (?, ?, ?, 'yfinance', ?)",
        ("STALE", "2026-01-01", 200.0, now),
    )
    conn.commit()

    run_valuation("STALE", "run1", conn)
    dcf_rows = conn.execute(
        "SELECT scenario, intrinsic_value_low, key_assumptions FROM valuations "
        "WHERE ticker='STALE' AND method='owner_earnings_dcf'"
    ).fetchall()
    assert len(dcf_rows) == 3
    for row in dcf_rows:
        assert row["intrinsic_value_low"] is None
        assert json.loads(row["key_assumptions"])["status"] == "unavailable"
