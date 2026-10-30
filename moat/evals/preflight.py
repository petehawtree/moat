"""Frozen v1 preflight: hashes, schemas, runs, lineage and identifiers."""

from __future__ import annotations

import platform
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .benchmark import canonical_ticker, load_benchmark
from .constants import (
    FROZEN_SPEC_SHA256, KNOWN_QUANT_METRICS, KNOWN_QUANT_STATUSES, RUN_IDS, SNAPSHOT_BYTES,
    SPEC_HASH_RELATIVE_PATH, SPEC_RELATIVE_PATH,
)
from .database import ImmutableDatabase
from .errors import PreflightError
from .util import path_is_within, sha256_file


@dataclass
class PreflightReport:
    checks: dict[str, str] = field(default_factory=dict)
    record_issues: dict[str, dict[str, list[str]]] = field(
        default_factory=lambda: {"baseline": {}, "candidate": {}}
    )

    def add_record_issue(self, run_set: str, ticker: str, code: str) -> None:
        self.record_issues[run_set].setdefault(ticker, []).append(code)

    def private_dict(self) -> dict:
        return {"checks": self.checks, "record_issues": self.record_issues}

    def public_dict(self) -> dict:
        return {
            "checks": self.checks,
            "record_issue_counts": {
                name: sum(len(codes) for codes in issues.values())
                for name, issues in self.record_issues.items()
            },
        }


def _resolved_manifest_path(raw: str, manifest_path: Path) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def validate_paths(
    *, repo_root: Path, manifest_path: Path, manifest: dict, baseline_db: Path,
    candidate_db: Path, benchmark_dir: Path, private_output_dir: Path, public_output: Path,
) -> None:
    repo_root = repo_root.resolve()
    if path_is_within(manifest_path, repo_root):
        raise PreflightError("execution manifest must be outside the repository")
    if path_is_within(private_output_dir, repo_root):
        raise PreflightError("private output directory must be outside the repository")
    if path_is_within(benchmark_dir, repo_root):
        raise PreflightError("benchmark directory must be outside the repository")
    for name, supplied in (("baseline", baseline_db), ("candidate", candidate_db)):
        declared = _resolved_manifest_path(manifest["run_sets"][name]["database"]["path"], manifest_path)
        if supplied.resolve() != declared:
            raise PreflightError(f"explicit {name} database path does not match manifest")
    declared_benchmark = _resolved_manifest_path(manifest["benchmark"]["directory"], manifest_path)
    if benchmark_dir.resolve() != declared_benchmark:
        raise PreflightError("explicit benchmark directory does not match manifest")
    if private_output_dir.resolve() == public_output.resolve():
        raise PreflightError("private and public output targets must be distinct")


def _verify_spec(repo_root: Path, manifest: dict) -> None:
    spec = repo_root / SPEC_RELATIVE_PATH
    sidecar = repo_root / SPEC_HASH_RELATIVE_PATH
    if not spec.is_file() or not sidecar.is_file():
        raise PreflightError("frozen eval specification or hash sidecar is missing")
    try:
        sidecar_hash = sidecar.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError) as exc:
        raise PreflightError("frozen eval specification hash sidecar is invalid") from exc
    actual = sha256_file(spec)
    if actual != sidecar_hash or actual != FROZEN_SPEC_SHA256 or actual != manifest["eval_spec"]["sha256"]:
        raise PreflightError("frozen eval specification hash mismatch")


def _verify_git(repo_root: Path, manifest: dict) -> None:
    try:
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PreflightError("harness Git state cannot be verified") from exc
    if actual != manifest["harness"]["git_commit"]:
        raise PreflightError("harness Git commit does not match manifest")
    if dirty != manifest["harness"]["git_dirty"]:
        raise PreflightError("harness dirty state does not match manifest")
    if manifest["evaluation_mode"] == "authoritative" and dirty:
        raise PreflightError("authoritative execution requires a clean harness worktree")


def _check_run(db: ImmutableDatabase, run_id: str, stage: str) -> None:
    row = db.run_row(run_id)
    if row is None:
        raise PreflightError(f"required {stage} run is missing")
    stage_order = {None: -1, "ingest": 0, "screen": 1, "quality": 2, "ai_analysis": 3, "valuation": 4, "committee": 5, "monitor": 6}
    required = {"screen": 2, "valuation": 4, "committee": 5}[stage]
    if row["stage_reached"] not in stage_order or stage_order[row["stage_reached"]] < required:
        raise PreflightError(f"required {stage} run did not reach its expected stage")
    if row["status"] not in {"complete", "partial", "failed"}:
        raise PreflightError(f"required {stage} run has an unknown status")


