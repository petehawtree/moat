"""AI qualitative analysis (PRD §5): business quality, moat, management, risk.

Sprint 3 scope. Two hard rules from docs/PRD_ADDENDUM.md §A3 and §A5 that
must hold from the first implementation, not retrofitted later:

1. Every claim must resolve to a citation (accession_number + quote).
   `validate_citations` below is the enforcement point — analysis that
   fails it must not be persisted.
2. AI stages are cached per docs/PRD_ADDENDUM.md §A5: only regenerate when
   the source filing's content_hash changes. Never re-run on every
   scheduled pipeline pass — that's the cost-control mechanism.
"""
from __future__ import annotations

ANALYSIS_TYPES = ["business_quality", "moat", "management", "risk"]


def needs_refresh(ticker: str, analysis_type: str, current_filing_hash: str, conn) -> bool:
    """True if there's no cached ai_analysis row for this cache_key (§A5)."""
    raise NotImplementedError


def build_prompt(analysis_type: str, filing_excerpts: list[dict]) -> str:
    """Construct the grounded prompt for one analysis type.

    TODO (Sprint 3): prompt template per analysis_type covering the PRD §5
    sub-points (e.g. moat: network effects, brand, switching costs, cost
    advantage, scale, regulation, IP/data advantage). Must instruct the
    model to cite the supplied excerpts and to say "insufficient evidence"
    rather than infer beyond them.
    """
    raise NotImplementedError


def validate_citations(content: str, citations: list[dict], filing_excerpts: list[dict]) -> bool:
    """Reject analysis whose citations don't resolve to a real, supplied excerpt.

    TODO (Sprint 3): every citation's quote must be a substring (or close
    fuzzy match) of one of the filing_excerpts actually given to the model —
    this blocks fabricated citations, not just missing ones.
    """
    raise NotImplementedError


def run_analysis(ticker: str, analysis_type: str, run_id: str, conn) -> None:
    """Full stage: gather excerpts, call Claude, validate, persist.

    TODO (Sprint 3): skip entirely if needs_refresh() is False — reuse the
    prior run's cached row instead (still copy it forward under the new
    run_id so downstream stages have a consistent read).
    """
    raise NotImplementedError
