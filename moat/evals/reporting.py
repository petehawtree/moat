"""Atomic private artifacts and aggregate-only public reporting."""

from __future__ import annotations

from pathlib import Path

from .constants import PUBLIC_DISCLOSURE_MIN_CELL, TIER_C_OUTCOMES
from .util import atomic_write_json, atomic_write_jsonl


def _suppressed_count(value: int):
    return value if value >= PUBLIC_DISCLOSURE_MIN_CELL else "suppressed_lt_5"


def _public_metric(metric: dict) -> dict:
    denominator = metric.get("denominator")
    numerator = metric.get("numerator")
    if isinstance(denominator, int) and denominator < PUBLIC_DISCLOSURE_MIN_CELL:
        return {"numerator": "suppressed_lt_5", "denominator": "suppressed_lt_5", "value": None, "kind": metric.get("kind")}
    if isinstance(numerator, int) and numerator < PUBLIC_DISCLOSURE_MIN_CELL:
        return {"numerator": "suppressed_lt_5", "denominator": denominator, "value": None, "kind": metric.get("kind")}
    return {key: metric[key] for key in ("numerator", "denominator", "value", "kind") if key in metric}


def _public_association(metric: dict) -> dict:
    denominator = metric.get("denominator", 0)
    value_key = "value" if "value" in metric else "spearman_rho"
    return {
        "denominator": denominator if denominator >= PUBLIC_DISCLOSURE_MIN_CELL else "suppressed_lt_5",
        value_key: metric.get(value_key) if denominator >= PUBLIC_DISCLOSURE_MIN_CELL else None,
        **({"kind": metric["kind"]} if "kind" in metric else {}),
    }


def public_tier_a(summary: dict) -> dict:
    table = {
        outcome: {label: _suppressed_count(count) for label, count in cells.items()}
        for outcome, cells in summary["pass_fail_abstain_by_moat"].items()
    }
    strata = {
        stratum: {label: _suppressed_count(count) for label, count in cells.items()}
        for stratum, cells in summary["moat_distribution_by_run_derived_stratum"].items()
    }
    return {
        "orientation": summary["orientation"],
        "universe_count": summary["universe_count"],
        "benchmark_labelled_count": summary["benchmark_labelled_count"],
        "screen_scored_count": summary["screen_scored_count"],
        "screen_abstention_count": summary["screen_abstention_count"],
        "screen_coverage_rate": summary["screen_coverage_rate"],
        "excluded_count": summary["excluded_count"],
        "pass_fail_abstain_by_moat": table,
        "metrics": {name: _public_metric(metric) for name, metric in summary["metrics"].items()},
        "wide_precision_bootstrap_interval": summary["wide_precision_bootstrap_interval"],
        "moat_distribution_by_run_derived_stratum": strata,
    }


def public_tier_c(summary: dict) -> dict:
    return {
        "lead_finding": summary["lead_finding"],
        "comparison_count": summary["comparison_count"],
        "outcome_distribution": {key: _suppressed_count(value) for key, value in summary["outcome_distribution"].items()},
        "comparable_count": _suppressed_count(summary["comparable_count"]),
        "comparable_coverage_rate": summary["comparable_coverage_rate"] if summary["comparable_count"] >= PUBLIC_DISCLOSURE_MIN_CELL else None,
        "extraction_accuracy": (
            summary["extraction_accuracy"]
            if summary["extraction_accuracy"]["denominator_match_plus_probable_defect"] >= PUBLIC_DISCLOSURE_MIN_CELL
            and summary["extraction_accuracy"]["numerator_match"] >= PUBLIC_DISCLOSURE_MIN_CELL
            else {"numerator_match": "suppressed_lt_5", "denominator_match_plus_probable_defect": _suppressed_count(summary["extraction_accuracy"]["denominator_match_plus_probable_defect"]), "value": None, "context": summary["extraction_accuracy"]["context"]}
        ),
        "outcomes_by_field": {
            field: {outcome: _suppressed_count(counts[outcome]) for outcome in TIER_C_OUTCOMES}
            for field, counts in summary["outcomes_by_field"].items()
        },
        "roe_average_equity_hypothesis": {key: _suppressed_count(value) for key, value in summary["roe_average_equity_hypothesis"].items()},
        "formulas": summary["formulas"],
    }


def public_valuation(summaries: dict[str, dict]) -> dict:
    output = {}
    for key, summary in summaries.items():
        output[key] = {
            "cohort": summary["cohort"],
            "view": summary["view"],
            "orientation": summary["orientation"],
            "eligible_count": summary["eligible_count"],
            "scored_count": summary["scored_count"],
            "abstention_count": summary["abstention_count"],
            "coverage_rate": summary["coverage_rate"],
            "moat_by_benchmark_table": {
                moat: {benchmark: _suppressed_count(count) for benchmark, count in cells.items()}
                for moat, cells in summary["moat_by_benchmark_table"].items()
            },
            "exact_agreement": _public_metric(summary["exact_agreement"]),
            "false_cheap_rate": _public_metric(summary["false_cheap_rate"]),
            "ordinal_spearman_rho": _public_association(summary["ordinal_spearman_rho"]),
            "point_fair_value_direction_agreement": _public_metric(summary["point_fair_value_direction_agreement"]),
            "benchmark_class_distribution": {key: _suppressed_count(value) for key, value in summary["benchmark_class_distribution"].items()},
            "moat_class_distribution": {key: _suppressed_count(value) for key, value in summary["moat_class_distribution"].items()},
        }
    return output


