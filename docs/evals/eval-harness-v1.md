# Evaluation harness v1

This is the mechanical implementation of the frozen contract in
`eval-spec-v1.md` plus frozen `eval-spec-v1-amendment-1.md`. It is intentionally separate from the production pipeline
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

The unchanged-input committee replicate is optional because the real sealed
replicate snapshot does not yet exist. When it does, bind its absolute path,
run ID, file hash, `candidate-v1` profile and runtime-provenance evidence into
the manifest with `--noise-floor-db`, `--noise-floor-run-id` and
`--noise-floor-runtime-provenance`. Pass the database and run ID explicitly to
the run command as well. The snapshot must be separate from both frozen
snapshots, sealed read-only and free of WAL/SHM sidecars.

For every company in the stored candidate committee cohort, preflight
independently reconstructs `committee_bundle_v1` from the frozen candidate
snapshot and the replicate snapshot. Both reconstructions must be available,
equal across snapshots and equal their respective verdict cache keys. The
replicate must contain the exact stored cohort and no cache-reused verdicts.
Replicate verdicts that fail committee output preflight abstain from the paired
noise calculation; they do not invalidate the cross-snapshot bundle proof.
The harness does not fall back to selected-input comparisons. A missing or
invalid experiment is unavailable and committee movement remains descriptive.

When the experiment is available, the harness sorts the preflight-valid
absolute stored-candidate/replicate overall-score deltas and selects one-based
rank `ceil(0.95 * n)`. Only paired baseline/candidate overall-score movement
receives the frozen at-or-below or above-boundary company-level label.
Component deltas remain diagnostic without labels. Status flips and Bear
severity changes are emitted only as aggregate baseline/candidate and
candidate/replicate counts and rates. The private and public aggregate reports
record the boundary, cohort size, nearest-rank method, counts under each frozen
overall-score meaning and the one-replicate limitation.

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
must say `authoritative`, bind both frozen specification documents, bind a clean
committed harness, and pass all frozen preflight checks.
