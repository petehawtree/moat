"""Build the company universe for Sprint 1: S&P 500 + NASDAQ 100 (US only).

See docs/PRD_ADDENDUM.md §A1 — FTSE 350 is deferred to a later sprint.

Source: Wikipedia constituent tables (free, no auth, refreshed periodically
by editors — good enough for a personal research tool; revisit if accuracy
becomes a problem).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


@dataclass
class CompanyRecord:
    ticker: str
    name: str
    sector: str
    industry: str
    exchange: str
    currency: str
    universe: str  # 'sp500' | 'nasdaq100' | 'sp500,nasdaq100'


def fetch_sp500_constituents() -> list[CompanyRecord]:
    """Scrape the current S&P 500 constituent list from Wikipedia.

    TODO (Sprint 1): implement with pandas.read_html(SP500_WIKI_URL),
    normalize tickers (e.g. BRK.B -> BRK-B for yfinance), map GICS sector.
    """
    raise NotImplementedError


def fetch_nasdaq100_constituents() -> list[CompanyRecord]:
    """Scrape the current NASDAQ-100 constituent list from Wikipedia.

    TODO (Sprint 1): implement with pandas.read_html(NASDAQ100_WIKI_URL).
    """
    raise NotImplementedError


def build_universe() -> list[CompanyRecord]:
    """Merge S&P 500 + NASDAQ 100, de-duplicating overlapping tickers."""
    sp500 = fetch_sp500_constituents()
    nasdaq100 = fetch_nasdaq100_constituents()

    by_ticker: dict[str, CompanyRecord] = {}
    for record in sp500 + nasdaq100:
        if record.ticker in by_ticker:
            existing = by_ticker[record.ticker]
            merged_universe = ",".join(
                sorted(set(existing.universe.split(",")) | set(record.universe.split(",")))
            )
            by_ticker[record.ticker] = CompanyRecord(**{**existing.__dict__, "universe": merged_universe})
        else:
            by_ticker[record.ticker] = record

    return list(by_ticker.values())


def persist_universe(records: list[CompanyRecord], conn) -> None:
    """Upsert company records into the `companies` table."""
    today = date.today().isoformat()
    conn.executemany(
        """
        INSERT INTO companies (ticker, name, sector, industry, exchange, currency, universe, is_active, added_date)
        VALUES (:ticker, :name, :sector, :industry, :exchange, :currency, :universe, 1, :added_date)
        ON CONFLICT(ticker) DO UPDATE SET
            name=excluded.name, sector=excluded.sector, industry=excluded.industry,
            exchange=excluded.exchange, universe=excluded.universe, is_active=1
        """,
        [{**r.__dict__, "added_date": today} for r in records],
    )
    conn.commit()
