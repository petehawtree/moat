#!/usr/bin/env python3
"""Ground any stored number in the filings it came from (docs/PRD_ADDENDUM.md §A11).

Sprint 2's dilution defect was mis-diagnosed twice — once in our own §A9
write-up, once by an external review — because checking a number against its
source filings meant writing a throwaway script both times. Verification that
costs a script gets skipped, and confident prose fills the gap. This makes it
one command.

Usage:
    python scripts/verify.py WMT shares_diluted
    python scripts/verify.py WMT shares_diluted --year 2022
    python scripts/verify.py AAPL revenue --year 2018
    python scripts/verify.py TKO --basis-changes

Reads the cached companyfacts payload (data/filings/) when present, so it's
offline and reproducible; falls back to fetching if the company isn't cached.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.db.connection import get_connection
from moat.ingest.fundamentals_edgar import (
    TAG_CANDIDATES,
    _accession_url,
    fetch_company_facts,
    lookup_cik,
)

# Fields whose XBRL source isn't a single tag (computed at extract time).
DERIVED = {
    "operating_margin": "operating_income / revenue",
    "gross_margin": "gross_profit / revenue (or (revenue - cost_of_revenue) / revenue)",
    "roic": "operating_income * (1 - ASSUMED_TAX_RATE) / (total_debt + equity - cash)",
    "roe": "net_income / stockholders_equity",
    "free_cash_flow": "operating_cash_flow - capex",
    "total_debt": "long_term_debt_noncurrent + long_term_debt_current",
}


def show_stored(conn, ticker: str, field: str, year: int | None) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fundamentals_annual)")}
    if field not in cols:
        print(f"  (no such column on fundamentals_annual: {field})")
        return
    sql = f"SELECT fiscal_year, period_end_date, {field}, accession_number, filed, quality_flags FROM fundamentals_annual WHERE ticker = ?"
    params: list = [ticker]
    if year:
        sql += " AND fiscal_year = ?"
        params.append(year)
    rows = conn.execute(sql + " ORDER BY fiscal_year", params).fetchall()
    if not rows:
        print("  (nothing stored)")
        return
    print(f"  {'FY':<6} {'period end':<12} {field:<22} {'from filing':<22} {'filed':<12} flags")
    for r in rows:
        val = r[field]
        val_s = f"{val:,.4f}".rstrip("0").rstrip(".") if isinstance(val, float) else str(val)
        print(
            f"  {r['fiscal_year']:<6} {r['period_end_date'] or '':<12} {val_s:<22} "
            f"{r['accession_number'] or '(pre-2.1)':<22} {r['filed'] or '':<12} {r['quality_flags'] or ''}"
        )


def show_filings_for_fact(cik: str, field: str, year: int | None) -> None:
    """Every filing that reported this fact, for every period — the evidence
    that separates a restatement from a genuine change in the underlying value.
    """
    if field in DERIVED:
        print(f"  '{field}' is derived, not a single XBRL tag: {DERIVED[field]}")
        print("  Verify its inputs individually.")
        return
    if field not in TAG_CANDIDATES:
        print(f"  '{field}' is not an XBRL-backed field. Known: {', '.join(sorted(TAG_CANDIDATES))}")
        return

    names, unit_key, kind = TAG_CANDIDATES[field]
    facts = fetch_company_facts(cik)
    gaap = facts.get("facts", {}).get("us-gaap", {})

    by_period: dict[str, list[dict]] = {}
    for tag in names:
        for row in gaap.get(tag, {}).get("units", {}).get(unit_key, []):
            if row.get("form") != "10-K":
                continue
            if kind == "duration":
                if "start" not in row or "end" not in row:
                    continue
                try:
                    days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                except ValueError:
                    continue
                if not (350 <= days <= 380):
                    continue
            if year and not row.get("end", "").startswith(str(year)):
                continue
            by_period.setdefault(row["end"], []).append({**row, "_tag": tag})

    if not by_period:
        print("  (no matching 10-K facts)")
        return

    for period in sorted(by_period):
        versions = sorted(by_period[period], key=lambda r: r.get("filed", ""))
        vals = {v["val"] for v in versions}
        marker = "  <-- RESTATED" if len(vals) > 1 else ""
        print(f"\n  period ending {period}{marker}")
        for v in versions:
            print(f"    {v['val']:>22,}  filed {v.get('filed','?')}  {v.get('accn','?')}  [{v['_tag']}]")
            print(f"                           {_accession_url(cik, v.get('accn',''))}")


def show_basis_changes(conn, ticker: str) -> None:
    rows = conn.execute(
        "SELECT * FROM share_basis_changes WHERE ticker = ? ORDER BY period_end_date", (ticker,)
    ).fetchall()
    if not rows:
        print("  none — no filing ever restated this company's share count.")
        print("  A large jump in its share count is therefore REAL dilution, not a split.")
        return
    for r in rows:
        print(
            f"  {r['period_end_date']}  {r['original_value']:>18,.0f} -> {r['restated_value']:>18,.0f}  "
            f"{r['ratio']:>12,.2f}x  {r['change_type']}"
        )
        print(f"      originally {r['original_accession']} ({r['original_filed']}), restated by {r['restated_accession']} ({r['restated_filed']})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ground a stored number in its source filings")
    ap.add_argument("ticker")
    ap.add_argument("field", nargs="?", help="e.g. shares_diluted, revenue, net_income")
    ap.add_argument("--year", type=int, help="restrict to one fiscal year")
    ap.add_argument("--basis-changes", action="store_true", help="show restatement history of the share count")
    args = ap.parse_args()

    ticker = args.ticker.upper()
    conn = get_connection()
    cik = lookup_cik(ticker)
    print(f"\n=== {ticker} (CIK {cik}) ===")

    if args.basis_changes or not args.field:
        print("\n-- share-basis changes (restatements of diluted share count) --")
        show_basis_changes(conn, ticker)
        if not args.field:
            conn.close()
            return

    print(f"\n-- stored in fundamentals_annual --")
    show_stored(conn, ticker, args.field, args.year)

    print(f"\n-- as reported across filings (SEC XBRL) --")
    show_filings_for_fact(cik, args.field, args.year)

    conn.close()


if __name__ == "__main__":
    main()
