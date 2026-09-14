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


def margin_of_safety(intrinsic_value_low: float, current_price: float) -> float | None:
    """PRD §1: valuation must allow for analytical error — use the
    conservative (low) end of the intrinsic value range, not the midpoint.

    Returns `None` — an explicit "not investable on this basis" verdict,
    never a number — when `intrinsic_value_low` is zero or negative. The
    naive `(low - price) / low` formula sign-flips for a negative `low`: a
    bear case worth less than nothing produces a *positive*-looking ratio,
    the least-safe possible result reading as the safest. A real bear-case
    owner-earnings stream going negative is not a hypothetical — it is
    exactly what a bear case is for (§A16.4, guarded here before the first
    such company reached the dashboard). Zero is guarded the same way:
    division by zero, and "worth exactly nothing" is no more investable
    than negative.

    A negative *result* is not itself the guarded case and must still come
    back as a real number — it's the ordinary, meaningful signal that the
    current price exceeds even the low end of the range (overpriced). Only
    a non-positive `intrinsic_value_low` — the input, not the output — is
    undefined.

    `None` maps to SQL NULL in `valuations.margin_of_safety_pct` (already
    nullable, no schema change needed). Callers (V5's persistence, V6's
    dashboard) must render it distinctly — e.g. "bear case: negative — not
    investable on this basis" — never as a blank or zero-looking number.
    """
    if intrinsic_value_low <= 0:
        return None
    return (intrinsic_value_low - current_price) / intrinsic_value_low


# ---------------------------------------------------------------------
# Supporting methods (PRD §6): cross-checks, not substitutes for the
# primary Owner Earnings DCF. Pure functions over already-extracted values,
# same design as owner_earnings/dcf_scenario above — V5's persistence
# stage does the DB assembly (market cap from price * shares_diluted,
# price-near-period-end matching for the P/E series), not this module.
# ---------------------------------------------------------------------

def fcf_yield(free_cash_flow: float | None, market_cap: float | None) -> float | None:
    """FCF yield = free cash flow / market cap (higher = cheaper — the
    inverse framing of a P/FCF multiple).

    `free_cash_flow` is already `None` whenever capex was unavailable at
    ingest (§A13: never substituted with operating cash flow) — that
    `None` propagates here rather than being fabricated as 0 or derived
    from a different figure. `market_cap` must be positive; non-positive
    (missing, zero, or — not a real state for a listed company — negative)
    returns `None` rather than a divide-by-zero or a meaningless ratio.

    Unlike margin_of_safety/ev_ebit, there's no sign-flip hazard to guard
    here: a negative free_cash_flow legitimately produces a negative
    yield (burning cash — unattractive, but not sign-ambiguous), since the
    denominator (market cap) is never negative for a real company.
    """
    if free_cash_flow is None or market_cap is None or market_cap <= 0:
        return None
    return free_cash_flow / market_cap


def ev_ebit(
    market_cap: float | None,
    total_debt: float | None,
    cash_and_equiv: float | None,
    operating_income: float | None,
) -> float | None:
    """Enterprise value / EBIT: (market_cap + total_debt - cash_and_equiv) / operating_income.

    `total_debt`/`cash_and_equiv` default to 0 when `None` — an unlevered
    company or one with no separately reported cash contributes nothing to
    EV, which is the ordinary convention, not a guess. This differs from
    Owner Earnings' capex/D&A, where a `None` is a genuine extraction gap
    that must not be papered over (see `owner_earnings`) — here a missing
    debt or cash figure legitimately means "none to add".

    Returns `None` when `operating_income` is non-positive — the same
    sign-flip hazard `margin_of_safety` guards against, one level removed:
    a negative EBIT flips the ratio's sign on the *denominator*, producing
    a small or negative multiple that reads as "cheap" for a company that
    is actually losing money at the operating level. A company burning
    cash operationally gets no EV/EBIT verdict, not a misleading one.
    Also `None` when `market_cap` is non-positive (missing or invalid,
    same as `fcf_yield`'s guard).
    """
    if operating_income is None or operating_income <= 0:
        return None
    if market_cap is None or market_cap <= 0:
        return None
    debt = total_debt or 0.0
    cash = cash_and_equiv or 0.0
    enterprise_value = market_cap + debt - cash
    return enterprise_value / operating_income


def annual_pe(price: float | None, eps_diluted: float | None) -> float | None:
    """One fiscal year's P/E: a price (near that year's period end) divided
    by that year's diluted EPS.

    Returns `None` for a loss-making year (`eps_diluted` <= 0) — the same
    sign-flip family as `margin_of_safety`/`ev_ebit`'s guards. A negative
    EPS produces a negative P/E that is not a "cheap" signal, it's a
    non-answer; letting it into a min/max historical range would corrupt
    the range with a number that doesn't mean what a P/E normally means,
    not merely omit a data point.
    """
    if price is None or eps_diluted is None or eps_diluted <= 0:
        return None
    return price / eps_diluted


# PRD §4's own floor for a defensible "preferably 5-10 year" history,
# reused here as the bar for a P/E range worth trusting rather than
# flagging as thin.
MIN_PE_RANGE_YEARS = 5


def pe_historical_range(historical_pe: list[float | None], current_pe: float | None) -> dict:
    """Compare a company's current P/E to its own trailing multiple range.

    `historical_pe` is this company's own annual P/E points (`annual_pe`
    per fiscal year, oldest first) — V5 assembles this by pairing each
    year's `eps_diluted` (`fundamentals_annual`) with the price nearest
    that year's `period_end_date` (`price_history`, extended to a 10y
    default window by V1 specifically for this method). `None` entries
    (loss-making years — see `annual_pe`) are expected in the input and
    are filtered out here, not treated as an error.

    Returns a dict, not a single number — PRD §6 asks for the range,
    current price and margin of safety together, not just a verdict.
    `low`/`high` are `None` when zero years are usable (rather than a
    range built on nothing). `years_covered` and `low_confidence` (true
    when under `MIN_PE_RANGE_YEARS`, e.g. a recently-listed company) make
    the coverage visible rather than silently truncating a thin history to
    look like a full 5-10yr range — this work item's own acceptance bar.
    """
    valid = [p for p in historical_pe if p is not None]
    return {
        "current": current_pe,
        "low": min(valid) if valid else None,
        "high": max(valid) if valid else None,
        "years_covered": len(valid),
        "low_confidence": len(valid) < MIN_PE_RANGE_YEARS,
    }


def run_valuation(ticker: str, run_id: str, conn) -> None:
    """Full stage: compute all methods/scenarios, persist to `valuations`.

    TODO (Sprint 4, V5): assemble each method's inputs per company from the
    DB (fundamentals_annual, price_history, quality_scores' current pass
    set) and persist one row per (method, scenario) into `valuations`,
    same pattern as W7's "read the latest successful run" fix.
    """
    raise NotImplementedError
