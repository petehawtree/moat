#!/usr/bin/env python3
"""Analyze — W3→W4→W5 pipeline for Sprint 3.

Usage:
  # Dry-run: count tokens, print pricing snapshot, no API call
  python scripts/analyze.py --tickers AAPL --dry-run

  # Synchronous call for one or more tickers (calls API + persists to DB)
  python scripts/analyze.py --tickers AAPL MSFT NVDA

  # Batch submission (returns immediately with batch_id; no W4/W5 yet)
  python scripts/analyze.py --tickers AAPL MSFT NVDA --batch

  # Choose model (default: claude-sonnet-4-6)
  python scripts/analyze.py --tickers AAPL --model claude-opus-4-8

On a cache hit (same bundle key already in DB) no API call is made — the
result is copied forward with reused_from_run_id.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic

from moat.config import ANTHROPIC_API_KEY  # loads .env as a side-effect
from moat.db.connection import get_connection, init_db
from moat.analysis.caller import _prompt_sha256, call_sync, submit_batch
from moat.analysis.parser import parse_and_validate
from moat.analysis.persist import compute_bundle_key, find_cached_run, persist_result, _supersede_for_bundle, _write_cache_attempt
from moat.analysis.pricing import DEFAULT_MODEL
from moat.analysis.prompt import (
    ANALYSIS_TYPES,
    PROTOCOL_VERSION,
    SYSTEM_PROMPT,
    build_request,
    compute_gap_sections,
)
from moat.ingest.section_extractor import NORM_VERSION


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze: W3→W4→W5")
    parser.add_argument("--tickers", nargs="+", required=True, metavar="TICKER")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="count tokens only; no API generation call")
    parser.add_argument("--batch", action="store_true",
                        help="submit via Batch API (50%% off; async)")
    parser.add_argument("--run-id", default=None,
                        help="explicit run_id (defaults to a fresh UUID)")
    args = parser.parse_args()

    run_id = args.run_id or str(uuid.uuid4())
    init_db()
    conn = get_connection()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if args.batch:
        batch_id, partials = submit_batch(client, args.tickers, conn, model_id=args.model)
        out = {
            "batch_id": batch_id,
            "tickers": args.tickers,
            "model_id": args.model,
            "partial_results": {
                t: {
                    "accession": p.accession,
                    "custom_id": p.custom_id,
                    "document_map": p.document_map,
                    "prompt_sha256": p.prompt_sha256,
                }
                for t, p in partials.items()
            },
        }
        print(json.dumps(out, indent=2))
        print(f"\nBatch submitted: {batch_id}", file=sys.stderr)
        return

    for ticker in args.tickers:
        ticker = ticker.upper()
        try:
            if args.dry_run:
                call_sync(client, ticker, conn, model_id=args.model, dry_run=True)
                continue

            # Build the prompt and compute the bundle key BEFORE any API call.
            # This lets us return immediately on a cache hit with zero spend.
            from moat.analysis.caller import prepare_sections
            sections = prepare_sections(
                conn.execute(
                    "SELECT accession_number FROM filings "
                    "WHERE ticker = ? AND local_path IS NOT NULL "
                    "ORDER BY period_of_report DESC LIMIT 1",
                    (ticker,),
                ).fetchone()["accession_number"],
                conn,
            )
            section_texts  = {k: v[0] for k, v in sections.items()}
            filing_doc_ids = {k: v[1] for k, v in sections.items()}
            # gap_sections must be computed identically to call_sync()'s, or the
            # prompt (and its sha256, and the bundle key derived from it) built
            # here diverges from the one call_sync() actually sends/stores.
            content, document_map = build_request(
                section_texts, ticker,
                conn.execute(
                    "SELECT period_of_report FROM filings "
                    "WHERE ticker = ? AND local_path IS NOT NULL "
                    "ORDER BY period_of_report DESC LIMIT 1",
                    (ticker,),
                ).fetchone()["period_of_report"] or "unknown",
                filing_doc_ids,
                gap_sections=compute_gap_sections(section_texts),
            )
            prompt_sha = _prompt_sha256(SYSTEM_PROMPT, content)
            doc_sha256s = []
            for fdi in filing_doc_ids.values():
                row = conn.execute(
                    "SELECT doc_sha256 FROM filing_documents WHERE filing_document_id = ?",
                    (fdi,),
                ).fetchone()
                if row:
                    doc_sha256s.append(row["doc_sha256"])
            bundle_key = compute_bundle_key(
                doc_sha256s, prompt_sha, args.model, NORM_VERSION, PROTOCOL_VERSION,
            )
            reused_from = find_cached_run(ticker, bundle_key, conn)

            if reused_from:
                from datetime import datetime, timezone
                from moat.analysis.persist import _ensure_pipeline_run
                _ensure_pipeline_run(run_id, conn)
                now = datetime.now(timezone.utc).isoformat()
                for at in ANALYSIS_TYPES:
                    orig = conn.execute(
                        "SELECT content, claim_coverage FROM ai_analysis "
                        "WHERE run_id = ? AND ticker = ? AND analysis_type = ?",
                        (reused_from, ticker, at),
                    ).fetchone()
                    if orig:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO ai_analysis
                              (run_id, ticker, analysis_type, content, model,
                               prompt_version, cache_key, is_current,
                               reused_from_run_id, claim_coverage, created_at)
                            VALUES (?,?,?,?,?,?,?,1,?,?,?)
                            """,
                            (run_id, ticker, at, orig["content"], args.model,
                             PROTOCOL_VERSION, bundle_key, reused_from,
                             orig["claim_coverage"], now),
                        )
                _supersede_for_bundle(conn, ticker, bundle_key, run_id)
                attempt_id = _write_cache_attempt(
                    conn, run_id, ticker, args.model, prompt_sha,
                    PROTOCOL_VERSION, reused_from, now,
                )
                conn.commit()
                print(json.dumps({
                    "ticker": ticker, "run_id": run_id,
                    "outcome": "cache_hit", "reused_from_run_id": reused_from,
                    "attempt_id": attempt_id,
                }, indent=2))
                continue

            result = call_sync(client, ticker, conn, model_id=args.model, dry_run=False)

        except (ValueError, RuntimeError, AttributeError) as exc:
            print(f"ERROR {ticker}: {exc}", file=sys.stderr)
            continue

        if result.stop_reason == "refusal":
            attempt_id = persist_result(result, None, run_id, conn)
            print(json.dumps({"ticker": ticker, "run_id": run_id,
                              "outcome": "refused", "attempt_id": attempt_id}, indent=2))
            continue

        parsed = parse_and_validate(result, conn)
        attempt_id = persist_result(result, parsed, run_id, conn)

        out = {
            "ticker":            ticker,
            "run_id":            run_id,
            "attempt_id":        attempt_id,
            "outcome":           "persisted" if parsed.is_valid else "validation_failed",
            "claim_coverage":    parsed.claim_coverage,
            "validation_errors": parsed.validation_errors,
            "cost_estimate":     result.cost_estimate,
            "usage":             result.usage,
        }
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
