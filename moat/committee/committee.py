"""Investment Committee (PRD §7) and final weighted score (PRD §8).

Sprint 5 scope. Three LLM personas (Quality / Bear / Valuation analyst)
consolidate the upstream ai_analysis + valuations rows into a single
ranked verdict per company.
"""
from __future__ import annotations

WEIGHTS = {
    "business_quality": 0.25,
    "competitive_moat": 0.20,
    "financial_strength": 0.15,
    "management": 0.10,
    "valuation": 0.25,
    "risk": 0.05,
}


def compute_overall_score(component_scores: dict[str, float]) -> float:
    """Weighted sum per PRD §8. Raises if any component is missing —
    an incomplete score should be visibly blocked, not silently partial.
    """
    missing = set(WEIGHTS) - set(component_scores)
    if missing:
        raise ValueError(f"Missing score components: {missing}")
    return sum(component_scores[k] * w for k, w in WEIGHTS.items())


def assign_status(overall_score: float, bear_case_severity: str) -> str:
    """Map score + bear-case strength to Investigate / Watch / Reject.

    TODO (Sprint 5): thresholds are a judgment call — start conservative
    (higher bar for "Investigate") and tune against real output, per
    PRD §1's "challenge every thesis."
    """
    raise NotImplementedError


def run_committee(ticker: str, run_id: str, conn) -> None:
    """Full stage: run 3 persona prompts, consolidate, persist to
    committee_verdicts, including the rolled-up data_confidence (§A4).

    TODO (Sprint 5).
    """
    raise NotImplementedError
