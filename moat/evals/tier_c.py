"""Mechanical Tier C fundamentals extraction comparisons."""

from __future__ import annotations

import calendar
import math
from collections import Counter, defaultdict
from datetime import date

from .benchmark import canonical_ticker
from .constants import TIER_C_OUTCOMES
from .database import ImmutableDatabase

MONETARY_FIELDS = {
    "revenue": "revenue",
    "net_income": "net_income",
    "operating_income": "operating_income",
    "operating_cash_flow": "operating_cash_flow",
    "capex": "capex",
    "free_cash_flow": "free_cash_flow",
    "total_debt": "total_debt",
    "cash_and_equiv": "cash_and_equiv",
    "total_equity": "stockholders_equity",
}
RATIO_FIELDS = {
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "roic": "roic",
    "roe": "roe",
}
BENCHMARK_VALUE_FIELDS = {"operating_income": "ebit"}


def symmetric_relative_difference(x: float, y: float) -> float:
    if x == 0 and y == 0:
        return 0.0
    denominator = abs(x) + abs(y)
    return 2 * abs(x - y) / denominator if denominator else math.inf


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _normalise_benchmark(field: str, value):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, None
    numeric = float(value)
    if field in MONETARY_FIELDS:
        normalized = numeric * 1_000_000.0
        if field == "capex":
            normalized = abs(normalized)
        return normalized, "benchmark_millions_to_currency_units; capex_absolute_outflow" if field == "capex" else "benchmark_millions_to_currency_units"
    if field == "shares_outstanding":
        return numeric * 1_000_000.0, "benchmark_millions_to_shares"
    # The benchmark states margins and returns in percent; Moat stores fractions.
    return numeric / 100.0, "benchmark_percent_to_fraction"


def _diagnostics(x: float, y: float, near_zero: float) -> dict:
    near = abs(x) <= near_zero or abs(y) <= near_zero
    return {
        "symmetric_relative_difference": None if near else symmetric_relative_difference(x, y),
        "absolute_difference": abs(x - y),
        "sign_agreement": (x == 0 and y == 0) or (x > 0) == (y > 0),
        "order_of_magnitude_difference": None if x == 0 or y == 0 else abs(math.log10(abs(x)) - math.log10(abs(y))),
        "near_zero_rule_used": near,
    }


def _ratio_outcome(benchmark_value: float, moat_value: float) -> tuple[str, str, dict]:
    diagnostic = _diagnostics(benchmark_value, moat_value, 0.0001)
    difference = diagnostic["absolute_difference"]
    sign_ok = diagnostic["sign_agreement"]
    if difference <= 0.01 and (sign_ok or abs(benchmark_value) <= 0.0001 and abs(moat_value) <= 0.0001):
        return "Match", "absolute_percentage_point_tolerance", diagnostic
    return "Probable defect", "absolute_percentage_point_tolerance", diagnostic


def _average_equity_roe(history: list[dict]) -> float | None:
    if len(history) < 2:
        return None
    current, previous = history[-1], history[-2]
    equity_now = current.get("stockholders_equity")
    equity_before = previous.get("stockholders_equity")
    net_income = current.get("net_income")
    if None in (equity_now, equity_before, net_income):
        return None
    average = (equity_now + equity_before) / 2
    return net_income / average if average else None


def _comparison(
    *, ticker: str, field: str, benchmark_value, moat_value, snapshot_date: str,
    period_end_date: str | None, currency: str | None, reconstructed: bool = False,
    definition_rule: str | None = None,
) -> dict:
    normalized_benchmark, normalization = _normalise_benchmark(field, benchmark_value)
    base = {
        "ticker": ticker,
        "field": field,
        "benchmark_normalized_value": normalized_benchmark,
        "moat_normalized_value": moat_value,
        "normalized_unit": "fraction" if field in RATIO_FIELDS or field == "roe_average_equity_hypothesis" else "currency_units",
        "normalization_formula": normalization,
        "benchmark_period": "TTM",
        "moat_period": "fiscal_year",
        "period_end_date": period_end_date,
        "snapshot_date": snapshot_date,
        "reconstructed": reconstructed,
    }
    if normalized_benchmark is None or moat_value is None:
        return {**base, "outcome": "Unavailable", "rule": "one_side_has_no_usable_value", "diagnostics": None, "sensitivity_within_four_months": False}
    sensitivity = False
    if period_end_date:
        try:
            snapshot = date.fromisoformat(snapshot_date)
            period = date.fromisoformat(period_end_date)
            sensitivity = _subtract_months(snapshot, 4) <= period <= snapshot
        except ValueError:
            pass
    base["sensitivity_within_four_months"] = sensitivity
    if currency not in (None, "USD") and field in MONETARY_FIELDS:
        return {**base, "outcome": "Inconclusive", "rule": "currency_basis_ambiguous", "diagnostics": _diagnostics(normalized_benchmark, moat_value, 1.0)}
    if definition_rule is not None:
        return {**base, "outcome": "Definitional difference", "rule": definition_rule, "diagnostics": _diagnostics(normalized_benchmark, moat_value, 1.0)}
    if field in RATIO_FIELDS or field == "roe_average_equity_hypothesis":
        outcome, rule, diagnostics = _ratio_outcome(normalized_benchmark, moat_value)
        return {**base, "outcome": outcome, "rule": rule, "diagnostics": diagnostics, "period_caveat": "TTM versus fiscal-year ratio"}
    return {
        **base,
        "outcome": "Inconclusive",
        "rule": "ttm_vs_fiscal_year_without_exact_reconciliation",
        "diagnostics": _diagnostics(normalized_benchmark, moat_value, 1.0),
    }


