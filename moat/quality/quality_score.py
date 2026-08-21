"""Deterministic pre-AI quality score (PRD §3's "quality score" stage).

This is distinct from the AI-driven "business quality" write-up in
moat/ai/ and the final weighted §8 score in moat/committee/ — see
docs/PRD_ADDENDUM.md note on §8 vs §3 potentially being conflated.
This module produces a purely numeric, explainable pre-filter score used
to decide which ~50-100 candidates proceed to the (expensive) AI stages.
"""
from __future__ import annotations

from moat.config import QUALITY_SCORE_PASS_THRESHOLD


def compute_quality_score(quant_score_rows: list[dict]) -> float:
    """Combine one company's `quant_scores` rows into a single 0-100 score.

    Sprint 2 (docs/PRD_ADDENDUM.md §A9): simple weighted count of metrics
    passed (absolute + sector-relative, i.e. `overall_pass`) — the
    straightforward first cut the original TODO called for. Every metric
    counts equally for now; revisit once real data shows some metrics are
    more discriminating than others.

    Raises ValueError on an empty list rather than silently returning 0 —
    a company with zero quant_scores rows means the screen never ran for
    it, which is a caller bug, not a legitimate "scored zero" result.
    """
    if not quant_score_rows:
        raise ValueError("compute_quality_score requires at least one quant_scores row")
    passed = sum(1 for r in quant_score_rows if r["overall_pass"])
    return 100.0 * passed / len(quant_score_rows)


def run_quality(run_id: str, conn) -> None:
    """Roll up this run's `quant_scores` into one `quality_scores` row per
    ticker: `composite_score` from compute_quality_score, `passed_screen` =
    composite_score >= QUALITY_SCORE_PASS_THRESHOLD (config.py).
    """
    rows_by_ticker: dict[str, list[dict]] = {}
    for row in conn.execute("SELECT * FROM quant_scores WHERE run_id = ?", (run_id,)):
        rows_by_ticker.setdefault(row["ticker"], []).append(dict(row))

    quality_rows = []
    for ticker, ticker_rows in rows_by_ticker.items():
        score = compute_quality_score(ticker_rows)
        quality_rows.append(
            {
                "run_id": run_id,
                "ticker": ticker,
                "passed_screen": int(score >= QUALITY_SCORE_PASS_THRESHOLD),
                "composite_score": score,
                "notes": None,
            }
        )

    conn.executemany(
        """
        INSERT INTO quality_scores (run_id, ticker, passed_screen, composite_score, notes)
        VALUES (:run_id, :ticker, :passed_screen, :composite_score, :notes)
        """,
        quality_rows,
    )
    conn.commit()
