"""Atomic private artifacts and aggregate-only public reporting."""

from __future__ import annotations

from pathlib import Path

from .constants import PUBLIC_DISCLOSURE_MIN_CELL, TIER_C_OUTCOMES
from .util import atomic_write_json, atomic_write_jsonl


SUPPRESSED_SMALL_CELL = "suppressed_lt_5"
WITHHELD_SECONDARY = "withheld_secondary_suppression"


def _suppressed_count(value: int):
    return value if value >= PUBLIC_DISCLOSURE_MIN_CELL else SUPPRESSED_SMALL_CELL


def _secondary_suppressed_count(value: int):
    """Hide a whole connected count family while retaining the reason."""
    return SUPPRESSED_SMALL_CELL if value < PUBLIC_DISCLOSURE_MIN_CELL else WITHHELD_SECONDARY


def _has_small_count(values) -> bool:
    if isinstance(values, bool):
        return False
    if isinstance(values, int):
        return values < PUBLIC_DISCLOSURE_MIN_CELL
    if isinstance(values, dict):
        return any(_has_small_count(value) for value in values.values())
    if isinstance(values, (list, tuple)):
        return any(_has_small_count(value) for value in values)
    return False


def _public_count_tree(values, *, secondary_suppression: bool):
    if isinstance(values, bool):
        return values
    if isinstance(values, int):
        return (
            _secondary_suppressed_count(values)
            if secondary_suppression
            else _suppressed_count(values)
        )
    if isinstance(values, dict):
        return {
            key: _public_count_tree(value, secondary_suppression=secondary_suppression)
            for key, value in values.items()
        }
    return values


def _small_cell_or_complement(numerator: int, denominator: int) -> bool:
    """Publishing both counts would also publish denominator - numerator."""
    return numerator < PUBLIC_DISCLOSURE_MIN_CELL or denominator - numerator < PUBLIC_DISCLOSURE_MIN_CELL


def _public_metric(metric: dict, *, secondary_suppression: bool = False) -> dict:
    denominator = metric.get("denominator")
    numerator = metric.get("numerator")
    if secondary_suppression:
        return {
            "numerator": (
                _secondary_suppressed_count(numerator)
                if isinstance(numerator, int)
                else None
            ),
            "denominator": (
                _secondary_suppressed_count(denominator)
                if isinstance(denominator, int)
                else None
            ),
            "value": None,
            "kind": metric.get("kind"),
        }
    if isinstance(denominator, int) and denominator < PUBLIC_DISCLOSURE_MIN_CELL:
        return {"numerator": SUPPRESSED_SMALL_CELL, "denominator": SUPPRESSED_SMALL_CELL, "value": None, "kind": metric.get("kind")}
    if isinstance(numerator, int) and isinstance(denominator, int) and _small_cell_or_complement(numerator, denominator):
        return {"numerator": SUPPRESSED_SMALL_CELL, "denominator": denominator, "value": None, "kind": metric.get("kind")}
    return {key: metric[key] for key in ("numerator", "denominator", "value", "kind") if key in metric}


def _public_association(metric: dict) -> dict:
    denominator = metric.get("denominator", 0)
    value_key = "value" if "value" in metric else "spearman_rho"
    return {
        "denominator": denominator if denominator >= PUBLIC_DISCLOSURE_MIN_CELL else "suppressed_lt_5",
        value_key: metric.get(value_key) if denominator >= PUBLIC_DISCLOSURE_MIN_CELL else None,
        **({"kind": metric["kind"]} if "kind" in metric else {}),
    }


def _tier_a_suppression(summary: dict) -> tuple[bool, bool]:
    coverage = _has_small_count({"excluded_count": summary["excluded_count"]})
    outcomes = _has_small_count(
        {
            "pass_fail_abstain_by_moat": summary["pass_fail_abstain_by_moat"],
            "moat_distribution_by_run_derived_stratum": summary[
                "moat_distribution_by_run_derived_stratum"
            ],
        }
    )
    return coverage, outcomes


