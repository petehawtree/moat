"""Frozen §12.1 unchanged-input committee replicate comparison."""

from __future__ import annotations

import math
import statistics
from collections import Counter

from .benchmark import canonical_ticker
from .constants import COMMITTEE_BUNDLE_METHOD, COMPONENT_WEIGHTS, RUN_IDS
from .database import ImmutableDatabase
from .util import sha256_file

AT_OR_BELOW_BOUNDARY = "indistinguishable from one-run model variation"
ABOVE_BOUNDARY = "exceeds the observed practical noise boundary"
NEAREST_RANK_METHOD = "empirical_95th_percentile_nearest_rank_ceil_0.95_n"


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "minimum": None, "median": None, "mean": None, "maximum": None}
    return {
        "count": len(values),
        "minimum": min(values),
        "median": statistics.median(values),
        "mean": sum(values) / len(values),
        "maximum": max(values),
    }


def _committee_output_is_valid(row: dict) -> bool:
    if row.get("status") not in {"Investigate", "Watch", "Reject"}:
        return False
    if row.get("bear_case_severity") not in {"low", "medium", "high"}:
        return False
    if not all(
        isinstance(row.get(component), (int, float))
        and not isinstance(row.get(component), bool)
        and math.isfinite(row[component])
        and 0 <= row[component] <= 100
        for component in COMPONENT_WEIGHTS
    ):
        return False
    if (
        not isinstance(row.get("overall_score"), (int, float))
        or isinstance(row.get("overall_score"), bool)
        or not math.isfinite(row["overall_score"])
    ):
        return False
    recomputed_score = sum(
        ((100.0 - row[component]) if component == "risk_score" else row[component]) * weight
        for component, weight in COMPONENT_WEIGHTS.items()
    )
    expected_status = (
        "Watch" if recomputed_score >= 70 and row["bear_case_severity"] == "high"
        else "Investigate" if recomputed_score >= 70
        else "Watch" if recomputed_score >= 50
        else "Reject"
    )
    return math.isclose(
        recomputed_score, row["overall_score"], rel_tol=1e-9, abs_tol=1e-9
    ) and row["status"] == expected_status


# Public name for the structural-validity rule. The committee replicate
# runner (scripts/run_committee_replicate.py) applies the same rule before
# sealing, so this is a shared operator/harness contract.
committee_output_is_valid = _committee_output_is_valid


def nearest_rank_percentile(values: list[float], probability: float) -> float | None:
    """Return the one-based nearest-rank empirical percentile."""
    if not values:
        return None
    if not 0 < probability <= 1:
        raise ValueError("nearest-rank probability must be in (0, 1]")
    ordered = sorted(values)
    return ordered[math.ceil(probability * len(ordered)) - 1]


def apply_noise_interpretation(
    committee_paired: dict,
    noise_summary: dict,
) -> tuple[dict, dict]:
    """Apply Amendment 1's overall-score-only interpretation rule."""
    paired = {
        **committee_paired,
        "records": [dict(record) for record in committee_paired.get("records", [])],
    }
    summary = dict(noise_summary)
    for record in paired["records"]:
        record.pop("noise_interpretation", None)
    if summary.get("status") != "available":
        return paired, summary
    boundary = summary["practical_noise_boundary"]["value"]
    if boundary is None:
        return paired, summary
    for record in paired["records"]:
        absolute_delta = abs(record["score_delta"])
        record["absolute_overall_score_delta"] = absolute_delta
        record["noise_interpretation"] = (
            AT_OR_BELOW_BOUNDARY if absolute_delta <= boundary else ABOVE_BOUNDARY
        )
    summary["overall_score_noise_interpretation_counts"] = {
        AT_OR_BELOW_BOUNDARY: sum(
            record["noise_interpretation"] == AT_OR_BELOW_BOUNDARY
            for record in paired["records"]
        ),
        ABOVE_BOUNDARY: sum(
            record["noise_interpretation"] == ABOVE_BOUNDARY
            for record in paired["records"]
        ),
    }
    baseline_denominator = paired["paired_common_count"]
    replicate_denominator = summary["paired_company_count"]
    summary["status_flip_comparison"] = {
        "baseline_candidate": {
            "count": paired["status_flip_count"],
            "denominator": baseline_denominator,
            "rate": paired["status_flip_count"] / baseline_denominator if baseline_denominator else None,
        },
        "candidate_replicate": {
            "count": summary["status_flip_count"],
            "denominator": replicate_denominator,
            "rate": summary["status_flip_count"] / replicate_denominator if replicate_denominator else None,
        },
    }
    summary["bear_severity_change_comparison"] = {
        "baseline_candidate": {
            "count": paired["bear_severity_change_count"],
            "denominator": baseline_denominator,
            "rate": paired["bear_severity_change_count"] / baseline_denominator if baseline_denominator else None,
        },
        "candidate_replicate": {
            "count": summary["bear_severity_change_count"],
            "denominator": replicate_denominator,
            "rate": summary["bear_severity_change_count"] / replicate_denominator if replicate_denominator else None,
        },
    }
    return paired, summary


