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


def write_private_outputs(
    directory: Path, *, manifest: dict, preflight: dict, tier_a_records: dict[str, list[dict]],
    tier_a_summaries: dict[str, dict], exclusions: dict[str, list[dict]],
    tier_c_records: dict[str, list[dict]], tier_c_summaries: dict[str, dict], paired: dict,
) -> None:
    atomic_write_json(directory / "execution-manifest.resolved.json", manifest)
    atomic_write_json(directory / "preflight.json", preflight)
    for name in ("baseline", "candidate"):
        atomic_write_jsonl(directory / f"tier-a-{name}-records.jsonl", tier_a_records[name])
        atomic_write_json(directory / f"tier-a-{name}-summary.json", tier_a_summaries[name])
        atomic_write_jsonl(directory / f"tier-a-{name}-exclusions.jsonl", exclusions[name])
        atomic_write_jsonl(directory / f"tier-c-{name}-records.jsonl", tier_c_records[name])
        atomic_write_json(directory / f"tier-c-{name}-summary.json", tier_c_summaries[name])
    atomic_write_json(directory / "tier-a-paired.json", paired)


def write_public_report(
    path: Path, *, manifest: dict, preflight_public: dict, tier_a_summaries: dict[str, dict],
    tier_c_summaries: dict[str, dict], paired: dict,
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
        "tier_c": {name: public_tier_c(summary) for name, summary in tier_c_summaries.items()},
        "paired_baseline_candidate": {
            key: value for key, value in paired.items() if key != "records"
        },
        "tier_b": {"status": "not_run", "reason": "outside mechanical Tier A/C phase"},
        "valuation_committee_persona": {"status": "not_implemented_in_this_phase"},
        "limitations": [
            "Development output is not an authoritative evaluation unless explicitly labelled authoritative.",
            "Tier C compares benchmark TTM with Moat fiscal-year data; absolute-value comparisons are generally Inconclusive.",
            "Tier B judging and human validation have not run.",
            "Baseline/candidate differences are the combined Phase 1 effect and are descriptive, not per-fix causal claims.",
        ],
        "claims": {
            "supported": ["mechanical Tier A and Tier C calculations completed under frozen v1 rules"],
            "unsupported": ["Tier B theme recall", "authoritative release conclusion", "single-fix causal attribution"],
            "unresolved": ["pending human validation", "committee stochastic-noise floor"],
        },
    }
    atomic_write_json(path, public)
