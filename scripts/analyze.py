#!/usr/bin/env python3
"""W3 — prompt, call and (optionally) batch for Sprint 3.

Usage:
  # Dry-run: count tokens, print pricing snapshot, no API call
  python scripts/analyze.py --tickers AAPL --dry-run

  # Synchronous call for one or more tickers
  python scripts/analyze.py --tickers AAPL MSFT NVDA

  # Batch submission (returns immediately with batch_id)
  python scripts/analyze.py --tickers AAPL MSFT NVDA --batch

  # Choose model (default: claude-sonnet-4-6)
  python scripts/analyze.py --tickers AAPL --model claude-opus-4-8

The raw CallResult is printed as JSON to stdout. Persistence (W5) is a
separate step — this script is the W3 acceptance path.
"""
from __future__ import annotations

import argparse
import json
import sys

import anthropic

from moat.config import ANTHROPIC_API_KEY  # loads .env as a side-effect
from moat.db.connection import get_connection, init_db
from moat.analysis.caller import call_sync, submit_batch
from moat.analysis.pricing import DEFAULT_MODEL


def main() -> None:
    parser = argparse.ArgumentParser(description="W3: prompt and call")
    parser.add_argument("--tickers", nargs="+", required=True, metavar="TICKER")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="count tokens only; no API generation call")
    parser.add_argument("--batch", action="store_true",
                        help="submit via Batch API (50%% off; async)")
    args = parser.parse_args()

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

        out = {
            "ticker":           result.ticker,
            "accession":        result.accession,
            "model_id":         result.model_id,
            "stop_reason":      result.stop_reason,
            "usage":            result.usage,
            "cost_estimate":    result.cost_estimate,
            "prompt_sha256":    result.prompt_sha256,
            "protocol_version": result.protocol_version,
            "document_map":     result.document_map,
            "content_blocks":   result.content_blocks,
        }
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