def public_tier_a(
    summary: dict, *, coverage_secondary: bool | None = None,
    outcome_secondary: bool | None = None,
) -> dict:
    local_coverage, local_outcomes = _tier_a_suppression(summary)
    coverage_secondary = local_coverage if coverage_secondary is None else coverage_secondary
    outcome_secondary = local_outcomes if outcome_secondary is None else outcome_secondary
    # Screen totals and both classification tables reconstruct the labelled
    # cohort, and screen totals reconstruct the universe. Coverage suppression
    # must therefore flow into the outcome family even if all outcome cells are
    # individually large enough to publish.
    outcome_secondary = coverage_secondary or outcome_secondary
    table = _public_count_tree(
        summary["pass_fail_abstain_by_moat"],
        secondary_suppression=outcome_secondary,
    )
    strata = _public_count_tree(
        summary["moat_distribution_by_run_derived_stratum"],
        secondary_suppression=outcome_secondary,
    )
    benchmark_labelled_secondary = coverage_secondary or outcome_secondary
    return {
        "orientation": summary["orientation"],
        "universe_count": (
            _secondary_suppressed_count(summary["universe_count"])
            if coverage_secondary else summary["universe_count"]
        ),
        "benchmark_labelled_count": (
            _secondary_suppressed_count(summary["benchmark_labelled_count"])
            if benchmark_labelled_secondary else summary["benchmark_labelled_count"]
        ),
        "screen_scored_count": (
            _secondary_suppressed_count(summary["screen_scored_count"])
            if outcome_secondary else summary["screen_scored_count"]
        ),
        "screen_abstention_count": (
            _secondary_suppressed_count(summary["screen_abstention_count"])
            if outcome_secondary else summary["screen_abstention_count"]
        ),
        "screen_coverage_rate": None if outcome_secondary else summary["screen_coverage_rate"],
        "excluded_count": (
            _secondary_suppressed_count(summary["excluded_count"])
            if coverage_secondary else _suppressed_count(summary["excluded_count"])
        ),
        "pass_fail_abstain_by_moat": table,
        "metrics": {
            name: _public_metric(metric, secondary_suppression=outcome_secondary)
            for name, metric in summary["metrics"].items()
        },
        "wide_precision_bootstrap_interval": (
            None if outcome_secondary else summary["wide_precision_bootstrap_interval"]
        ),
        "moat_distribution_by_run_derived_stratum": strata,
    }


def _tier_c_suppression(summary: dict) -> bool:
    return _has_small_count(
        {
            "outcome_distribution": summary["outcome_distribution"],
            "outcomes_by_field": summary["outcomes_by_field"],
            "roe_average_equity_hypothesis": summary["roe_average_equity_hypothesis"],
            "extraction_accuracy": {
                "numerator": summary["extraction_accuracy"]["numerator_match"],
                "denominator": summary["extraction_accuracy"][
                    "denominator_match_plus_probable_defect"
                ],
            },
        }
    )