def evaluate_noise_floor(
    candidate_db: ImmutableDatabase,
    replicate_db: ImmutableDatabase | None,
    *,
    stored_run_id: str,
    replicate_run_id: str | None,
    candidate_quality_run_id: str,
    candidate_valuation_run_id: str,
    reference_candidate_sha256: str | None,
    declared_candidate_sha256: str,
    runtime_provenance: str | None,
    invalid_tickers: set[str] | None = None,
) -> tuple[list[dict], dict]:
    invalid_tickers = invalid_tickers or set()
    if replicate_run_id is None:
        return [], {
            "status": "unavailable",
            "reason": "replicate_run_id_not_supplied",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    frozen_run_ids = {run_id for runs in RUN_IDS.values() for run_id in runs.values()}
    if replicate_run_id in frozen_run_ids:
        return [], {
            "status": "unavailable",
            "reason": "replicate_run_id_is_not_distinct_from_every_frozen_run",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    if replicate_db is None:
        return [], {
            "status": "unavailable",
            "reason": "replicate_database_not_supplied",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    actual_candidate_sha256 = sha256_file(candidate_db.path)
    if not (
        reference_candidate_sha256
        == declared_candidate_sha256
        == actual_candidate_sha256
    ):
        return [], {
            "status": "unavailable",
            "reason": "candidate_snapshot_does_not_match_equivalence_reference",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    if replicate_db.path in {candidate_db.path}:
        return [], {
            "status": "unavailable",
            "reason": "replicate_database_aliases_candidate_snapshot",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    run = replicate_db.run_row(replicate_run_id)
    if run is None:
        return [], {
            "status": "unavailable",
            "reason": "replicate_run_not_found_in_replicate_snapshot",
            "replicate_run_id": replicate_run_id,
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    if runtime_provenance is None:
        return [], {
            "status": "unavailable",
            "reason": "equivalent_runtime_environment_not_evidenced",
            "replicate_run_id": replicate_run_id,
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    if run["stage_reached"] not in {"committee", "monitor"} or run["status"] not in {"complete", "partial"}:
        return [], {
            "status": "unavailable",
            "reason": "replicate_run_did_not_complete_committee_stage",
            "replicate_run_id": replicate_run_id,
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    stored_rows = candidate_db.committee_full_rows(stored_run_id)
    replicate_rows = replicate_db.committee_full_rows(replicate_run_id)
    stored = {canonical_ticker(row["ticker"]): row for row in stored_rows}
    replicate = {canonical_ticker(row["ticker"]): row for row in replicate_rows}
    if len(stored) != len(stored_rows) or len(replicate) != len(replicate_rows):
        return [], {
            "status": "unavailable",
            "reason": "committee_cohort_contains_ambiguous_canonical_identifiers",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    if set(stored) != set(replicate):
        return [], {
            "status": "unavailable",
            "reason": "replicate_cohort_does_not_equal_stored_candidate_cohort",
            "stored_count": len(stored),
            "replicate_count": len(replicate),
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    if any(row["reused_from_run_id"] is not None for row in replicate.values()):
        return [], {
            "status": "unavailable",
            "reason": "replicate_contains_cache_reused_verdicts",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    wrong_lineage = [
        row for row in (*stored.values(), *replicate.values())
        if row["quality_run_id"] != candidate_quality_run_id
        or row["valuation_run_id"] != candidate_valuation_run_id
    ]
    if wrong_lineage:
        return [], {
            "status": "unavailable",
            "reason": "committee_verdict_lineage_does_not_match_frozen_candidate_inputs",
            "affected_count": len(wrong_lineage),
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    bundle_failures = Counter()
    if not candidate_db.can_recompute_committee_bundle():
        bundle_failures["candidate_bundle_capability_unavailable"] += len(stored)
    if not replicate_db.can_recompute_committee_bundle():
        bundle_failures["replicate_bundle_capability_unavailable"] += len(replicate)
    if bundle_failures:
        return [], {
            "status": "unavailable",
            "reason": "committee_bundle_equivalence_failed",
            "failure_counts": dict(sorted(bundle_failures.items())),
            "input_equivalence_method": COMMITTEE_BUNDLE_METHOD,
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    for ticker in sorted(stored):
        candidate_bundle = candidate_db.recompute_committee_bundle_key(
            stored[ticker]["ticker"], candidate_valuation_run_id, candidate_quality_run_id
        )
        replicate_bundle = replicate_db.recompute_committee_bundle_key(
            replicate[ticker]["ticker"], candidate_valuation_run_id, candidate_quality_run_id
        )
        if candidate_bundle is None:
            bundle_failures["candidate_bundle_unavailable"] += 1
        if replicate_bundle is None:
            bundle_failures["replicate_bundle_unavailable"] += 1
        if candidate_bundle is not None and candidate_bundle != stored[ticker]["cache_key"]:
            bundle_failures["candidate_cache_key_mismatch"] += 1
        if replicate_bundle is not None and replicate_bundle != replicate[ticker]["cache_key"]:
            bundle_failures["replicate_cache_key_mismatch"] += 1
        if (
            candidate_bundle is not None
            and replicate_bundle is not None
            and candidate_bundle != replicate_bundle
        ):
            bundle_failures["cross_snapshot_bundle_mismatch"] += 1
    if bundle_failures:
        return [], {
            "status": "unavailable",
            "reason": "committee_bundle_equivalence_failed",
            "failure_counts": dict(sorted(bundle_failures.items())),
            "input_equivalence_method": COMMITTEE_BUNDLE_METHOD,
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    # Stored verdicts that failed preflight abstain (§3); they remain part of
    # the bundle-equivalence proof but are not paired for noise measurement.
    stored_output_invalid = {
        ticker for ticker, row in stored.items() if not _committee_output_is_valid(row)
    }
    replicate_output_invalid = {
        ticker for ticker, row in replicate.items() if not _committee_output_is_valid(row)
    }
    paired_tickers = sorted(
        set(stored) - invalid_tickers - stored_output_invalid - replicate_output_invalid
    )
    records = []
    status_pairs = []
    severity_pairs = []
    for ticker in paired_tickers:
        left, right = stored[ticker], replicate[ticker]
        component_deltas = {
            component: right[component] - left[component] for component in COMPONENT_WEIGHTS
        }
        overall_delta = right["overall_score"] - left["overall_score"]
        records.append({
            "ticker": ticker,
            "component_score_deltas": component_deltas,
            "overall_score_delta": overall_delta,
            "absolute_overall_score_delta": abs(overall_delta),
        })
        status_pairs.append((left["status"], right["status"]))
        severity_pairs.append((left["bear_case_severity"], right["bear_case_severity"]))
    if not records:
        return [], {
            "status": "unavailable",
            "reason": "no_preflight_valid_paired_companies",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    absolute_deltas = [row["absolute_overall_score_delta"] for row in records]
    boundary = nearest_rank_percentile(absolute_deltas, 0.95)
    summary = {
        "status": "available",
        "replicate_run_id": replicate_run_id,
        "runtime_provenance": runtime_provenance,
        "input_equivalence_method": COMMITTEE_BUNDLE_METHOD,
        "paired_company_count": len(records),
        "stored_preflight_invalid_abstention_count": len(
            (set(stored) & invalid_tickers) | stored_output_invalid
        ),
        "replicate_preflight_invalid_abstention_count": len(replicate_output_invalid),
        "component_score_delta_distributions": {
            component: _distribution([row["component_score_deltas"][component] for row in records])
            for component in COMPONENT_WEIGHTS
        },
        "overall_score_delta_distribution": _distribution([row["overall_score_delta"] for row in records]),
        "absolute_overall_score_delta_distribution": _distribution(absolute_deltas),
        "practical_noise_boundary": {
            "value": boundary,
            "cohort_size": len(absolute_deltas),
            "calculation_method": NEAREST_RANK_METHOD,
        },
        "status_flip_count": sum(left != right for left, right in status_pairs),
        "status_flip_rate": sum(left != right for left, right in status_pairs) / len(status_pairs),
        "status_transitions": {
            f"{left}_to_{right}": count
            for (left, right), count in sorted(Counter(status_pairs).items())
        },
        "bear_severity_change_count": sum(left != right for left, right in severity_pairs),
        "bear_severity_change_rate": sum(left != right for left, right in severity_pairs) / len(severity_pairs),
        "bear_severity_transitions": {
            f"{left}_to_{right}": count
            for (left, right), count in sorted(Counter(severity_pairs).items())
        },
        "limitation": "one replicate estimates a practical cross-company noise floor, not the full model sampling distribution",
    }
    return records, summary
