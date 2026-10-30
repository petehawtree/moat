"""Frozen §12.1 unchanged-input committee replicate comparison."""

from __future__ import annotations

import math
import statistics
from collections import Counter

from .benchmark import canonical_ticker
from .constants import COMPONENT_WEIGHTS
from .database import ImmutableDatabase


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


def evaluate_noise_floor(
    db: ImmutableDatabase,
    *,
    stored_run_id: str,
    replicate_run_id: str | None,
    candidate_quality_run_id: str,
    candidate_valuation_run_id: str,
    runtime_provenance: str | None,
) -> tuple[list[dict], dict]:
    if replicate_run_id is None:
        return [], {
            "status": "unavailable",
            "reason": "replicate_run_id_not_supplied",
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    run = db.run_row(replicate_run_id)
    if run is None:
        return [], {
            "status": "unavailable",
            "reason": "replicate_run_not_found_in_candidate_snapshot",
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
    if run["stage_reached"] != "committee" or run["status"] not in {"complete", "partial"}:
        return [], {
            "status": "unavailable",
            "reason": "replicate_run_did_not_complete_committee_stage",
            "replicate_run_id": replicate_run_id,
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    stored = {canonical_ticker(row["ticker"]): row for row in db.committee_full_rows(stored_run_id)}
    replicate = {canonical_ticker(row["ticker"]): row for row in db.committee_full_rows(replicate_run_id)}
    if set(stored) != set(replicate):
        return [], {
            "status": "unavailable",
            "reason": "replicate_cohort_does_not_equal_stored_candidate_cohort",
            "stored_count": len(stored),
            "replicate_count": len(replicate),
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    invalid = [
        row for ticker, row in replicate.items()
        if row["reused_from_run_id"] is not None
        or row["quality_run_id"] != candidate_quality_run_id
        or row["valuation_run_id"] != candidate_valuation_run_id
        or row["cache_key"] != stored[ticker]["cache_key"]
        or any(
            not isinstance(row.get(component), (int, float))
            or isinstance(row.get(component), bool)
            or not math.isfinite(row[component])
            or not isinstance(stored[ticker].get(component), (int, float))
            or isinstance(stored[ticker].get(component), bool)
            or not math.isfinite(stored[ticker][component])
            for component in (*COMPONENT_WEIGHTS, "overall_score")
        )
    ]
    if invalid:
        return [], {
            "status": "unavailable",
            "reason": "replicate_did_not_prove_cache_bypass_and_exact_candidate_lineage",
            "affected_count": len(invalid),
            "interpretation": "committee baseline/candidate movement remains descriptive only",
        }
    records = []
    for ticker in sorted(stored):
        left, right = stored[ticker], replicate[ticker]
        component_deltas = {
            component: right[component] - left[component] for component in COMPONENT_WEIGHTS
        }
        records.append({
            "ticker": ticker,
            "component_score_deltas": component_deltas,
            "overall_score_delta": right["overall_score"] - left["overall_score"],
            "stored_status": left["status"],
            "replicate_status": right["status"],
            "status_flip": left["status"] != right["status"],
            "stored_bear_severity": left["bear_case_severity"],
            "replicate_bear_severity": right["bear_case_severity"],
            "bear_severity_changed": left["bear_case_severity"] != right["bear_case_severity"],
        })
    summary = {
        "status": "available",
        "replicate_run_id": replicate_run_id,
        "runtime_provenance": runtime_provenance,
        "paired_company_count": len(records),
        "component_score_delta_distributions": {
            component: _distribution([row["component_score_deltas"][component] for row in records])
            for component in COMPONENT_WEIGHTS
        },
        "overall_score_delta_distribution": _distribution([row["overall_score_delta"] for row in records]),
        "status_flip_count": sum(row["status_flip"] for row in records),
        "status_transitions": {
            f"{left}_to_{right}": count
            for (left, right), count in sorted(Counter((row["stored_status"], row["replicate_status"]) for row in records).items())
        },
        "bear_severity_change_count": sum(row["bear_severity_changed"] for row in records),
        "limitation": "one replicate estimates a practical cross-company noise floor, not the full model sampling distribution",
    }
    return records, summary