def public_tier_c(summary: dict, *, secondary_suppression: bool | None = None) -> dict:
    if secondary_suppression is None:
        secondary_suppression = _tier_c_suppression(summary)
    if secondary_suppression:
        accuracy = {
            "numerator_match": _secondary_suppressed_count(
                summary["extraction_accuracy"]["numerator_match"]
            ),
            "denominator_match_plus_probable_defect": _secondary_suppressed_count(
                summary["extraction_accuracy"]["denominator_match_plus_probable_defect"]
            ),
            "value": None,
            "context": summary["extraction_accuracy"]["context"],
        }
    else:
        accuracy = (
            summary["extraction_accuracy"]
            if summary["extraction_accuracy"]["denominator_match_plus_probable_defect"] >= PUBLIC_DISCLOSURE_MIN_CELL
            and summary["extraction_accuracy"]["numerator_match"] >= PUBLIC_DISCLOSURE_MIN_CELL
            else {"numerator_match": SUPPRESSED_SMALL_CELL, "denominator_match_plus_probable_defect": _suppressed_count(summary["extraction_accuracy"]["denominator_match_plus_probable_defect"]), "value": None, "context": summary["extraction_accuracy"]["context"]}
        )
    return {
        "lead_finding": summary["lead_finding"],
        "comparison_count": (
            _secondary_suppressed_count(summary["comparison_count"])
            if secondary_suppression else summary["comparison_count"]
        ),
        "outcome_distribution": _public_count_tree(
            summary["outcome_distribution"],
            secondary_suppression=secondary_suppression,
        ),
        "comparable_count": (
            _secondary_suppressed_count(summary["comparable_count"])
            if secondary_suppression else _suppressed_count(summary["comparable_count"])
        ),
        "comparable_coverage_rate": (
            None
            if secondary_suppression or summary["comparable_count"] < PUBLIC_DISCLOSURE_MIN_CELL
            else summary["comparable_coverage_rate"]
        ),
        "extraction_accuracy": accuracy,
        "outcomes_by_field": _public_count_tree(
            {
                field: {outcome: counts[outcome] for outcome in TIER_C_OUTCOMES}
                for field, counts in summary["outcomes_by_field"].items()
            },
            secondary_suppression=secondary_suppression,
        ),
        "roe_average_equity_hypothesis": _public_count_tree(
            summary["roe_average_equity_hypothesis"],
            secondary_suppression=secondary_suppression,
        ),
        "formulas": summary["formulas"],
    }


def _valuation_suppression(summary: dict) -> bool:
    return _has_small_count(
        {
            "moat_by_benchmark_table": summary["moat_by_benchmark_table"],
            "benchmark_class_distribution": summary["benchmark_class_distribution"],
            "moat_class_distribution": summary["moat_class_distribution"],
            "exact_agreement": {
                "numerator": summary["exact_agreement"].get("numerator"),
                "denominator": summary["exact_agreement"].get("denominator"),
            },
            "false_cheap_rate": {
                "numerator": summary["false_cheap_rate"].get("numerator"),
                "denominator": summary["false_cheap_rate"].get("denominator"),
            },
        }
    )


def public_valuation(
    summaries: dict[str, dict], *, secondary_cohorts: set[str] | None = None,
) -> dict:
    output = {}
    for key, summary in summaries.items():
        secondary = (
            _valuation_suppression(summary)
            if secondary_cohorts is None
            else summary["cohort"] in secondary_cohorts
        )
        output[key] = {
            "cohort": summary["cohort"],
            "view": summary["view"],
            "orientation": summary["orientation"],
            "eligible_count": (
                _secondary_suppressed_count(summary["eligible_count"])
                if secondary else summary["eligible_count"]
            ),
            "scored_count": (
                _secondary_suppressed_count(summary["scored_count"])
                if secondary else summary["scored_count"]
            ),
            "abstention_count": (
                _secondary_suppressed_count(summary["abstention_count"])
                if secondary else summary["abstention_count"]
            ),
            "coverage_rate": None if secondary else summary["coverage_rate"],
            "moat_by_benchmark_table": _public_count_tree(
                summary["moat_by_benchmark_table"],
                secondary_suppression=secondary,
            ),
            "exact_agreement": _public_metric(
                summary["exact_agreement"], secondary_suppression=secondary
            ),
            "false_cheap_rate": _public_metric(
                summary["false_cheap_rate"], secondary_suppression=secondary
            ),
            "ordinal_spearman_rho": (
                {
                    "denominator": _secondary_suppressed_count(
                        summary["ordinal_spearman_rho"]["denominator"]
                    ),
                    "value": None,
                    "kind": summary["ordinal_spearman_rho"].get("kind"),
                }
                if secondary else _public_association(summary["ordinal_spearman_rho"])
            ),
            "point_fair_value_direction_agreement": _public_metric(
                summary["point_fair_value_direction_agreement"],
                secondary_suppression=secondary,
            ),
            "benchmark_class_distribution": _public_count_tree(
                summary["benchmark_class_distribution"],
                secondary_suppression=secondary,
            ),
            "moat_class_distribution": _public_count_tree(
                summary["moat_class_distribution"],
                secondary_suppression=secondary,
            ),
        }
    return output


