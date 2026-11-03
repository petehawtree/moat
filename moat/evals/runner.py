"""Orchestration for frozen v1 mechanical evaluation excluding Tier B."""

from __future__ import annotations

from pathlib import Path

from .committee_eval import evaluate_committee, paired_committee
from .database import ImmutableDatabase
from .errors import ManifestError
from .manifest import load_manifest
from .noise_floor import apply_noise_interpretation, evaluate_noise_floor
from .persona import evaluate_persona_influence
from .preflight import run_preflight
from .reporting import write_private_outputs, write_public_report
from .replay import evaluate_replays, load_replay_inputs
from .tier_a import evaluate_tier_a, paired_tier_a
from .tier_c import evaluate_tier_c
from .valuation import evaluate_valuation, paired_valuation
from .util import atomic_write_json


def run_evaluation(
    *, repo_root: Path, baseline_db: Path, candidate_db: Path, benchmark_dir: Path,
    manifest_path: Path, private_output_dir: Path, public_output: Path,
    preflight_only: bool = False, verify_git: bool = True,
    noise_floor_db: Path | None = None,
    noise_floor_run_id: str | None = None,
    per_fix_replay_input: Path | None = None,
) -> dict:
    manifest = load_manifest(manifest_path)
    noise_floor = manifest["experiments"]["noise_floor"]
    declared_noise = noise_floor["run_id"]
    if noise_floor_run_id != declared_noise:
        raise ManifestError("explicit noise-floor run ID does not match manifest")
    declared_noise_db = noise_floor["database"]["path"] if noise_floor["database"] else None
    supplied_noise_db = str(noise_floor_db.resolve()) if noise_floor_db else None
    if supplied_noise_db != declared_noise_db:
        raise ManifestError("explicit noise-floor database does not match manifest")
    declared_replay = manifest["experiments"]["per_fix_replay"]["path"]
    supplied_replay = str(per_fix_replay_input.resolve()) if per_fix_replay_input else None
    if supplied_replay != declared_replay:
        raise ManifestError("explicit per-fix replay input does not match manifest")
    preflight, benchmark = run_preflight(
        repo_root=repo_root,
        manifest_path=manifest_path,
        manifest=manifest,
        baseline_db=baseline_db,
        candidate_db=candidate_db,
        benchmark_dir=benchmark_dir,
        private_output_dir=private_output_dir,
        public_output=public_output,
        noise_floor_db=noise_floor_db,
        verify_git=verify_git,
    )
    if preflight_only:
        atomic_write_json(private_output_dir / "preflight.json", preflight.private_dict())
        return {"status": "preflight_passed", "tier_b": "not_run", "authoritative": False}

    tier_a_records, tier_a_summaries, exclusions = {}, {}, {}
    tier_c_records, tier_c_summaries = {}, {}
    valuation_records, valuation_summaries = {}, {}
    committee_records, committee_summaries = {}, {}
    persona_records, persona_summaries = {}, {}
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
            v_records, v_summary = evaluate_valuation(
                db,
                run_id=config["runs"]["valuation"],
                benchmark_records=benchmark,
            )
            committee_invalid = set(preflight.record_issues[name])
            committee_detail, committee_summary = evaluate_committee(
                db,
                committee_run_id=config["runs"]["committee"],
                screen_run_id=config["runs"]["screen"],
                benchmark_records=benchmark,
                invalid_tickers=committee_invalid,
            )
            persona_detail, persona_summary = evaluate_persona_influence(
                db,
                committee_run_id=config["runs"]["committee"],
                benchmark_records=benchmark,
                primary=name == "candidate",
                invalid_tickers=committee_invalid,
            )
        tier_a_records[name], tier_a_summaries[name], exclusions[name] = a_records, a_summary, a_exclusions
        tier_c_records[name], tier_c_summaries[name] = c_records, c_summary
        valuation_records[name], valuation_summaries[name] = v_records, v_summary
        committee_records[name], committee_summaries[name] = committee_detail, committee_summary
        persona_records[name], persona_summaries[name] = persona_detail, persona_summary
    paired = paired_tier_a(
        tier_a_records["baseline"],
        tier_a_records["candidate"],
        bootstrap_seed=manifest["seeds"]["bootstrap"],
        bootstrap_replicates=manifest["bootstrap_replicates"],
    )
    valuation_paired = paired_valuation(valuation_records["baseline"], valuation_records["candidate"])
    committee_paired = paired_committee(committee_records["baseline"], committee_records["candidate"])
    candidate_config = manifest["run_sets"]["candidate"]
    with ImmutableDatabase(candidate_db, candidate_config["database"]["profile"]) as candidate_open:
        if noise_floor_db is None:
            noise_records, noise_summary = evaluate_noise_floor(
                candidate_open,
                None,
                stored_run_id=candidate_config["runs"]["committee"],
                replicate_run_id=None,
                candidate_quality_run_id=candidate_config["runs"]["screen"],
                candidate_valuation_run_id=candidate_config["runs"]["valuation"],
                runtime_provenance=None,
                invalid_tickers=set(preflight.record_issues["candidate"]),
            )
        else:
            with ImmutableDatabase(noise_floor_db, noise_floor["database"]["profile"]) as replicate_open:
                noise_records, noise_summary = evaluate_noise_floor(
                    candidate_open,
                    replicate_open,
                    stored_run_id=candidate_config["runs"]["committee"],
                    replicate_run_id=noise_floor_run_id,
                    candidate_quality_run_id=candidate_config["runs"]["screen"],
                    candidate_valuation_run_id=candidate_config["runs"]["valuation"],
                    runtime_provenance=noise_floor["runtime_provenance"],
                    invalid_tickers=set(preflight.record_issues["candidate"]),
                )
    committee_paired, noise_summary = apply_noise_interpretation(
        committee_paired, noise_summary
    )
    replay_inputs = load_replay_inputs(per_fix_replay_input) if per_fix_replay_input else None
    baseline_config = manifest["run_sets"]["baseline"]
    with ImmutableDatabase(
        baseline_db, baseline_config["database"]["profile"]
    ) as db:
        replay_records, replay_summary = evaluate_replays(
            replay_inputs,
            baseline_db=db,
            baseline_screen_run_id=baseline_config["runs"]["screen"],
            baseline_valuation_run_id=baseline_config["runs"]["valuation"],
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
        valuation_records=valuation_records,
        valuation_summaries=valuation_summaries,
        valuation_paired=valuation_paired,
        committee_records=committee_records,
        committee_summaries=committee_summaries,
        committee_paired=committee_paired,
        noise_records=noise_records,
        noise_summary=noise_summary,
        replay_records=replay_records,
        replay_summary=replay_summary,
        persona_records=persona_records,
        persona_summaries=persona_summaries,
    )
    write_public_report(
        public_output,
        manifest=manifest,
        preflight_public=preflight.public_dict(),
        tier_a_summaries=tier_a_summaries,
        tier_c_summaries=tier_c_summaries,
        paired=paired,
        valuation_summaries=valuation_summaries,
        valuation_paired=valuation_paired,
        committee_summaries=committee_summaries,
        committee_paired=committee_paired,
        noise_summary=noise_summary,
        replay_summary=replay_summary,
        persona_summaries=persona_summaries,
    )
    return {
        "status": "mechanical_eval_v1_without_tier_b_complete",
        "tier_b": "not_run",
        "authoritative": manifest["evaluation_mode"] == "authoritative",
        "private_artifact_count": 31,
        "public_report_created": True,
    }
