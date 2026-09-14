"""Valuation engine (PRD §6): Owner Earnings DCF + supporting methods.

Sprint 4 scope. Primary method is Owner Earnings DCF with bear/base/bull
scenarios; FCF yield, EV/EBIT, P/E and historical ranges are supporting
cross-checks, not substitutes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

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

    Returns `None` when `total_debt` is `None` — found by the judge review
    that flagged this V5/V6 push, and confirmed against real data before
    accepting it: this pipeline already knows `total_debt IS NULL` is not
    reliably "no debt" for 18 of the 91 `passed_screen` companies (the
    pre-existing §A17 debt-tag-extraction gap — `LongTermDebt` and
    `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`
    aren't in the candidate list). AMT is the sharpest case: stored
    `total_debt` $3.39bn against SEC's real ~$37.2bn including current
    maturities. Treating that gap as "genuinely zero debt" here would be
    exactly the silent-wrong-number substitution `owner_earnings()`
    already refuses to make for a missing capex/D&A (see its docstring) —
    this function originally treated debt differently on the theory that
    a missing figure "legitimately means none to add", which doesn't hold
    for this specific, already-confirmed extraction gap. `cash_and_equiv`
    is different and still defaults to 0 when `None`: it's carried by a
    near-universal single XBRL tag with no equivalent documented gap, so a
    missing value there is far more likely to be a genuine "none reported"
    than an extraction failure.

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
    if total_debt is None:
        return None
    debt = total_debt
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


# ---------------------------------------------------------------------
# Growth-rate derivation for dcf_scenario()'s growth_rate parameter. §A16.3
# fixes the discount rate and terminal growth; it says nothing about how a
# per-company, per-scenario growth_rate is derived — that decision belongs
# to whichever work item actually calls dcf_scenario() against real
# companies, which is this one (V5). Same "starting-point heuristic, not
# a locked-in final number, revisit against real output" posture as
# DISCOUNT_RATE/SCENARIO_TERMINAL_GROWTH above.
# ---------------------------------------------------------------------

GROWTH_RATE_FLOOR = -0.10
GROWTH_RATE_CEILING = 0.20

# Additive, not multiplicative — bear = base - spread, bull = base + spread.
# A multiplicative spread (e.g. bear = base * 0.5) breaks for a shrinking
# company: multiplying an already-negative base growth rate by a number
# > 1 for "bull" makes it MORE negative, the opposite of what bull should
# mean. Additive keeps bear <= base <= bull regardless of the base rate's
# sign.
SCENARIO_GROWTH_SPREAD = 0.04
SCENARIO_GROWTH_DIRECTION = {"bear": -1, "base": 0, "bull": 1}


def historical_revenue_cagr(fundamentals_rows: list[dict]) -> float | None:
    """CAGR of revenue across a company's own trailing fundamentals rows
    (any order in — sorted internally by fiscal_year).

    Endpoint CAGR (first usable year to last), not a trend fit across every
    year — simple, but sensitive to whichever single year lands on either
    endpoint; a real limitation, not hidden here as if this were a more
    careful regression.

    `None` with fewer than two usable (strictly positive revenue) years,
    or when the years don't actually span any time (fiscal_year
    collision) — a rate of change needs two distinct points, and a
    non-positive revenue at either endpoint makes the ratio undefined
    (fractional-power-of-a-negative-number territory, not just "small").

    Filters to *strictly positive* revenue, not merely present/non-zero —
    found by a judge review: a `revenue` that's falsy-but-present (0) or
    genuinely negative (a real, if rare, EDGAR restatement/contra-entry
    possibility) used to pass the old `if r.get("revenue")` truthy check,
    then reach the endpoint math unguarded whenever it landed on the
    *last* point specifically (`first_revenue <= 0` was already checked,
    `last_revenue` never was) — `(negative / positive) ** fractional`
    produces a complex number in Python, which then raises `TypeError`
    the moment `scenario_growth_rates()` tries to compare it against
    `GROWTH_RATE_FLOOR`. Filtering both endpoints to strictly positive
    up front closes this rather than special-casing the comparison.
    """
    points = sorted(
        ((r["fiscal_year"], r["revenue"]) for r in fundamentals_rows if r.get("revenue") and r["revenue"] > 0),
        key=lambda p: p[0],
    )
    if len(points) < 2:
        return None
    first_year, first_revenue = points[0]
    last_year, last_revenue = points[-1]
    years = last_year - first_year
    if years <= 0:
        return None
    return (last_revenue / first_revenue) ** (1 / years) - 1


def scenario_growth_rates(fundamentals_rows: list[dict]) -> dict[str, float]:
    """Derive a bear/base/bull `growth_rate` for `dcf_scenario()` from a
    company's own historical revenue CAGR.

    Revenue CAGR rather than owner-earnings CAGR: more stable (owner
    earnings' single-year swings are exactly what averaging the trailing
    series into `dcf_scenario()`'s base year already smooths out — reusing
    that same noisy series here would double up on the same problem), and
    implicitly conservative in the PRD §1 sense — it assumes margins hold
    rather than assuming they expand.

    No usable history falls back to a flat 0% base-case rate rather than a
    guessed number PRD §1's conservative-assumptions principle wouldn't
    otherwise license. The base rate is capped to
    [GROWTH_RATE_FLOOR, GROWTH_RATE_CEILING] before the scenario spread is
    applied, and each scenario's result is capped again after — a single
    early hyper-growth or hyper-decline period must not compound unchecked
    for `DEFAULT_PROJECTION_YEARS`, and the spread itself must not push a
    scenario back out past the cap it was just clamped to.
    """
    cagr = historical_revenue_cagr(fundamentals_rows)
    base_rate = 0.0 if cagr is None else max(GROWTH_RATE_FLOOR, min(GROWTH_RATE_CEILING, cagr))
    return {
        scenario: max(GROWTH_RATE_FLOOR, min(GROWTH_RATE_CEILING, base_rate + direction * SCENARIO_GROWTH_SPREAD))
        for scenario, direction in SCENARIO_GROWTH_DIRECTION.items()
    }


# ---------------------------------------------------------------------
# Stage orchestration (V5): the one place in this module that touches the
# database. Everything above is a pure function over already-extracted
# values (same design note as the supporting-methods section above) —
# this function does the DB assembly those functions' docstrings deferred
# here: fundamentals_annual + price_history reads, market cap from
# price * shares_diluted, and price-near-period-end matching for the P/E
# series.
# ---------------------------------------------------------------------

def _fundamentals_history(ticker: str, conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM fundamentals_annual WHERE ticker = ? ORDER BY fiscal_year", (ticker,)
    ).fetchall()
    return [dict(r) for r in rows]


def _price_history(ticker: str, conn) -> list[dict]:
    rows = conn.execute(
        "SELECT date, close FROM price_history WHERE ticker = ? ORDER BY date", (ticker,)
    ).fetchall()
    return [dict(r) for r in rows]


def _price_on_or_before(date_str: str | None, prices: list[dict]) -> float | None:
    """Latest close on or before `date_str` — `prices` must already be
    sorted ascending by date (see `_price_history`). Linear scan: fine at
    this scale (a handful of fiscal-year lookups against a few thousand
    price rows at most), not worth a bisect for the data volume here.
    """
    if date_str is None:
        return None
    candidate = None
    for row in prices:
        if row["date"] > date_str:
            break
        candidate = row["close"]
    return candidate


def _replace_valuations(conn, run_id: str, ticker: str, rows: list[dict]) -> None:
    """Delete then re-insert this company's rows under this run_id, rather
    than an `INSERT ... ON CONFLICT` upsert keyed on the table's own
    (run_id, ticker, method, scenario) primary key.

    That key includes `scenario`, which is legitimately NULL for the three
    non-scenario methods (fcf_yield, ev_ebit, pe_historical) — and SQL
    NULLs are never equal to each other for uniqueness purposes, so a
    conflict on a repeat insert with the same NULL scenario never fires:
    `ON CONFLICT` silently inserted a duplicate row instead of updating in
    place. Caught by this stage's own idempotency test — delete-then-insert
    sidesteps the NULL-in-composite-key behavior entirely rather than
    working around it with a sentinel value that would also have to be
    threaded through the schema comment and every future reader of this
    table.
    """
    conn.execute("DELETE FROM valuations WHERE run_id = ? AND ticker = ?", (run_id, ticker))
    conn.executemany(
        """
        INSERT INTO valuations (
            run_id, ticker, method, scenario, intrinsic_value_low, intrinsic_value_high,
            current_price, margin_of_safety_pct, key_assumptions, created_at
        ) VALUES (
            :run_id, :ticker, :method, :scenario, :intrinsic_value_low, :intrinsic_value_high,
            :current_price, :margin_of_safety_pct, :key_assumptions, :created_at
        )
        """,
        rows,
    )


def run_valuation(ticker: str, run_id: str, conn) -> tuple[int, str | None]:
    """Full stage: compute all methods/scenarios for one company, persist
    to `valuations`. Returns (rows_written, error) — same shape as
    `fundamentals_edgar.run_for_ticker`/`prices.run_for_ticker`, for
    `run_pipeline.py`'s stage loop to aggregate the same way ingest does.

    One transaction per company (`conn.commit()`/`rollback()` here, not
    per-row): either every row for this ticker under this `run_id` lands,
    or none do — no partial company writes, this work item's own
    acceptance bar. Idempotent per `run_id` via delete-then-reinsert (see
    `_replace_valuations` for why that's used instead of an `ON CONFLICT`
    upsert on the table's own primary key): re-running the same run_id
    recomputes and replaces this ticker's rows in place rather than
    erroring or duplicating. Cheap either way — this stage is
    deterministic arithmetic over already-ingested data, no API cost to
    avoid by skipping an unchanged recompute (§A16's cost section).

    A company this can't value at all (no fundamentals, no price, no
    shares_diluted for the per-share conversion DCF needs — see
    `dcf_scenario`'s docstring) gets no rows and an explicit `error`
    string back, not a silent skip — `run_pipeline.py`'s ingest stage
    already reports failures this way, matching convention. A company
    that *can* be valued but where one particular method can't (e.g. no
    usable D&A for owner_earnings, or a thin price history for the P/E
    range) still gets a row for that method, with the value columns NULL
    and the reason recorded in `key_assumptions` — Definition of Done's
    "full valuation or an explicit reason it doesn't" bar applies per
    method, not just per company.
    """
    fundamentals_rows = _fundamentals_history(ticker, conn)
    if not fundamentals_rows:
        return 0, "no fundamentals_annual data"

    prices = _price_history(ticker, conn)
    if not prices:
        return 0, "no price_history data"
    current_price = prices[-1]["close"]

    latest = fundamentals_rows[-1]
    shares = latest.get("shares_diluted")
    if not shares or shares <= 0:
        # DCF's owner_earnings_series is a company-level (aggregate $)
        # figure; every method here needs shares_diluted to convert to a
        # price-comparable per-share value (DCF) or to build market_cap
        # (FCF yield, EV/EBIT, P/E's implied price) — without it nothing
        # in this stage produces a number comparable to current_price.
        return 0, "no shares_diluted available for per-share/market-cap conversion"
    market_cap = current_price * shares

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []

    def base_row(method: str, scenario: str | None) -> dict:
        return {
            "run_id": run_id,
            "ticker": ticker,
            "method": method,
            "scenario": scenario,
            "intrinsic_value_low": None,
            "intrinsic_value_high": None,
            "current_price": current_price,
            "margin_of_safety_pct": None,
            "key_assumptions": "{}",
            "created_at": now_iso,
        }

    # --- Primary method: Owner Earnings DCF, three scenarios ---
    # Only the company's own trailing RECENT_YEARS_WINDOW fiscal years are
    # eligible, not every historically-computable year regardless of age.
    # Found on real data (NVDA): capex is only tagged for FY2010-2012 in
    # this pipeline's ingest (the pre-existing capex-tag gap §A13 already
    # documents, 155/505 companies) — with no recency filter, owner_earnings()
    # silently used three 14+-year-old data points from when NVDA was a
    # ~$600M-net-income company, averaged them, and divided by *today's*
    # 24.5bn diluted share count (post-splits) to produce $0.71/share
    # against a real ~$217 price. Correct arithmetic on stale, unrepresentative
    # inputs is still a wrong number. Restricting to recent years means a
    # company whose recent years are gapped now honestly reports DCF as
    # unavailable (see below) instead of silently substituting ancient data.
    recent_rows = fundamentals_rows[-DEFAULT_PROJECTION_YEARS:]
    oe_series = [oe for r in recent_rows if (oe := owner_earnings(r)) is not None]
    growth_rates = scenario_growth_rates(fundamentals_rows)
    for scenario in SCENARIOS:
        row = base_row("owner_earnings_dcf", scenario)
        if not oe_series:
            row["key_assumptions"] = json.dumps(
                {
                    "status": "unavailable",
                    "reason": f"no fiscal year in the trailing {DEFAULT_PROJECTION_YEARS} has all of net_income/D&A/capex",
                }
            )
            rows.append(row)
            continue
        growth_rate = growth_rates[scenario]
        terminal_growth = SCENARIO_TERMINAL_GROWTH[scenario]
        pv = dcf_scenario(oe_series, growth_rate=growth_rate, discount_rate=DISCOUNT_RATE, terminal_growth=terminal_growth)
        per_share = pv / shares
        row["intrinsic_value_low"] = per_share
        row["intrinsic_value_high"] = per_share
        row["margin_of_safety_pct"] = margin_of_safety(per_share, current_price)
        row["key_assumptions"] = json.dumps(
            {
                "discount_rate": DISCOUNT_RATE,
                "growth_rate": growth_rate,
                "terminal_growth": terminal_growth,
                "projection_years": DEFAULT_PROJECTION_YEARS,
                "trailing_years_used": len(oe_series),
                "base_owner_earnings_avg": sum(oe_series) / len(oe_series),
                # Full series, not just its average — a base near zero or
                # negative is indistinguishable from a normal one without
                # seeing the years behind it (the SHOP finding: one real
                # loss year can drag a short trailing average near zero;
                # this makes that visible instead of hidden inside a
                # single summary number).
                "trailing_owner_earnings_series": oe_series,
            }
        )
        rows.append(row)

    # --- Supporting method: FCF yield ---
    fcf_row = base_row("fcf_yield", None)
    yield_value = fcf_yield(latest.get("free_cash_flow"), market_cap)
    if yield_value is None:
        fcf_row["key_assumptions"] = json.dumps(
            {"status": "unavailable", "reason": "free_cash_flow unavailable (capex not ingested for the latest fiscal year)"}
        )
    else:
        fcf_row["key_assumptions"] = json.dumps({"fcf_yield": yield_value, "market_cap": market_cap})
    rows.append(fcf_row)

    # --- Supporting method: EV/EBIT ---
    ev_row = base_row("ev_ebit", None)
    multiple = ev_ebit(market_cap, latest.get("total_debt"), latest.get("cash_and_equiv"), latest.get("operating_income"))
    if multiple is None:
        ev_row["key_assumptions"] = json.dumps(
            {
                "status": "unavailable",
                "reason": "operating_income non-positive/missing, or total_debt unavailable "
                "(extraction gap, not assumed zero — §A17) for the latest fiscal year",
            }
        )
    else:
        ev_row["key_assumptions"] = json.dumps({"ev_ebit_multiple": multiple, "market_cap": market_cap})
    rows.append(ev_row)

    # --- Supporting method: P/E vs. own historical range ---
    pe_row = base_row("pe_historical", None)
    historical_pe = [
        annual_pe(_price_on_or_before(r.get("period_end_date"), prices), r.get("eps_diluted"))
        for r in fundamentals_rows
    ]
    current_eps = latest.get("eps_diluted")
    current_pe = annual_pe(current_price, current_eps)
    pe_range = pe_historical_range(historical_pe, current_pe)
    if pe_range["low"] is not None and current_eps is not None and current_eps > 0:
        implied_low = pe_range["low"] * current_eps
        implied_high = pe_range["high"] * current_eps
        pe_row["intrinsic_value_low"] = implied_low
        pe_row["intrinsic_value_high"] = implied_high
        pe_row["margin_of_safety_pct"] = margin_of_safety(implied_low, current_price)
    pe_row["key_assumptions"] = json.dumps(
        {
            **pe_range,
            "status": "ok" if pe_range["low"] is not None else "unavailable",
            "reason": None if pe_range["low"] is not None else "no fiscal year had both a usable price match and positive EPS",
        }
    )
    rows.append(pe_row)

    try:
        _replace_valuations(conn, run_id, ticker, rows)
    except Exception as exc:
        conn.rollback()
        return 0, str(exc)

    conn.commit()
    return len(rows), None
