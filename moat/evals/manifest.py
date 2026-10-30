"""Strict loading and creation of the v1 execution manifest."""

from __future__ import annotations

import json
import platform
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .constants import (
    DEFAULT_BOOTSTRAP_REPLICATES,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_SAMPLING_SEED,
    FROZEN_SPEC_SHA256,
    MANIFEST_VERSION,
    PROFILE_NAMES,
    RUN_IDS,
    SNAPSHOT_SHA256,
    SPEC_RELATIVE_PATH,
    SPEC_VERSION,
)
from .errors import ManifestError
from .util import sha256_file


ROOT_KEYS = {
    "manifest_version", "evaluation_mode", "eval_spec", "harness", "run_sets",
    "benchmark", "judging", "seeds", "bootstrap_replicates", "execution",
}


def _require_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"manifest field {label} must be an object")
    return value


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    missing, unknown = expected - set(value), set(value) - expected
    if missing or unknown:
        raise ManifestError(
            f"manifest field {label} has {len(missing)} missing and {len(unknown)} unknown key(s)"
        )


def _is_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("execution manifest is unreadable or invalid JSON") from exc
    validate_manifest(data)
    return data


def validate_manifest(data: dict) -> None:
    data = _require_object(data, "root")
    _exact_keys(data, ROOT_KEYS, "root")
    if data["manifest_version"] != MANIFEST_VERSION:
        raise ManifestError("unsupported execution manifest version")
    if data["evaluation_mode"] not in {"development", "authoritative"}:
        raise ManifestError("evaluation_mode must be development or authoritative")

    spec = _require_object(data["eval_spec"], "eval_spec")
    _exact_keys(spec, {"version", "path", "sha256", "status"}, "eval_spec")
    if (spec["version"], spec["path"], spec["sha256"], spec["status"]) != (
        SPEC_VERSION, SPEC_RELATIVE_PATH, FROZEN_SPEC_SHA256, "FROZEN"
    ):
        raise ManifestError("manifest does not bind the frozen v1 specification")

    harness = _require_object(data["harness"], "harness")
    _exact_keys(harness, {"git_commit", "git_dirty"}, "harness")
    if not isinstance(harness["git_commit"], str) or not harness["git_commit"]:
        raise ManifestError("harness.git_commit must be non-empty")
    if not isinstance(harness["git_dirty"], bool):
        raise ManifestError("harness.git_dirty must be boolean")

    run_sets = _require_object(data["run_sets"], "run_sets")
    _exact_keys(run_sets, {"baseline", "candidate"}, "run_sets")
    for name in ("baseline", "candidate"):
        run_set = _require_object(run_sets[name], f"run_sets.{name}")
        _exact_keys(run_set, {"database", "runs", "production_provenance"}, f"run_sets.{name}")
        database = _require_object(run_set["database"], f"run_sets.{name}.database")
        _exact_keys(database, {"path", "sha256", "profile"}, f"run_sets.{name}.database")
        if not isinstance(database["path"], str) or not _is_sha256(database["sha256"]):
            raise ManifestError(f"run_sets.{name}.database path/hash is invalid")
        if database["profile"] != PROFILE_NAMES[name]:
            raise ManifestError(f"run_sets.{name}.database has the wrong capability profile")
        runs = _require_object(run_set["runs"], f"run_sets.{name}.runs")
        _exact_keys(runs, {"screen", "valuation", "committee"}, f"run_sets.{name}.runs")
        if runs != RUN_IDS[name]:
            raise ManifestError(f"run_sets.{name}.runs does not match frozen v1")
        provenance = _require_object(run_set["production_provenance"], f"run_sets.{name}.production_provenance")
        _exact_keys(provenance, {"screen", "valuation", "committee"}, f"run_sets.{name}.production_provenance")
        for stage, record in provenance.items():
            record = _require_object(record, f"run_sets.{name}.production_provenance.{stage}")
            _exact_keys(
                record,
                {"git_commit", "status", "evidence", "python_version", "dependencies_sha256", "driver_sha256s"},
                f"run_sets.{name}.production_provenance.{stage}",
            )
            if record["status"] not in {"verified", "reconstructed_unverified"}:
                raise ManifestError("production provenance status is unknown")
            if not all(isinstance(record[k], str) and record[k] for k in ("git_commit", "evidence")):
                raise ManifestError("production provenance commit/evidence must be non-empty")
            if record["dependencies_sha256"] is not None and not _is_sha256(record["dependencies_sha256"]):
                raise ManifestError("production provenance contains an invalid dependency SHA-256")
            if not isinstance(record["driver_sha256s"], list) or any(not _is_sha256(value) for value in record["driver_sha256s"]):
                raise ManifestError("production provenance contains an invalid driver SHA-256 list")

    benchmark = _require_object(data["benchmark"], "benchmark")
    _exact_keys(benchmark, {"directory", "files", "version"}, "benchmark")
    if benchmark["version"] != "morningstar-benchmark-v1":
        raise ManifestError("benchmark version is not frozen v1")
    files = _require_object(benchmark["files"], "benchmark.files")
    _exact_keys(files, {"tier_a_screener"}, "benchmark.files")
    tier_a = _require_object(files["tier_a_screener"], "benchmark.files.tier_a_screener")
    _exact_keys(tier_a, {"path", "sha256"}, "benchmark.files.tier_a_screener")
    if not isinstance(tier_a["path"], str) or not _is_sha256(tier_a["sha256"]):
        raise ManifestError("tier-a benchmark path/hash is invalid")

    judging = _require_object(data["judging"], "judging")
    _exact_keys(judging, {"status", "rubric_sha256", "model", "prompt_sha256", "cohort_sha256"}, "judging")
    if judging["status"] != "not_run":
        raise ManifestError("this harness phase requires judging.status=not_run")
    if any(judging[k] is not None for k in ("rubric_sha256", "model", "prompt_sha256", "cohort_sha256")):
        raise ManifestError("Tier B judge fields must be null while judging is not run")

    seeds = _require_object(data["seeds"], "seeds")
    _exact_keys(seeds, {"bootstrap", "sampling"}, "seeds")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in seeds.values()):
        raise ManifestError("manifest seeds must be integers")
    if data["bootstrap_replicates"] != DEFAULT_BOOTSTRAP_REPLICATES:
        raise ManifestError("v1 bootstrap replicate count must be 10000")

    execution = _require_object(data["execution"], "execution")
    _exact_keys(execution, {"timestamp", "python_version", "sqlite_version", "platform"}, "execution")
    if not all(isinstance(v, str) and v for v in execution.values()):
        raise ManifestError("execution metadata must be non-empty strings")
    try:
        datetime.fromisoformat(execution["timestamp"])
    except ValueError as exc:
        raise ManifestError("execution timestamp must be ISO-8601") from exc
    if data["seeds"]["bootstrap"] != DEFAULT_BOOTSTRAP_SEED:
        raise ManifestError("v1 bootstrap seed must be 61001")
    if data["evaluation_mode"] == "authoritative" and harness["git_dirty"]:
        raise ManifestError("authoritative execution requires a clean harness commit")
    if data["evaluation_mode"] == "authoritative":
        for name in ("baseline", "candidate"):
            if data["run_sets"][name]["database"]["sha256"] != SNAPSHOT_SHA256[name]:
                raise ManifestError("authoritative execution must bind the frozen database digests")


