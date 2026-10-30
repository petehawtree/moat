# Evaluation harness v1

This is the mechanical implementation of the frozen contract in
`eval-spec-v1.md`. It is intentionally separate from the production pipeline
and opens both SQLite snapshots with `mode=ro&immutable=1` plus
`PRAGMA query_only=ON`.

The current phase implements preflight, Tier A, Tier C, paired descriptive
screen movement, private atomic records, and a disclosure-safe aggregate JSON
report. It does **not** run Tier B judging, committee noise-floor calls, or an
authoritative evaluation.

Benchmark inputs, the execution manifest, and detailed outputs must stay
outside the repository:

```bash
python scripts/build_eval_manifest_v1.py \
  --baseline-db data/moat-baseline-pre-sprint6.0.db \
  --candidate-db data/moat-candidate-sprint6.0.db \
  --benchmark-dir /absolute/private/benchmark/v1 \
  --output /absolute/private/eval-runs/run-001/manifest.json

python scripts/run_eval_v1.py \
  --baseline-db data/moat-baseline-pre-sprint6.0.db \
  --candidate-db data/moat-candidate-sprint6.0.db \
  --benchmark-dir /absolute/private/benchmark/v1 \
  --manifest /absolute/private/eval-runs/run-001/manifest.json \
  --private-output-dir /absolute/private/eval-runs/run-001/details \
  --public-output docs/evals/generated-tier-a-c-development.json
```

Use `--preflight-only` to validate hashes, profiles, run stages, lineage,
categories, and canonical joins without scoring. Creating a manifest hashes
inputs but does not inspect or score benchmark rows. Public output is built
from an explicit aggregate allowlist; benchmark-derived cells below five are
suppressed. Exceptions and logs contain rule names and counts only.

An authoritative run is deliberately not implied by the command. Its manifest
must say `authoritative`, bind a clean committed harness, and pass all frozen
preflight checks.
