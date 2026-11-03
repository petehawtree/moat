"""Synthetic-only coverage for the frozen mechanical Tier A/C harness."""

from __future__ import annotations

import json
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from moat.evals.benchmark import load_benchmark
from moat.evals.committee_eval import evaluate_committee
from moat.evals.constants import PROFILE_COLUMNS, RUN_IDS
from moat.evals.database import ImmutableDatabase
from moat.evals.errors import ManifestError, PreflightError
from moat.evals.manifest import build_manifest, validate_manifest
from moat.evals.noise_floor import (
    ABOVE_BOUNDARY,
    AT_OR_BELOW_BOUNDARY,
    NEAREST_RANK_METHOD,
    apply_noise_interpretation,
    evaluate_noise_floor,
    nearest_rank_percentile,
)
from moat.evals.persona import evaluate_persona_influence
from moat.evals.preflight import run_preflight, validate_paths
from moat.evals.reporting import public_tier_a, public_tier_c
from moat.evals.runner import run_evaluation
from moat.evals.replay import evaluate_replays
from moat.evals.tier_a import evaluate_tier_a
from moat.evals.tier_c import evaluate_tier_c, symmetric_relative_difference
from moat.evals.valuation import classify_moat_valuation, evaluate_valuation

REPO_ROOT = Path(__file__).resolve().parents[1]


def _column_type(name: str) -> str:
    if name in {
        "is_active", "fiscal_year", "metrics_assessed", "metrics_passed", "passed_screen",
        "absolute_floor_pass", "sector_relative_pass", "overall_pass",
    }:
        return "INTEGER"
    if name in {
        "revenue", "eps_diluted", "net_income", "operating_income", "operating_margin",
        "gross_margin", "roic", "roe", "free_cash_flow", "operating_cash_flow", "capex",
        "depreciation_amortization", "working_capital_change", "stockholders_equity",
        "total_debt", "cash_and_equiv", "shares_diluted", "close", "composite_score",
        "value", "sector_percentile", "intrinsic_value_low", "intrinsic_value_high",
        "current_price", "margin_of_safety_pct", "business_quality_score",
        "competitive_moat_score", "financial_strength_score", "management_score",
        "valuation_score", "risk_score", "overall_score",
    }:
        return "REAL"
    return "TEXT"


def _create_profile_db(path: Path, profile: str, *, candidate_values: bool = False) -> None:
    conn = sqlite3.connect(path)
    for table, columns in PROFILE_COLUMNS[profile].items():
        definitions = ", ".join(f'"{column}" {_column_type(column)}' for column in columns)
        conn.execute(f'CREATE TABLE "{table}" ({definitions})')
    conn.execute(
        "CREATE TABLE ai_analysis (ticker TEXT, analysis_type TEXT, cache_key TEXT, is_current INTEGER)"
    )
    for ticker, sector in [
        ("AAA", "Technology"), ("BBB", "Technology"), ("CCC", "Energy"),
        ("DDD", "Financials"), ("EEE", "Industrials"), ("FFF", "Consumer Defensive"),
    ]:
        conn.execute(
            "INSERT INTO companies (ticker,name,sector,currency,universe,is_active,added_date) VALUES (?,?,?,?,?,1,?)",
            (ticker, f"Synthetic {ticker}", sector, "USD", "synthetic", "2026-01-01"),
        )
    name = "candidate" if profile == "candidate-v1" else "baseline"
    runs = RUN_IDS[name]
    conn.executemany(
        "INSERT INTO pipeline_runs (run_id,started_at,stage_reached,status) VALUES (?,?,?,?)",
        [
            (runs["screen"], "2026-01-01T00:00:00Z", "quality", "partial"),
            (runs["valuation"], "2026-01-01T00:01:00Z", "valuation", "partial"),
            (runs["committee"], "2026-01-01T00:02:00Z", "committee", "partial"),
        ],
    )
    outcomes = {
        "AAA": (1, 75.0, 8, 6),
        "BBB": (1, 62.5, 8, 5),
        "CCC": (0, 25.0, 8, 2),
        "EEE": (0 if not candidate_values else 1, 37.5 if not candidate_values else 50.0, 8, 3 if not candidate_values else 4),
        "FFF": (1, 50.0, 8, 4),
    }
    for ticker, values in outcomes.items():
        conn.execute(
            "INSERT INTO quality_scores (run_id,ticker,passed_screen,composite_score,metrics_assessed,metrics_passed) VALUES (?,?,?,?,?,?)",
            (runs["screen"], ticker, *values),
        )
        conn.execute(
            "INSERT INTO quant_scores (run_id,ticker,metric,value,overall_pass,status) VALUES (?,?,?,?,?,?)",
            (runs["screen"], ticker, "roic", 0.15, values[0], "pass" if values[0] else "fail"),
        )
    for scenario, value in (("bear", 80.0), ("base", 100.0), ("bull", 120.0)):
        columns = "run_id,ticker,method,scenario,intrinsic_value_low,intrinsic_value_high,current_price,margin_of_safety_pct,created_at"
        values = [runs["valuation"], "AAA", "owner_earnings_dcf", scenario, value, value, 70.0, (value - 70.0) / value, "2026-01-01T00:01:00Z"]
        if profile == "candidate-v1":
            columns += ",price_date"
            values.append("2026-01-01")
        conn.execute(
            f"INSERT INTO valuations ({columns}) VALUES ({','.join('?' for _ in values)})",
            values,
        )
    severity = "high" if candidate_values else "medium"
    status = "Watch" if severity == "high" else "Investigate"
    conn.execute(
        "INSERT INTO committee_verdicts (run_id,ticker,status,bear_case_severity,cache_key,valuation_run_id,quality_run_id,created_at,business_quality_score,competitive_moat_score,financial_strength_score,management_score,valuation_score,risk_score,overall_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (runs["committee"], "AAA", status, severity, "synthetic-key", runs["valuation"], runs["screen"], "2026-01-01T00:02:00Z", 80.0, 80.0, 70.0, 60.0, 75.0, 30.0, 74.75),
    )
    for ticker in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"):
        conn.executemany(
            "INSERT INTO ai_analysis (ticker,analysis_type,cache_key,is_current) VALUES (?,?,?,1)",
            [(ticker, analysis_type, f"synthetic-{ticker}-{analysis_type}") for analysis_type in (
                "business_quality", "management", "moat", "risk",
            )],
        )
        rows = [
            (2024, "2024-09-30", 90_000_000.0, 18_000_000.0, 0.40, 0.20, 0.15, 0.10, 12_000_000.0, 20_000_000.0, 8_000_000.0, 30_000_000.0, 5_000_000.0, 10_000_000.0, 45_000_000.0),
            (2025, "2025-09-30", 100_000_000.0, 20_000_000.0, 0.40, 0.20, 0.15, 0.10, 14_000_000.0, 22_000_000.0, 8_000_000.0, 32_000_000.0, 6_000_000.0, 10_000_000.0, 55_000_000.0),
        ]
        for fy, period, revenue, op_income, gm, om, roic, roe, fcf, ocf, capex, debt, cash, shares, equity in rows:
            columns = [
                "ticker", "fiscal_year", "period_end_date", "revenue", "net_income",
                "operating_income", "operating_margin", "gross_margin", "roic", "roe",
                "free_cash_flow", "operating_cash_flow", "capex", "total_debt",
                "cash_and_equiv", "shares_diluted", "source", "confidence", "retrieved_at",
            ]
            values = [
                ticker, fy, period, revenue, 5_000_000.0, op_income, om, gm, roic, roe,
                fcf, ocf, capex, debt, cash, shares, "synthetic", "high", "2026-01-01T00:00:00Z",
            ]
            if profile == "candidate-v1":
                columns.append("stockholders_equity")
                values.append(equity)
            placeholders = ",".join("?" for _ in values)
            conn.execute(
                f"INSERT INTO fundamentals_annual ({','.join(columns)}) VALUES ({placeholders})",
                values,
            )
    conn.commit()
    conn.close()
    path.chmod(0o444)
    with ImmutableDatabase(path, profile) as db:
        cache_key = db.recompute_committee_bundle_key(
            "AAA", runs["valuation"], runs["screen"]
        )
    assert cache_key is not None
    path.chmod(0o644)
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE committee_verdicts SET cache_key=? WHERE run_id=? AND ticker='AAA'",
        (cache_key, runs["committee"]),
    )
    conn.commit()
    conn.close()
    path.chmod(0o444)


