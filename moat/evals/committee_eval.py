"""Frozen §8 committee alignment and paired-movement evaluation."""

from __future__ import annotations

import math
from collections import Counter

from .benchmark import canonical_ticker, star_rating_value
from .constants import STATUS_ORDINAL
from .database import ImmutableDatabase
from .statistics import spearman_rho
from .valuation import benchmark_valuation

SUPPORT_CATEGORIES = ("strong", "weak", "ambiguous", "contradicted", "abstain")
_SUPPORT_ORDINAL = {"contradicted": 0.0, "ambiguous": 1.0, "weak": 2.0, "strong": 3.0}


def external_support(moat_label: str | None, valuation_class: str) -> str:
    if moat_label == "None" or valuation_class == "Expensive":
        return "contradicted"
    if moat_label is None or valuation_class == "Abstain":
        return "abstain"
    if moat_label == "Wide" and valuation_class == "Cheap":
        return "strong"
    if moat_label == "Narrow" and valuation_class == "Cheap":
        return "weak"
    return "ambiguous"


def evaluate_committee(
    db: ImmutableDatabase,
    *,
    committee_run_id: str,
    screen_run_id: str,
    benchmark_records: list[dict],
    invalid_tickers: set[str] | None = None,
) -> tuple[list[dict], dict]:
    invalid_tickers = invalid_tickers or set()
    benchmark = {row["ticker"]: row for row in benchmark_records}
    verdicts = {canonical_ticker(row["ticker"]): row for row in db.committee_full_rows(committee_run_id)}
    passers = {
        canonical_ticker(row["ticker"]): row for row in db.quality_rows(screen_run_id)
        if row["passed_screen"] == 1
    }
    details: list[dict] = []
    for ticker in sorted(passers):
        source = benchmark.get(ticker)
        benchmark_value = benchmark_valuation(source) if source else {"classification": "Abstain"}
        verdict = None if ticker in invalid_tickers else verdicts.get(ticker)
        if verdict is not None and (
            not isinstance(verdict.get("overall_score"), (int, float))
            or isinstance(verdict.get("overall_score"), bool)
            or not math.isfinite(verdict["overall_score"])
        ):
            verdict = None
        support = external_support(source.get("economic_moat") if source else None, benchmark_value["classification"])
        details.append({
            "ticker": ticker,
            "sector": passers[ticker]["sector"],
            "committee_outcome": verdict["status"] if verdict else "Abstain",
            "overall_score": verdict["overall_score"] if verdict else None,
            "bear_case_severity": verdict["bear_case_severity"] if verdict else None,
            "external_support": support,
            "benchmark_moat": source.get("economic_moat") if source else None,
            "benchmark_valuation": benchmark_value["classification"],
            "benchmark_star_rating": star_rating_value(source.get("star_rating")) if source else None,
            "coverage_tier": source.get("coverage_tier") if source else None,
            "preflight_valid": ticker not in invalid_tickers,
        })

    scored = [row for row in details if row["committee_outcome"] != "Abstain"]
    investigates = [row for row in scored if row["committee_outcome"] == "Investigate"]
    contradicted = sum(row["external_support"] == "contradicted" for row in investigates)
    by_status = {
        status: {category: sum(row["committee_outcome"] == status and row["external_support"] == category for row in scored) for category in SUPPORT_CATEGORIES}
        for status in ("Reject", "Watch", "Investigate")
    }
    enrichment = {}
    for status in ("Reject", "Watch", "Investigate"):
        rows = [row for row in scored if row["committee_outcome"] == status and row["external_support"] != "abstain"]
        supported = sum(row["external_support"] in {"strong", "weak"} for row in rows)
        enrichment[status] = {
            "supported_numerator": supported,
            "denominator": len(rows),
            "supported_rate": supported / len(rows) if rows else None,
            "composition": dict(Counter(row["external_support"] for row in rows)),
        }
    watch = [row for row in scored if row["committee_outcome"] == "Watch" and row["external_support"] in _SUPPORT_ORDINAL and row["overall_score"] is not None]
    ranking = [row for row in scored if row["external_support"] in _SUPPORT_ORDINAL]
    direct_star = [row for row in scored if row["benchmark_star_rating"] is not None]
    summary = {
        "eligible_screen_passer_count": len(details),
        "committee_scored_count": len(scored),
        "committee_abstention_count": len(details) - len(scored),
        "committee_coverage_rate": len(scored) / len(details) if details else None,
        "investigate_support_composition": dict(Counter(row["external_support"] for row in investigates)),
        "investigate_contradiction_rate": {
            "numerator": contradicted,
            "denominator": len(investigates),
            "value": contradicted / len(investigates) if investigates else None,
            "kind": "descriptive_conservative",
        },
        "support_composition_by_status": by_status,
        "research_priority_enrichment": enrichment,
        "priority_support_spearman_rho": {
            "denominator": len(ranking),
            "value": spearman_rho(
                [STATUS_ORDINAL[row["committee_outcome"]] for row in ranking],
                [_SUPPORT_ORDINAL[row["external_support"]] for row in ranking],
            ),
            "kind": "exploratory",
        },
        "watch_score_external_support_trend": {
            "denominator": len(watch),
            "value": spearman_rho(
                [row["overall_score"] for row in watch],
                [_SUPPORT_ORDINAL[row["external_support"]] for row in watch],
            ),
            "kind": "exploratory",
        },
        "direct_status_star_rating_spearman_rho": {
            "denominator": len(direct_star),
            "value": spearman_rho(
                [STATUS_ORDINAL[row["committee_outcome"]] for row in direct_star],
                [float(row["benchmark_star_rating"]) for row in direct_star],
            ),
            "kind": "secondary_exploratory",
        },
        "external_support_numeric_mapping": _SUPPORT_ORDINAL,
    }
    return details, summary


def paired_committee(baseline: list[dict], candidate: list[dict]) -> dict:
    before = {row["ticker"]: row for row in baseline if row["committee_outcome"] != "Abstain"}
    after = {row["ticker"]: row for row in candidate if row["committee_outcome"] != "Abstain"}
    common = sorted(set(before) & set(after))
    records = []
    for ticker in common:
        left, right = before[ticker], after[ticker]
        records.append({
            "ticker": ticker,
            "baseline_score": left["overall_score"],
            "candidate_score": right["overall_score"],
            "score_delta": right["overall_score"] - left["overall_score"],
            "baseline_status": left["committee_outcome"],
            "candidate_status": right["committee_outcome"],
            "status_ordinal_delta": STATUS_ORDINAL[right["committee_outcome"]] - STATUS_ORDINAL[left["committee_outcome"]],
        })
    transitions = Counter((row["baseline_status"], row["candidate_status"]) for row in records)
    return {
        "claim_boundary": "combined Phase 1 effect; committee movement is descriptive until compared with the unchanged-input noise floor",
        "paired_common_count": len(records),
        "baseline_only_count": len(set(before) - set(after)),
        "candidate_only_count": len(set(after) - set(before)),
        "status_transitions": {f"{left}_to_{right}": count for (left, right), count in sorted(transitions.items())},
        "status_flip_count": sum(row["baseline_status"] != row["candidate_status"] for row in records),
        "mean_overall_score_delta": sum(row["score_delta"] for row in records) / len(records) if records else None,
        "records": records,
    }
