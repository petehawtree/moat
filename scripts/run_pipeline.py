#!/usr/bin/env python3
"""Run the Project Moat pipeline end-to-end (or from a given stage).

Stage order mirrors PRD §3:
  universe -> ingest (prices+fundamentals) -> screen -> quality ->
  ai_analysis -> valuation -> committee -> monitor

Sprint 1: 'universe' and 'ingest' are real (US-only, docs/PRD_ADDENDUM.md
§A1). Sprint 2: 'screen' and 'quality' are real (sector-relative screen,
§A2/§A9); Sprint 2.1 adds ingest provenance + share-basis detection
(§A10/§A11). 'ai_analysis' onward still raise NotImplementedError until
Sprint 3+.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat import config
from moat.db.connection import get_connection, init_db
from moat.ingest import fundamentals_edgar, prices, universe
from moat.quality import quality_score
from moat.screen import quant_screen

STAGES = [
    "universe",
    "ingest",
    "screen",
    "quality",
    "ai_analysis",
    "valuation",
    "committee",
    "monitor",
]


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def start_run(conn, run_id: str) -> None:
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, started_at, status) VALUES (?, ?, 'running')",
        (run_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def complete_run(conn, run_id: str, stage_reached: str, status: str = "complete") -> None:
    conn.execute(
        "UPDATE pipeline_runs SET completed_at = ?, stage_reached = ?, status = ? WHERE run_id = ?",
        (datetime.now(timezone.utc).isoformat(), stage_reached, status, run_id),
    )
    conn.commit()


def run_universe_stage(conn) -> list[str]:
    records = universe.run(conn)
    no_sector = sum(1 for r in records if not r.sector)
    print(
        f"  universe: {len(records)} unique companies "
        f"({sum(1 for r in records if r.universe == 'sp500')} sp500-only, "
        f"{sum(1 for r in records if r.universe == 'nasdaq100')} nasdaq100-only, "
        f"{sum(1 for r in records if ',' in r.universe)} both). "
        f"{no_sector} missing sector (NASDAQ-100-only names not covered by the "
        f"Wikipedia GICS source — see docs/PRD_ADDENDUM.md ingest note)."
    )
    return [r.ticker for r in records]


def run_ingest_stage(conn, tickers: list[str], limit: int | None) -> None:
    if limit:
        tickers = tickers[:limit]

    fundamentals_ok, fundamentals_failed = [], []
    prices_ok, prices_failed = [], []
    started = time.monotonic()

    for i, ticker in enumerate(tickers, 1):
        years_written, err = fundamentals_edgar.run_for_ticker(ticker, conn)
        if err:
            fundamentals_failed.append((ticker, err))
        else:
            fundamentals_ok.append((ticker, years_written))

        rows_written, err = prices.run_for_ticker(ticker, conn)
        if err:
            prices_failed.append((ticker, err))
        else:
            prices_ok.append((ticker, rows_written))

        if i % 25 == 0 or i == len(tickers):
            elapsed = time.monotonic() - started
            print(f"  ingest: {i}/{len(tickers)} tickers processed ({elapsed:.0f}s elapsed)")

    thin_history = [t for t, years in fundamentals_ok if years < 3]

    print("\n--- Sprint 1 ingest report ---")
    print(f"Fundamentals: {len(fundamentals_ok)} ok, {len(fundamentals_failed)} failed")
    print(f"  Companies with <3 years of annual history: {len(thin_history)} {thin_history[:10]}")
    print(f"  Failures (first 15): {fundamentals_failed[:15]}")
    print(f"Prices: {len(prices_ok)} ok, {len(prices_failed)} failed")
    print(f"  Failures (first 15): {prices_failed[:15]}")

    # Sprint 2.1 provenance/validation (docs/PRD_ADDENDUM.md §A10, §A11)
    basis = conn.execute(
        "SELECT change_type, COUNT(*) AS n FROM share_basis_changes GROUP BY change_type"
    ).fetchall()
    flagged = conn.execute(
        "SELECT COUNT(*) AS n FROM fundamentals_annual WHERE quality_flags IS NOT NULL"
    ).fetchone()["n"]
    filings_n = conn.execute("SELECT COUNT(*) AS n FROM filings").fetchone()["n"]
    print(f"Provenance: {filings_n} filings recorded")
    print(f"  Share-basis changes: {_basis_summary(basis)}")
    print(f"  Rows failing ingest validation (flagged, not dropped): {flagged}")


def _basis_summary(rows) -> str:
    return ", ".join(f"{r['n']} {r['change_type']}" for r in rows) or "none"


def run_screen_stage(conn, run_id: str) -> None:
    quant_screen.run_screen(run_id, conn)
    n = conn.execute("SELECT COUNT(*) AS n FROM quant_scores WHERE run_id = ?", (run_id,)).fetchone()["n"]
    n_tickers = conn.execute("SELECT COUNT(DISTINCT ticker) AS n FROM quant_scores WHERE run_id = ?", (run_id,)).fetchone()["n"]
    print(f"  screen: {n} quant_scores rows written across {n_tickers} companies ({len(quant_screen.METRICS)} metrics each)")


def run_quality_stage(conn, run_id: str) -> None:
    quality_score.run_quality(run_id, conn)
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(passed_screen) AS passed FROM quality_scores WHERE run_id = ?", (run_id,)
    ).fetchone()
    total, passed = row["total"], row["passed"] or 0
    print(
        f"  quality: {passed}/{total} companies passed the screen "
        f"(composite_score >= {config.QUALITY_SCORE_PASS_THRESHOLD})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Project Moat pipeline")
    parser.add_argument("--from-stage", choices=STAGES, default=STAGES[0])
    parser.add_argument("--init-db", action="store_true", help="Create schema if missing")
    parser.add_argument("--limit", type=int, default=None, help="Only ingest the first N tickers (testing)")
    args = parser.parse_args()

    if args.init_db:
        init_db()
        print("Database schema initialized.")

    conn = get_connection()
    run_id = new_run_id()
    start_run(conn, run_id)
    print(f"Starting pipeline run {run_id} from stage '{args.from_stage}'")

    start_index = STAGES.index(args.from_stage)
    stages_to_run = STAGES[start_index:]

    last_completed_stage = None
    try:
        tickers: list[str] = []
        for stage in stages_to_run:
            if stage == "universe":
                print("-> stage 'universe'")
                tickers = run_universe_stage(conn)
            elif stage == "ingest":
                print("-> stage 'ingest'")
                if not tickers:
                    tickers = [row["ticker"] for row in conn.execute("SELECT ticker FROM companies WHERE is_active = 1")]
                run_ingest_stage(conn, tickers, args.limit)
            elif stage == "screen":
                print("-> stage 'screen'")
                run_screen_stage(conn, run_id)
            elif stage == "quality":
                print("-> stage 'quality'")
                run_quality_stage(conn, run_id)
            else:
                print(f"-> stage '{stage}': not yet implemented (see docs/PRD_ADDENDUM.md sprint plan)")
                raise NotImplementedError(f"Stage '{stage}' lands in a later sprint")
            last_completed_stage = stage
    except NotImplementedError as exc:
        complete_run(conn, run_id, stage_reached=last_completed_stage or "none", status="failed")
        print(f"\nRun {run_id} stopped: {exc}")
        return
    finally:
        conn.close()

    complete_run(conn, run_id, stage_reached=stages_to_run[-1], status="complete")
    print(f"\nRun {run_id} complete.")


if __name__ == "__main__":
    main()
