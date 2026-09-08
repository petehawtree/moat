#!/usr/bin/env python3
"""Run the Project Moat pipeline end-to-end (or from a given stage).

Stage order mirrors PRD §3:
  universe -> ingest (prices+fundamentals) -> screen -> quality ->
  ai_analysis -> valuation -> committee -> monitor

Sprint 1: 'universe' and 'ingest' are real (US-only, docs/PRD_ADDENDUM.md
§A1). Sprint 2: 'screen' and 'quality' are real (sector-relative screen,
§A2/§A9); Sprint 2.1 adds ingest provenance + share-basis detection
(§A10/§A11). Sprint 3: 'ai_analysis' is real (citation-enforced qualitative
analysis, §A15). 'valuation' onward still raise NotImplementedError until
Sprint 4+.
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


def _latest_quality_run_id(conn) -> str | None:
    """Return the run_id of the most recent complete/partial quality run with passed tickers."""
    row = conn.execute(
        """
        SELECT qs.run_id
        FROM quality_scores qs
        JOIN pipeline_runs pr ON pr.run_id = qs.run_id
        WHERE qs.passed_screen = 1
          AND pr.status IN ('complete', 'partial')
        GROUP BY qs.run_id
        ORDER BY qs.run_id DESC
        LIMIT 1
        """
    ).fetchone()
    return row["run_id"] if row else None


def run_ai_analysis_stage(
    conn,
    run_id: str,
    offline: bool = False,
    model_id: str | None = None,
    dry_run: bool = False,
    cost_cap_usd: float | None = None,
) -> None:
    """W1→W3→W4→W5 for every ticker that passed the quant screen.

    Reads the screened ticker list from the *latest* quality_scores run with
    passed tickers — not the current run_id, which has no quality_scores rows
    when the pipeline starts from this stage.

    offline=True: skip W1 network calls; use whatever is already on disk.
    Each company is cache-checked before any API call; an unchanged bundle
    makes zero API calls and costs nothing toward the cap.
    """
    # Lazy imports — keep AI deps out of module-level load for other stages.
    import anthropic as _anthropic
    from moat.config import ANTHROPIC_API_KEY
    from moat.analysis.persist import run_analysis
    from moat.analysis.pricing import DEFAULT_MODEL, PILOT_CAP_USD
    from moat.ingest.filing_fetcher import run_for_ticker as w1_fetch

    if model_id is None:
        model_id = DEFAULT_MODEL
    if cost_cap_usd is None:
        cost_cap_usd = PILOT_CAP_USD

    # --- Find screened tickers ---
    quality_run = _latest_quality_run_id(conn)
    if not quality_run:
        raise RuntimeError(
            "No quality_scores run with passed_screen tickers found. "
            "Run the pipeline from 'screen' stage first, or provide "
            "a --from-stage that includes 'quality'."
        )
    tickers = [
        row["ticker"] for row in conn.execute(
            "SELECT ticker FROM quality_scores WHERE run_id = ? AND passed_screen = 1 "
            "ORDER BY ticker",
            (quality_run,),
        )
    ]
    print(
        f"  ai_analysis: {len(tickers)} tickers from quality run {quality_run}"
        + ("  [offline]" if offline else "")
    )

    # --- W1: fetch / verify filings ---
    w1_ok, w1_failed = [], []
    for ticker in tickers:
        accession, err = w1_fetch(ticker, conn, offline=offline)
        if err:
            w1_failed.append((ticker, err))
            print(f"    W1 {ticker}: {err}", file=sys.stderr)
        else:
            w1_ok.append(ticker)
    print(f"    filings: {len(w1_ok)} ok, {len(w1_failed)} failed")
    if w1_failed:
        print(f"    W1 failures: {[t for t, _ in w1_failed[:10]]}")

    # Persist structured failure rows for W1 errors so every screened ticker
    # has an analysis_attempts record (audit completeness).
    if w1_failed and not dry_run:
        from moat.analysis.persist import write_stage_failure
        for ticker, err_msg in w1_failed:
            write_stage_failure(ticker, run_id, f"w1_failed: {err_msg}", model_id, conn)

    # --- W3→W4→W5 ---
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot run AI analysis")
    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    outcomes: dict[str, int] = {}
    accumulated_cost = 0.0
    fallback_count = 0
    analyzed_count = 0

    print(
        f"  ai_analysis: W3→W5 — {len(w1_ok)} eligible, model={model_id}, "
        f"cap=${cost_cap_usd:.2f}"
        + ("  [dry-run]" if dry_run else "")
    )

    for ticker in w1_ok:
        if not dry_run and accumulated_cost >= cost_cap_usd:
            capped_after = sum(outcomes.values())
            print(
                f"    cost cap ${cost_cap_usd:.2f} reached after "
                f"{capped_after} tickers — halting. "
                f"Re-run from ai_analysis to continue; cached tickers cost $0."
            )
            break

        result = run_analysis(
            ticker, run_id, client, conn,
            model_id=model_id, dry_run=dry_run,
        )
        outcome = result.get("outcome", "api_error")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        cost = result.get("cost_estimate") or 0.0
        accumulated_cost += cost

        analyzed_count += 1
        if result.get("used_full_fallback"):
            fallback_count += 1

        if dry_run:
            tokens = result.get("input_tokens", "?")
            fb_tag = " [full_fallback]" if result.get("used_full_fallback") else ""
            print(f"    {ticker}: {outcome} ({tokens:,} tokens){fb_tag}")
        else:
            errors = result.get("errors") or result.get("reason") or ""
            suffix = f"  [{errors}]" if errors else ""
            print(f"    {ticker}: {outcome} (${accumulated_cost:.3f} cumulative){suffix}")

        # 10% full_fallback rate gate: if more than 1-in-10 companies fall back
        # to the whole filing, the section extractor likely needs attention.
        if not dry_run and analyzed_count >= 5 and fallback_count / analyzed_count > 0.10:
            print(
                f"\n    HALTING: full_fallback rate "
                f"{fallback_count}/{analyzed_count} "
                f"({fallback_count/analyzed_count:.0%}) exceeds 10% — "
                f"fix section extractor before continuing."
            )
            break

    print(f"\n  ai_analysis summary:")
    for k in sorted(outcomes):
        if outcomes[k]:
            print(f"    {k}: {outcomes[k]}")
    if not dry_run:
        print(f"    total cost estimate: ${accumulated_cost:.3f}")
        print(f"    cap: ${cost_cap_usd:.2f}")
        if analyzed_count:
            print(f"    full_fallback: {fallback_count}/{analyzed_count} "
                  f"({fallback_count/analyzed_count:.0%})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Project Moat pipeline")
    parser.add_argument("--from-stage", choices=STAGES, default=STAGES[0])
    parser.add_argument("--init-db", action="store_true", help="Create schema if missing, then run the pipeline")
    parser.add_argument("--init-only", action="store_true", help="With --init-db: create the schema and exit without running the pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Only ingest the first N tickers (testing)")
    # ai_analysis stage flags
    parser.add_argument("--model", default=None, help="Model for ai_analysis stage (default: claude-sonnet-4-6)")
    parser.add_argument("--dry-run", action="store_true", help="ai_analysis: count tokens only, no API calls")
    parser.add_argument("--offline", action="store_true", help="ai_analysis: skip W1 network calls, use cached filings only")
    parser.add_argument("--cost-cap", type=float, default=None, metavar="USD",
                        help="ai_analysis: halt when accumulated cost exceeds this (default: $15.00 pilot cap)")
    args = parser.parse_args()

    if args.init_db:
        migrated = init_db()
        print("Database schema initialized." + (f" Migrated columns: {', '.join(migrated)}" if migrated else ""))
        if args.init_only:
            return

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
            elif stage == "ai_analysis":
                print("-> stage 'ai_analysis'")
                run_ai_analysis_stage(
                    conn, run_id,
                    offline=args.offline,
                    model_id=args.model,
                    dry_run=args.dry_run,
                    cost_cap_usd=args.cost_cap,
                )
            else:
                print(f"-> stage '{stage}': not yet implemented (see docs/PRD_ADDENDUM.md sprint plan)")
                raise NotImplementedError(f"Stage '{stage}' lands in a later sprint")
            last_completed_stage = stage
    except NotImplementedError as exc:
        # Reaching an unbuilt stage is the expected end of a run today, not a
        # failure — Sprint 2 marked these 'failed', so every successful screen
        # was recorded as a failure and the dashboard read its results from
        # runs labelled failed (§A13). 'failed' is reserved for real errors.
        complete_run(conn, run_id, stage_reached=last_completed_stage or "none", status="partial")
        print(f"\nRun {run_id} complete through '{last_completed_stage}' (stopped: {exc})")
        return
    except Exception:
        complete_run(conn, run_id, stage_reached=last_completed_stage or "none", status="failed")
        raise
    finally:
        conn.close()

    complete_run(conn, run_id, stage_reached=stages_to_run[-1], status="complete")
    print(f"\nRun {run_id} complete.")


if __name__ == "__main__":
    main()
