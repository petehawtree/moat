"""Frozen §14 persona influence and external-alignment analysis."""

from __future__ import annotations

import math
import statistics
from collections import Counter

from .benchmark import canonical_ticker
from .constants import (
    COMPONENT_WEIGHTS, PERSONA_COMPONENTS, UNCERTAINTY_ORDINAL, VALUATION_ORDINAL,
)
from .database import ImmutableDatabase
from .statistics import spearman_rho
from .valuation import benchmark_valuation

_MOAT_ORDINAL = {"None": 0.0, "Narrow": 1.0, "Wide": 2.0}
_CAPITAL_ALLOCATION_ORDINAL = {"Poor": 0.0, "Standard": 1.0, "Exemplary": 2.0}


def _grade_ordinal(value) -> float | None:
    if not isinstance(value, str):
        return None
    grade = value.strip().upper()
    if not grade or grade[0] not in {"A", "B", "C", "D", "F"} or grade[1:] not in {"", "+", "-"}:
        return None
    base = {"F": 0.0, "D": 1.0, "C": 2.0, "B": 3.0, "A": 4.0}[grade[0]]
    return base + (0.33 if grade.endswith("+") else -0.33 if grade.endswith("-") else 0.0)


def _benefit(component: str, raw_value: float) -> float:
    return 100.0 - raw_value if component == "risk_score" else raw_value


def compute_overall(scores: dict[str, float]) -> float:
    return sum(_benefit(component, scores[component]) * weight for component, weight in COMPONENT_WEIGHTS.items())


def assign_status(score: float, severity: str, *, apply_bear_cap: bool = True) -> str:
    if score >= 70.0:
        return "Watch" if apply_bear_cap and severity == "high" else "Investigate"
    if score >= 50.0:
        return "Watch"
    return "Reject"


def _renormalized_score(scores: dict[str, float], removed: set[str]) -> float:
    remaining_weight = sum(weight for component, weight in COMPONENT_WEIGHTS.items() if component not in removed)
    return sum(
        _benefit(component, scores[component]) * weight
        for component, weight in COMPONENT_WEIGHTS.items() if component not in removed
    ) / remaining_weight


def _replace(scores: dict[str, float], components: tuple[str, ...], replacements: dict[str, float]) -> dict[str, float]:
    result = dict(scores)
    for component in components:
        result[component] = replacements[component]
    return result


def _valid_row(row: dict) -> bool:
    return (
        row.get("bear_case_severity") in {"low", "medium", "high"}
        and row.get("status") in {"Investigate", "Watch", "Reject"}
        and isinstance(row.get("overall_score"), (int, float))
        and not isinstance(row.get("overall_score"), bool)
        and math.isfinite(row["overall_score"])
        and all(
            isinstance(row.get(component), (int, float))
            and not isinstance(row.get(component), bool)
            and math.isfinite(row[component])
            and 0 <= row[component] <= 100
            for component in COMPONENT_WEIGHTS
        )
    )


def _transition_summary(records: list[dict], key: str) -> dict:
    transitions = Counter((row["stored_status"], row[key]) for row in records)
    return {
        "status_change_count": sum(count for (left, right), count in transitions.items() if left != right),
        "transitions": {f"{left}_to_{right}": count for (left, right), count in sorted(transitions.items())},
        "mean_score_delta": sum(row[f"{key}_score"] - row["stored_overall_score"] for row in records) / len(records) if records else None,
    }