def _create_replicate_db(
    candidate: Path,
    replicate: Path,
    *,
    run_id: str = "noise-run",
    aaa_scores: tuple = (81.0, 79.0, 71.0, 61.0, 74.0, 31.0, 74.75),
    aaa_status: str = "Investigate",
    aaa_severity: str = "medium",
) -> None:
    shutil.copyfile(candidate, replicate)
    replicate.chmod(0o644)
    conn = sqlite3.connect(replicate)
    conn.execute(
        "INSERT INTO pipeline_runs (run_id,started_at,stage_reached,status) VALUES (?,?,?,?)",
        (run_id, "2026-01-02", "committee", "partial"),
    )
    conn.execute(
        "INSERT INTO committee_verdicts "
        "(run_id,ticker,status,bear_case_severity,cache_key,valuation_run_id,quality_run_id,created_at,"
        "business_quality_score,competitive_moat_score,financial_strength_score,management_score,"
        "valuation_score,risk_score,overall_score) "
        "SELECT ?,ticker,status,bear_case_severity,cache_key,valuation_run_id,quality_run_id,?,"
        "business_quality_score,competitive_moat_score,financial_strength_score,management_score,"
        "valuation_score,risk_score,overall_score FROM committee_verdicts WHERE run_id=?",
        (run_id, "2026-01-02", RUN_IDS["candidate"]["committee"]),
    )
    conn.execute(
        "UPDATE committee_verdicts SET status=?,bear_case_severity=?,business_quality_score=?,"
        "competitive_moat_score=?,financial_strength_score=?,management_score=?,valuation_score=?,"
        "risk_score=?,overall_score=? WHERE run_id=? AND ticker='AAA'",
        (aaa_status, aaa_severity, *aaa_scores, run_id),
    )
    conn.commit()
    conn.close()
    replicate.chmod(0o444)


def _mutate_synthetic_db(path: Path, sql: str, params: tuple = ()) -> None:
    path.chmod(0o644)
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()
    path.chmod(0o444)


def _benchmark_record(ticker: str, label: str | None, *, ratio_shift: float = 0.0) -> dict:
    return {
        "ticker": ticker,
        "economic_moat": label,
        "coverage_tier": "analyst",
        "stratum": "pass",
        "snapshot_date": "2026-01-15",
        "source": "synthetic",
        "fair_value": 100.0,
        "last_price": 70.0,
        "five_star_price": 75.0,
        "one_star_price": 125.0,
        "uncertainty": "Medium",
        "star_rating": 5,
        "revenue": 100.0,
        "net_income": 5.0,
        "operating_cash_flow": 22.0,
        "capex": -8.0,
        "free_cash_flow": 14.0,
        "total_debt": 32.0,
        "cash_and_equiv": 6.0,
        "total_equity": 55.0,
        "shares_outstanding": 10.0,
        # Percent, as in the real benchmark; the fixture DB stores fractions.
        "gross_margin": 40.0 + ratio_shift,
        "operating_margin": 20.0,
        "roic": 15.0,
        "roe": 10.0,
    }


@pytest.fixture
def synthetic_inputs(tmp_path):
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    benchmark_dir = tmp_path / "benchmark"
    benchmark_dir.mkdir()
    _create_profile_db(baseline, "baseline-v1")
    _create_profile_db(candidate, "candidate-v1", candidate_values=True)
    records = [
        _benchmark_record("AAA", "Wide"),
        _benchmark_record("BBB", "Narrow"),
        _benchmark_record("CCC", "None", ratio_shift=3.0),
        _benchmark_record("DDD", "Wide"),
        _benchmark_record("EEE", "Wide"),
        _benchmark_record("FFF", "None"),
    ]
    benchmark_file = benchmark_dir / "tier-a-screener.jsonl"
    benchmark_file.write_text("".join(json.dumps(record) + "\n" for record in records))
    return baseline, candidate, benchmark_dir, records


def test_immutable_adapter_uses_exact_profile_and_cannot_write(synthetic_inputs):
    baseline, _, _, _ = synthetic_inputs
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        db.validate_schema()
        with pytest.raises(sqlite3.OperationalError):
            db.conn.execute("INSERT INTO companies (ticker) VALUES ('WRITE')")
    assert not Path(str(baseline) + "-wal").exists()
    assert not Path(str(baseline) + "-shm").exists()


def test_unknown_profile_shape_fails_closed(tmp_path):
    path = tmp_path / "shape.db"
    _create_profile_db(path, "baseline-v1")
    path.chmod(0o644)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE valuations ADD COLUMN price_date TEXT")
    conn.close()
    path.chmod(0o444)
    with ImmutableDatabase(path, "baseline-v1") as db:
        with pytest.raises(PreflightError, match="schema does not match"):
            db.validate_schema()


def test_declared_not_applicable_quant_status_is_accepted(synthetic_inputs):
    baseline, _, _, _ = synthetic_inputs
    baseline.chmod(0o644)
    conn = sqlite3.connect(baseline)
    conn.execute(
        "UPDATE quant_scores SET status='not_applicable' WHERE ticker='CCC'"
    )
    conn.commit()
    conn.close()
    baseline.chmod(0o444)
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        assert any(row["status"] == "not_applicable" for row in db.quant_rows(RUN_IDS["baseline"]["screen"]))


def test_benchmark_alias_collision_and_unknown_fields_fail_closed(tmp_path):
    path = tmp_path / "tier-a-screener.jsonl"
    first = _benchmark_record("BRK.B", "Wide")
    second = _benchmark_record("BRK-B", "Wide")
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    with pytest.raises(PreflightError, match="duplicate canonical"):
        load_benchmark(path)
    first["surprise"] = 1
    path.write_text(json.dumps(first) + "\n")
    with pytest.raises(PreflightError, match="unknown field"):
        load_benchmark(path)