def public_committee(summary: dict) -> dict:
    investigate_denominator = summary["investigate_contradiction_rate"]["denominator"]
    hide_external_support = _small_cell_or_complement(
        summary["external_support_abstention_count"],
        summary["committee_scored_count"],
    )
    result = {
        "eligible_screen_passer_count": summary["eligible_screen_passer_count"],
        "committee_scored_count": summary["committee_scored_count"],
        "committee_abstention_count": summary["committee_abstention_count"],
        "committee_coverage_rate": summary["committee_coverage_rate"],
        # The two counts partition the public committee_scored_count, so one
        # small cell would be recoverable by subtraction from the other.
        **(
            {
                "external_support_scored_count": "suppressed_lt_5",
                "external_support_abstention_count": "suppressed_lt_5",
            }
            if hide_external_support
            else {
                "external_support_scored_count": summary["external_support_scored_count"],
                "external_support_abstention_count": summary["external_support_abstention_count"],
            }
        ),
        "investigate_support_composition": {
            key: _suppressed_count(value) for key, value in summary["investigate_support_composition"].items()
        },
        "investigate_support_composition_denominator": _suppressed_count(
            summary["investigate_support_composition_denominator"]
        ),
        "investigate_contradiction_rate": (
            _public_metric(summary["investigate_contradiction_rate"])
            if investigate_denominator >= PUBLIC_DISCLOSURE_MIN_CELL
            else {"numerator": "suppressed_lt_5", "denominator": "suppressed_lt_5", "value": None, "kind": summary["investigate_contradiction_rate"]["kind"], "analysis_status": "ran_but_publicly_suppressed"}
        ),
        "support_composition_by_status": {
            status: {key: _suppressed_count(value) for key, value in cells.items()}
            for status, cells in summary["support_composition_by_status"].items()
        },
        "support_composition_by_status_denominators": {
            status: _suppressed_count(value)
            for status, value in summary["support_composition_by_status_denominators"].items()
        },
        "research_priority_enrichment": {
            status: {
                "supported_numerator": (
                    "suppressed_lt_5"
                    if _small_cell_or_complement(values["supported_numerator"], values["denominator"])
                    else values["supported_numerator"]
                ),
                "denominator": _suppressed_count(values["denominator"]),
                "supported_rate": (
                    None
                    if values["denominator"] < PUBLIC_DISCLOSURE_MIN_CELL
                    or _small_cell_or_complement(values["supported_numerator"], values["denominator"])
                    else values["supported_rate"]
                ),
                "composition": {key: _suppressed_count(value) for key, value in values["composition"].items()},
            }
            for status, values in summary["research_priority_enrichment"].items()
        },
        "priority_support_spearman_rho": _public_association(summary["priority_support_spearman_rho"]),
        "watch_score_external_support_trend": _public_association(summary["watch_score_external_support_trend"]),
        "direct_status_star_rating_spearman_rho": _public_association(summary["direct_status_star_rating_spearman_rho"]),
    }
    if hide_external_support:
        # These fields are all derived from the same external-support cohort.
        # Their denominators and count partitions would otherwise reconstruct
        # the suppressed split, either directly or by summing across statuses.
        result["investigate_support_composition"] = {
            key: "suppressed_lt_5" for key in summary["investigate_support_composition"]
        }
        result["investigate_support_composition_denominator"] = "suppressed_lt_5"
        result["investigate_contradiction_rate"] = {
            "numerator": "suppressed_lt_5",
            "denominator": "suppressed_lt_5",
            "value": None,
            "kind": summary["investigate_contradiction_rate"]["kind"],
            "analysis_status": "ran_but_publicly_suppressed",
        }
        result["support_composition_by_status"] = {
            status: {key: "suppressed_lt_5" for key in cells}
            for status, cells in summary["support_composition_by_status"].items()
        }
        result["support_composition_by_status_denominators"] = {
            status: "suppressed_lt_5"
            for status in summary["support_composition_by_status_denominators"]
        }
        result["research_priority_enrichment"] = {
            status: {
                "supported_numerator": "suppressed_lt_5",
                "denominator": "suppressed_lt_5",
                "supported_rate": None,
                "composition": {key: "suppressed_lt_5" for key in values["composition"]},
            }
            for status, values in summary["research_priority_enrichment"].items()
        }
        for field in ("priority_support_spearman_rho", "watch_score_external_support_trend"):
            association = result[field]
            association["denominator"] = "suppressed_lt_5"
            association["value"] = None
    return result


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
        "preflight_invalid_abstention_count": summary["preflight_invalid_abstention_count"],
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
        "input_equivalence_method": summary["input_equivalence_method"],
        "paired_company_count": summary["paired_company_count"],
        "stored_preflight_invalid_abstention_count": summary["stored_preflight_invalid_abstention_count"],
        "replicate_preflight_invalid_abstention_count": summary["replicate_preflight_invalid_abstention_count"],
        "component_score_delta_distributions": summary["component_score_delta_distributions"],
        "overall_score_delta_distribution": summary["overall_score_delta_distribution"],
        "absolute_overall_score_delta_distribution": summary["absolute_overall_score_delta_distribution"],
        "practical_noise_boundary": summary["practical_noise_boundary"],
        "overall_score_noise_interpretation_counts": summary["overall_score_noise_interpretation_counts"],
        "status_flip_count": summary["status_flip_count"],
        "status_flip_rate": summary["status_flip_rate"],
        "status_transitions": summary["status_transitions"],
        "bear_severity_change_count": summary["bear_severity_change_count"],
        "bear_severity_change_rate": summary["bear_severity_change_rate"],
        "bear_severity_transitions": summary["bear_severity_transitions"],
        "status_flip_comparison": summary["status_flip_comparison"],
        "bear_severity_change_comparison": summary["bear_severity_change_comparison"],
        "limitation": summary["limitation"],
    }