def evaluate_persona_influence(
    db: ImmutableDatabase,
    *,
    committee_run_id: str,
    benchmark_records: list[dict],
    primary: bool,
) -> tuple[list[dict], dict]:
    rows = [row for row in db.committee_full_rows(committee_run_id) if _valid_row(row)]
    medians = {
        component: statistics.median(row[component] for row in rows)
        for component in COMPONENT_WEIGHTS
    } if rows else {}
    details: list[dict] = []
    for row in rows:
        ticker = canonical_ticker(row["ticker"])
        scores = {component: float(row[component]) for component in COMPONENT_WEIGHTS}
        stored_score = float(row["overall_score"])
        severity = row["bear_case_severity"]
        record = {
            "ticker": ticker,
            "stored_overall_score": stored_score,
            "stored_status": row["status"],
            "bear_case_severity": severity,
            "recomputed_overall_score": compute_overall(scores),
            "weighted_contributions": {
                persona: sum(_benefit(component, scores[component]) * COMPONENT_WEIGHTS[component] for component in components)
                for persona, components in PERSONA_COMPONENTS.items()
            },
            "counterfactuals": {},
        }
        for persona, components in PERSONA_COMPONENTS.items():
            typical_scores = _replace(scores, components, medians)
            midpoint_scores = _replace(scores, components, {component: 50.0 for component in components})
            zero_replacements = {
                component: 100.0 if component == "risk_score" else 0.0 for component in components
            }
            zero_scores = _replace(scores, components, zero_replacements)
            renormalized = _renormalized_score(scores, set(components))
            for name, value in (
                ("typical_output_replacement", compute_overall(typical_scores)),
                ("mid_scale_replacement", compute_overall(midpoint_scores)),
                ("zero_contribution_removal", compute_overall(zero_scores)),
                ("renormalized_removal", renormalized),
            ):
                record["counterfactuals"][f"{persona}_{name}"] = {
                    "score": value,
                    "status": assign_status(value, severity, apply_bear_cap=True),
                }
        no_cap_status = assign_status(stored_score, severity, apply_bear_cap=False)
        full_bear_score = _renormalized_score(scores, {"risk_score"})
        record["counterfactuals"]["Bear_cap_disabled"] = {
            "score": stored_score,
            "status": no_cap_status,
        }
        record["counterfactuals"]["Bear_full_removal_secondary"] = {
            "score": full_bear_score,
            "status": assign_status(full_bear_score, severity, apply_bear_cap=False),
        }
        details.append(record)

    flattened: list[dict] = []
    for record in details:
        flat = {
            "ticker": record["ticker"],
            "stored_status": record["stored_status"],
            "stored_overall_score": record["stored_overall_score"],
        }
        for key, value in record["counterfactuals"].items():
            flat[key] = value["status"]
            flat[f"{key}_score"] = value["score"]
        flattened.append(flat)

    benchmark = {row["ticker"]: row for row in benchmark_records}
    quality_alignment, business_alignment, moat_alignment = [], [], []
    management_alignment, valuation_alignment, valuation_component_alignment = [], [], []
    financial_strength_alignment, bear_alignment = [], []
    for row, detail in zip(rows, details):
        source = benchmark.get(detail["ticker"])
        if source is None:
            continue
        scores = {component: float(row[component]) for component in COMPONENT_WEIGHTS}
        quality_index = sum(scores[component] * COMPONENT_WEIGHTS[component] for component in PERSONA_COMPONENTS["Quality"]) / 0.55
        valuation_index = sum(scores[component] * COMPONENT_WEIGHTS[component] for component in PERSONA_COMPONENTS["Valuation"]) / 0.40
        if source.get("economic_moat") in _MOAT_ORDINAL:
            quality_alignment.append((quality_index, _MOAT_ORDINAL[source["economic_moat"]]))
            business_alignment.append((scores["business_quality_score"], _MOAT_ORDINAL[source["economic_moat"]]))
            moat_alignment.append((scores["competitive_moat_score"], _MOAT_ORDINAL[source["economic_moat"]]))
        if source.get("capital_allocation") in _CAPITAL_ALLOCATION_ORDINAL:
            management_alignment.append((scores["management_score"], _CAPITAL_ALLOCATION_ORDINAL[source["capital_allocation"]]))
        target = benchmark_valuation(source)["classification"]
        if target in VALUATION_ORDINAL and source.get("coverage_tier") == "analyst":
            valuation_alignment.append((valuation_index, VALUATION_ORDINAL[target]))
            valuation_component_alignment.append((scores["valuation_score"], VALUATION_ORDINAL[target]))
        grade = _grade_ordinal(source.get("financial_health_grade"))
        if grade is not None:
            financial_strength_alignment.append((scores["financial_strength_score"], grade))
        if source.get("uncertainty") in UNCERTAINTY_ORDINAL:
            bear_alignment.append((scores["risk_score"], UNCERTAINTY_ORDINAL[source["uncertainty"]]))

    counterfactual_summary = {}
    if flattened:
        for key in details[0]["counterfactuals"]:
            counterfactual_summary[key] = _transition_summary(flattened, key)
    contributions = {
        persona: {
            "mean_weighted_score_points": sum(row["weighted_contributions"][persona] for row in details) / len(details) if details else None,
            "designed_weight": sum(COMPONENT_WEIGHTS[component] for component in components),
        }
        for persona, components in PERSONA_COMPONENTS.items()
    }
    severities = Counter(row["bear_case_severity"] for row in details)
    summary = {
        "role": "primary_candidate" if primary else "secondary_baseline",
        "valid_verdict_count": len(details),
        "invalid_or_missing_numeric_count": len(db.committee_full_rows(committee_run_id)) - len(details),
        "component_medians": medians,
        "median_cohort": "all valid verdicts in the evaluated committee run",
        "score_distribution": {
            "minimum": min((row["stored_overall_score"] for row in details), default=None),
            "median": statistics.median((row["stored_overall_score"] for row in details)) if details else None,
            "maximum": max((row["stored_overall_score"] for row in details), default=None),
        },
        "severity_distribution": dict(severities),
        "high_severity_pipeline_diagnostic": {
            "numerator": severities["high"],
            "denominator": len(details),
            "value": severities["high"] / len(details) if details else None,
        },
        "weighted_score_point_contributions": contributions,
        "counterfactuals": counterfactual_summary,
        "external_alignment": {
            "Quality_vs_economic_moat": {"denominator": len(quality_alignment), "spearman_rho": spearman_rho([x for x, _ in quality_alignment], [y for _, y in quality_alignment])},
            "business_quality_score_vs_economic_moat": {"denominator": len(business_alignment), "spearman_rho": spearman_rho([x for x, _ in business_alignment], [y for _, y in business_alignment])},
            "competitive_moat_score_vs_economic_moat": {"denominator": len(moat_alignment), "spearman_rho": spearman_rho([x for x, _ in moat_alignment], [y for _, y in moat_alignment])},
            "Quality_management_vs_capital_allocation": {"denominator": len(management_alignment), "spearman_rho": spearman_rho([x for x, _ in management_alignment], [y for _, y in management_alignment])},
            "Valuation_vs_analyst_uncertainty_adjusted_value": {"denominator": len(valuation_alignment), "spearman_rho": spearman_rho([x for x, _ in valuation_alignment], [y for _, y in valuation_alignment])},
            "valuation_score_vs_analyst_uncertainty_adjusted_value": {"denominator": len(valuation_component_alignment), "spearman_rho": spearman_rho([x for x, _ in valuation_component_alignment], [y for _, y in valuation_component_alignment])},
            "financial_strength_score_vs_financial_health_grade": {"denominator": len(financial_strength_alignment), "spearman_rho": spearman_rho([x for x, _ in financial_strength_alignment], [y for _, y in financial_strength_alignment])},
            "Bear_risk_vs_uncertainty": {"denominator": len(bear_alignment), "spearman_rho": spearman_rho([x for x, _ in bear_alignment], [y for _, y in bear_alignment])},
        },
        "interpretation_boundary": "arithmetic influence and external alignment, not persona effectiveness",
        "designed_weights_note": "Quality 55%, Valuation 40%, Bear 5% plus categorical cap",
    }
    return details, summary
