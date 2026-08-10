"""Deterministic quantitative screen (PRD §4) with sector-relative bars (§A2).

Sprint 2 scope — stubbed now so the pipeline skeleton (scripts/run_pipeline.py)
has a stage to call, and so the schema (quant_scores) has a producer to
validate against early.
"""
from __future__ import annotations

METRICS = [
    "roic",
    "roe",
    "free_cash_flow",
    "revenue_eps_growth",
    "operating_margin",
    "debt",
    "share_dilution",
    "gross_margin",
]


def compute_sector_percentile(ticker: str, metric: str, sector: str, all_values_by_sector: dict) -> float:
    """Percentile rank of `ticker`'s metric value within its own sector.

    TODO (Sprint 2): implement per docs/PRD_ADDENDUM.md §A2 — this is the
    mechanism that keeps capital-intensive sectors from being screened out
    by thresholds tuned for asset-light businesses.
    """
    raise NotImplementedError


def run_screen(run_id: str, conn) -> None:
    """Score every active company against METRICS, write to quant_scores,
    then roll up into quality_scores.passed_screen.

    TODO (Sprint 2): for each metric, compute absolute_floor_pass (from
    moat/config.py:ABSOLUTE_FLOORS) and sector_relative_pass (from
    compute_sector_percentile), combine per §A2, persist per-metric rows.
    """
    raise NotImplementedError
