"""Valuation engine (PRD §6): Owner Earnings DCF + supporting methods.

Sprint 4 scope. Primary method is Owner Earnings DCF with bear/base/bull
scenarios; FCF yield, EV/EBIT, P/E and historical ranges are supporting
cross-checks, not substitutes.
"""
from __future__ import annotations

SCENARIOS = ["bear", "base", "bull"]

# §A16.3: a single fixed, conservative discount rate applied uniformly
# across every company and scenario — not a per-company CAPM/WACC build.
# The bear/base/bull spread is where PRD §1's margin-for-error is meant to
# live instead. Still an explicit starting point, not a locked-in final
# number (sprint-4-plan.md's "Open for discussion" leaves the exact figure
# open) — 9.5% is the midpoint of the addendum's proposed 9-10% range,
# picked here so V2's functions have something concrete to run against;
# revisit once V5 wires this against real companies, same posture as the
# quality score's 50.0 threshold and the screen's 66.7th-percentile bar.
DISCOUNT_RATE = 0.095

# §A16.3: terminal growth differs by scenario, discount rate does not.
# Same "starting point, not final" caveat as DISCOUNT_RATE above.
SCENARIO_TERMINAL_GROWTH = {"bear": 0.015, "base": 0.025, "bull": 0.03}

# Explicit DCF projection horizon. PRD §4 already prefers a "5-10 year
# history" for the FCF screening criterion; applying the same horizon to
# the projection window keeps the two consistent rather than inventing an
# unrelated number.
DEFAULT_PROJECTION_YEARS = 10


def owner_earnings(fundamentals_row: dict) -> float | None:
    """Owner earnings = net income + D&A - maintenance capex - working capital change.

    Returns `None` — not a guessed number — when a required input is
    missing: `net_income`, `depreciation_amortization`, or `capex`. These
    three get no default because a missing value for any of them is an
    extraction gap (§A16.2: 5 of the current 91-company pass set have no
    usable 10-K D&A tag at all), not a confirmed zero — treating a missing
    capex as zero, for instance, would make a capital-intensive business
    with real maintenance spend look like it has none, the same class of
    silent-wrong-number bug §A13 fixed for free_cash_flow's OCF
    substitution.

    `working_capital_change` is the one input that IS allowed to default to
    0 when missing, because that's the documented ingest-time decision
    behind `quality_flags == 'nwc_unavailable_treated_as_zero'` (§A16.2) —
    not a guess made here, a decision already made and flagged at the row
    level, which this function is just honoring.

    Conservative-assumption posture from PRD §1: capex is treated as fully
    maintenance capex (no ingest source separates maintenance from growth
    capex), which understates owner earnings rather than overstating it —
    the same direction of error PRD §1's "margin of safety" principle
    prefers.

    Sign convention for `working_capital_change` — positive means operating
    capital *increased* (a use of cash), so it's subtracted, matching how
    the formula is stated. Hand-verified against Coca-Cola's real FY2025
    10-K (accession 0001628280-26-010047, all figures as-filed): net income
    $13,107M + D&A $1,050M = $14,157M; reported operating cash flow was
    $7,408M; the $7,208M `working_capital_change` figure for that year,
    subtracted, closes the gap to within $459M — well within the range of
    stock comp/deferred-tax/other non-cash reconciling items a real 10-K
    carries and that this pipeline doesn't ingest separately. The opposite
    sign (adding rather than subtracting) would overshoot the real OCF by
    roughly $14bn, which rules it out.
    """
    net_income = fundamentals_row.get("net_income")
    depreciation_amortization = fundamentals_row.get("depreciation_amortization")
    capex = fundamentals_row.get("capex")
    if net_income is None or depreciation_amortization is None or capex is None:
        return None

    working_capital_change = fundamentals_row.get("working_capital_change") or 0.0
    return net_income + depreciation_amortization - capex - working_capital_change


def dcf_scenario(
    owner_earnings_series: list[float],
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    projection_years: int = DEFAULT_PROJECTION_YEARS,
) -> float:
    """Discount one bear/base/bull scenario's projected owner-earnings
    stream to a present value (company-level dollars, not per-share —
    dividing by `shares_diluted` to get a price-comparable figure is the
    persistence stage's job, V5, once it's reading shares alongside this).

    `owner_earnings_series` is the company's trailing annual owner-earnings
    history (as many years as `owner_earnings()` could compute, oldest
    first) — it is AVERAGED into the base (year-0) figure the projection
    grows from, not itself the projected stream (the stream is generated
    here from that base via `growth_rate`). PRD §1's conservative-
    assumptions principle argues against anchoring the whole valuation on
    whichever single year happened to be most recent; averaging a
    multi-year trailing window is a standard smoothing choice, and matches
    PRD §4's own preference for a "5-10yr history" over a single year for
    the same underlying reason. A starting-parameter decision, not settled
    in advance of seeing real output any more than the discount rate was —
    worth revisiting once real company output can be eyeballed.

    Two-stage DCF: `projection_years` of the base compounding at
    `growth_rate`, each year's cash flow discounted at `discount_rate`,
    plus a Gordon-growth terminal value (on the final projected year, at
    `terminal_growth`) also discounted back at `discount_rate`.

    Run once per scenario with that scenario's `growth_rate`/
    `terminal_growth` at the same `discount_rate` (§A16.3: the discount
    rate does not vary by scenario, only growth/terminal-growth do).
    """
    if not owner_earnings_series:
        raise ValueError("owner_earnings_series must have at least one year of history")
    if terminal_growth >= discount_rate:
        raise ValueError(
            f"terminal_growth ({terminal_growth}) must be less than discount_rate "
            f"({discount_rate}) — the Gordon growth formula diverges otherwise"
        )

    base = sum(owner_earnings_series) / len(owner_earnings_series)

    pv_explicit = 0.0
    year_value = base
    for year in range(1, projection_years + 1):
        year_value *= 1 + growth_rate
        pv_explicit += year_value / (1 + discount_rate) ** year

    terminal_value = year_value * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / (1 + discount_rate) ** projection_years

    return pv_explicit + pv_terminal


def margin_of_safety(intrinsic_value_low: float, current_price: float) -> float:
    """PRD §1: valuation must allow for analytical error — use the
    conservative (low) end of the intrinsic value range, not the midpoint.
    """
    return (intrinsic_value_low - current_price) / intrinsic_value_low


def run_valuation(ticker: str, run_id: str, conn) -> None:
    """Full stage: compute all methods/scenarios, persist to `valuations`.

    TODO (Sprint 4): also compute FCF yield, EV/EBIT, P/E vs its own
    5-10yr historical range (all supporting methods per PRD §6).
    """
    raise NotImplementedError
