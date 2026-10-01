"""Daily price ingestion via yfinance.

US-only for Sprint 1 (docs/PRD_ADDENDUM.md §A1), so no ticker-suffix or
FX handling needed yet.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import yfinance as yf

from moat.config import PRICE_MAX_STALENESS_TRADING_DAYS


def fetch_price_history(ticker: str, start: str | None = None, period: str = "10y") -> list[dict]:
    """Fetch daily close prices for one ticker via yfinance.

    Pass `start` (YYYY-MM-DD) for an incremental fetch; otherwise falls
    back to `period` (default 10y, extended from Sprint 1-3's 2y — PRD §6's
    P/E-vs-own-historical-range method needs 5-10 years of price history,
    which a 2y default can't support; see docs/PRD_ADDENDUM.md §A16.4. A
    one-parameter change with no effect on the existing 2y consumers
    (current-price, near-term monitoring), which just get more history than
    they need.

    This only extends *new* fetches — a ticker with price_history rows
    already stored still refreshes incrementally via run_for_ticker's
    `start=`, so it does not retroactively backfill years it never fetched.
    Whether/how to backfill the existing universe is open — see
    docs/sprints/sprint-4-plan.md's "Open for discussion".
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


def trading_days_behind(last_close: str, as_of: date) -> int:
    """Weekdays strictly after `last_close` (ISO date) and strictly before `as_of`.

    `as_of` itself doesn't count: today's close may not exist yet, so a
    ticker whose last close is yesterday's is not behind. Exchange holidays
    are ignored (weekdays only) — a deliberate small slack that can only make
    a ticker look slightly more behind than it is, which
    PRICE_MAX_STALENESS_TRADING_DAYS absorbs. Clamped at 0. GitHub #15.
    """
    start = date.fromisoformat(last_close) + timedelta(days=1)
    return max(0, int(np.busday_count(start, as_of)))


def is_stale(
    last_close: str | None,
    as_of: date,
    max_days: int = PRICE_MAX_STALENESS_TRADING_DAYS,
) -> bool:
    """True when there is no price at all or the latest close is more than
    `max_days` trading days behind `as_of` (GitHub #15)."""
    return last_close is None or trading_days_behind(last_close, as_of) > max_days


def run_for_ticker(ticker: str, conn) -> tuple[int, str | None]:
    """Incremental refresh: only pull rows newer than what's already stored.

    An *empty* response is an error, not "up to date" (GitHub #15). yfinance's
    `start=` is inclusive, so a ticker that is genuinely current still gets
    back at least its last stored day; zero rows therefore always means
    something went wrong (throttling, delisting, a bad symbol), whether or
    not the ticker already has stored rows. That is distinct from a
    non-empty response whose rows are all <= the stored latest date, which
    is the normal already-up-to-date case and returns (0, None). No retries
    here: throttling is unconfirmed, and surfacing it as failures is the point.
    """
    latest = _latest_price_date(ticker, conn)
    try:
        rows = fetch_price_history(ticker, start=latest)
    except Exception as exc:  # yfinance raises a variety of exception types on bad tickers
        return 0, str(exc)

    if not rows:
        return 0, f"empty yfinance response (start={latest})"

    if latest is not None:
        # yfinance's `start` is inclusive; drop the day we already have.
        rows = [r for r in rows if r["date"] > latest]

    if not rows:
        return 0, None

    persist_prices(ticker, rows, conn)
    return len(rows), None
