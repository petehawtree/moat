"""Build the company universe for Sprint 1: S&P 500 + NASDAQ 100 (US only).

See docs/PRD_ADDENDUM.md §A1 — FTSE 350 is deferred to a later sprint.

Sources:
  - S&P 500: Wikipedia's constituent table (free, includes GICS sector and
    CIK — best free source for this list).
  - NASDAQ-100: Wikipedia's article no longer carries a components table
    (checked 2026-08; the "Components" section has been removed from
    en.wikipedia.org/wiki/Nasdaq-100). Nasdaq's own public quote-list API
    (api.nasdaq.com) is used instead — official, free, no auth required.
    It does not return GICS sector, so NASDAQ-100-only companies (i.e. not
    already in the S&P 500) start with sector=None. Backfilling that with
    a SIC-code description would mix two incompatible taxonomies in one
    column used for sector-relative screening (§A2) — left as a Sprint 2
    follow-up rather than silently faked here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timezone, datetime
from io import StringIO

import requests

WIKI_HEADERS = {"User-Agent": "Project Moat (personal research tool; see README)"}
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

NASDAQ_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
NASDAQ100_API_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"


@dataclass
class CompanyRecord:
    ticker: str
    name: str
    sector: str | None
    industry: str | None
    exchange: str | None
    currency: str
    universe: str  # 'sp500' | 'nasdaq100' | 'nasdaq100,sp500'
    cik: str | None = None


def _normalize_ticker(raw: str) -> str:
    """Wikipedia lists class shares as 'BRK.B'; yfinance/most APIs want 'BRK-B'."""
    return raw.strip().upper().replace(".", "-")


def fetch_sp500_constituents() -> list[CompanyRecord]:
    html = requests.get(SP500_WIKI_URL, headers=WIKI_HEADERS, timeout=30).text
    import pandas as pd

    table = pd.read_html(StringIO(html))[0]
    records = []
    for _, row in table.iterrows():
        # Wikipedia's CIK column occasionally carries a stray footnote glyph
        # merged into the cell text (e.g. "0000066740 |") — strip to digits.
        cik_digits = re.sub(r"\D", "", str(row.get("CIK", "")))
        cik = cik_digits.zfill(10) if cik_digits else None
        records.append(
            CompanyRecord(
                ticker=_normalize_ticker(str(row["Symbol"])),
                name=str(row["Security"]).strip(),
                sector=str(row["GICS Sector"]).strip() or None,
                industry=str(row.get("GICS Sub-Industry", "")).strip() or None,
                exchange=None,
                currency="USD",
                universe="sp500",
                cik=cik,
            )
        )
    return records


def fetch_nasdaq100_constituents() -> list[CompanyRecord]:
    resp = requests.get(NASDAQ100_API_URL, headers=NASDAQ_API_HEADERS, timeout=30)
    resp.raise_for_status()
    rows = resp.json()["data"]["data"]["rows"]

    records = []
    for row in rows:
        name = re.sub(r"\s+Common Stock.*$", "", row["companyName"]).strip()
        records.append(
            CompanyRecord(
                ticker=_normalize_ticker(row["symbol"]),
                name=name,
                sector=(row.get("sector") or "").strip() or None,
                industry=None,
                exchange="NASDAQ",
                currency="USD",
                universe="nasdaq100",
                cik=None,
            )
        )
    return records


def build_universe() -> list[CompanyRecord]:
    """Merge S&P 500 + NASDAQ 100, de-duplicating overlapping tickers.

    When a ticker appears in both, prefer the S&P 500 record's sector/CIK
    (Wikipedia's GICS sector is more reliable than what Nasdaq's API returns)
    but keep both universe tags.
    """
    sp500 = fetch_sp500_constituents()
    nasdaq100 = fetch_nasdaq100_constituents()

    by_ticker: dict[str, CompanyRecord] = {}
    for record in sp500 + nasdaq100:
        existing = by_ticker.get(record.ticker)
        if existing is None:
            by_ticker[record.ticker] = record
            continue

        merged_universe = ",".join(sorted(set(existing.universe.split(",")) | set(record.universe.split(","))))
        # Prefer whichever record already has richer sector/CIK data, defaulting to `existing` (sp500 processed first).
        preferred, other = (existing, record) if existing.sector or existing.cik else (record, existing)
        by_ticker[record.ticker] = CompanyRecord(
            ticker=preferred.ticker,
            name=preferred.name,
            sector=preferred.sector or other.sector,
            industry=preferred.industry or other.industry,
            exchange=preferred.exchange or other.exchange,
            currency=preferred.currency,
            universe=merged_universe,
            cik=preferred.cik or other.cik,
        )

    return list(by_ticker.values())


def persist_universe(records: list[CompanyRecord], conn) -> None:
    """Upsert company records into the `companies` table."""
    today = date.today().isoformat()
    conn.executemany(
        """
        INSERT INTO companies (ticker, cik, name, sector, industry, exchange, currency, universe, is_active, added_date)
        VALUES (:ticker, :cik, :name, :sector, :industry, :exchange, :currency, :universe, 1, :added_date)
        ON CONFLICT(ticker) DO UPDATE SET
            cik=COALESCE(excluded.cik, companies.cik),
            name=excluded.name,
            sector=COALESCE(excluded.sector, companies.sector),
            industry=COALESCE(excluded.industry, companies.industry),
            exchange=COALESCE(excluded.exchange, companies.exchange),
            universe=excluded.universe,
            is_active=1
        """,
        [{**r.__dict__, "added_date": today} for r in records],
    )
    conn.commit()


def run(conn) -> list[CompanyRecord]:
    """Full universe stage: fetch, dedupe, persist. Returns the records."""
    records = build_universe()
    persist_universe(records, conn)
    return records
