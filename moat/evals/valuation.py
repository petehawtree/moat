"""Frozen §7 as-run and price-normalised valuation comparisons."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from .benchmark import canonical_ticker
from .constants import VALUATION_ORDINAL
from .database import ImmutableDatabase
from .statistics import spearman_rho

VALUATION_CLASSES = ("Cheap", "Fair", "Expensive", "Abstain")
_POINT_TOLERANCE = {"rel_tol": 1e-9, "abs_tol": 1e-9}


def benchmark_valuation(record: dict) -> dict:
    """Classify the frozen uncertainty-adjusted benchmark target."""
    price = record.get("last_price")
    lower = record.get("five_star_price")
    upper = record.get("one_star_price")
    fair_value = record.get("fair_value")
    values = (price, lower, upper)
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0 for value in values):
        classification = "Abstain"
        rule = "missing_or_nonpositive_uncertainty_band_input"
    elif not lower < upper:
        classification = "Abstain"
        rule = "invalid_uncertainty_band_order"
    elif price <= lower:
        classification = "Cheap"
        rule = "price_at_or_below_five_star_boundary"
    elif price > upper:
        classification = "Expensive"
        rule = "price_strictly_above_one_star_boundary"
    else:
        classification = "Fair"
        rule = "price_between_uncertainty_boundaries"
    point_direction = "Unavailable"
    if (
        isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price)
        and isinstance(fair_value, (int, float)) and not isinstance(fair_value, bool)
        and math.isfinite(fair_value) and fair_value > 0
    ):
        point_direction = "At_or_below_point_fair_value" if price <= fair_value else "Above_point_fair_value"
    return {
        "classification": classification,
        "rule": rule,
        "comparison_price": price,
        "lower_boundary": lower,
        "upper_boundary": upper,
        "point_fair_value_direction": point_direction,
    }


def classify_moat_valuation(rows: list[dict], price: float | None) -> dict:
    """Apply §7.2 to exactly three owner-earnings DCF scenario rows."""
    scenarios: dict[str, dict] = {}
    issue = None
    for row in rows:
        if row.get("method") != "owner_earnings_dcf" or row.get("scenario") not in {"bear", "base", "bull"}:
            continue
        if row["scenario"] in scenarios:
            issue = "duplicate_dcf_scenario"
            break
        low, high = row.get("intrinsic_value_low"), row.get("intrinsic_value_high")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in (low, high)):
            issue = "missing_or_non_numeric_intrinsic_value"
            break
        if not math.isclose(low, high, **_POINT_TOLERANCE):
            issue = "material_within_row_intrinsic_range"
            break
        scenarios[row["scenario"]] = row
    if issue is None and set(scenarios) != {"bear", "base", "bull"}:
        issue = "missing_dcf_scenario"
    values = None
    if issue is None:
        values = {name: scenarios[name]["intrinsic_value_low"] for name in ("bear", "base", "bull")}
        if any(value <= 0 for value in values.values()):
            issue = "nonpositive_intrinsic_value"
        elif not values["bear"] < values["base"] < values["bull"]:
            issue = "incorrect_dcf_scenario_order"
    if issue is None and (
        not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0
    ):
        issue = "missing_or_nonpositive_price"
    if issue:
        return {
            "classification": "Abstain",
            "rule": issue,
            "bear_value": values["bear"] if values else None,
            "base_value": values["base"] if values else None,
            "bull_value": values["bull"] if values else None,
            "comparison_price": price,
            "base_margin_of_safety": None,
            "point_fair_value_direction": "Unavailable",
        }
    if price <= values["bear"]:
        classification, rule = "Cheap", "price_at_or_below_bear_value"
    elif price >= values["bull"]:
        classification, rule = "Expensive", "price_at_or_above_bull_value"
    else:
        classification, rule = "Fair", "price_strictly_between_bear_and_bull"
    return {
        "classification": classification,
        "rule": rule,
        "bear_value": values["bear"],
        "base_value": values["base"],
        "bull_value": values["bull"],
        "comparison_price": price,
        "base_margin_of_safety": (values["base"] - price) / values["base"],
        "point_fair_value_direction": "At_or_below_point_fair_value" if price <= values["base"] else "Above_point_fair_value",
    }


def _summary(records: list[dict], *, cohort: str, view: str) -> dict:
    eligible = [row for row in records if row["benchmark_class"] != "Abstain"]
    scored = [row for row in eligible if row["moat_class"] != "Abstain"]
    table = {
        moat: {benchmark: sum(row["moat_class"] == moat and row["benchmark_class"] == benchmark for row in records) for benchmark in VALUATION_CLASSES}
        for moat in VALUATION_CLASSES
    }
    agreement = sum(row["moat_class"] == row["benchmark_class"] for row in scored)
    false_cheap = sum(row["moat_class"] == "Cheap" and row["benchmark_class"] != "Cheap" for row in scored)
    moat_cheap = sum(row["moat_class"] == "Cheap" for row in scored)
    ordinal = [row for row in scored if row["moat_class"] in VALUATION_ORDINAL and row["benchmark_class"] in VALUATION_ORDINAL]
    point_rows = [
        row for row in scored
        if row["moat_point_fair_value_direction"] != "Unavailable"
        and row["benchmark_point_fair_value_direction"] != "Unavailable"
    ]
    point_agreement = sum(
        row["moat_point_fair_value_direction"] == row["benchmark_point_fair_value_direction"]
        for row in point_rows
    )
    return {
        "cohort": cohort,
        "view": view,
        "orientation": "primary" if cohort == "analyst" else "secondary",
        "eligible_count": len(eligible),
        "scored_count": len(scored),
        "abstention_count": len(eligible) - len(scored),
        "coverage_rate": len(scored) / len(eligible) if eligible else None,
        "moat_by_benchmark_table": table,
        "exact_agreement": {"numerator": agreement, "denominator": len(scored), "value": agreement / len(scored) if scored else None, "kind": "descriptive"},
        "false_cheap_rate": {"numerator": false_cheap, "denominator": moat_cheap, "value": false_cheap / moat_cheap if moat_cheap else None, "kind": "conservative_descriptive"},
        "ordinal_spearman_rho": {
            "denominator": len(ordinal),
            "value": spearman_rho(
                [VALUATION_ORDINAL[row["moat_class"]] for row in ordinal],
                [VALUATION_ORDINAL[row["benchmark_class"]] for row in ordinal],
            ),
            "kind": "exploratory_ordinal_association",
        },
        "point_fair_value_direction_agreement": {
            "numerator": point_agreement,
            "denominator": len(point_rows),
            "value": point_agreement / len(point_rows) if point_rows else None,
            "kind": "secondary_diagnostic",
        },
        "benchmark_class_distribution": dict(Counter(row["benchmark_class"] for row in eligible)),
        "moat_class_distribution": dict(Counter(row["moat_class"] for row in eligible)),
    }


def evaluate_valuation(
    db: ImmutableDatabase, *, run_id: str, benchmark_records: list[dict]
) -> tuple[list[dict], dict]:
    benchmark = {row["ticker"]: row for row in benchmark_records}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in db.valuation_rows(run_id):
        grouped[canonical_ticker(row["ticker"])].append(row)
    details: list[dict] = []
    for ticker, source in sorted(benchmark.items()):
        target = benchmark_valuation(source)
        dcf_rows = grouped.get(ticker, [])
        persisted_prices = {
            row["current_price"] for row in dcf_rows
            if row.get("method") == "owner_earnings_dcf" and row.get("current_price") is not None
        }
        as_run_price = next(iter(persisted_prices)) if len(persisted_prices) == 1 else None
        as_run = classify_moat_valuation(dcf_rows, as_run_price)
        if len(persisted_prices) > 1:
            as_run = {**as_run, "classification": "Abstain", "rule": "inconsistent_persisted_scenario_prices", "base_margin_of_safety": None}
        normalized = classify_moat_valuation(dcf_rows, target["comparison_price"])
        if db.profile == "candidate-v1":
            price_dates = {
                row.get("price_date") for row in dcf_rows
                if row.get("method") == "owner_earnings_dcf" and row.get("price_date") is not None
            }
            price_date = next(iter(price_dates)) if len(price_dates) == 1 else None
            price_date_status = "persisted_verified_shape" if len(price_dates) == 1 else "unavailable"
        elif as_run_price is not None:
            reconstructed = db.reconstruct_price_date(ticker, as_run_price)
            price_date, price_date_status = reconstructed["price_date"], reconstructed["status"]
        else:
            price_date, price_date_status = None, "unavailable"
        common = {
            "ticker": ticker,
            "coverage_tier": source["coverage_tier"],
            "benchmark_class": target["classification"],
            "benchmark_rule": target["rule"],
            "benchmark_point_fair_value_direction": target["point_fair_value_direction"],
        }
        details.extend([
            {**common, **{f"moat_{key}": value for key, value in as_run.items()}, "view": "as_run", "price_date": price_date, "price_date_status": price_date_status},
            {**common, **{f"moat_{key}": value for key, value in normalized.items()}, "view": "price_normalised", "price_date": source.get("snapshot_date"), "price_date_status": "frozen_benchmark_snapshot_date"},
        ])
    summaries = {}
    for view in ("as_run", "price_normalised"):
        for cohort in ("analyst", "quantitative"):
            subset = [
                {
                    **row,
                    "moat_class": row["moat_classification"],
                }
                for row in details if row["view"] == view and row["coverage_tier"] == cohort
            ]
            summaries[f"{view}_{cohort}"] = _summary(subset, cohort=cohort, view=view)
    return details, summaries


def paired_valuation(baseline: list[dict], candidate: list[dict]) -> dict:
    output = {}
    for view in ("as_run", "price_normalised"):
        before = {row["ticker"]: row for row in baseline if row["view"] == view}
        after = {row["ticker"]: row for row in candidate if row["view"] == view}
        records = []
        for ticker in sorted(set(before) & set(after)):
            left, right = before[ticker], after[ticker]
            if left["moat_classification"] == "Abstain" or right["moat_classification"] == "Abstain":
                continue
            records.append({
                "ticker": ticker,
                "baseline_class": left["moat_classification"],
                "candidate_class": right["moat_classification"],
                "baseline_base_margin_of_safety": left["moat_base_margin_of_safety"],
                "candidate_base_margin_of_safety": right["moat_base_margin_of_safety"],
                "base_margin_of_safety_delta": right["moat_base_margin_of_safety"] - left["moat_base_margin_of_safety"],
            })
        transitions = Counter((row["baseline_class"], row["candidate_class"]) for row in records)
        output[view] = {
            "paired_scored_count": len(records),
            "class_transitions": {f"{left}_to_{right}": count for (left, right), count in sorted(transitions.items())},
            "mean_base_margin_of_safety_delta": sum(row["base_margin_of_safety_delta"] for row in records) / len(records) if records else None,
            "records": records,
        }
    output["claim_boundary"] = "combined Phase 1 effect; as-run and price-normalised views remain separate"
    return output