def evaluate_tier_c(db: ImmutableDatabase, *, benchmark_records: list[dict]) -> tuple[list[dict], dict]:
    histories = {canonical_ticker(ticker): rows for ticker, rows in db.fundamentals_history().items()}
    companies = {canonical_ticker(row["ticker"]): row for row in db.active_companies()}
    details: list[dict] = []
    fields = list(MONETARY_FIELDS) + list(RATIO_FIELDS) + ["shares_outstanding", "depreciation_amortization"]
    for benchmark in benchmark_records:
        ticker = benchmark["ticker"]
        history = histories.get(ticker, [])
        latest = history[-1] if history else {}
        company = companies.get(ticker, {})
        for field in fields:
            if field == "shares_outstanding":
                moat_field = "shares_diluted"
            elif field == "depreciation_amortization":
                moat_field = "depreciation_amortization"
            else:
                moat_field = MONETARY_FIELDS.get(field, RATIO_FIELDS.get(field))
            definition_rule = None
            if field == "free_cash_flow":
                definition_rule = "moat_ocf_minus_capex_vs_benchmark_adjusted_fcf"
            elif field == "total_debt" and "debt_includes_finance_leases" not in (latest.get("quality_flags") or ""):
                definition_rule = "lease_treatment_not_reconciled"
            elif field in {"operating_income", "operating_margin"}:
                definition_rule = "moat_gaap_reported_vs_benchmark_adjusted"
            elif field == "roe" and db.profile == "baseline-v1":
                definition_rule = "baseline_ending_equity_vs_benchmark_average_equity"
            details.append(_comparison(
                ticker=ticker,
                field=field,
                benchmark_value=(
                    None if field == "depreciation_amortization"
                    else benchmark.get(BENCHMARK_VALUE_FIELDS.get(field, field))
                ),
                moat_value=latest.get(moat_field) if moat_field else None,
                snapshot_date=benchmark["snapshot_date"],
                period_end_date=latest.get("period_end_date"),
                currency=company.get("currency"),
                definition_rule=definition_rule,
            ))
        hypothesis = _average_equity_roe(history)
        details.append(_comparison(
            ticker=ticker,
            field="roe_average_equity_hypothesis",
            benchmark_value=benchmark.get("roe"),
            moat_value=hypothesis,
            snapshot_date=benchmark["snapshot_date"],
            period_end_date=latest.get("period_end_date"),
            currency=company.get("currency"),
            reconstructed=True,
        ))

    outcomes = Counter(row["outcome"] for row in details)
    by_field: dict[str, Counter] = defaultdict(Counter)
    for row in details:
        by_field[row["field"]][row["outcome"]] += 1
    comparable = outcomes["Match"] + outcomes["Probable defect"]
    roe_pairs: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in details:
        if row["field"] in {"roe", "roe_average_equity_hypothesis"}:
            roe_pairs[row["ticker"]][row["field"]] = row
    improvement = Counter()
    for pair in roe_pairs.values():
        persisted, hypothesis = pair.get("roe"), pair.get("roe_average_equity_hypothesis")
        if not persisted or not hypothesis or not persisted["diagnostics"] or not hypothesis["diagnostics"]:
            improvement["unavailable"] += 1
            continue
        left = persisted["diagnostics"]["absolute_difference"]
        right = hypothesis["diagnostics"]["absolute_difference"]
        improvement["improved" if right < left else "worsened" if right > left else "unchanged"] += 1
    summary = {
        "lead_finding": "outcome_distribution_and_comparable_coverage",
        "comparison_count": len(details),
        "outcome_distribution": {outcome: outcomes[outcome] for outcome in TIER_C_OUTCOMES},
        "comparable_count": comparable,
        "comparable_coverage_rate": comparable / len(details) if details else None,
        "extraction_accuracy": {
            "numerator_match": outcomes["Match"],
            "denominator_match_plus_probable_defect": comparable,
            "value": outcomes["Match"] / comparable if comparable else None,
            "context": "thin denominators must not be presented without outcome coverage",
        },
        "outcomes_by_field": {
            field: {outcome: counts[outcome] for outcome in TIER_C_OUTCOMES}
            for field, counts in sorted(by_field.items())
        },
        "roe_average_equity_hypothesis": dict(improvement),
        "formulas": {
            "symmetric_relative_difference": "2 * abs(x-y) / (abs(x)+abs(y)); both zero => 0",
            "monetary_and_shares_tolerance": "2% symmetric relative difference when comparable",
            "ratio_tolerance": "1 percentage point absolute difference",
            "near_zero_ratio_rule": "abs(value) <= 0.0001 uses sign-aware absolute difference",
            "normalization": "benchmark monetary/share values in millions multiplied by 1,000,000; benchmark percent ratios divided by 100 to fractions; capex uses absolute outflow",
        },
    }
    return details, summary
