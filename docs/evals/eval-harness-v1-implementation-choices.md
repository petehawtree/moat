# Eval harness v1 — implementation choices and unresolved decisions

This ledger records material behavior that `eval-spec-v1.md` does not fully
determine. It is implementation disclosure, not an amendment to the frozen
specification.

## Cross-cutting choices

- The report format is JSON. Detailed company records are separate atomic
  JSONL files in the private output directory.
- Public disclosure suppresses each benchmark-derived count below five. A rate
  is hidden when its numerator, denominator or complement (denominator minus
  numerator) is below five.
- Manifest objects and normalized input records reject unknown keys and
  categories. Development manifests may bind synthetic inputs; authoritative
  manifests must bind the two frozen snapshot digests and sizes.
- A runtime match means exact Python, SQLite, and platform strings. The harness
  worktree must be clean for an authoritative manifest.
- Ticker syntax outside the explicit BRK/BF aliases is accepted only when it
  matches the versioned uppercase ticker grammar.

## Tier A choices retained from the first harness review

- Run-derived strata use the pre-existing capture rule: pass; otherwise fewer
  than six assessed metrics is definitional for Financials/Real Estate and a
  data gap elsewhere; otherwise score at least 33 is boundary fail and the
  remainder is clear fail.
- Moat strength is ordered None=0, Narrow=1, Wide=2 and associated with score
  using Spearman's rho with average ranks.
- Recall is conditional on companies with a screen result. Abstentions are
  disclosed separately and are not converted to false negatives.
- Bootstrap intervals use the linear 2.5th/97.5th percentiles. A metric gets no
  interval below its own denominator of 20 or with one observed outcome class.

## Valuation choices (§7)

- The captured five-star and one-star prices are the uncertainty-adjusted
  lower and upper boundaries; the harness does not reconstruct boundaries from
  a generic uncertainty multiplier.
- Equality with the lower boundary is Cheap. Equality with the upper benchmark
  boundary is Fair because the spec says Expensive *exceeds* that boundary.
- Analyst-covered companies are primary and quantitative coverage is
  secondary, but both use their captured row-specific band boundaries.
- The valuation coverage denominator is every benchmark record in the cohort,
  not only screen passers. A company with no valuation rows therefore Abstains.
- Scenario point equality uses `math.isclose(rel_tol=1e-9, abs_tol=1e-9)`.
  Bear/base/bull ordering must be strict and all three values must be positive.
- All three DCF rows must carry one consistent as-run price. Baseline price
  dates are reconstructed only from a unique matching close and remain
  `reconstructed_unverified`; candidate dates must have one persisted value.
- Base margin of safety is `(base intrinsic value - price) / base intrinsic
  value`. The secondary point-fair-value diagnostic compares price with the
  base DCF value.
- Exact categorical agreement, false-Cheap rate, and Spearman ordinal
  association are reported in addition to the complete table. These summary
  metrics are descriptive; the spec does not name a primary valuation accuracy
  scalar.

## Committee choices (§8)

- When contradiction and support rules could both be read, contradiction takes
  precedence: None moat or Expensive valuation is contradicted.
- For enrichment and trend only, support is ordered contradicted=0,
  ambiguous=1, weak=2, strong=3. “Supported” means strong or weak.
- Committee coverage uses run-specific screen passers as its denominator. A
  missing verdict, a row-level preflight failure, or a verdict without a usable
  overall score Abstains.
- Reject/Watch/Investigate are ordered 0/1/2 for exploratory association.
  Direct star-rating association accepts numeric, textual numeric, and star
  glyph forms and remains secondary.
- Amendment 1 makes every quantitative-only external-support classification
  `abstain`. The abstention is counted separately and excluded from support
  composition, enrichment, contradiction-rate and trend denominators. It does
  not change committee workflow coverage, Tier A or the secondary direct
  status-versus-star-rating association.

## Noise floor and replay choices (§12 and Amendment 1)