def _validate_db_records(db: ImmutableDatabase, name: str, report: PreflightReport) -> set[str]:
    runs = RUN_IDS[name]
    for stage, run_id in runs.items():
        _check_run(db, run_id, stage)
    quality = db.quality_rows(runs["screen"])
    if not quality:
        raise PreflightError("required screen run contains no quality rows")
    for row in quality:
        if row["passed_screen"] not in (0, 1):
            raise PreflightError("quality rows contain an unknown screen category")
        for key in ("metrics_assessed", "metrics_passed"):
            if row[key] is not None and (not isinstance(row[key], int) or row[key] < 0 or row[key] > 8):
                raise PreflightError("quality rows contain invalid metric counts")
    quant = db.quant_rows(runs["screen"])
    if not quant:
        raise PreflightError("required screen run contains no quant rows")
    for row in quant:
        if row["metric"] not in KNOWN_QUANT_METRICS or row["status"] not in KNOWN_QUANT_STATUSES:
            raise PreflightError("quant rows contain an unknown metric or status category")
    if db.valuation_count(runs["valuation"]) == 0:
        raise PreflightError("required valuation run contains no rows")
    committee = db.committee_rows(runs["committee"])
    if not committee:
        raise PreflightError("required committee run contains no rows")
    valid_tickers: set[str] = set()
    for row in committee:
        ticker = canonical_ticker(row["ticker"])
        valid = True
        if row["status"] not in {"Investigate", "Watch", "Reject"}:
            report.add_record_issue(name, ticker, "committee_unknown_status")
            valid = False
        if row["bear_case_severity"] not in {"low", "medium", "high"}:
            report.add_record_issue(name, ticker, "committee_unknown_severity")
            valid = False
        if row["valuation_run_id"] != runs["valuation"] or row["quality_run_id"] != runs["screen"]:
            report.add_record_issue(name, ticker, "committee_lineage_mismatch")
            valid = False
        current, seen = row, set()
        while current["reused_from_run_id"] is not None:
            source_id = current["reused_from_run_id"]
            if source_id in seen:
                report.add_record_issue(name, ticker, "committee_reuse_cycle")
                valid = False
                break
            seen.add(source_id)
            source = db.committee_source(source_id, row["ticker"])
            if source is None:
                report.add_record_issue(name, ticker, "committee_reuse_source_missing")
                valid = False
                break
            if source["cache_key"] != row["cache_key"]:
                report.add_record_issue(name, ticker, "committee_cache_key_mismatch")
                valid = False
                break
            current = source
        if db.can_recompute_committee_bundle():
            recomputed = db.recompute_committee_bundle_key(
                row["ticker"], runs["valuation"], runs["screen"]
            )
            if recomputed is None:
                report.add_record_issue(name, ticker, "committee_cache_bundle_inputs_incomplete")
                valid = False
            elif recomputed != row["cache_key"]:
                report.add_record_issue(name, ticker, "committee_cache_bundle_mismatch")
                valid = False
        if valid:
            valid_tickers.add(ticker)
    return valid_tickers


def run_preflight(
    *, repo_root: Path, manifest_path: Path, manifest: dict, baseline_db: Path,
    candidate_db: Path, benchmark_dir: Path, private_output_dir: Path, public_output: Path,
    verify_git: bool = True,
) -> tuple[PreflightReport, list[dict]]:
    report = PreflightReport()
    validate_paths(
        repo_root=repo_root, manifest_path=manifest_path, manifest=manifest,
        baseline_db=baseline_db, candidate_db=candidate_db, benchmark_dir=benchmark_dir,
        private_output_dir=private_output_dir, public_output=public_output,
    )
    report.checks["path_boundary"] = "passed"
    _verify_spec(repo_root, manifest)
    report.checks["frozen_spec"] = "passed"
    execution = manifest["execution"]
    if (
        execution["python_version"] != platform.python_version()
        or execution["sqlite_version"] != sqlite3.sqlite_version
        or execution["platform"] != platform.platform()
    ):
        raise PreflightError("execution runtime versions do not match manifest")
    report.checks["runtime_versions"] = "passed"
    if verify_git:
        _verify_git(repo_root, manifest)
        report.checks["harness_git"] = "passed"

    benchmark_file = benchmark_dir / manifest["benchmark"]["files"]["tier_a_screener"]["path"]
    if sha256_file(benchmark_file) != manifest["benchmark"]["files"]["tier_a_screener"]["sha256"]:
        raise PreflightError("tier-a benchmark hash mismatch")
    records = load_benchmark(benchmark_file)
    report.checks["benchmark_hash_and_schema"] = "passed"

    canonical_db_tickers: set[str] = set()
    for name, path in (("baseline", baseline_db), ("candidate", candidate_db)):
        declared = manifest["run_sets"][name]["database"]
        if sha256_file(path) != declared["sha256"]:
            raise PreflightError(f"{name} database hash mismatch")
        if manifest["evaluation_mode"] == "authoritative" and path.stat().st_size != SNAPSHOT_BYTES[name]:
            raise PreflightError(f"authoritative {name} database byte size mismatch")
        with ImmutableDatabase(path, declared["profile"]) as db:
            db.validate_schema()
            tickers = [canonical_ticker(row["ticker"]) for row in db.active_companies()]
            if len(tickers) != len(set(tickers)):
                raise PreflightError(f"{name} database has ambiguous canonical identifiers")
            canonical_db_tickers.update(tickers)
            _validate_db_records(db, name, report)
        report.checks[f"{name}_database"] = "passed"
    benchmark_tickers = {record["ticker"] for record in records}
    if benchmark_tickers - canonical_db_tickers:
        raise PreflightError("benchmark contains identifiers not mapped to either database profile")
    report.checks["canonical_identifiers"] = "passed"
    report.checks["tier_b_judging"] = "not_run"
    return report, records
