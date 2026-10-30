#!/usr/bin/env python3
"""Create the frozen v1 execution manifest before any scoring occurs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.evals.errors import EvalHarnessError
from moat.evals.manifest import build_manifest
from moat.evals.util import atomic_write_json, path_is_within

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-db", required=True, type=Path)
    parser.add_argument("--candidate-db", required=True, type=Path)
    parser.add_argument("--benchmark-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Private manifest path outside the repository")
    parser.add_argument("--mode", choices=("development", "authoritative"), default="development")
    parser.add_argument("--noise-floor-run-id", help="Optional unchanged-input committee replicate run ID")
    parser.add_argument("--noise-floor-runtime-provenance", help="Evidence that the replicate used the equivalent runtime environment")
    parser.add_argument("--per-fix-replay-input", type=Path, help="Optional private deterministic replay JSONL")
    args = parser.parse_args()
    try:
        if path_is_within(args.output, REPO_ROOT):
            raise EvalHarnessError("execution manifest must be written outside the repository")
        manifest = build_manifest(
            repo_root=REPO_ROOT,
            baseline_db=args.baseline_db,
            candidate_db=args.candidate_db,
            benchmark_dir=args.benchmark_dir,
            mode=args.mode,
            noise_floor_run_id=args.noise_floor_run_id,
            noise_floor_runtime_provenance=args.noise_floor_runtime_provenance,
            per_fix_replay_input=args.per_fix_replay_input,
        )
        atomic_write_json(args.output, manifest)
    except EvalHarnessError as exc:
        print(f"eval manifest failed: {exc}", file=sys.stderr)
        return 2
    print("execution manifest created; no benchmark rows were scored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
