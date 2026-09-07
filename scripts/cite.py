#!/usr/bin/env python3
"""W6 — recall surface for cited qualitative analyses (Sprint 3).

Shows every claim and its grounding evidence in the local filing receipt,
then links the EDGAR filing index and primary document. Checking the evidence
behind any claim takes one command (PRD Addendum §A11).

Usage:
  # All claims for a ticker (latest run, current analyses only)
  python scripts/cite.py AAPL

  # All claims that cite a specific accession number (inverted view)
  python scripts/cite.py --filing 0000320193-25-000079

  # Include historical (superseded) runs
  python scripts/cite.py AAPL --all-runs

  # Re-run citation resolution ladder and write events
  python scripts/cite.py AAPL --reanchor

Claim states:
  [CURRENT]             - citation resolves exactly on the current filing receipt
  [HISTORICAL]          - ai_analysis row is superseded (is_current=0)
  [STALE SOURCE]        - filing doc sha256 has changed since the citation was written
  [ANCHOR UNRESOLVED]   - byte offsets no longer match the stored quote
  [INSUFFICIENT EVIDENCE] - claim is an IE entry (no citation expected)
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.db.connection import get_connection, init_db


# ---------------------------------------------------------------------------
# EDGAR URL helpers
# ---------------------------------------------------------------------------

def _filing_index_url(accession: str) -> str:
    """SEC filing index page for this accession (no CIK lookup needed)."""
    clean = accession.replace("-", "")
    # accession format: XXXXXXXXXX-YY-NNNNNN → CIK is the first 10 digits
    cik_part = clean[:10].lstrip("0")
    return (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik_part}&type=10-K&dateb=&owner=include&count=10"
    )


# ---------------------------------------------------------------------------
# Claim state determination
# ---------------------------------------------------------------------------

def _citation_state(cite_row, conn) -> tuple[str, str]:
    """Return (state_label, detail) for a citations row.

    Checks in order:
      1. INSUFFICIENT EVIDENCE  — claim has no citation (handled by caller)
      2. STALE SOURCE           — doc_sha256 in filing_documents has changed
      3. ANCHOR UNRESOLVED      — byte offsets no longer match the local file
      4. CURRENT                — all checks pass
    """
    row = conn.execute(
        "SELECT doc_sha256, local_path FROM filing_documents "
        "WHERE accession_number = ? AND section_id = ? AND norm_version = ?",
        (cite_row["accession_number"], cite_row["section_id"], cite_row["norm_version"]),
    ).fetchone()

    if not row:
        return "ANCHOR UNRESOLVED", "filing_documents row missing"

    if row["doc_sha256"] != cite_row["doc_sha256"]:
        return "STALE SOURCE", f"doc_sha256 changed: stored {cite_row['doc_sha256'][:12]}… vs current {row['doc_sha256'][:12]}…"

    local = Path(row["local_path"])
    if not local.exists():
        return "ANCHOR UNRESOLVED", f"local file missing: {local}"

    text = local.read_text(encoding="utf-8")
    start, end = cite_row["start_char"], cite_row["end_char"]
    if start < 0 or end > len(text) or end <= start:
        return "ANCHOR UNRESOLVED", f"offsets [{start}:{end}] out of range (len={len(text)})"

    extracted = text[start:end]
    if extracted != cite_row["quote"]:
        return "ANCHOR UNRESOLVED", f"byte mismatch at [{start}:{end}]"

    return "CURRENT", ""


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_INDENT = "    "
_QUOTE_WIDTH = 100
_CONTEXT_WIDTH = 60

_STATE_MARKERS = {
    "CURRENT":              "[CURRENT]",
    "HISTORICAL":           "[HISTORICAL]",
    "STALE SOURCE":         "[STALE SOURCE]",
    "ANCHOR UNRESOLVED":    "[ANCHOR UNRESOLVED]",
    "INSUFFICIENT EVIDENCE": "[INSUFFICIENT EVIDENCE]",
}


def _wrap(text: str, width: int = _QUOTE_WIDTH, indent: str = _INDENT) -> str:
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


def _print_claim(claim_row, conn, is_historical: bool = False) -> None:
    status = claim_row["assertion_status"]
    at = claim_row["analysis_type"].upper().replace("_", " ")
    order = claim_row["claim_order"]
    text = claim_row["claim_text"]

    header = f"  [{at} #{order}]"
    if is_historical:
        header += " [HISTORICAL]"

    print(header)
    print(_wrap(text, indent=_INDENT))

    if status == "insufficient_evidence":
        print(f"{_INDENT}[INSUFFICIENT EVIDENCE]")
        print()
        return

    # Load citations for this claim
    cites = conn.execute(
        "SELECT c.*, f.filing_date, f.document_url, f.primary_document_url "
        "FROM citations c "
        "JOIN filings f ON f.accession_number = c.accession_number "
        "WHERE c.claim_id = ?",
        (claim_row["claim_id"],),
    ).fetchall()

    if not cites:
        print(f"{_INDENT}[NO CITATIONS STORED]")
        print()
        return

    for i, cite in enumerate(cites, 1):
        state, detail = _citation_state(cite, conn)
        marker = _STATE_MARKERS.get(state, f"[{state}]")

        quote = cite["quote"]
        prefix = (cite["prefix"] or "")[-_CONTEXT_WIDTH:]
        suffix = (cite["suffix"] or "")[:_CONTEXT_WIDTH]

        section = cite["section_id"]
        filed = cite["filing_date"] or "unknown date"

        print(f"{_INDENT}Citation {i}: {marker}")
        print(f"{_INDENT}  section:  {section}  ({cite['accession_number']}, filed {filed})")
        if detail:
            print(f"{_INDENT}  detail:   {detail}")
        print(f"{_INDENT}  offsets:  [{cite['start_char']}:{cite['end_char']}]")
        print()

        # Render the quote from the local receipt
        if prefix:
            print(f"{_INDENT}  …{prefix}", end="")
        print(f"\033[1m«{quote}»\033[0m", end="")
        if suffix:
            print(f"{suffix}…", end="")
        print()
        print()

        # EDGAR links
        if cite["primary_document_url"]:
            print(f"{_INDENT}  primary doc:  {cite['primary_document_url']}")
        if cite["document_url"]:
            print(f"{_INDENT}  filing index: {cite['document_url']}")
        print()

    print()


def _print_run_header(run_id: str, ticker: str, conn) -> None:
    row = conn.execute(
        "SELECT MIN(created_at) as created_at, MAX(is_current) as is_current, "
        "model, cache_key, reused_from_run_id "
        "FROM ai_analysis WHERE run_id = ? AND ticker = ?",
        (run_id, ticker),
    ).fetchone()
    if not row:
        return
    status = "current" if row["is_current"] else "superseded"
    reused = f" (reused from {row['reused_from_run_id'][:8]}…)" if row["reused_from_run_id"] else ""
    print(f"\n=== Run {run_id[:8]}… | {ticker} | {status}{reused} ===")
    print(f"    model: {row['model']}  created: {row['created_at']}")
    print()


# ---------------------------------------------------------------------------
# Reanchor
# ---------------------------------------------------------------------------

def _reanchor(ticker: str, conn) -> None:
    """Run the resolution ladder for every current citation for this ticker.

    Only the 'exact' rung is implemented (byte equality — same as write-time).
    Further rungs (moved, renormalized, fuzzy) are Sprint 4 scope.

    Writes citation_resolution_events rows and prints a summary.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    claims = conn.execute(
        """
        SELECT ac.claim_id, ac.analysis_type, ac.claim_order
        FROM analysis_claims ac
        JOIN ai_analysis aa ON aa.run_id = ac.run_id AND aa.ticker = ac.ticker
                           AND aa.analysis_type = ac.analysis_type
        WHERE ac.ticker = ? AND aa.is_current = 1
        """,
        (ticker,),
    ).fetchall()

    total = exact = unresolved = 0

    for claim in claims:
        cites = conn.execute(
            "SELECT * FROM citations WHERE claim_id = ?",
            (claim["claim_id"],),
        ).fetchall()

        for cite in cites:
            total += 1
            state, detail = _citation_state(cite, conn)

            result = "exact" if state == "CURRENT" else "unresolved"
            if state == "CURRENT":
                exact += 1
            else:
                unresolved += 1

            conn.execute(
                """
                INSERT INTO citation_resolution_events
                  (citation_id, checked_at, result, score,
                   resolved_doc_sha256, resolved_start, resolved_end)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    cite["citation_id"], now, result, 1.0 if result == "exact" else None,
                    cite["doc_sha256"] if result == "exact" else None,
                    cite["start_char"] if result == "exact" else None,
                    cite["end_char"] if result == "exact" else None,
                ),
            )

    conn.commit()

    print(f"\n=== Reanchor: {ticker} ===")
    print(f"    Citations checked: {total}")
    print(f"    exact:             {exact}")
    print(f"    unresolved:        {unresolved}")
    if total > 0:
        pct = 100 * exact / total
        print(f"    exact rate:        {pct:.1f}%")
    print(f"    events written:    {total}")
    print()


# ---------------------------------------------------------------------------
# Main views
# ---------------------------------------------------------------------------

def show_ticker(ticker: str, conn, all_runs: bool = False) -> None:
    """Print all claims for a ticker, grouped by run."""
    where_current = "" if all_runs else "AND aa.is_current = 1"
    runs = conn.execute(
        f"""
        SELECT DISTINCT aa.run_id
        FROM ai_analysis aa
        WHERE aa.ticker = ? {where_current}
        ORDER BY aa.created_at DESC
        """,
        (ticker,),
    ).fetchall()

    if not runs:
        print(f"No analysis found for {ticker}. Run: python scripts/analyze.py --tickers {ticker}")
        return

    for run_row in runs:
        run_id = run_row["run_id"]
        is_hist = conn.execute(
            "SELECT MAX(is_current) FROM ai_analysis WHERE run_id = ? AND ticker = ?",
            (run_id, ticker),
        ).fetchone()[0] == 0

        _print_run_header(run_id, ticker, conn)

        for at in ("business_quality", "moat", "management", "risk"):
            claims = conn.execute(
                """
                SELECT ac.*
                FROM analysis_claims ac
                WHERE ac.run_id = ? AND ac.ticker = ? AND ac.analysis_type = ?
                ORDER BY ac.claim_order
                """,
                (run_id, ticker, at),
            ).fetchall()
            if not claims:
                continue
            print(f"  --- {at.upper().replace('_', ' ')} ---")
            for claim in claims:
                _print_claim(claim, conn, is_historical=is_hist)


def show_filing(accession: str, conn) -> None:
    """Print all claims that cite a specific accession number."""
    cites = conn.execute(
        """
        SELECT DISTINCT ac.run_id, ac.ticker, ac.analysis_type,
               ac.claim_order, ac.claim_text, ac.assertion_status, ac.claim_id,
               aa.is_current
        FROM citations c
        JOIN analysis_claims ac ON ac.claim_id = c.claim_id
        JOIN ai_analysis aa ON aa.run_id = ac.run_id AND aa.ticker = ac.ticker
                           AND aa.analysis_type = ac.analysis_type
        WHERE c.accession_number = ?
        ORDER BY ac.ticker, aa.is_current DESC, ac.analysis_type, ac.claim_order
        """,
        (accession,),
    ).fetchall()

    if not cites:
        print(f"No claims cite accession {accession}.")
        return

    # Get filing metadata
    filing = conn.execute(
        "SELECT ticker, filing_date, form_type, period_of_report, document_url, primary_document_url "
        "FROM filings WHERE accession_number = ?",
        (accession,),
    ).fetchone()

    print(f"\n=== Filing {accession} ===")
    if filing:
        print(f"    {filing['ticker']}  {filing['form_type']}  period: {filing['period_of_report']}  filed: {filing['filing_date']}")
        if filing["primary_document_url"]:
            print(f"    primary doc:  {filing['primary_document_url']}")
        if filing["document_url"]:
            print(f"    filing index: {filing['document_url']}")
    print()

    prev_ticker = None
    for row in cites:
        if row["ticker"] != prev_ticker:
            print(f"  === {row['ticker']} ===")
            prev_ticker = row["ticker"]
        is_hist = row["is_current"] == 0
        _print_claim(row, conn, is_historical=is_hist)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Show citation evidence for AI analyses")
    ap.add_argument("ticker", nargs="?", help="company ticker (e.g. AAPL)")
    ap.add_argument("--filing", metavar="ACCN", help="show all claims citing this accession")
    ap.add_argument("--all-runs", action="store_true", help="include superseded runs")
    ap.add_argument("--reanchor", action="store_true",
                    help="re-run resolution ladder and write citation_resolution_events")
    args = ap.parse_args()

    if not args.ticker and not args.filing:
        ap.error("provide a TICKER or --filing ACCN")

    init_db()
    conn = get_connection()

    if args.reanchor:
        if not args.ticker:
            ap.error("--reanchor requires a TICKER")
        _reanchor(args.ticker.upper(), conn)
        conn.close()
        return

    if args.filing:
        show_filing(args.filing, conn)
    else:
        show_ticker(args.ticker.upper(), conn, all_runs=args.all_runs)

    conn.close()


if __name__ == "__main__":
    main()
