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
import difflib
import hashlib
import re
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
# Reanchor — citation resolution ladder (§A15.5)
#
# Six rungs, tried in order, first hit wins. The anchor (the citations row
# itself) is never touched — only citation_resolution_events records which
# rung answered, so an 'unresolved' result is recorded, not silently retried
# forever or papered over by re-pointing the anchor at a lookalike span.
# ---------------------------------------------------------------------------

# Below this similarity ratio a fuzzy candidate isn't a match — it's noise.
_FUZZY_FLOOR = 0.60

# Rungs at or below this are "degraded": the exact bytes written at analysis
# time are gone, and this ladder found something on scent rather than on
# offset. stale_analysis is set on the analysis when its citations land here.
_DEGRADED_RESULTS = {"fuzzy", "unresolved"}

# Unicode variants normalize.py folds — matched interchangeably here too, so
# a pre-fold quote still finds its post-fold text (and vice versa).
_PUNCT_CLASSES = {
    '"': '["“”]',
    "'": "['‘’]",
    "-": "[-‐‑‒–—]",
}


def _whitespace_flexible_pattern(quote: str) -> str | None:
    """Build a regex matching `quote` with runs of whitespace and common
    unicode punctuation variants treated as interchangeable, so offsets
    invalidated by a norm_version bump can still be found by content."""
    tokens = quote.split()
    if not tokens:
        return None
    escaped = []
    for tok in tokens:
        parts = [_PUNCT_CLASSES.get(ch, re.escape(ch)) for ch in tok]
        escaped.append("".join(parts))
    return r"\s+".join(escaped)