def _git_state(repo_root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManifestError("unable to record harness Git state") from exc
    return commit, dirty


def build_manifest(
    *, repo_root: Path, baseline_db: Path, candidate_db: Path, benchmark_dir: Path,
    mode: str = "development", bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    sampling_seed: int = DEFAULT_SAMPLING_SEED,
) -> dict:
    """Build a pre-scoring manifest for the fixed v1 run sets.

    This hashes inputs only; it never opens databases or scores benchmark rows.
    """
    tier_a = benchmark_dir / "tier-a-screener.jsonl"
    for path in (baseline_db, candidate_db, tier_a):
        if not path.is_file():
            raise ManifestError("one or more manifest inputs do not exist")
    commit, dirty = _git_state(repo_root)
    baseline_provenance = {
        "screen": _provenance("252348cfa880833851671c02a8674e34ce58035c", "reconstructed_unverified", "timestamp reconstruction; see sprint-6.0-run-provenance.md"),
        "valuation": _provenance("252348cfa880833851671c02a8674e34ce58035c", "reconstructed_unverified", "timestamp reconstruction; see sprint-6.0-run-provenance.md"),
        "committee": _provenance("7dae6d6e32c58fd9f3cc6b3e7d917873171941e1", "reconstructed_unverified", "timestamp reconstruction; see sprint-6.0-run-provenance.md"),
    }
    py39_dependencies = sha256_file(repo_root / "docs/evals/sprint-6.0-env-py39.txt")
    py311_dependencies = sha256_file(repo_root / "docs/evals/sprint-6.0-env-py311.txt")
    candidate_provenance = {
        "screen": _provenance("a71b1e4eaa5f12c4593699594167cbd88fd88f8b", "verified", "frozen run provenance", "3.9.6", "829be8f0a07497fffbedd980b1949cd4a9b50e55c119fec578ee263479d1394d", py39_dependencies),
        "valuation": _provenance("a71b1e4eaa5f12c4593699594167cbd88fd88f8b", "verified", "frozen run provenance", "3.9.6", "6d12c214f28a2c53bd23cc46e24cd8bfde29465e6addbf08ee94fb58ca5e609b", py39_dependencies),
        "committee": _provenance(
            "79450dd9297b46fee00f8e5ac3681f97bb9dc690",
            "verified",
            "frozen run provenance; final run used forward and reverse workers",
            "3.11.16",
            [
                "fa04ca2274efcbef71ce80905828dd10ce5045465289e24dbceb8aa83d91bef3",
                "6912d3b292ad5aa0e1183c02c0cd1223366855a5ea82e1dd4368b462833eec7d",
            ],
            py311_dependencies,
        ),
    }
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "evaluation_mode": mode,
        "eval_spec": {"version": SPEC_VERSION, "path": SPEC_RELATIVE_PATH, "sha256": FROZEN_SPEC_SHA256, "status": "FROZEN"},
        "harness": {"git_commit": commit, "git_dirty": dirty},
        "run_sets": {
            "baseline": {"database": {"path": str(baseline_db.resolve()), "sha256": sha256_file(baseline_db), "profile": PROFILE_NAMES["baseline"]}, "runs": RUN_IDS["baseline"], "production_provenance": baseline_provenance},
            "candidate": {"database": {"path": str(candidate_db.resolve()), "sha256": sha256_file(candidate_db), "profile": PROFILE_NAMES["candidate"]}, "runs": RUN_IDS["candidate"], "production_provenance": candidate_provenance},
        },
        "benchmark": {"directory": str(benchmark_dir.resolve()), "version": "morningstar-benchmark-v1", "files": {"tier_a_screener": {"path": "tier-a-screener.jsonl", "sha256": sha256_file(tier_a)}}},
        "judging": {"status": "not_run", "rubric_sha256": None, "model": None, "prompt_sha256": None, "cohort_sha256": None},
        "seeds": {"bootstrap": bootstrap_seed, "sampling": sampling_seed},
        "bootstrap_replicates": DEFAULT_BOOTSTRAP_REPLICATES,
        "execution": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "platform": platform.platform(),
        },
    }
    validate_manifest(manifest)
    return manifest


def _provenance(
    commit: str,
    status: str,
    evidence: str,
    python_version: str = "unverified",
    driver_sha256s: str | list[str] | None = None,
    dependencies_sha256: str | None = None,
) -> dict:
    return {
        "git_commit": commit,
        "status": status,
        "evidence": evidence,
        "python_version": python_version,
        "dependencies_sha256": dependencies_sha256,
        "driver_sha256s": (
            [] if driver_sha256s is None
            else [driver_sha256s] if isinstance(driver_sha256s, str)
            else driver_sha256s
        ),
    }