def public_committee(summary: dict) -> dict:
    investigate_denominator = summary["investigate_contradiction_rate"]["denominator"]
    return {
        "eligible_screen_passer_count": summary["eligible_screen_passer_count"],
        "committee_scored_count": summary["committee_scored_count"],
        "committee_abstention_count": summary["committee_abstention_count"],
        "committee_coverage_rate": summary["committee_coverage_rate"],
        "investigate_support_composition": {
            key: _suppressed_count(value) for key, value in summary["investigate_support_composition"].items()
        },
        "investigate_contradiction_rate": (
            _public_metric(summary["investigate_contradiction_rate"])
            if investigate_denominator >= PUBLIC_DISCLOSURE_MIN_CELL
            else {"numerator": "suppressed_lt_5", "denominator": "suppressed_lt_5", "value": None, "kind": summary["investigate_contradiction_rate"]["kind"], "analysis_status": "ran_but_publicly_suppressed"}
        ),
        "support_composition_by_status": {
            status: {key: _suppressed_count(value) for key, value in cells.items()}
            for status, cells in summary["support_composition_by_status"].items()
        },
        "research_priority_enrichment": {
            status: {
                "supported_numerator": _suppressed_count(values["supported_numerator"]),
                "denominator": _suppressed_count(values["denominator"]),
                "supported_rate": values["supported_rate"] if values["denominator"] >= PUBLIC_DISCLOSURE_MIN_CELL and values["supported_numerator"] >= PUBLIC_DISCLOSURE_MIN_CELL else None,
                "composition": {key: _suppressed_count(value) for key, value in values["composition"].items()},
            }
            for status, values in summary["research_priority_enrichment"].items()
        },
        "priority_support_spearman_rho": _public_association(summary["priority_support_spearman_rho"]),
        "watch_score_external_support_trend": _public_association(summary["watch_score_external_support_trend"]),
        "direct_status_star_rating_spearman_rho": _public_association(summary["direct_status_star_rating_spearman_rho"]),
    }


def public_persona(summary: dict) -> dict:
    counterfactuals = {}
    for name, values in summary["counterfactuals"].items():
        counterfactuals[name] = {
            "status_change_count": _suppressed_count(values["status_change_count"]),
            "transitions": {key: _suppressed_count(value) for key, value in values["transitions"].items()},
            "mean_score_delta": values["mean_score_delta"],
        }
    return {
        "role": summary["role"],
        "valid_verdict_count": summary["valid_verdict_count"],
        "invalid_or_missing_numeric_count": summary["invalid_or_missing_numeric_count"],
        "component_medians": summary["component_medians"],
        "median_cohort": summary["median_cohort"],
        "score_distribution": summary["score_distribution"],
        "severity_distribution": {key: _suppressed_count(value) for key, value in summary["severity_distribution"].items()},
        "high_severity_pipeline_diagnostic": summary["high_severity_pipeline_diagnostic"],
        "weighted_score_point_contributions": summary["weighted_score_point_contributions"],
        "counterfactuals": counterfactuals,
        "external_alignment": {
            name: _public_association(values) for name, values in summary["external_alignment"].items()
        },
        "interpretation_boundary": summary["interpretation_boundary"],
        "designed_weights_note": summary["designed_weights_note"],
    }


def public_noise_floor(summary: dict) -> dict:
    if summary.get("status") != "available":
        return {
            "status": summary.get("status"),
            "reason": summary.get("reason"),
            "interpretation": summary.get("interpretation"),
        }
    return {
        "status": "available",
        "replicate_run_id": summary["replicate_run_id"],
        "paired_company_count": summary["paired_company_count"],
        "component_score_delta_distributions": summary["component_score_delta_distributions"],
        "overall_score_delta_distribution": summary["overall_score_delta_distribution"],
        "status_flip_count": summary["status_flip_count"],
        "status_transitions": summary["status_transitions"],
        "bear_severity_change_count": summary["bear_severity_change_count"],
        "limitation": summary["limitation"],
    }