- The replicate database path and run ID are explicit manifest and CLI inputs.
  The manifest uses the strict `experiments.noise_floor` object from Amendment
  1; all experiment fields are either populated together or null together.
- Replicate file integrity and sealing failures fail preflight. Experiment
  validity failures after the snapshot is safely opened make the experiment
  unavailable. Neither path emits company identifiers or proprietary values.
- The replicate run must be in the separate snapshot, have exactly the stored
  candidate cohort, reach committee, contain no reused verdict, and preserve
  candidate quality/valuation lineage.
- `committee_bundle_v1` is independently reconstructed in both snapshots for
  every stored cohort member. Both digests must be non-null, equal each other
  and equal the cache key on the corresponding verdict. No selected-input
  fallback is implemented.
- Runtime equivalence is not persisted by the committee schema, so a non-empty
  manifest runtime-provenance statement is also required. Without it the
  experiment is unavailable rather than silently assumed equivalent.
- The input-equivalence reference digest, the manifest's candidate snapshot
  digest and the digest of the candidate file actually opened must all match.
  This enforces Amendment 1 A1 and A4's proof boundary, as approved by Pete on
  2026-11-03. A development run on a non-frozen candidate therefore reports
  the noise-floor experiment as unavailable rather than claiming equivalence
  to the frozen candidate snapshot.
- Noise distributions report count, minimum, median, mean and maximum for each
  component and for signed and absolute overall-score movement. The practical
  boundary is the Amendment 1 nearest-rank 95th percentile of absolute overall
  deltas.
- The two frozen company-level meanings are applied only to paired
  baseline/candidate overall-score movement. Component deltas have no noise
  label. Individual status and Bear-severity changes are not emitted by the
  paired comparison; only aggregate counts, rates and transition tables are
  retained, with baseline/candidate and candidate/replicate rates shown side
  by side.
- One replicate is explicitly not treated as a full sampling distribution.
- Per-fix replay is never inferred from baseline/candidate differences. An
  optional private, hash-bound JSONL input must provide isolated before and
  counterfactual states, evidence, and `other_inputs_held_constant=true`.
- Screen replay uses the frozen production roll-up: pass/fail are assessable,
  threshold 50, and at least six assessed metrics. Valuation replay uses the
  same three-scenario classifier as §7.

## Persona choices (§14)

- “Status changes under removal” includes a zero-contribution removal in
  addition to the required typical-output, midpoint and renormalized-removal
  counterfactuals. Risk's zero contribution is represented by raw risk=100.
- A persona's typical replacement substitutes all of that persona's numerical
  components simultaneously with component medians from all valid verdicts in
  the evaluated run.
- Renormalized Quality or Valuation removal leaves Bear's categorical cap in
  force. Bear numerical removal also leaves the cap in force; the separate cap
  counterfactual holds the stored numerical score fixed. Full Bear removal
  both renormalizes away risk and disables the cap and is secondary.
- Quality external alignment uses its 55%-weighted component index versus
  economic moat, with business-quality and competitive-moat components also
  shown separately; management score uses Capital Allocation. Valuation uses
  its 40%-weighted index and valuation score versus analyst-only value class;
  financial strength uses Financial Health Grade. Bear uses raw risk versus
  uncertainty. Letter grades map F/D/C/B/A to 0/1/2/3/4 with +/- 0.33.
  Associations are Spearman correlations.

## Owner-decided open items — behavior deliberately unchanged

- Candidate stage provenance remains `verified`, even though environment
  freezes post-date the runs and 36 reused verdicts originated under the cap-9
  committee driver rather than the recorded final cap-5 file.
- Ratios are still scored when the latest fiscal year is very stale.
- Debt rows flagged as lease-inclusive fallback remain Inconclusive rather
  than Definitional difference.
- Structured D&A rows remain in the comparable-coverage denominator.
- Balance-sheet benchmark fields remain labelled TTM.
- Tier B has no frozen rubric, moat/capital-allocation atomic-unit rule, or
  frozen 24-ticker cohort hash. No Tier B judging is implemented or run.