def _fixed_transitions(transitions: dict, categories: tuple[str, ...]) -> dict:
    return {
        f"{left}_to_{right}": transitions.get(f"{left}_to_{right}", 0)
        for left in categories
        for right in categories
    }


def public_paired_tier_a(summary: dict, *, force_secondary: bool = False) -> dict:
    transitions = _fixed_transitions(summary["outcome_transitions"], ("pass", "fail", "abstain"))
    count_group = {
        "paired_common_count": summary["paired_common_count"],
        "entrants": summary["entrants"],
        "exits": summary["exits"],
        "score_delta_denominator": summary["score_delta_denominator"],
        "outcome_transitions": transitions,
    }
    secondary = force_secondary or _has_small_count(count_group)
    public_counts = _public_count_tree(count_group, secondary_suppression=secondary)
    return {
        "claim_boundary": summary["claim_boundary"],
        **public_counts,
        "mean_composite_score_delta": None if secondary else summary["mean_composite_score_delta"],
        "pass_rate_delta_paired_bootstrap_interval": (
            None if secondary else summary["pass_rate_delta_paired_bootstrap_interval"]
        ),
        "mean_score_delta_paired_bootstrap_interval": (
            None if secondary else summary["mean_score_delta_paired_bootstrap_interval"]
        ),
    }


def public_paired_valuation(
    summaries: dict, *, secondary_cohorts: set[str] | None = None,
) -> dict:
    output = {}
    categories = ("Cheap", "Fair", "Expensive")
    for key, summary in summaries.items():
        if not isinstance(summary, dict):
            output[key] = summary
            continue
        cohort = "analyst" if key.endswith("_analyst") else "quantitative"
        transitions = _fixed_transitions(summary["class_transitions"], categories)
        counts = {
            "paired_scored_count": summary["paired_scored_count"],
            "class_transitions": transitions,
        }
        secondary = (
            cohort in secondary_cohorts
            if secondary_cohorts is not None
            else _has_small_count(counts)
        )
        secondary = secondary or _has_small_count(counts)
        output[key] = {
            "orientation": summary["orientation"],
            **_public_count_tree(counts, secondary_suppression=secondary),
            "mean_base_margin_of_safety_delta": (
                None if secondary else summary["mean_base_margin_of_safety_delta"]
            ),
        }
    return output


