"""Valuation engine (PRD §6): Owner Earnings DCF + supporting methods.

Sprint 4 scope. Primary method is Owner Earnings DCF with bear/base/bull
scenarios; FCF yield, EV/EBIT, P/E and historical ranges are supporting
cross-checks, not substitutes.
"""
from __future__ import annotations

SCENARIOS = ["bear", "base", "bull"]


def owner_earnings(fundamentals_row: dict) -> float:
    """Owner earnings = net income + D&A - maintenance capex - working capital changes.

    TODO (Sprint 4): PRD explicitly wants conservative assumptions (§1) —
    default to treating capex as fully maintenance capex unless growth
    capex can be separated out, which understates owner earnings rather
    than overstating it.
    """
    raise NotImplementedError


def dcf_scenario(owner_earnings_series: list[float], growth_rate: float, discount_rate: float, terminal_growth: float) -> float:
    """Discount a projected owner-earnings stream to a present value.

    TODO (Sprint 4): implement for one scenario; run three times (bear/base/bull)
    with different growth_rate/discount_rate assumptions per company.
    """
    raise NotImplementedError


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
