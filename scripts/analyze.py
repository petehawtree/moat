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

import anthropic

from moat.config import ANTHROPIC_API_KEY  # loads .env as a side-effect
from moat.db.connection import get_connection, init_db
from moat.analysis.caller import call_sync, submit_batch
from moat.analysis.parser import parse_and_validate
from moat.analysis.persist import find_cached_run, persist_result, compute_bundle_key, _doc_sha256s_for_result
from moat.analysis.pricing import DEFAULT_MODEL
from moat.analysis.prompt import PROTOCOL_VERSION, SYSTEM_PROMPT, build_request
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
        try:
            result = call_sync(
                client, ticker, conn, model_id=args.model, dry_run=args.dry_run
            )
        except (ValueError, RuntimeError) as exc:
            print(f"ERROR {ticker}: {exc}", file=sys.stderr)
            continue

        if args.dry_run:
            continue  # report already printed by call_sync

        # Cache check: skip API persist if bundle already stored
        doc_sha256s = _doc_sha256s_for_result(result, conn)
        bundle_key = compute_bundle_key(
            doc_sha256s,
            result.prompt_sha256,
            result.model_id,
            NORM_VERSION,
            result.protocol_version,
        )
        reused_from = find_cached_run(ticker, bundle_key, conn)

        parsed = parse_and_validate(result, conn)
        attempt_id = persist_result(
            result, parsed, run_id, conn,
            reused_from_run_id=reused_from,
        )

        out = {
            "ticker":           ticker,
            "run_id":           run_id,
            "attempt_id":       attempt_id,
            "outcome":          "cache_hit" if reused_from else (
                                    "persisted" if parsed.is_valid else "validation_failed"
                                ),
            "reused_from_run_id": reused_from,
            "claim_coverage":   parsed.claim_coverage,
            "validation_errors": parsed.validation_errors,
            "cost_estimate":    result.cost_estimate,
            "usage":            result.usage,
        }
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