def public_paired_committee(summary: dict) -> dict:
    statuses = ("Reject", "Watch", "Investigate")
    severities = ("low", "medium", "high")
    counts = {
        "paired_common_count": summary["paired_common_count"],
        "baseline_only_count": summary["baseline_only_count"],
        "candidate_only_count": summary["candidate_only_count"],
        "status_transitions": _fixed_transitions(summary["status_transitions"], statuses),
        "status_flip_count": summary["status_flip_count"],
        "bear_severity_transitions": _fixed_transitions(
            summary["bear_severity_transitions"], severities
        ),
        "bear_severity_change_count": summary["bear_severity_change_count"],
    }
    secondary = _has_small_count(counts)
    return {
        "claim_boundary": summary["claim_boundary"],
        **_public_count_tree(counts, secondary_suppression=secondary),
        "status_flip_rate": None if secondary else summary["status_flip_rate"],
        "bear_severity_change_rate": (
            None if secondary else summary["bear_severity_change_rate"]
        ),
        "mean_overall_score_delta": None if secondary else summary["mean_overall_score_delta"],
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
    tier_a_suppression = [_tier_a_suppression(summary) for summary in tier_a_summaries.values()]
    coverage_secondary = any(coverage for coverage, _ in tier_a_suppression)
    tier_a_outcome_secondary = coverage_secondary or any(
        outcomes for _, outcomes in tier_a_suppression
    )
    tier_c_secondary = any(_tier_c_suppression(summary) for summary in tier_c_summaries.values())
    valuation_secondary_cohorts = {
        summary["cohort"]
        for run_summaries in valuation_summaries.values()
        for summary in run_summaries.values()
        if _valuation_suppression(summary)
    }
    public = {
        "report_version": "eval-public-tier-a-c-v1",
        "evaluation_mode": manifest["evaluation_mode"],
        "authoritative": manifest["evaluation_mode"] == "authoritative",
        "execution": {
            "timestamp": manifest["execution"]["timestamp"],
            "eval_spec": manifest["eval_spec"],
            "harness_git_commit": manifest["harness"]["git_commit"],
        },
        "reproducibility_checks": preflight_public,
        "universe_flow": {
            name: {
                "universe_count": (
                    _secondary_suppressed_count(summary["universe_count"])
                    if coverage_secondary else summary["universe_count"]
                ),
                "scored_count": (
                    _secondary_suppressed_count(summary["screen_scored_count"])
                    if tier_a_outcome_secondary else summary["screen_scored_count"]
                ),
                "abstention_count": (
                    _secondary_suppressed_count(summary["screen_abstention_count"])
                    if tier_a_outcome_secondary else summary["screen_abstention_count"]
                ),
                "exclusion_count": (
                    _secondary_suppressed_count(summary["excluded_count"])
                    if coverage_secondary else _suppressed_count(summary["excluded_count"])
                ),
            }
            for name, summary in tier_a_summaries.items()
        },
        "tier_a": {
            name: public_tier_a(
                summary,
                coverage_secondary=coverage_secondary,
                outcome_secondary=tier_a_outcome_secondary,
            )
            for name, summary in tier_a_summaries.items()
        },
        "valuation": {
            name: public_valuation(
                summary, secondary_cohorts=valuation_secondary_cohorts
            )
            for name, summary in valuation_summaries.items()
        },
        "committee": {name: public_committee(summary) for name, summary in committee_summaries.items()},
        "tier_c": {
            name: public_tier_c(summary, secondary_suppression=tier_c_secondary)
            for name, summary in tier_c_summaries.items()
        },
        "tier_b": {"status": "not_run", "reason": "rubric, atomic themes, and frozen 24-company cohort are not decided"},
        "paired_baseline_candidate": {
            "tier_a": public_paired_tier_a(
                paired,
                force_secondary=coverage_secondary or tier_a_outcome_secondary,
            ),
            "valuation": public_paired_valuation(
                valuation_paired,
                secondary_cohorts=valuation_secondary_cohorts,
            ),
            "committee": public_paired_committee(committee_paired),
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
