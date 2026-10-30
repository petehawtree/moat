# Evaluation harness v1

This is the mechanical implementation of the frozen contract in
`eval-spec-v1.md`. It is intentionally separate from the production pipeline
and opens both SQLite snapshots with `mode=ro&immutable=1` plus
`PRAGMA query_only=ON`.

The harness implements preflight, Tier A, valuation, committee, Tier C,
baseline/candidate movement, optional committee noise-floor comparison,
optional deterministic per-fix replay, persona influence, private atomic
records, and a disclosure-safe aggregate JSON report. It does **not** run Tier
B judging or an authoritative evaluation.

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
  --public-output docs/evals/generated-eval-v1-development.json
```

The unchanged-input committee replicate is optional because it does not yet
exist. When it does, bind its run ID and runtime-provenance evidence into the
manifest with `--noise-floor-run-id` and
`--noise-floor-runtime-provenance`, then pass the run ID explicitly to the run
command. If the run is absent, its cohort differs, any row is cache-reused, its
input lineage or cache bundle differs, or runtime equivalence is not evidenced,
§12.1 is reported as unavailable and committee movement remains descriptive.

Per-fix replay is also optional. `--per-fix-replay-input` binds a private JSONL
file by hash. The file supplies isolated screen metric-status states or DCF
scenario states before and after one fix, supporting evidence, and an explicit
attestation that all other inputs were held constant. Without those inputs the
harness emits no per-fix causal result.

Use `--preflight-only` to validate hashes, profiles, run stages, lineage,
categories, and canonical joins without scoring. Creating a manifest hashes
inputs but does not inspect or score benchmark rows. Public output is built
from an explicit aggregate allowlist; benchmark-derived cells below five are
suppressed. Exceptions and logs contain rule names and counts only.

Material choices not fixed by the specification are recorded in
`eval-harness-v1-implementation-choices.md` rather than being hidden in code.

An authoritative run is deliberately not implied by the command. Its manifest
must say `authoritative`, bind a clean committed harness, and pass all frozen
preflight checks.