def test_manifest_is_strict_and_records_tier_b_not_run(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    manifest = build_manifest(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
    )
    assert manifest["judging"] == {
        "status": "not_run", "rubric_sha256": None, "model": None,
        "prompt_sha256": None, "cohort_sha256": None,
    }
    assert manifest["bootstrap_replicates"] == 10_000
    assert manifest["eval_spec"]["base"]["status"] == "FROZEN"
    assert manifest["eval_spec"]["amendment_1"]["status"] == "FROZEN"
    assert manifest["experiments"]["noise_floor"] == {
        "run_id": None,
        "database": None,
        "runtime_provenance": None,
        "input_equivalence": None,
    }
    assert manifest["run_sets"]["candidate"]["production_provenance"]["screen"]["dependencies_sha256"]
    assert len(manifest["run_sets"]["candidate"]["production_provenance"]["committee"]["driver_sha256s"]) == 2
    broken = dict(manifest)
    broken["unexpected"] = True
    with pytest.raises(ManifestError, match="unknown key"):
        validate_manifest(broken)
    with pytest.raises(ManifestError, match="supplied together"):
        build_manifest(
            repo_root=REPO_ROOT,
            baseline_db=baseline,
            candidate_db=candidate,
            benchmark_dir=benchmark_dir,
            noise_floor_run_id="future-replicate",
        )


def _run_synthetic_preflight(
    *, baseline: Path, candidate: Path, benchmark_dir: Path, replicate: Path,
    manifest: dict, tmp_path: Path,
):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return run_preflight(
        repo_root=REPO_ROOT,
        manifest_path=manifest_path,
        manifest=manifest,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        private_output_dir=tmp_path / "private",
        public_output=tmp_path / "public.json",
        noise_floor_db=replicate,
        verify_git=False,
    )


def test_noise_floor_manifest_is_strict_and_complete(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    manifest = build_manifest(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        noise_floor_run_id="noise-run",
        noise_floor_db=replicate,
        noise_floor_runtime_provenance="synthetic equivalent runtime",
    )
    noise = manifest["experiments"]["noise_floor"]
    assert noise["database"]["path"] == str(replicate.resolve())
    assert noise["database"]["profile"] == "candidate-v1"
    assert noise["input_equivalence"] == {
        "method": "committee_bundle_v1",
        "reference_candidate_sha256": "61b9514a068b855998377f7262f55386783c2339f659370f8baea980bb7c3828",
        "quality_run_id": RUN_IDS["candidate"]["screen"],
        "valuation_run_id": RUN_IDS["candidate"]["valuation"],
    }
    partial = deepcopy(manifest)
    partial["experiments"]["noise_floor"]["runtime_provenance"] = None
    with pytest.raises(ManifestError, match="all null or all populated"):
        validate_manifest(partial)
    unknown = deepcopy(manifest)
    unknown["experiments"]["noise_floor"]["surprise"] = True
    with pytest.raises(ManifestError, match="unknown key"):
        validate_manifest(unknown)
    wrong_profile = deepcopy(manifest)
    wrong_profile["experiments"]["noise_floor"]["database"]["profile"] = "baseline-v1"
    with pytest.raises(ManifestError, match="candidate-v1"):
        validate_manifest(wrong_profile)
    aliased = deepcopy(manifest)
    aliased["experiments"]["noise_floor"]["database"]["path"] = str(candidate.resolve())
    with pytest.raises(ManifestError, match="alias"):
        validate_manifest(aliased)
    live = deepcopy(manifest)
    live["experiments"]["noise_floor"]["database"]["path"] = str(
        (tmp_path / "moat.db").resolve()
    )
    with pytest.raises(ManifestError, match="live data/moat.db"):
        validate_manifest(live)


def test_noise_floor_manifest_rejects_missing_and_aliased_databases(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    with pytest.raises(ManifestError, match="do not exist"):
        build_manifest(
            repo_root=REPO_ROOT,
            baseline_db=baseline,
            candidate_db=candidate,
            benchmark_dir=benchmark_dir,
            noise_floor_run_id="noise-run",
            noise_floor_db=tmp_path / "missing.db",
            noise_floor_runtime_provenance="synthetic equivalent runtime",
        )
    with pytest.raises(ManifestError, match="distinct"):
        build_manifest(
            repo_root=REPO_ROOT,
            baseline_db=baseline,
            candidate_db=candidate,
            benchmark_dir=benchmark_dir,
            noise_floor_run_id="noise-run",
            noise_floor_db=candidate,
            noise_floor_runtime_provenance="synthetic equivalent runtime",
        )
    hardlink = tmp_path / "hardlink-replicate.db"
    hardlink.hardlink_to(candidate)
    with pytest.raises(ManifestError, match="distinct"):
        build_manifest(
            repo_root=REPO_ROOT,
            baseline_db=baseline,
            candidate_db=candidate,
            benchmark_dir=benchmark_dir,
            noise_floor_run_id="noise-run",
            noise_floor_db=hardlink,
            noise_floor_runtime_provenance="synthetic equivalent runtime",
        )


@pytest.mark.parametrize("failure", ["writable", "sidecar", "wrong_hash", "live_name"])
def test_noise_floor_preflight_rejects_unsafe_snapshot_inputs(synthetic_inputs, tmp_path, failure):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    replicate = tmp_path / ("moat.db" if failure == "live_name" else "replicate.db")
    _create_replicate_db(candidate, replicate)
    if failure == "live_name":
        with pytest.raises(ManifestError, match="live data/moat.db"):
            build_manifest(
                repo_root=REPO_ROOT,
                baseline_db=baseline,
                candidate_db=candidate,
                benchmark_dir=benchmark_dir,
                noise_floor_run_id="noise-run",
                noise_floor_db=replicate,
                noise_floor_runtime_provenance="synthetic equivalent runtime",
            )
        return
    manifest = build_manifest(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        noise_floor_run_id="noise-run",
        noise_floor_db=replicate,
        noise_floor_runtime_provenance="synthetic equivalent runtime",
    )
    if failure == "writable":
        replicate.chmod(0o644)
        expected = "sealed read-only"
    elif failure == "sidecar":
        Path(str(replicate) + "-wal").write_bytes(b"")
        expected = "WAL/SHM sidecar"
    elif failure == "wrong_hash":
        manifest["experiments"]["noise_floor"]["database"]["sha256"] = "0" * 64
        expected = "hash mismatch"
    with pytest.raises(PreflightError, match=expected):
        _run_synthetic_preflight(
            baseline=baseline,
            candidate=candidate,
            benchmark_dir=benchmark_dir,
            replicate=replicate,
            manifest=manifest,
            tmp_path=tmp_path,
        )


def test_private_paths_inside_repository_are_rejected(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    manifest = build_manifest(
        repo_root=REPO_ROOT, baseline_db=baseline, candidate_db=candidate, benchmark_dir=benchmark_dir
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(PreflightError, match="private output"):
        validate_paths(
            repo_root=REPO_ROOT,
            manifest_path=manifest_path,
            manifest=manifest,
            baseline_db=baseline,
            candidate_db=candidate,
            benchmark_dir=benchmark_dir,
            private_output_dir=REPO_ROOT / "bad-private-output",
            public_output=tmp_path / "public.json",
        )


def test_tier_a_abstentions_metrics_strata_and_aliases(synthetic_inputs):
    baseline, _, _, records = synthetic_inputs
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        details, summary, exclusions = evaluate_tier_a(
            db,
            run_id=RUN_IDS["baseline"]["screen"],
            benchmark_records=records,
            bootstrap_seed=61001,
            bootstrap_replicates=10_000,
        )
    assert summary["pass_fail_abstain_by_moat"] == {
        "pass": {"Wide": 1, "Narrow": 1, "None": 1},
        "fail": {"Wide": 1, "Narrow": 0, "None": 1},
        "abstain": {"Wide": 1, "Narrow": 0, "None": 0},
    }
    assert summary["metrics"]["wide_precision"]["value"] == pytest.approx(1 / 3)
    assert summary["screen_abstention_count"] == 1
    assert next(row for row in details if row["ticker"] == "DDD")["run_derived_stratum"] == "abstain"
    assert exclusions == []


def test_tier_c_taxonomy_period_rules_and_roe_hypothesis(synthetic_inputs):
    _, candidate, _, records = synthetic_inputs
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        details, summary = evaluate_tier_c(db, benchmark_records=records)
    aaa = {(row["field"], row["ticker"]): row for row in details}
    assert aaa[("revenue", "AAA")]["outcome"] == "Inconclusive"
    assert aaa[("free_cash_flow", "AAA")]["outcome"] == "Definitional difference"
    assert aaa[("total_debt", "AAA")]["outcome"] == "Definitional difference"
    assert aaa[("gross_margin", "AAA")]["outcome"] == "Match"
    assert aaa[("gross_margin", "AAA")]["benchmark_normalized_value"] == pytest.approx(0.40)
    assert aaa[("gross_margin", "AAA")]["normalization_formula"] == "benchmark_percent_to_fraction"
    assert aaa[("operating_margin", "AAA")]["outcome"] == "Definitional difference"
    assert aaa[("gross_margin", "CCC")]["outcome"] == "Probable defect"
    assert aaa[("depreciation_amortization", "AAA")]["outcome"] == "Unavailable"
    assert aaa[("roe_average_equity_hypothesis", "AAA")]["reconstructed"] is True
    assert summary["comparable_count"] > 0
    assert symmetric_relative_difference(0, 0) == 0
    assert symmetric_relative_difference(10, 12) == pytest.approx(4 / 22)


def test_tier_c_debt_definition_is_row_specific_for_flagged_lease_fallback(synthetic_inputs):
    _, candidate, _, records = synthetic_inputs
    candidate.chmod(0o644)
    conn = sqlite3.connect(candidate)
    conn.execute(
        "UPDATE fundamentals_annual SET quality_flags='debt_includes_finance_leases' "
        "WHERE ticker='AAA' AND fiscal_year=2025"
    )
    conn.commit()
    conn.close()
    candidate.chmod(0o444)
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        details, _ = evaluate_tier_c(db, benchmark_records=records)
    debt = next(row for row in details if row["ticker"] == "AAA" and row["field"] == "total_debt")
    assert debt["outcome"] == "Inconclusive"
    assert debt["rule"] == "ttm_vs_fiscal_year_without_exact_reconciliation"


def test_valuation_joins_frozen_run_rows_and_keeps_price_views_separate(synthetic_inputs):
    _, candidate, _, records = synthetic_inputs
    changed = [dict(record) for record in records]
    changed[0]["last_price"] = 130.0
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        details, summaries = evaluate_valuation(
            db,
            run_id=RUN_IDS["candidate"]["valuation"],
            benchmark_records=changed,
        )
    aaa = {row["view"]: row for row in details if row["ticker"] == "AAA"}
    assert aaa["as_run"]["moat_classification"] == "Cheap"
    assert aaa["as_run"]["moat_comparison_price"] == 70.0
    assert aaa["price_normalised"]["moat_classification"] == "Expensive"
    assert aaa["price_normalised"]["moat_comparison_price"] == 130.0
    assert aaa["as_run"]["benchmark_class"] == "Expensive"
    assert summaries["as_run_analyst"]["scored_count"] == 1


def test_valuation_unknown_within_row_shape_abstains():
    rows = [
        {"method": "owner_earnings_dcf", "scenario": scenario, "intrinsic_value_low": value, "intrinsic_value_high": value}
        for scenario, value in (("bear", 80.0), ("base", 100.0), ("bull", 120.0))
    ]
    rows[1]["intrinsic_value_high"] = 101.0
    result = classify_moat_valuation(rows, 90.0)
    assert result["classification"] == "Abstain"
    assert result["rule"] == "material_within_row_intrinsic_range"


def test_committee_support_and_persona_cap_counterfactuals(synthetic_inputs):
    baseline, candidate, _, records = synthetic_inputs
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        details, summary = evaluate_committee(
            db,
            committee_run_id=RUN_IDS["baseline"]["committee"],
            screen_run_id=RUN_IDS["baseline"]["screen"],
            benchmark_records=records,
        )
    aaa = next(row for row in details if row["ticker"] == "AAA")
    assert aaa["committee_outcome"] == "Investigate"
    assert aaa["external_support"] == "strong"
    assert summary["investigate_support_composition"] == {
        "strong": 1, "weak": 0, "ambiguous": 0, "contradicted": 0, "abstain": 0,
    }

    with ImmutableDatabase(candidate, "candidate-v1") as db:
        persona_records, persona_summary = evaluate_persona_influence(
            db,
            committee_run_id=RUN_IDS["candidate"]["committee"],
            benchmark_records=records,
            primary=True,
        )
    assert persona_summary["weighted_score_point_contributions"]["Quality"]["designed_weight"] == pytest.approx(0.55)
    assert persona_summary["weighted_score_point_contributions"]["Valuation"]["designed_weight"] == pytest.approx(0.40)
    assert persona_summary["weighted_score_point_contributions"]["Bear"]["designed_weight"] == pytest.approx(0.05)
    aaa_persona = next(row for row in persona_records if row["ticker"] == "AAA")
    assert aaa_persona["counterfactuals"]["Bear_cap_disabled"]["status"] == "Investigate"


def test_noise_floor_optional_and_cross_snapshot_bundle_equivalence(synthetic_inputs, tmp_path):
    _, candidate, _, _ = synthetic_inputs
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        records, summary = evaluate_noise_floor(
            db,
            None,
            stored_run_id=RUN_IDS["candidate"]["committee"],
            replicate_run_id=None,
            candidate_quality_run_id=RUN_IDS["candidate"]["screen"],
            candidate_valuation_run_id=RUN_IDS["candidate"]["valuation"],
            runtime_provenance=None,
        )
    assert records == []
    assert summary["status"] == "unavailable"
    assert summary["reason"] == "replicate_run_id_not_supplied"

    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    with ImmutableDatabase(candidate, "candidate-v1") as db, ImmutableDatabase(
        replicate, "candidate-v1"
    ) as replicate_db:
        noise_records, noise_summary = evaluate_noise_floor(
            db,
            replicate_db,
            stored_run_id=RUN_IDS["candidate"]["committee"],
            replicate_run_id="noise-run",
            candidate_quality_run_id=RUN_IDS["candidate"]["screen"],
            candidate_valuation_run_id=RUN_IDS["candidate"]["valuation"],
            runtime_provenance="synthetic equivalent runtime",
        )
    assert noise_summary["status"] == "available"
    assert noise_summary["paired_company_count"] == 1
    assert noise_summary["replicate_preflight_invalid_abstention_count"] == 0
    assert noise_records[0]["component_score_deltas"]["business_quality_score"] == 1.0


def test_preflight_only_establishes_replicate_bundle_contract(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    manifest = build_manifest(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        noise_floor_run_id="noise-run",
        noise_floor_db=replicate,
        noise_floor_runtime_provenance="synthetic equivalent runtime",
    )
    report, _ = _run_synthetic_preflight(
        baseline=baseline,
        candidate=candidate,
        benchmark_dir=benchmark_dir,
        replicate=replicate,
        manifest=manifest,
        tmp_path=tmp_path,
    )
    assert report.checks["noise_floor_database"] == "passed"
    assert report.checks["noise_floor_experiment"] == "passed"


def test_preflight_marks_missing_bundle_capability_unavailable(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    _mutate_synthetic_db(replicate, "DROP TABLE ai_analysis")
    manifest = build_manifest(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        noise_floor_run_id="noise-run",
        noise_floor_db=replicate,
        noise_floor_runtime_provenance="synthetic equivalent runtime",
    )
    report, _ = _run_synthetic_preflight(
        baseline=baseline,
        candidate=candidate,
        benchmark_dir=benchmark_dir,
        replicate=replicate,
        manifest=manifest,
        tmp_path=tmp_path,
    )
    assert report.checks["noise_floor_experiment"] == (
        "unavailable:committee_bundle_equivalence_failed"
    )


def test_nearest_rank_boundary_and_overall_score_labels_are_frozen():
    assert nearest_rank_percentile([float(value) for value in range(1, 21)], 0.95) == 19.0
    paired = {
        "paired_common_count": 2,
        "status_flip_count": 1,
        "bear_severity_change_count": 0,
        "records": [
            {"ticker": "AAA", "score_delta": -2.0},
            {"ticker": "BBB", "score_delta": 2.01},
        ],
    }
    noise = {
        "status": "available",
        "paired_company_count": 20,
        "status_flip_count": 2,
        "bear_severity_change_count": 3,
        "practical_noise_boundary": {
            "value": 2.0,
            "cohort_size": 20,
            "calculation_method": NEAREST_RANK_METHOD,
        },
    }
    interpreted, summary = apply_noise_interpretation(paired, noise)
    assert interpreted["records"][0]["noise_interpretation"] == AT_OR_BELOW_BOUNDARY
    assert interpreted["records"][1]["noise_interpretation"] == ABOVE_BOUNDARY
    assert summary["status_flip_comparison"] == {
        "baseline_candidate": {"count": 1, "denominator": 2, "rate": 0.5},
        "candidate_replicate": {"count": 2, "denominator": 20, "rate": 0.1},
    }
    assert summary["overall_score_noise_interpretation_counts"] == {
        AT_OR_BELOW_BOUNDARY: 1,
        ABOVE_BOUNDARY: 1,
    }


def test_unavailable_noise_floor_emits_no_company_level_label():
    paired = {
        "paired_common_count": 1,
        "status_flip_count": 0,
        "bear_severity_change_count": 0,
        "records": [{"ticker": "AAA", "score_delta": 0.1}],
    }
    interpreted, summary = apply_noise_interpretation(
        paired,
        {"status": "unavailable", "reason": "synthetic"},
    )
    assert "noise_interpretation" not in interpreted["records"][0]
    assert "status_flip_comparison" not in summary


def test_noise_floor_private_and_public_outputs_follow_amendment_interpretation(
    synthetic_inputs, tmp_path
):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    manifest = build_manifest(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        noise_floor_run_id="noise-run",
        noise_floor_db=replicate,
        noise_floor_runtime_provenance="synthetic equivalent runtime",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    private = tmp_path / "private"
    public = tmp_path / "public.json"
    run_evaluation(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        manifest_path=manifest_path,
        private_output_dir=private,
        public_output=public,
        noise_floor_db=replicate,
        noise_floor_run_id="noise-run",
        verify_git=False,
    )
    paired = json.loads((private / "committee-paired.json").read_text())
    paired_record = paired["records"][0]
    assert paired_record["noise_interpretation"] == AT_OR_BELOW_BOUNDARY
    assert not {"baseline_status", "candidate_status", "status_flip"} & set(paired_record)
    noise_record = json.loads(
        (private / "committee-noise-floor-records.jsonl").read_text().splitlines()[0]
    )
    assert "noise_interpretation" not in noise_record
    assert not {
        "stored_status", "replicate_status", "status_flip",
        "stored_bear_severity", "replicate_bear_severity", "bear_severity_changed",
    } & set(noise_record)
    assert all("noise" not in key for key in noise_record["component_score_deltas"])
    public_data = json.loads(public.read_text())
    public_noise = public_data["sensitivity_analyses"]["committee_noise_floor"]
    assert public_noise["practical_noise_boundary"]["calculation_method"] == NEAREST_RANK_METHOD
    assert public_noise["status_flip_comparison"]["baseline_candidate"]["count"] == 1
    assert public_noise["status_flip_comparison"]["candidate_replicate"]["count"] == 1
    assert public_noise["overall_score_noise_interpretation_counts"] == {
        AT_OR_BELOW_BOUNDARY: 1,
        ABOVE_BOUNDARY: 0,
    }
    assert "AAA" not in json.dumps(public_noise)


@pytest.mark.parametrize(
    ("failure", "failure_key"),
    [
        ("candidate_bundle_unavailable", "candidate_bundle_unavailable"),
        ("replicate_bundle_unavailable", "replicate_bundle_unavailable"),
        ("candidate_cache_key_mismatch", "candidate_cache_key_mismatch"),
        ("replicate_cache_key_mismatch", "replicate_cache_key_mismatch"),
        ("cross_snapshot_bundle_mismatch", "cross_snapshot_bundle_mismatch"),
    ],
)
def test_noise_floor_rejects_each_bundle_mismatch_class(
    synthetic_inputs, tmp_path, failure, failure_key
):
    _, candidate, _, _ = synthetic_inputs
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    if failure == "candidate_bundle_unavailable":
        _mutate_synthetic_db(
            candidate,
            "DELETE FROM ai_analysis WHERE ticker='AAA' AND analysis_type='risk'",
        )
    elif failure == "replicate_bundle_unavailable":
        _mutate_synthetic_db(
            replicate,
            "DELETE FROM ai_analysis WHERE ticker='AAA' AND analysis_type='risk'",
        )
    elif failure == "candidate_cache_key_mismatch":
        _mutate_synthetic_db(
            candidate,
            "UPDATE committee_verdicts SET cache_key=? WHERE run_id=? AND ticker='AAA'",
            ("0" * 64, RUN_IDS["candidate"]["committee"]),
        )
    elif failure == "replicate_cache_key_mismatch":
        _mutate_synthetic_db(
            replicate,
            "UPDATE committee_verdicts SET cache_key=? WHERE run_id='noise-run' AND ticker='AAA'",
            ("0" * 64,),
        )
    else:
        _mutate_synthetic_db(
            replicate,
            "UPDATE ai_analysis SET cache_key='different-input' "
            "WHERE ticker='AAA' AND analysis_type='risk'",
        )
        with ImmutableDatabase(replicate, "candidate-v1") as replicate_db:
            changed_key = replicate_db.recompute_committee_bundle_key(
                "AAA", RUN_IDS["candidate"]["valuation"], RUN_IDS["candidate"]["screen"]
            )
        assert changed_key is not None
        _mutate_synthetic_db(
            replicate,
            "UPDATE committee_verdicts SET cache_key=? WHERE run_id='noise-run' AND ticker='AAA'",
            (changed_key,),
        )
    with ImmutableDatabase(candidate, "candidate-v1") as candidate_db, ImmutableDatabase(
        replicate, "candidate-v1"
    ) as replicate_db:
        records, summary = evaluate_noise_floor(
            candidate_db,
            replicate_db,
            stored_run_id=RUN_IDS["candidate"]["committee"],
            replicate_run_id="noise-run",
            candidate_quality_run_id=RUN_IDS["candidate"]["screen"],
            candidate_valuation_run_id=RUN_IDS["candidate"]["valuation"],
            runtime_provenance="synthetic equivalent runtime",
        )
    assert records == []
    assert summary["status"] == "unavailable"
    assert summary["failure_counts"][failure_key] == 1


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("missing_run", "replicate_run_not_found_in_replicate_snapshot"),
        ("wrong_stage", "replicate_run_did_not_complete_committee_stage"),
        ("wrong_status", "replicate_run_did_not_complete_committee_stage"),
        ("cohort", "replicate_cohort_does_not_equal_stored_candidate_cohort"),
        ("cache_reuse", "replicate_contains_cache_reused_verdicts"),
        ("lineage", "committee_verdict_lineage_does_not_match_frozen_candidate_inputs"),
        ("invalid_output", "no_preflight_valid_paired_companies"),
    ],
)
def test_noise_floor_rejects_invalid_replicate_run_contract(
    synthetic_inputs, tmp_path, failure, reason
):
    _, candidate, _, _ = synthetic_inputs
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    run_id = "absent-run" if failure == "missing_run" else "noise-run"
    if failure == "wrong_stage":
        _mutate_synthetic_db(
            replicate,
            "UPDATE pipeline_runs SET stage_reached='valuation' WHERE run_id='noise-run'",
        )
    elif failure == "wrong_status":
        _mutate_synthetic_db(
            replicate,
            "UPDATE pipeline_runs SET status='failed' WHERE run_id='noise-run'",
        )
    elif failure == "cohort":
        _insert_verdict(
            replicate,
            "noise-run",
            "BBB",
            (60.0, 60.0, 60.0, 60.0, 60.0, 40.0, 60.0),
            status="Watch",
            severity="low",
        )
    elif failure == "cache_reuse":
        _mutate_synthetic_db(
            replicate,
            "UPDATE committee_verdicts SET reused_from_run_id=? WHERE run_id='noise-run'",
            (RUN_IDS["candidate"]["committee"],),
        )
    elif failure == "lineage":
        _mutate_synthetic_db(
            replicate,
            "UPDATE committee_verdicts SET quality_run_id='wrong' WHERE run_id='noise-run'",
        )
    elif failure == "invalid_output":
        _mutate_synthetic_db(
            replicate,
            "UPDATE committee_verdicts SET overall_score=999 WHERE run_id='noise-run'",
        )
    with ImmutableDatabase(candidate, "candidate-v1") as candidate_db, ImmutableDatabase(
        replicate, "candidate-v1"
    ) as replicate_db:
        records, summary = evaluate_noise_floor(
            candidate_db,
            replicate_db,
            stored_run_id=RUN_IDS["candidate"]["committee"],
            replicate_run_id=run_id,
            candidate_quality_run_id=RUN_IDS["candidate"]["screen"],
            candidate_valuation_run_id=RUN_IDS["candidate"]["valuation"],
            runtime_provenance="synthetic equivalent runtime",
        )
    assert records == []
    assert summary["status"] == "unavailable"
    assert summary["reason"] == reason


def _replace_synthetic_screen_rows(path: Path, ticker: str, statuses: dict[str, str]) -> None:
    path.chmod(0o644)
    conn = sqlite3.connect(path)
    conn.execute(
        "DELETE FROM quant_scores WHERE run_id=? AND ticker=?",
        (RUN_IDS["baseline"]["screen"], ticker),
    )
    conn.executemany(
        "INSERT INTO quant_scores (run_id,ticker,metric,overall_pass,status) VALUES (?,?,?,?,?)",
        [
            (
                RUN_IDS["baseline"]["screen"], ticker, metric,
                int(status == "pass"), status,
            )
            for metric, status in statuses.items()
        ],
    )
    conn.commit()
    conn.close()
    path.chmod(0o444)


def test_per_fix_replay_verifies_frozen_before_states_and_recomputes_transitions(synthetic_inputs):
    baseline, _, _, _ = synthetic_inputs
    metrics = {metric: "fail" for metric in {
        "roic", "roe", "free_cash_flow", "revenue_eps_growth", "operating_margin",
        "debt", "share_dilution", "gross_margin",
    }}
    for metric in ("roic", "roe", "gross_margin"):
        metrics[metric] = "pass"
    counterfactual = dict(metrics)
    counterfactual["debt"] = "pass"
    _replace_synthetic_screen_rows(baseline, "AAA", metrics)
    records = [
        {
            "fix_id": "synthetic-screen-fix",
            "ticker": "AAA",
            "stage": "screen",
            "before": {"metric_statuses": metrics},
            "counterfactual": {"metric_statuses": counterfactual},
            "evidence": "synthetic isolated metric change",
            "other_inputs_held_constant": True,
        },
        {
            "fix_id": "synthetic-valuation-fix",
            "ticker": "AAA",
            "stage": "valuation",
            "before": {"bear": 80.0, "base": 100.0, "bull": 120.0, "price": 70.0},
            "counterfactual": {"bear": 50.0, "base": 60.0, "bull": 65.0, "price": 70.0},
            "evidence": "synthetic isolated DCF change",
            "other_inputs_held_constant": True,
        },
    ]
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        traces, summary = evaluate_replays(
            records,
            baseline_db=db,
            baseline_screen_run_id=RUN_IDS["baseline"]["screen"],
            baseline_valuation_run_id=RUN_IDS["baseline"]["valuation"],
        )
    assert summary["status"] == "available"
    assert traces[0]["final_transition"] == "fail_to_pass"
    assert traces[1]["final_transition"] == "Cheap_to_Expensive"


@pytest.mark.parametrize("stage", ["screen", "valuation"])
def test_per_fix_replay_rejects_before_state_mismatches(synthetic_inputs, stage):
    baseline, _, _, _ = synthetic_inputs
    metrics = {metric: "fail" for metric in {
        "roic", "roe", "free_cash_flow", "revenue_eps_growth", "operating_margin",
        "debt", "share_dilution", "gross_margin",
    }}
    metrics["roic"] = "pass"
    _replace_synthetic_screen_rows(baseline, "AAA", metrics)
    before = (
        {"metric_statuses": {**metrics, "debt": "pass"}}
        if stage == "screen"
        else {"bear": 80.0, "base": 100.0, "bull": 120.0, "price": 71.0}
    )
    record = {
        "fix_id": "synthetic-mismatch",
        "ticker": "AAA",
        "stage": stage,
        "before": before,
        "counterfactual": before,
        "evidence": "synthetic mismatch",
        "other_inputs_held_constant": True,
    }
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        with pytest.raises(PreflightError, match="does not match the frozen baseline"):
            evaluate_replays(
                [record],
                baseline_db=db,
                baseline_screen_run_id=RUN_IDS["baseline"]["screen"],
                baseline_valuation_run_id=RUN_IDS["baseline"]["valuation"],
            )


def test_tier_c_average_equity_roe_requires_consecutive_fiscal_years(synthetic_inputs):
    _, candidate, _, records = synthetic_inputs
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        details, _ = evaluate_tier_c(db, benchmark_records=records)
    roe = {row["ticker"]: row for row in details if row["field"] == "roe_average_equity_hypothesis"}
    assert roe["AAA"]["moat_normalized_value"] == pytest.approx(5_000_000.0 / 50_000_000.0)

    candidate.chmod(0o644)
    conn = sqlite3.connect(candidate)
    conn.execute("UPDATE fundamentals_annual SET fiscal_year=2023 WHERE ticker='AAA' AND fiscal_year=2024")
    conn.commit()
    conn.close()
    candidate.chmod(0o444)
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        details, _ = evaluate_tier_c(db, benchmark_records=records)
    gap = next(row for row in details if row["ticker"] == "AAA" and row["field"] == "roe_average_equity_hypothesis")
    assert gap["moat_normalized_value"] is None
    assert gap["outcome"] == "Unavailable"


def test_public_allowlist_suppresses_small_cells_and_contains_no_records(synthetic_inputs):
    baseline, _, _, records = synthetic_inputs
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        _, a_summary, _ = evaluate_tier_a(
            db, run_id=RUN_IDS["baseline"]["screen"], benchmark_records=records,
            bootstrap_seed=61001, bootstrap_replicates=10_000,
        )
        _, c_summary = evaluate_tier_c(db, benchmark_records=records)
    public_a = public_tier_a(a_summary)
    public_c = public_tier_c(c_summary)
    encoded = json.dumps({"a": public_a, "c": public_c})
    assert "AAA" not in encoded
    assert public_a["pass_fail_abstain_by_moat"]["pass"]["Wide"] == "suppressed_lt_5"
    assert "benchmark_normalized_value" not in encoded


def test_synthetic_end_to_end_writes_private_details_and_public_aggregates(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    manifest = build_manifest(
        repo_root=REPO_ROOT, baseline_db=baseline, candidate_db=candidate, benchmark_dir=benchmark_dir
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    private = tmp_path / "private" / "details"
    public = tmp_path / "public.json"
    result = run_evaluation(
        repo_root=REPO_ROOT,
        baseline_db=baseline,
        candidate_db=candidate,
        benchmark_dir=benchmark_dir,
        manifest_path=manifest_path,
        private_output_dir=private,
        public_output=public,
        verify_git=False,
    )
    assert result["status"] == "mechanical_eval_v1_without_tier_b_complete"
    assert result["tier_b"] == "not_run"
    assert (private / "tier-a-baseline-records.jsonl").is_file()
    assert (private / "tier-c-candidate-records.jsonl").is_file()
    private_text = (private / "tier-a-baseline-records.jsonl").read_text()
    assert '"ticker": "AAA"' in private_text
    public_data = json.loads(public.read_text())
    assert public_data["tier_b"]["status"] == "not_run"
    assert "AAA" not in public.read_text()
    assert public_data["paired_baseline_candidate"]["tier_a"]["claim_boundary"].startswith("combined Phase 1 effect")
    assert public_data["persona_influence_and_external_alignment"]["candidate"]["external_alignment"]["Quality_vs_economic_moat"]["denominator"] == "suppressed_lt_5"


def _insert_verdict(path: Path, run_id: str, ticker: str, scores: tuple, *, status: str, severity: str,
                    cache_key: str = "synthetic-key", profile_name: str = "candidate") -> None:
    path.chmod(0o644)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO committee_verdicts (run_id,ticker,status,bear_case_severity,cache_key,valuation_run_id,quality_run_id,created_at,business_quality_score,competitive_moat_score,financial_strength_score,management_score,valuation_score,risk_score,overall_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, ticker, status, severity, cache_key, RUN_IDS[profile_name]["valuation"], RUN_IDS[profile_name]["screen"], "2026-01-02", *scores),
    )
    conn.commit()
    conn.close()
    path.chmod(0o444)


def test_persona_excludes_preflight_invalid_verdicts_from_medians_and_counterfactuals(synthetic_inputs):
    _, candidate, _, records = synthetic_inputs
    # Numerically valid, but preflight flagged it (e.g. lineage or cache-key failure).
    _insert_verdict(
        candidate, RUN_IDS["candidate"]["committee"], "BBB",
        (10.0, 10.0, 10.0, 10.0, 10.0, 90.0, 9.0), status="Reject", severity="low",
    )
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        persona_records, summary = evaluate_persona_influence(
            db,
            committee_run_id=RUN_IDS["candidate"]["committee"],
            benchmark_records=records,
            primary=True,
            invalid_tickers={"BBB"},
        )
    assert [row["ticker"] for row in persona_records] == ["AAA"]
    assert summary["valid_verdict_count"] == 1
    assert summary["preflight_invalid_abstention_count"] == 1
    assert summary["invalid_or_missing_numeric_count"] == 0
    assert summary["component_medians"]["business_quality_score"] == 80.0


def test_noise_floor_rejects_stored_run_as_its_own_replicate(synthetic_inputs, tmp_path):
    baseline, candidate, benchmark_dir, _ = synthetic_inputs
    stored = RUN_IDS["candidate"]["committee"]
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    with pytest.raises(ManifestError, match="distinct"):
        build_manifest(
            repo_root=REPO_ROOT,
            baseline_db=baseline,
            candidate_db=candidate,
            benchmark_dir=benchmark_dir,
            noise_floor_run_id=stored,
            noise_floor_db=replicate,
            noise_floor_runtime_provenance="synthetic equivalent runtime",
        )
    with ImmutableDatabase(candidate, "candidate-v1") as db, ImmutableDatabase(
        replicate, "candidate-v1"
    ) as replicate_db:
        records, summary = evaluate_noise_floor(
            db,
            replicate_db,
            stored_run_id=stored,
            replicate_run_id=stored,
            candidate_quality_run_id=RUN_IDS["candidate"]["screen"],
            candidate_valuation_run_id=RUN_IDS["candidate"]["valuation"],
            runtime_provenance="synthetic equivalent runtime",
        )
    assert records == []
    assert summary["status"] == "unavailable"
    assert summary["reason"] == "replicate_run_id_is_not_distinct_from_every_frozen_run"


def test_noise_floor_does_not_pair_preflight_invalid_stored_verdicts(synthetic_inputs, tmp_path):
    _, candidate, _, _ = synthetic_inputs
    stored = RUN_IDS["candidate"]["committee"]
    _insert_verdict(candidate, stored, "BBB", (10.0, 10.0, 10.0, 10.0, 10.0, 90.0, 9.0), status="Reject", severity="low")
    # Give the synthetic BBB row a valid bundle key before cloning the snapshot.
    with ImmutableDatabase(candidate, "candidate-v1") as db:
        bbb_key = db.recompute_committee_bundle_key(
            "BBB", RUN_IDS["candidate"]["valuation"], RUN_IDS["candidate"]["screen"]
        )
    assert bbb_key is None  # BBB lacks valuation inputs and is preflight-invalid.
    replicate = tmp_path / "replicate.db"
    _create_replicate_db(candidate, replicate)
    with ImmutableDatabase(candidate, "candidate-v1") as db, ImmutableDatabase(
        replicate, "candidate-v1"
    ) as replicate_db:
        records, summary = evaluate_noise_floor(
            db,
            replicate_db,
            stored_run_id=stored,
            replicate_run_id="noise-run",
            candidate_quality_run_id=RUN_IDS["candidate"]["screen"],
            candidate_valuation_run_id=RUN_IDS["candidate"]["valuation"],
            runtime_provenance="synthetic equivalent runtime",
            invalid_tickers={"BBB"},
        )
    assert records == []
    assert summary["status"] == "unavailable"
    assert summary["failure_counts"]["candidate_bundle_unavailable"] == 1
    assert summary["failure_counts"]["replicate_bundle_unavailable"] == 1


def test_public_category_cells_have_fixed_keys_so_zero_and_small_cells_look_alike(synthetic_inputs):
    from moat.evals.reporting import public_committee, public_valuation

    baseline, _, _, records = synthetic_inputs
    with ImmutableDatabase(baseline, "baseline-v1") as db:
        _, committee = evaluate_committee(
            db,
            committee_run_id=RUN_IDS["baseline"]["committee"],
            screen_run_id=RUN_IDS["baseline"]["screen"],
            benchmark_records=records,
        )
        _, valuation = evaluate_valuation(db, run_id=RUN_IDS["baseline"]["valuation"], benchmark_records=records)
    public_c = public_committee(committee)
    composition = public_c["investigate_support_composition"]
    assert set(composition) == {"strong", "weak", "ambiguous", "contradicted", "abstain"}
    assert set(composition.values()) == {"suppressed_lt_5"}
    for values in public_c["research_priority_enrichment"].values():
        assert set(values["composition"]) == {"strong", "weak", "ambiguous", "contradicted"}
    for summary in public_valuation(valuation).values():
        assert set(summary["benchmark_class_distribution"]) == {"Cheap", "Fair", "Expensive", "Abstain"}
        assert set(summary["moat_class_distribution"]) == {"Cheap", "Fair", "Expensive", "Abstain"}


def test_public_rates_do_not_reveal_a_small_complement_cell():
    from moat.evals.reporting import _public_metric, public_committee

    # 6 of 8 would publish the 2 companies outside the numerator by subtraction.
    hidden = _public_metric({"numerator": 6, "denominator": 8, "value": 0.75, "kind": "descriptive"})
    assert hidden["numerator"] == "suppressed_lt_5"
    assert hidden["value"] is None
    shown = _public_metric({"numerator": 6, "denominator": 12, "value": 0.5, "kind": "descriptive"})
    assert shown == {"numerator": 6, "denominator": 12, "value": 0.5, "kind": "descriptive"}

    association = {"denominator": 0, "value": None, "kind": "exploratory"}
    enrichment = {
        status: {"supported_numerator": 6, "denominator": 8, "supported_rate": 0.75,
                 "composition": {"strong": 6, "weak": 0, "ambiguous": 2, "contradicted": 0}}
        for status in ("Reject", "Watch", "Investigate")
    }
    public = public_committee({
        "eligible_screen_passer_count": 8, "committee_scored_count": 8,
        "committee_abstention_count": 0, "committee_coverage_rate": 1.0,
        "investigate_support_composition": {},
        "investigate_contradiction_rate": {"numerator": 0, "denominator": 8, "value": 0.0, "kind": "descriptive_conservative"},
        "support_composition_by_status": {},
        "research_priority_enrichment": enrichment,
        "priority_support_spearman_rho": association,
        "watch_score_external_support_trend": association,
        "direct_status_star_rating_spearman_rho": association,
    })
    watch = public["research_priority_enrichment"]["Watch"]
    assert watch["supported_numerator"] == "suppressed_lt_5"
    assert watch["supported_rate"] is None


def test_paired_valuation_keeps_cohorts_separate_and_requires_benchmark_eligibility():
    from moat.evals.valuation import paired_valuation

    def row(ticker, tier, benchmark_class, moat_class, margin):
        return {
            "ticker": ticker, "view": "as_run", "coverage_tier": tier,
            "benchmark_class": benchmark_class, "moat_classification": moat_class,
            "moat_base_margin_of_safety": margin,
        }

    baseline = [
        row("AAA", "analyst", "Fair", "Fair", 0.0),
        row("BBB", "quantitative", "Fair", "Fair", 0.0),
        row("CCC", "analyst", "Abstain", "Fair", 0.0),
    ]
    candidate = [
        row("AAA", "analyst", "Fair", "Cheap", 0.3),
        row("BBB", "quantitative", "Fair", "Expensive", -0.3),
        row("CCC", "analyst", "Abstain", "Cheap", 0.3),
    ]
    paired = paired_valuation(baseline, candidate)
    assert paired["as_run_analyst"]["paired_scored_count"] == 1
    assert paired["as_run_analyst"]["class_transitions"] == {"Fair_to_Cheap": 1}
    assert paired["as_run_analyst"]["orientation"] == "primary"
    assert paired["as_run_quantitative"]["class_transitions"] == {"Fair_to_Expensive": 1}
    assert paired["as_run_quantitative"]["orientation"] == "secondary"
