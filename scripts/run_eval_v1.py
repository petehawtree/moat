#!/usr/bin/env python3
"""Run frozen v1 preflight and the mechanical Tier A/C harness only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.evals.errors import EvalHarnessError
from moat.evals.runner import run_evaluation

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-db", required=True, type=Path)
    parser.add_argument("--candidate-db", required=True, type=Path)
    parser.add_argument("--benchmark-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--private-output-dir", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    try:
        result = run_evaluation(
            repo_root=REPO_ROOT,
            baseline_db=args.baseline_db,
            candidate_db=args.candidate_db,
            benchmark_dir=args.benchmark_dir,
            manifest_path=args.manifest,
            private_output_dir=args.private_output_dir,
            public_output=args.public_output,
            preflight_only=args.preflight_only,
        )
    except EvalHarnessError as exc:
        print(f"eval harness failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
