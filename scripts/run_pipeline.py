#!/usr/bin/env python3
"""Run the Project Moat pipeline end-to-end (or from a given stage).

Stage order mirrors PRD §3:
  universe -> prices/fundamentals -> quant screen -> quality score ->
  AI analysis -> valuation -> committee -> monitor

Sprint 0: this wires the skeleton and writes a pipeline_runs row per
invocation. Each stage function currently raises NotImplementedError
until its sprint lands — see docs/PRD_ADDENDUM.md for the sprint plan.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.db.connection import get_connection, init_db

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Project Moat pipeline")
    parser.add_argument("--from-stage", choices=STAGES, default=STAGES[0])
    parser.add_argument("--init-db", action="store_true", help="Create schema if missing")
    args = parser.parse_args()

    if args.init_db:
        init_db()
        print("Database schema initialized.")

    conn = get_connection()
    run_id = new_run_id()
    start_run(conn, run_id)
    print(f"Starting pipeline run {run_id} from stage '{args.from_stage}'")

    start_index = STAGES.index(args.from_stage)
    try:
        for stage in STAGES[start_index:]:
            print(f"  -> stage '{stage}': not yet implemented (see docs/PRD_ADDENDUM.md sprint plan)")
            raise NotImplementedError(f"Stage '{stage}' lands in a later sprint")
    except NotImplementedError as exc:
        complete_run(conn, run_id, stage_reached=args.from_stage, status="failed")
        print(f"Run {run_id} stopped: {exc}")
        return
    finally:
        conn.close()


if __name__ == "__main__":
    main()
