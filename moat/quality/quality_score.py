"""Deterministic pre-AI quality score (PRD §3's "quality score" stage).

This is distinct from the AI-driven "business quality" write-up in
moat/ai/ and the final weighted §8 score in moat/committee/ — see
docs/PRD_ADDENDUM.md note on §8 vs §3 potentially being conflated.
This module produces a purely numeric, explainable pre-filter score used
to decide which ~50-100 candidates proceed to the (expensive) AI stages.
"""
from __future__ import annotations

from moat.config import MIN_METRICS_ASSESSED, QUALITY_SCORE_PASS_THRESHOLD


def compute_quality_score(quant_score_rows: list[dict]) -> tuple[float, int, int]:
    """Combine one company's `quant_scores` rows into (score, assessed, passed).

    `score` is the percentage of **assessable** metrics passed — metrics whose
    status is 'unavailable' are excluded from both numerator and denominator
    (docs/PRD_ADDENDUM.md §A13).

    Sprint 2 divided by all eight regardless, so a company whose ROIC simply
    wasn't computable was scored identically to one with genuinely poor ROIC.
    Across the universe that mislabelled 257 ROIC, 202 gross-margin and 201
    debt results as failures. Dividing by what we could actually measure is
    the honest denominator; `assessed` is returned alongside so the caller can
    refuse to rank a company we barely measured (config.MIN_METRICS_ASSESSED)
    — otherwise one measurable passing metric would score 100.

    Raises ValueError on an empty list rather than silently returning 0 — a
    company with zero quant_scores rows means the screen never ran for it,
    which is a caller bug, not a legitimate "scored zero" result.
    """
    if not quant_score_rows:
        raise ValueError("compute_quality_score requires at least one quant_scores row")
    assessable = [r for r in quant_score_rows if r.get("status") in ("pass", "fail")]
    if not assessable:
        return 0.0, 0, 0
    passed = sum(1 for r in assessable if r["overall_pass"])
    return 100.0 * passed / len(assessable), len(assessable), passed


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
        score, assessed, passed = compute_quality_score(ticker_rows)
        # Two conditions, not one: a good score AND enough coverage to mean
        # anything. A company measured on two metrics that happens to clear
        # both is not a better candidate than one measured on all eight.
        clears_bar = score >= QUALITY_SCORE_PASS_THRESHOLD and assessed >= MIN_METRICS_ASSESSED
        notes = None
        if assessed < MIN_METRICS_ASSESSED:
            n_na = sum(1 for r in ticker_rows if r.get("status") == "not_applicable")
            notes = f"insufficient coverage: only {assessed}/8 metrics assessable"
            if n_na:
                notes += f" ({n_na} not applicable to this sector — see PRD_ADDENDUM §A14)"
        quality_rows.append(
            {
                "run_id": run_id,
                "ticker": ticker,
                "passed_screen": int(clears_bar),
                "composite_score": score,
                "metrics_assessed": assessed,
                "metrics_passed": passed,
                "notes": notes,
            }
        )

    conn.executemany(
        """
        INSERT INTO quality_scores (
            run_id, ticker, passed_screen, composite_score, metrics_assessed, metrics_passed, notes
        ) VALUES (
            :run_id, :ticker, :passed_screen, :composite_score, :metrics_assessed, :metrics_passed, :notes
        )
        """,
        quality_rows,
    )
    conn.commit()
