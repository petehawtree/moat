"""Deterministic pre-AI quality score (PRD §3's "quality score" stage).

This is distinct from the AI-driven "business quality" write-up in
moat/ai/ and the final weighted §8 score in moat/committee/ — see
docs/PRD_ADDENDUM.md note on §8 vs §3 potentially being conflated.
This module produces a purely numeric, explainable pre-filter score used
to decide which ~50-100 candidates proceed to the (expensive) AI stages.
"""
from __future__ import annotations


def compute_quality_score(quant_score_rows: list[dict]) -> float:
    """Combine quant_scores rows for one company into a single 0-100 score.

    TODO (Sprint 2): simple weighted count of metrics passed (absolute +
    sector-relative) is a reasonable first cut; refine once real data shows
    how discriminating each metric actually is.
    """
    raise NotImplementedError