def _disambiguate(
    text: str, candidates: list[tuple[int, int]], prefix: str, suffix: str,
) -> tuple[int, int] | None:
    """Pick the one candidate span whose surrounding text matches the
    citation's own stored prefix/suffix — §A15.3's repeated-quote
    disambiguator, and a confirmed gap (judge report 20260909-134928): the
    moved/moved_section/renormalized rungs used to accept the *first*
    occurrence of an exact-content match with no check that it was the
    right one, so a quote repeated elsewhere in the filing (confirmed to
    happen in the JPM pilot corpus) could silently resolve to different
    evidence than the citation actually supports.

    Zero candidates -> None (not found at this rung). One candidate -> it,
    unconditionally (nothing to disambiguate). Multiple candidates -> only
    the single one whose exact context matches; if none or more than one
    do, None — falling through to the next rung beats guessing.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    matches = [
        (s, e) for s, e in candidates
        if text[max(0, s - len(prefix)): s] == prefix
        and text[e: e + len(suffix)] == suffix
    ]
    return matches[0] if len(matches) == 1 else None


def _find_all_exact(text: str, quote: str) -> list[tuple[int, int]]:
    """Every non-overlapping occurrence of `quote` in `text`."""
    if not quote:
        return []
    spans = []
    start = 0
    while True:
        idx = text.find(quote, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(quote)))
        start = idx + 1
    return spans


def _find_renormalized(text: str, quote: str, prefix: str, suffix: str) -> tuple[int, int] | None:
    """Rung 4: whitespace/normalization-insensitive exact-content search,
    disambiguated the same way as rungs 2/3 when the pattern matches more
    than once."""
    pattern = _whitespace_flexible_pattern(quote)
    if pattern is None:
        return None
    candidates = [(m.start(), m.end()) for m in re.finditer(pattern, text)]
    return _disambiguate(text, candidates, prefix, suffix)


def _find_fuzzy(text: str, quote: str) -> tuple[int, int, float] | None:
    """Rung 5: longest common contiguous run between quote and text, as a
    fraction of the quote's length. A moved quote with light edits (a typo
    fix, a renumbered cross-reference) still shares most of its bytes with
    its new location; this is what finds it. Above _FUZZY_FLOOR only.

    Known limitation, not fixed here: unlike rungs 2-4, this does not
    disambiguate a quote with light edits repeated at more than one
    location — it takes the single longest match difflib finds. Rungs 2-4
    are exact-content searches where a repeat is unambiguous to detect and
    dangerous to guess at (§A15.3); fuzzy is already the last, approximate
    rung before 'unresolved', and folding prefix/suffix into an inherently
    approximate similarity match is a larger redesign than this fix covers.
    """
    if not quote:
        return None
    matcher = difflib.SequenceMatcher(None, quote, text, autojunk=False)
    match = matcher.find_longest_match(0, len(quote), 0, len(text))
    if match.size == 0:
        return None
    score = match.size / len(quote)
    if score < _FUZZY_FLOOR:
        return None
    return match.b, match.b + match.size, score


def _resolve_citation(cite_row, conn) -> dict:
    """Run the six-rung ladder for one citation. Never raises — a filesystem
    or DB miss at any rung just falls through to the next one.

    Returns {"result", "score", "doc_sha256", "start", "end"}.
    """
    accession    = cite_row["accession_number"]
    section_id   = cite_row["section_id"]
    norm_version = cite_row["norm_version"]
    quote        = cite_row["quote"]
    prefix       = cite_row["prefix"] or ""
    suffix       = cite_row["suffix"] or ""

    own = conn.execute(
        "SELECT doc_sha256, local_path FROM filing_documents "
        "WHERE accession_number = ? AND section_id = ? AND norm_version = ?",
        (accession, section_id, norm_version),
    ).fetchone()
    own_text = None
    if own is not None:
        path = Path(own["local_path"])
        if path.exists():
            own_text = path.read_text(encoding="utf-8")

    # Rung 1: exact — same doc_sha256, offsets still land on the stored quote.
    if own is not None and own_text is not None and own["doc_sha256"] == cite_row["doc_sha256"]:
        start, end = cite_row["start_char"], cite_row["end_char"]
        if 0 <= start < end <= len(own_text) and own_text[start:end] == quote:
            return {"result": "exact", "score": 1.0, "doc_sha256": own["doc_sha256"],
                    "start": start, "end": end}

    # Rung 2: moved — exact quote search within the same section. A repeated
    # quote is disambiguated by prefix/suffix (§A15.3), not just taken as the
    # first hit — see _disambiguate()'s docstring for why that used to be a
    # silent-wrong-pointer risk.
    if own_text is not None:
        hit = _disambiguate(own_text, _find_all_exact(own_text, quote), prefix, suffix)
        if hit is not None:
            start, end = hit
            return {"result": "moved", "score": 1.0, "doc_sha256": own["doc_sha256"],
                    "start": start, "end": end}

    # Rung 3: moved_section — exact quote search across every other section
    # of the same filing (e.g. re-extraction drew the item boundary
    # differently). A sibling with an unresolvable repeat is skipped in
    # favor of one with a unique match, rather than accepted blindly.
    siblings = conn.execute(
        "SELECT doc_sha256, local_path FROM filing_documents "
        "WHERE accession_number = ? AND norm_version = ? AND section_id != ?",
        (accession, norm_version, section_id),
    ).fetchall()
    for sib in siblings:
        path = Path(sib["local_path"])
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        hit = _disambiguate(text, _find_all_exact(text, quote), prefix, suffix)
        if hit is not None:
            start, end = hit
            return {"result": "moved_section", "score": 1.0, "doc_sha256": sib["doc_sha256"],
                    "start": start, "end": end}

    # Rung 4: renormalized — whitespace/unicode-insensitive search, own section.
    if own_text is not None:
        hit = _find_renormalized(own_text, quote, prefix, suffix)
        if hit is not None:
            start, end = hit
            return {"result": "renormalized", "score": 1.0, "doc_sha256": own["doc_sha256"],
                    "start": start, "end": end}

    # Rung 5: fuzzy — similarity floor, own section only.
    if own_text is not None:
        hit = _find_fuzzy(own_text, quote)
        if hit is not None:
            start, end, score = hit
            return {"result": "fuzzy", "score": score, "doc_sha256": own["doc_sha256"],
                    "start": start, "end": end}

    # Rung 6: nothing above the floor anywhere it was looked for.
    return {"result": "unresolved", "score": None, "doc_sha256": None, "start": None, "end": None}


def _reanchor(ticker: str, conn) -> None:
    """Run the six-rung resolution ladder for every current citation for this
    ticker. Writes one citation_resolution_events row per citation (never an
    overwrite — see module note above) and sets ai_analysis.stale_analysis on
    any (run_id, ticker, analysis_type) whose citations included a fuzzy or
    unresolved result. Prints a summary.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    # Cache-forward analyses (reused_from_run_id set) store no claims of
    # their own — their claims live under the *source* run
    # (moat/analysis/persist.py's copy-forward writes ai_analysis only, same
    # convention show_ticker() already follows below: "Cache-forward runs
    # store claims on the source run"). Joining on aa.run_id directly, as an
    # earlier version of this query did, finds zero claims for any cached
    # analysis and silently reanchors nothing for it. stale_analysis must
    # still land on the *current* row (aa.run_id) — the one actually shown —
    # not the historical source row.
    claims = conn.execute(
        """
        SELECT ac.claim_id, aa.run_id AS analysis_run_id, aa.analysis_type, ac.claim_order
        FROM ai_analysis aa
        JOIN analysis_claims ac
          ON ac.run_id = COALESCE(aa.reused_from_run_id, aa.run_id)
         AND ac.ticker = aa.ticker
         AND ac.analysis_type = aa.analysis_type
        WHERE aa.ticker = ? AND aa.is_current = 1
        """,
        (ticker,),
    ).fetchall()

    tally: dict[str, int] = {}
    degraded_analyses: set[tuple[str, str]] = set()  # (run_id, analysis_type)

    for claim in claims:
        cites = conn.execute(
            "SELECT * FROM citations WHERE claim_id = ?",
            (claim["claim_id"],),
        ).fetchall()

        for cite in cites:
            resolved = _resolve_citation(cite, conn)
            result = resolved["result"]
            tally[result] = tally.get(result, 0) + 1

            if result in _DEGRADED_RESULTS:
                degraded_analyses.add((claim["analysis_run_id"], claim["analysis_type"]))

            conn.execute(
                """
                INSERT INTO citation_resolution_events
                  (citation_id, checked_at, result, score,
                   resolved_doc_sha256, resolved_start, resolved_end)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    cite["citation_id"], now, result, resolved["score"],
                    resolved["doc_sha256"], resolved["start"], resolved["end"],
                ),
            )

    for run_id, analysis_type in degraded_analyses:
        conn.execute(
            "UPDATE ai_analysis SET stale_analysis = 1 "
            "WHERE run_id = ? AND ticker = ? AND analysis_type = ?",
            (run_id, ticker, analysis_type),
        )

    conn.commit()

    total = sum(tally.values())
    print(f"\n=== Reanchor: {ticker} ===")
    print(f"    Citations checked: {total}")
    for result in ("exact", "moved", "moved_section", "renormalized", "fuzzy", "unresolved"):
        if tally.get(result):
            print(f"    {result:<14}{tally[result]}")
    if total > 0:
        exact_rate = 100 * tally.get("exact", 0) / total
        print(f"    exact rate:        {exact_rate:.1f}%")
    if degraded_analyses:
        print(f"    stale_analysis set on {len(degraded_analyses)} analysis(es) "
              f"(fuzzy or unresolved citations)")
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
        meta = conn.execute(
            "SELECT MAX(is_current) as is_current, reused_from_run_id "
            "FROM ai_analysis WHERE run_id = ? AND ticker = ?",
            (run_id, ticker),
        ).fetchone()
        is_hist = meta["is_current"] == 0
        # Cache-forward runs store claims on the source run
        claims_run_id = meta["reused_from_run_id"] or run_id

        _print_run_header(run_id, ticker, conn)

        for at in ("business_quality", "moat", "management", "risk"):
            claims = conn.execute(
                """
                SELECT ac.*
                FROM analysis_claims ac
                WHERE ac.run_id = ? AND ac.ticker = ? AND ac.analysis_type = ?
                ORDER BY ac.claim_order
                """,
                (claims_run_id, ticker, at),
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
