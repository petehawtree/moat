"""Daily price ingestion via yfinance.

US-only for Sprint 1 (docs/PRD_ADDENDUM.md §A1), so no ticker-suffix or
FX handling needed yet.
"""
from __future__ import annotations

from datetime import date


def fetch_price_history(ticker: str, start: date | None = None) -> list[dict]:
    """Fetch daily close prices for one ticker via yfinance.

    TODO (Sprint 1): yf.Ticker(ticker).history(start=start), map to
    price_history schema rows with source='yfinance'.
    """
    raise NotImplementedError


def refresh_all_prices(tickers: list[str], conn) -> None:
    """Refresh price_history for every active company (daily cadence, §A6).

    TODO (Sprint 1): incremental fetch — only pull rows newer than the
    latest date already stored per ticker, not a full re-download each run.
    """
    raise NotImplementedError