def write_private_outputs(
    directory: Path, *, manifest: dict, preflight: dict, tier_a_records: dict[str, list[dict]],
    tier_a_summaries: dict[str, dict], exclusions: dict[str, list[dict]],
    tier_c_records: dict[str, list[dict]], tier_c_summaries: dict[str, dict], paired: dict,
    valuation_records: dict[str, list[dict]], valuation_summaries: dict[str, dict], valuation_paired: dict,
    committee_records: dict[str, list[dict]], committee_summaries: dict[str, dict], committee_paired: dict,
    noise_records: list[dict], noise_summary: dict,
    replay_records: list[dict], replay_summary: dict,
    persona_records: dict[str, list[dict]], persona_summaries: dict[str, dict],
) -> None:
    atomic_write_json(directory / "execution-manifest.resolved.json", manifest)
    atomic_write_json(directory / "preflight.json", preflight)
    for name in ("baseline", "candidate"):
        atomic_write_jsonl(directory / f"tier-a-{name}-records.jsonl", tier_a_records[name])
        atomic_write_json(directory / f"tier-a-{name}-summary.json", tier_a_summaries[name])
        atomic_write_jsonl(directory / f"tier-a-{name}-exclusions.jsonl", exclusions[name])
        atomic_write_jsonl(directory / f"tier-c-{name}-records.jsonl", tier_c_records[name])
        atomic_write_json(directory / f"tier-c-{name}-summary.json", tier_c_summaries[name])
        atomic_write_jsonl(directory / f"valuation-{name}-records.jsonl", valuation_records[name])
        atomic_write_json(directory / f"valuation-{name}-summary.json", valuation_summaries[name])
        atomic_write_jsonl(directory / f"committee-{name}-records.jsonl", committee_records[name])
        atomic_write_json(directory / f"committee-{name}-summary.json", committee_summaries[name])
        atomic_write_jsonl(directory / f"persona-{name}-records.jsonl", persona_records[name])
        atomic_write_json(directory / f"persona-{name}-summary.json", persona_summaries[name])
    atomic_write_json(directory / "tier-a-paired.json", paired)
    atomic_write_json(directory / "valuation-paired.json", valuation_paired)
    atomic_write_json(directory / "committee-paired.json", committee_paired)
    atomic_write_jsonl(directory / "committee-noise-floor-records.jsonl", noise_records)
    atomic_write_json(directory / "committee-noise-floor-summary.json", noise_summary)
    atomic_write_jsonl(directory / "per-fix-replay-records.jsonl", replay_records)
    atomic_write_json(directory / "per-fix-replay-summary.json", replay_summary)


def write_public_report(
    path: Path, *, manifest: dict, preflight_public: dict, tier_a_summaries: dict[str, dict],
    tier_c_summaries: dict[str, dict], paired: dict,
    valuation_summaries: dict[str, dict], valuation_paired: dict,
    committee_summaries: dict[str, dict], committee_paired: dict,
    noise_summary: dict, replay_summary: dict,
    persona_summaries: dict[str, dict],
) -> None:
    public = {
        "report_version": "eval-public-tier-a-c-v1",
        "evaluation_mode": manifest["evaluation_mode"],
        "authoritative": manifest["evaluation_mode"] == "authoritative",
        "execution": {
            "timestamp": manifest["execution"]["timestamp"],
            "eval_spec_version": manifest["eval_spec"]["version"],
            "eval_spec_sha256": manifest["eval_spec"]["sha256"],
            "harness_git_commit": manifest["harness"]["git_commit"],
        },
        "reproducibility_checks": preflight_public,
        "universe_flow": {
            name: {
                "universe_count": summary["universe_count"],
                "scored_count": summary["screen_scored_count"],
                "abstention_count": summary["screen_abstention_count"],
                "exclusion_count": summary["excluded_count"],
            }
            for name, summary in tier_a_summaries.items()
        },
        "tier_a": {name: public_tier_a(summary) for name, summary in tier_a_summaries.items()},
        "valuation": {name: public_valuation(summary) for name, summary in valuation_summaries.items()},
        "committee": {name: public_committee(summary) for name, summary in committee_summaries.items()},
        "tier_c": {name: public_tier_c(summary) for name, summary in tier_c_summaries.items()},
        "tier_b": {"status": "not_run", "reason": "rubric, atomic themes, and frozen 24-company cohort are not decided"},
        "paired_baseline_candidate": {
            "tier_a": {key: value for key, value in paired.items() if key != "records"},
            "valuation": {
                key: (
                    {inner_key: inner_value for inner_key, inner_value in value.items() if inner_key != "records"}
                    if isinstance(value, dict) else value
                )
                for key, value in valuation_paired.items()
            },
            "committee": {key: value for key, value in committee_paired.items() if key != "records"},
        },
        "persona_influence_and_external_alignment": {
            name: public_persona(summary) for name, summary in persona_summaries.items()
        },
        "sensitivity_analyses": {
            "committee_noise_floor": public_noise_floor(noise_summary),
            "deterministic_per_fix_replay": replay_summary,
        },
        "limitations": [
            "Development output is not an authoritative evaluation unless explicitly labelled authoritative.",
            "Tier C compares benchmark TTM with Moat fiscal-year data; absolute-value comparisons are generally Inconclusive.",
            "Tier B judging and human validation have not run.",
            "Committee movement is descriptive when the unchanged-input noise-floor replicate is unavailable.",
            "Baseline/candidate differences are the combined Phase 1 effect and are descriptive, not per-fix causal claims.",
        ],
        "claims": {
            "supported": ["mechanical Tier A, valuation, committee, Tier C, and persona calculations completed under frozen v1 rules"],
            "unsupported": ["Tier B theme recall", "authoritative release conclusion"],
            "unresolved": ["pending human validation", "committee stochastic-noise floor when unavailable", "per-fix causation without isolated replay evidence"],
        },
    }
    atomic_write_json(path, public)
