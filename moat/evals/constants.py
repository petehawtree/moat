"""Versioned constants from ``docs/evals/eval-spec-v1.md``."""

from __future__ import annotations

SPEC_VERSION = "v1"
MANIFEST_VERSION = "eval-manifest-v1"
SPEC_RELATIVE_PATH = "docs/evals/eval-spec-v1.md"
SPEC_HASH_RELATIVE_PATH = "docs/evals/eval-spec-v1.md.sha256"
FROZEN_SPEC_SHA256 = "76ad487b10f1e114ff4ccd01247a110821cb495de43ed618f9b32f4a5a1163d6"

RUN_IDS = {
    "baseline": {
        "screen": "20260923T122501Z",
        "valuation": "20260923T122513Z",
        "committee": "20260923T153351Z",
    },
    "candidate": {
        "screen": "20260929T135425Z",
        "valuation": "20260929T135455Z",
        "committee": "20260930T133528Z",
    },
}

SNAPSHOT_SHA256 = {
    "baseline": "c9f7974e8732547ea3e734539bd382d559112d11a4fb2d492e767d85b42d65e9",
    "candidate": "61b9514a068b855998377f7262f55386783c2339f659370f8baea980bb7c3828",
}
SNAPSHOT_BYTES = {"baseline": 58_593_280, "candidate": 70_959_104}

COMMITTEE_MODEL = "claude-sonnet-4-6"
COMMITTEE_PROTOCOL_VERSION = "v1"

DEFAULT_BOOTSTRAP_SEED = 61001
DEFAULT_BOOTSTRAP_REPLICATES = 10_000
DEFAULT_SAMPLING_SEED = 61002

CANONICAL_ALIASES = {
    "BRK.B": "BRK-B",
    "BRK-B": "BRK-B",
    "BF.B": "BF-B",
    "BF-B": "BF-B",
}

MOAT_LABELS = ("Wide", "Narrow", "None")
COVERAGE_TIERS = ("analyst", "quantitative")
CAPTURE_STRATA = (
    "pass",
    "unassessable-datagap",
    "fail-boundary",
    "unassessable-definitional",
    "fail-clear",
    "unscreened",
)

TIER_C_OUTCOMES = (
    "Match",
    "Definitional difference",
    "Probable defect",
    "Inconclusive",
    "Unavailable",
)

PROFILE_NAMES = {"baseline": "baseline-v1", "candidate": "candidate-v1"}

EXPECTED_COLUMNS = {
    "companies": (
        "ticker", "cik", "name", "sector", "industry", "exchange", "currency",
        "universe", "is_active", "added_date", "removed_date",
    ),
    "price_history": ("ticker", "date", "close", "volume", "source", "retrieved_at"),
    "pipeline_runs": ("run_id", "started_at", "completed_at", "stage_reached", "status", "notes"),
    "quant_scores": (
        "run_id", "ticker", "sector_peer_group", "metric", "value",
        "absolute_floor_pass", "sector_percentile", "sector_relative_pass",
        "overall_pass", "status",
    ),
    "quality_scores": (
        "run_id", "ticker", "passed_screen", "composite_score", "notes",
        "metrics_assessed", "metrics_passed",
    ),
    "committee_verdicts": (
        "run_id", "ticker", "quality_analyst_view", "bear_analyst_view",
        "valuation_analyst_view", "business_quality_score",
        "competitive_moat_score", "financial_strength_score", "management_score",
        "valuation_score", "risk_score", "overall_score", "status",
        "data_confidence", "created_at", "bear_case_severity", "investment_thesis",
        "key_things_to_monitor", "ai_conclusion", "cache_key", "reused_from_run_id",
        "valuation_run_id", "quality_run_id", "ai_claims_run_ids",
    ),
}

BASELINE_FUNDAMENTALS_COLUMNS = (
    "ticker", "fiscal_year", "period_end_date", "revenue", "eps_diluted",
    "net_income", "operating_income", "operating_margin", "gross_margin", "roic",
    "roe", "free_cash_flow", "capex", "total_debt", "cash_and_equiv",
    "shares_diluted", "source", "confidence", "retrieved_at", "accession_number",
    "filed", "quality_flags", "operating_cash_flow", "depreciation_amortization",
    "working_capital_change",
)
BASELINE_VALUATION_COLUMNS = (
    "run_id", "ticker", "method", "scenario", "intrinsic_value_low",
    "intrinsic_value_high", "current_price", "margin_of_safety_pct",
    "key_assumptions", "created_at",
)

PROFILE_COLUMNS = {
    "baseline-v1": {
        **EXPECTED_COLUMNS,
        "fundamentals_annual": BASELINE_FUNDAMENTALS_COLUMNS,
        "valuations": BASELINE_VALUATION_COLUMNS,
    },
    "candidate-v1": {
        **EXPECTED_COLUMNS,
        "fundamentals_annual": BASELINE_FUNDAMENTALS_COLUMNS + ("stockholders_equity",),
        "valuations": BASELINE_VALUATION_COLUMNS + ("price_date",),
    },
}

KNOWN_QUANT_METRICS = {
    "roic", "roe", "free_cash_flow", "revenue_eps_growth", "operating_margin",
    "debt", "share_dilution", "gross_margin",
}
KNOWN_QUANT_STATUSES = {"pass", "fail", "unavailable", "not_applicable"}

BENCHMARK_FIELDS = {
    "ticker", "company_name", "ms_sector", "ms_industry_group", "ms_industry",
    "economic_moat", "moat_trend", "star_rating", "uncertainty",
    "capital_allocation", "fair_value", "price_fair_value", "last_price",
    "market_cap", "one_star_price", "five_star_price", "roic_normalized", "roic",
    "roe_normalized", "roe_forward", "roe", "revenue", "gross_margin",
    "operating_margin", "net_income", "ebitda", "ebit", "operating_cash_flow",
    "capex", "free_cash_flow", "total_debt", "cash_and_equiv", "total_equity",
    "working_capital", "goodwill_and_intangibles", "shares_outstanding",
    "enterprise_value", "financial_health_grade", "profitability_grade",
    "growth_grade", "analyst", "report_date", "price_fair_value_calc",
    "required_margin_of_safety_pct", "band_width_x", "uncertainty_implied_by_band",
    "coverage_tier", "coverage_signals", "stratum", "snapshot_date", "source",
    "source_file",
}
REQUIRED_BENCHMARK_FIELDS = {
    "ticker", "economic_moat", "coverage_tier", "stratum", "snapshot_date", "source",
}

PUBLIC_DISCLOSURE_MIN_CELL = 5
