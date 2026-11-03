# Project Moat eval-spec-v1 amendment 1

**Status:** FROZEN (approved by Pete, 2026-11-03)

**Amendment owner:** Pete

**Base specification:** `docs/evals/eval-spec-v1.md`

**Base specification SHA-256:** `76ad487b10f1e114ff4ccd01247a110821cb495de43ed618f9b32f4a5a1163d6`

**Purpose:** Resolve the three open decisions for the committee noise floor and quantitative-only external support

This is an append-only amendment to the frozen v1 specification. The base
document and its checksum remain unchanged. This amendment controls wherever
it is more specific than, or conflicts with, the base specification. All other
v1 rules remain in force.

An execution manifest produced after this amendment MUST bind both the base
specification and this amendment by version, path, SHA-256 and `FROZEN` status.
The amendment digest is recorded in
`docs/evals/eval-spec-v1-amendment-1.md.sha256`.

## A1. Separate committee-replicate snapshot

The unchanged-input committee replicate required by §12.1 MUST live in a
separate SQLite snapshot. It cannot be added to the frozen candidate snapshot.

The manifest's `experiments.noise_floor` object is:

```json
{
  "run_id": "<distinct committee run id or null>",
  "database": {
    "path": "/absolute/private/path/replicate.db",
    "sha256": "<64 lowercase hexadecimal characters>",
    "profile": "candidate-v1"
  },
  "runtime_provenance": "<non-empty evidence or null>",
  "input_equivalence": {
    "method": "committee_bundle_v1",
    "reference_candidate_sha256": "61b9514a068b855998377f7262f55386783c2339f659370f8baea980bb7c3828",
    "quality_run_id": "20260929T135425Z",
    "valuation_run_id": "20260929T135455Z"
  }
}
```

When no equivalent replicate is available, `run_id`, `database`,
`runtime_provenance` and `input_equivalence` are all null, and the noise floor
is reported as unavailable. Partial population of the object fails manifest
validation.

When populated, preflight MUST establish all of the following:

1. The replicate snapshot is distinct from the baseline and candidate files,
   is sealed read-only, has no WAL/SHM sidecars, is opened with
   `mode=ro&immutable=1`, matches its manifest hash, and exactly matches the
   `candidate-v1` capability profile. The live `data/moat.db` is forbidden.
2. The replicate run ID differs from every frozen run ID. The run exists in
   the replicate snapshot, reached the committee stage, and has status
   `complete` or `partial`.
3. The replicate cohort exactly equals the stored candidate committee cohort.
   Every replicate verdict bypassed cache reuse.
4. For every company in that cohort, the harness independently recomputes the
   `committee_bundle_v1` digest in the frozen candidate snapshot and in the
   replicate snapshot using the frozen quality and valuation run IDs. Both
   digests must be available, equal across snapshots, and equal the cache key
   stored on both verdict rows.
5. Bundle equivalence covers the committee inputs represented by the existing
   candidate bundle: quality rows, quant rows, valuation rows, current
   AI-analysis cache keys, fundamentals-confidence roll-up, company name and
   sector, model identifier and protocol version.
6. Equivalent model/runtime configuration is evidenced separately by
   `runtime_provenance`. Bundle equality alone does not prove runtime
   equivalence.

Any failed requirement makes the noise-floor experiment unavailable. The
harness MUST NOT weaken the comparison or fall back to matching only selected
inputs.

## A2. Overall-score practical noise boundary

The unchanged-input replicate produces one absolute overall-score delta per
preflight-valid paired company:

```text
abs(replicate overall score - stored candidate overall score)
```

The practical noise boundary is the empirical 95th percentile of those
absolute deltas. The harness uses the nearest-rank definition: sort the values
ascending and select rank `ceil(0.95 * n)`, using one-based ranks. The boundary,
cohort size and calculation rule are recorded in the private and public
aggregate reports.

For each company in the paired baseline/candidate committee cohort:

- if its absolute baseline/candidate overall-score delta is at or below the
  boundary, the movement is labelled **indistinguishable from one-run model
  variation**;
- if it is above the boundary, the movement is labelled **exceeds the observed
  practical noise boundary**; and
- if the noise floor is unavailable, no noise label is emitted and committee
  movement remains descriptive.

Only overall-score movement receives a company-level noise label. Component
score deltas remain diagnostics and MUST NOT receive company-level
noise/noise-exceedance labels.

Status flips and Bear-severity changes are compared only in aggregate. The
report shows the baseline/candidate counts and rates beside the unchanged-input
replicate counts and rates. It does not label an individual company's status
or severity change as noise, and it does not infer a company-level conclusion
from a component diagnostic.

One replicate still estimates only a practical cross-company noise boundary;
it does not fully characterise the model's sampling distribution. The report
retains that limitation.

## A3. Quantitative-only committee external support

Morningstar's quantitative-only coverage uses a different uncertainty-band
formula from analyst coverage. Until that formula is separately validated,
quantitative-only companies MUST have `external_support = abstain` for §8
committee external-support scoring.

They remain included in:

- eligible screen-passer counts;
- committee scored/abstention and coverage counts; and
- other analyses that do not use the §8 external-support categories.

They are excluded from the denominators of support composition, support
enrichment and external-support trend metrics because their external-support
classification abstains. They MUST NOT be pooled with analyst-covered
companies as strong, weak, ambiguous or contradicted support.

This rule does not change Tier A. It also does not change the separately named,
secondary direct committee-status versus star-rating analysis, which is not an
external-support metric.

## A4. Approval and versioning

These decisions were approved before an authoritative evaluation or Tier B
judging was run:

- separate sealed replicate snapshot with cross-snapshot input-equivalence
  proof: **approved**;
- company-level score-noise boundary: **95th percentile, overall score only**;
- component changes: **diagnostic only**;
- status and Bear-severity changes: **aggregate comparison only**; and
- quantitative-only committee external support: **Abstain while retaining
  coverage accounting**.

**Owner approval:** Pete, 2026-11-03

**Frozen at:** 2026-11-03

**Frozen amendment SHA-256:** recorded in
`docs/evals/eval-spec-v1-amendment-1.md.sha256`, computed over this file exactly
as committed.
