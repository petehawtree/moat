"""Mechanical Tier A screen/moat-strength evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict

from .benchmark import canonical_ticker
from .constants import MOAT_LABELS
from .database import ImmutableDatabase
from .statistics import bootstrap_interval, spearman_rho

_LABEL_ORDINAL = {"None": 0.0, "Narrow": 1.0, "Wide": 2.0}
_OUTCOMES = ("pass", "fail", "abstain")


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _run_stratum(row: dict | None) -> str:
    if row is None:
        return "abstain"
    if row["passed_screen"] == 1:
        return "pass"
    assessed = row["metrics_assessed"]
    if assessed is None or assessed < 6:
        return "unassessable-definitional" if row["sector"] in {"Financials", "Real Estate"} else "unassessable-datagap"
    score = row["composite_score"]
    return "fail-boundary" if score is not None and score >= 33.0 else "fail-clear"


def _screen_outcome(row: dict | None) -> str:
    if row is None:
        return "abstain"
    return "pass" if row["passed_screen"] == 1 else "fail"


def evaluate_tier_a(
    db: ImmutableDatabase, *, run_id: str, benchmark_records: list[dict],
    bootstrap_seed: int, bootstrap_replicates: int,
) -> tuple[list[dict], dict, list[dict]]:
    benchmark = {row["ticker"]: row for row in benchmark_records}
    companies = {canonical_ticker(row["ticker"]): row for row in db.active_companies()}
    quality = {canonical_ticker(row["ticker"]): row for row in db.quality_rows(run_id)}
    details: list[dict] = []
    exclusions: list[dict] = []

    for ticker in sorted(companies):
        company = companies[ticker]
        source = quality.get(ticker)
        target = benchmark.get(ticker)
        label = target["economic_moat"] if target else None
        if target is None or label is None:
            exclusions.append({
                "ticker": ticker,
                "rule_id": "v1-structural-absent-benchmark-coverage",
                "reason": "absent benchmark coverage",
                "remains_in_coverage_denominator": True,
            })
        details.append({
            "ticker": ticker,
            "sector": company["sector"],
            "screen_outcome": _screen_outcome(source),
            "run_derived_stratum": _run_stratum(source),
            "composite_score": source["composite_score"] if source else None,
            "metrics_assessed": source["metrics_assessed"] if source else None,
            "benchmark_moat": label,
            "benchmark_coverage_tier": target["coverage_tier"] if target else None,
            "capture_stratum_metadata": target["stratum"] if target else None,
        })

    labelled = [row for row in details if row["benchmark_moat"] in MOAT_LABELS]
    table = {
        outcome: {label: sum(row["screen_outcome"] == outcome and row["benchmark_moat"] == label for row in labelled) for label in MOAT_LABELS}
        for outcome in _OUTCOMES
    }
    scored = [row for row in labelled if row["screen_outcome"] != "abstain"]
    passers = [row for row in scored if row["screen_outcome"] == "pass"]
    wide = sum(row["benchmark_moat"] == "Wide" for row in passers)
    moat = sum(row["benchmark_moat"] in {"Wide", "Narrow"} for row in passers)
    none = sum(row["benchmark_moat"] == "None" for row in passers)
    total_wide = sum(row["benchmark_moat"] == "Wide" for row in scored)
    total_moat = sum(row["benchmark_moat"] in {"Wide", "Narrow"} for row in scored)
    wide_precision = _rate(wide, len(passers))
    moat_precision = _rate(moat, len(passers))
    wide_base = _rate(total_wide, len(scored))
    moat_base = _rate(total_moat, len(scored))

    association_rows = [row for row in scored if row["composite_score"] is not None]
    strata: dict[str, dict[str, int]] = defaultdict(lambda: {label: 0 for label in MOAT_LABELS})
    for row in labelled:
        strata[row["run_derived_stratum"]][row["benchmark_moat"]] += 1
    abstentions = [row for row in labelled if row["screen_outcome"] == "abstain"]

    def precision_metric(rows: list[dict]) -> float | None:
        selected = [row for row in rows if row["screen_outcome"] == "pass"]
        return _rate(sum(row["benchmark_moat"] == "Wide" for row in selected), len(selected))

    interval = None
    if len(passers) >= 20 and len({row["benchmark_moat"] == "Wide" for row in passers}) > 1:
        interval = bootstrap_interval(
            scored, precision_metric, seed=bootstrap_seed, replicates=bootstrap_replicates
        )
    summary = {
        "orientation": "descriptive_frozen_2026_universe",
        "universe_count": len(details),
        "benchmark_labelled_count": len(labelled),
        "screen_scored_count": sum(row["screen_outcome"] != "abstain" for row in details),
        "screen_abstention_count": sum(row["screen_outcome"] == "abstain" for row in details),
        "screen_coverage_rate": _rate(sum(row["screen_outcome"] != "abstain" for row in details), len(details)),
        "excluded_count": len(exclusions),
        "pass_fail_abstain_by_moat": table,
        "benchmark_labels_among_abstentions": dict(Counter(row["benchmark_moat"] for row in abstentions)),
        "metrics": {
            "wide_precision": {"numerator": wide, "denominator": len(passers), "value": wide_precision, "abstentions": len(abstentions), "exclusions": len(exclusions), "kind": "descriptive"},
            "wide_or_narrow_precision": {"numerator": moat, "denominator": len(passers), "value": moat_precision, "abstentions": len(abstentions), "exclusions": len(exclusions), "kind": "descriptive"},
            "none_rate_among_passers": {"numerator": none, "denominator": len(passers), "value": _rate(none, len(passers)), "abstentions": len(abstentions), "exclusions": len(exclusions), "kind": "descriptive"},
            "wide_recall_conditional_on_scored": {"numerator": wide, "denominator": total_wide, "value": _rate(wide, total_wide), "abstentions": len(abstentions), "exclusions": len(exclusions), "kind": "secondary_descriptive"},
            "wide_or_narrow_recall_conditional_on_scored": {"numerator": moat, "denominator": total_moat, "value": _rate(moat, total_moat), "abstentions": len(abstentions), "exclusions": len(exclusions), "kind": "secondary_descriptive"},
            "wide_lift_over_base_rate": {"numerator": wide, "denominator": len(passers), "value": wide_precision / wide_base if wide_precision is not None and wide_base else None, "base_rate": wide_base, "kind": "descriptive"},
            "wide_or_narrow_lift_over_base_rate": {"numerator": moat, "denominator": len(passers), "value": moat_precision / moat_base if moat_precision is not None and moat_base else None, "base_rate": moat_base, "kind": "descriptive"},
            "score_rating_spearman_rho": {"numerator": None, "denominator": len(association_rows), "value": spearman_rho([row["composite_score"] for row in association_rows], [_LABEL_ORDINAL[row["benchmark_moat"]] for row in association_rows]), "abstentions": len(abstentions), "exclusions": len(exclusions), "kind": "ordinal_association_exploratory"},
        },
        "wide_precision_bootstrap_interval": interval,
        "moat_distribution_by_run_derived_stratum": dict(strata),
        "strata_rule": "pass; else <6 assessed => sector-definitional or data-gap; else score >=33 => boundary; else clear fail",
    }
    return details, summary, exclusions


def paired_tier_a(
    baseline: list[dict], candidate: list[dict], *, bootstrap_seed: int, bootstrap_replicates: int,
) -> dict:
    before = {row["ticker"]: row for row in baseline}
    after = {row["ticker"]: row for row in candidate}
    common = sorted(set(before) & set(after))
    rows = []
    for ticker in common:
        left, right = before[ticker], after[ticker]
        rows.append({
            "ticker": ticker,
            "sector": left["sector"],
            "baseline_outcome": left["screen_outcome"],
            "candidate_outcome": right["screen_outcome"],
            "baseline_score": left["composite_score"],
            "candidate_score": right["composite_score"],
        })
    transitions = Counter((row["baseline_outcome"], row["candidate_outcome"]) for row in rows)
    score_deltas = [row["candidate_score"] - row["baseline_score"] for row in rows if row["baseline_score"] is not None and row["candidate_score"] is not None]
    paired_scored = [row for row in rows if row["baseline_outcome"] != "abstain" and row["candidate_outcome"] != "abstain"]
    for row in paired_scored:
        row["pass_indicator_delta"] = (row["candidate_outcome"] == "pass") - (row["baseline_outcome"] == "pass")
    pass_delta_interval = None
    if len(paired_scored) >= 20 and len({row["pass_indicator_delta"] for row in paired_scored}) > 1:
        pass_delta_interval = bootstrap_interval(
            paired_scored,
            lambda sample: sum(row["pass_indicator_delta"] for row in sample) / len(sample),
            seed=bootstrap_seed,
            replicates=bootstrap_replicates,
        )
    paired_scores = [
        {**row, "score_delta": row["candidate_score"] - row["baseline_score"]}
        for row in rows if row["baseline_score"] is not None and row["candidate_score"] is not None
    ]
    score_delta_interval = None
    if len(paired_scores) >= 20 and len({row["score_delta"] for row in paired_scores}) > 1:
        score_delta_interval = bootstrap_interval(
            paired_scores,
            lambda sample: sum(row["score_delta"] for row in sample) / len(sample),
            seed=bootstrap_seed,
            replicates=bootstrap_replicates,
        )
    return {
        "claim_boundary": "combined Phase 1 effect; descriptive association, not per-fix causation",
        "paired_common_count": len(rows),
        "entrants": len(set(after) - set(before)),
        "exits": len(set(before) - set(after)),
        "outcome_transitions": {f"{a}_to_{b}": n for (a, b), n in sorted(transitions.items())},
        "mean_composite_score_delta": sum(score_deltas) / len(score_deltas) if score_deltas else None,
        "score_delta_denominator": len(score_deltas),
        "pass_rate_delta_paired_bootstrap_interval": pass_delta_interval,
        "mean_score_delta_paired_bootstrap_interval": score_delta_interval,
        "records": rows,
    }
