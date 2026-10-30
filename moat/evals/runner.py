"""Orchestration for the frozen mechanical Tier A/C phase."""

from __future__ import annotations

from pathlib import Path

from .database import ImmutableDatabase
from .manifest import load_manifest
from .preflight import run_preflight
from .reporting import write_private_outputs, write_public_report
from .tier_a import evaluate_tier_a, paired_tier_a
from .tier_c import evaluate_tier_c
from .util import atomic_write_json


def run_evaluation(
    *, repo_root: Path, baseline_db: Path, candidate_db: Path, benchmark_dir: Path,
    manifest_path: Path, private_output_dir: Path, public_output: Path,
    preflight_only: bool = False, verify_git: bool = True,
) -> dict:
    manifest = load_manifest(manifest_path)
    preflight, benchmark = run_preflight(
        repo_root=repo_root,
        manifest_path=manifest_path,
        manifest=manifest,
        baseline_db=baseline_db,
        candidate_db=candidate_db,
        benchmark_dir=benchmark_dir,
        private_output_dir=private_output_dir,
        public_output=public_output,
        verify_git=verify_git,
    )
    if preflight_only:
        atomic_write_json(private_output_dir / "preflight.json", preflight.private_dict())
        return {"status": "preflight_passed", "tier_b": "not_run", "authoritative": False}

    tier_a_records, tier_a_summaries, exclusions = {}, {}, {}
    tier_c_records, tier_c_summaries = {}, {}
    for name, path in (("baseline", baseline_db), ("candidate", candidate_db)):
        config = manifest["run_sets"][name]
        with ImmutableDatabase(path, config["database"]["profile"]) as db:
            a_records, a_summary, a_exclusions = evaluate_tier_a(
                db,
                run_id=config["runs"]["screen"],
                benchmark_records=benchmark,
                bootstrap_seed=manifest["seeds"]["bootstrap"],
                bootstrap_replicates=manifest["bootstrap_replicates"],
            )
            c_records, c_summary = evaluate_tier_c(db, benchmark_records=benchmark)
        tier_a_records[name], tier_a_summaries[name], exclusions[name] = a_records, a_summary, a_exclusions
        tier_c_records[name], tier_c_summaries[name] = c_records, c_summary
    paired = paired_tier_a(
        tier_a_records["baseline"],
        tier_a_records["candidate"],
        bootstrap_seed=manifest["seeds"]["bootstrap"],
        bootstrap_replicates=manifest["bootstrap_replicates"],
    )
    write_private_outputs(
        private_output_dir,
        manifest=manifest,
        preflight=preflight.private_dict(),
        tier_a_records=tier_a_records,
        tier_a_summaries=tier_a_summaries,
        exclusions=exclusions,
        tier_c_records=tier_c_records,
        tier_c_summaries=tier_c_summaries,
        paired=paired,
    )
    write_public_report(
        public_output,
        manifest=manifest,
        preflight_public=preflight.public_dict(),
        tier_a_summaries=tier_a_summaries,
        tier_c_summaries=tier_c_summaries,
        paired=paired,
    )
    return {
        "status": "mechanical_tier_a_c_complete",
        "tier_b": "not_run",
        "authoritative": manifest["evaluation_mode"] == "authoritative",
        "private_artifact_count": 12,
        "public_report_created": True,
    }
