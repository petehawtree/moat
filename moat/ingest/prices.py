"""Daily price ingestion via yfinance.

US-only for Sprint 1 (docs/PRD_ADDENDUM.md §A1), so no ticker-suffix or
FX handling needed yet.
"""
from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf


def fetch_price_history(ticker: str, start: str | None = None, period: str = "2y") -> list[dict]:
    """Fetch daily close prices for one ticker via yfinance.

    Pass `start` (YYYY-MM-DD) for an incremental fetch; otherwise falls
    back to `period` (default 2y — enough for current-price and near-term
    monitoring; PRD §6/§10 valuation work reads from fundamentals, not a
    long price history, so a deep multi-decade backfill isn't needed here).
    """
    t = yf.Ticker(ticker)
    hist = t.history(start=start) if start else t.history(period=period)
    if hist.empty:
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for idx, row in hist.iterrows():
        if row["Close"] != row["Close"]:  # NaN check; a day with no trade shouldn't happen but skip defensively
            continue
        rows.append(
            {
                "date": idx.date().isoformat(),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else None,  # NaN check
                "source": "yfinance",
                "retrieved_at": now_iso,
            }
        )
    return rows


def _latest_price_date(ticker: str, conn) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) AS latest FROM price_history WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row["latest"] if row and row["latest"] else None


def persist_prices(ticker: str, rows: list[dict], conn) -> None:
    conn.executemany(
        """
        INSERT INTO price_history (ticker, date, close, volume, source, retrieved_at)
        VALUES (:ticker, :date, :close, :volume, :source, :retrieved_at)
        ON CONFLICT(ticker, date) DO UPDATE SET
            close=excluded.close, volume=excluded.volume,
            source=excluded.source, retrieved_at=excluded.retrieved_at
        """,
        [{**r, "ticker": ticker} for r in rows],
    )
    conn.commit()


def run_for_ticker(ticker: str, conn) -> tuple[int, str | None]:
    """Incremental refresh: only pull rows newer than what's already stored."""
    latest = _latest_price_date(ticker, conn)
    try:
        rows = fetch_price_history(ticker, start=latest)
    except Exception as exc:  # yfinance raises a variety of exception types on bad tickers
        return 0, str(exc)

    if latest is not None and rows:
        # yfinance's `start` is inclusive; drop the day we already have.
        rows = [r for r in rows if r["date"] > latest]

    if not rows:
        return 0, None

    persist_prices(ticker, rows, conn)
    return len(rows), None
