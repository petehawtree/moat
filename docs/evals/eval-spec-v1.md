# Project Moat external benchmark evaluation specification v1

**Status:** FROZEN (approved by Pete, 2026-10-06)  
**Specification owner:** Pete  
**Benchmark:** Morningstar benchmark v1, held outside the repository  
**Purpose:** Frozen scoring contract for Sprint 6 Phase 2

This document defines the evaluation before the candidate results are
inspected. It separates evaluation policy from implementation, makes missing
outputs visible, and prevents either the production-code author or the LLM
judge from changing the rules in response to results.

Once Pete explicitly approves this document, its status changes to `FROZEN`.
Substantive changes after that point require a new specification version or a
clearly labelled sensitivity analysis. Results produced while this document is
approval-pending are development results, not authoritative eval results.

## 1. Evaluation purpose

Project Moat's committee result combines two purposes:

1. investment attractiveness, based on business quality, financial evidence,
   risk and current valuation; and
2. research prioritisation—identifying companies worth additional independent
   analysis.

The statuses are not trade instructions:

- **Investigate:** a high-priority candidate for further independent research.
  It is not a recommendation to transact.
- **Watch:** not currently a top-priority candidate, but potentially worth
  further research, particularly when its continuous score ranks highly among
  other Watch companies.
- **Reject:** insufficiently promising under the current evidence and
  valuation; users are generally expected to deprioritise it.

The evaluation follows an intentionally conservative risk posture. A false
positive that promotes an unsuitable company to Investigate is more costly
than a missed opportunity. Uncertainty and unreliable data should abstain or
demote rather than promote.

Version 1 is descriptive. It establishes baseline measurements and an error
taxonomy; it does not impose a numerical release gate. Thresholds derived from
these results are calibration and must not be described as independent
validation.

## 2. Roles and separation of responsibilities

| Responsibility | Owner | Boundary |
|---|---|---|
| Core Moat implementation | Claude Code | May implement and explain the system under test; does not set favourable eval rules or exclusions. |
| Eval questions, targets, exclusions and tolerances | Pete | Owns this specification and approves every substantive version. |
| Mechanical Tier A/C harness | Codex acting as eval engineer | Implements this specification without changing its meaning. Uses synthetic test fixtures in the repository. |
| Harness integration review | Claude Code | May check schema, units, joins and provenance; does not redefine scoring policy. |
| Tier B rubric | Pete, assisted by Codex | Frozen before judge execution. |
| Tier B judgments | Two fresh, blinded Codex judge runs | Apply the frozen rubric only; cannot modify themes or scoring rules. |
| Authoritative execution | Pete or deterministic CI | Executes the frozen harness against immutable inputs. |
| Interpretation | Pete, assisted by Codex | Distinguishes description, association and causation. |
| Independent validation | Future human evaluator | Audits the frozen sample, unresolved judgments and a random consensus sample. |
| Production fixes arising from the eval | Claude Code | Implemented after findings are recorded, then assessed in a new run. |

An LLM may diagnose an execution failure, but it must not interactively remove
records, retry only unfavourable judgments, or change rules during the
authoritative run.

## 3. Fixed run sets

| Run set | Screen | Valuation | Committee |
|---|---|---|---|
| Baseline | `20260923T122501Z` | `20260923T122513Z` | `20260923T153351Z` |
| Candidate | `20260929T135425Z` | `20260929T135455Z` | `20260930T133528Z` |

Run IDs alone are not sufficient for reproducibility because annual
fundamentals are mutable and are not versioned by run ID. Each run set MUST be
bound to an immutable SQLite snapshot and its SHA-256 digest in the execution
manifest. The run sets are bound to these snapshots (both `chmod 444`, with no
`-wal`/`-shm` sidecars; hashes re-verified 2026-10-02):

| Run set | Snapshot | Bytes | SHA-256 |
|---|---|---:|---|
| Baseline | `data/moat-baseline-pre-sprint6.0.db` | 58,593,280 | `c9f7974e8732547ea3e734539bd382d559112d11a4fb2d492e767d85b42d65e9` |
| Candidate | `data/moat-candidate-sprint6.0.db` | 70,959,104 | `61b9514a068b855998377f7262f55386783c2339f659370f8baea980bb7c3828` |

The baseline file's header is still WAL mode. It was sealed by removing its
empty `-wal` and stale `-shm` and making it read-only, which preserves the hash
above. The harness MUST therefore open it with SQLite's `immutable=1` URI
parameter, because a plain read-only open of a WAL-mode file recreates
sidecars. The candidate is a rollback-journal file. Per-stage run provenance
and driver-script hashes are recorded in
`docs/evals/sprint-6.0-run-provenance.md`.

The execution manifest MUST also record:

- eval-spec version and file hash;
- harness Git commit;
- production-code Git commit associated with each run set;
- database paths and SHA-256 hashes;
- benchmark file hashes;
- screen, valuation and committee run IDs;
- judge rubric, model and prompt hashes;
- bootstrap and sampling seeds;
- execution timestamp and software/runtime versions.

Historical provenance is not upgraded from inference to fact. Each stage-level
provenance record has a status of `verified` or `reconstructed_unverified`.
Where a historical commit cannot be proved, the manifest records the best
reconstruction, its evidence and its uncertainty rather than failing the whole
evaluation or presenting the reconstruction as verified. Any uncommitted
driver used for a stage is preserved and identified by SHA-256; committing the
driver later does not retroactively make the historical execution clean. The
candidate manifest MUST record its stage-specific commit, Python version,
relevant dependency versions and driver-script hash because the stages were
executed across more than one environment. Future authoritative pipeline runs
created after this specification is frozen MUST have verified provenance.

The baseline and candidate database schemas are declared capability profiles,
not assumed identical. The baseline profile lacks
`valuations.price_date` and `fundamentals_annual.stockholders_equity`; the
candidate profile contains them. A declared missing historical column is not
an unknown-schema failure. The harness MUST use a version-specific adapter and
mark any derived substitute as reconstructed. Any schema shape outside the two
declared profiles fails preflight. A baseline price date, if reconstructed from
price history, remains `reconstructed_unverified` unless the match is unique
and independently evidenced.

The harness MUST open database snapshots read-only and verify that committee
rows point to the specified valuation and quality runs through their persisted
provenance.

Committee cache reuse has row-level rather than run-level validity. A reused
verdict remains eligible when its source pipeline run is `partial` or `failed`
provided that:

- the source verdict row exists;
- the reuse chain is complete and acyclic;
- the stored cache key matches the current row's cache key;
- the current verdict row points to the required valuation and quality runs;
  and
- the cache bundle can be recomputed from the declared inputs where the schema
  permits it.

The enclosing source run is not required to have completed: a run may have
persisted valid per-company results before a later usage limit or failure.
Missing source rows, key mismatches, provenance mismatches or cycles fail the
affected record's preflight and produce Abstain; they are not silently repaired.

## 4. Cohorts, identifiers and missing outputs

### 4.1 Canonical identifiers

The harness MUST canonicalise known share-class aliases before joining. At a
minimum this includes:

- `BRK.B` and `BRK-B`;
- `BF.B` and `BF-B`.

The alias table is versioned as part of the harness. Unmapped or ambiguous
identifiers fail preflight rather than being silently dropped.

### 4.2 Abstentions

A missing output at any pipeline stage is `Abstain`, never an inferred failure,
negative judgment or Reject.

- Screen abstentions remain in the full-universe coverage denominator.
- Valuation and committee abstentions are treated the same way.
- Conditional performance is reported over scored companies alongside the
  abstention count and coverage rate.
- The benchmark-label distribution among abstentions is reported privately and
  in disclosure-safe aggregate form publicly.
- Operationally, an abstaining company does not advance automatically, but
  this is a workflow consequence rather than a negative investment judgment.

### 4.3 Coverage tiers

- Tier A economic-moat evaluation includes analyst-covered and quantitatively
  rated benchmark companies.
- The primary uncertainty-adjusted valuation analysis includes analyst-covered
  companies only because quantitative coverage uses a different band formula.
- Quantitatively rated companies are reported as a separate secondary cohort.
- Tier B reasoning recall excludes quantitative-only companies from the recall
  denominator because no analyst narrative exists. They remain visible in
  cohort accounting as structurally unavailable.

### 4.4 Exclusions

Only structural reasons declared before scoring may exclude a record, such as
absent benchmark coverage or incompatible methodology. Outlier status,
disagreement or poor performance is never an exclusion reason.

Every exclusion MUST appear in an exclusion ledger with its rule identifier and
reason. Excluded records normally remain in coverage denominators. A new
exclusion discovered after results are visible requires a new spec version or
a labelled sensitivity analysis.

## 5. Comparison structure

Morningstar moat rating, valuation rating and Moat committee status measure
different constructs. The evaluation therefore uses three comparisons rather
than treating star rating as a direct committee gold label:

1. **Quality:** Moat screen and quality scores versus economic-moat strength.
2. **Valuation:** Moat valuation versus Morningstar's uncertainty-adjusted
   cheap/fair/expensive signal.
3. **Committee:** exploratory combined research-priority alignment using both
   quality and valuation evidence.

Direct committee-status versus star-rating agreement is secondary and
exploratory. There is no primary single-number committee accuracy metric.

## 6. Tier A: quantitative screen and moat strength

### 6.1 Target ordering

Morningstar's three categories remain separate:

1. `Wide`—primary positive target;
2. `Narrow`—weaker positive outcome;
3. `None`—clear negative target.

A screen passer rated Wide is the strongest success. A Narrow passer is a
weaker success. A None passer is the clearest screen false positive.

### 6.2 Primary outputs

The harness reports:

- the complete pass/fail/abstain × Wide/Narrow/None table;
- Wide precision among screen passers;
- Wide-or-Narrow precision among screen passers;
- None rate among screen passers;
- screen coverage and abstention rate;
- benchmark-label distribution among abstentions;
- Wide recall as a secondary metric;
- Wide-or-Narrow recall as a secondary metric;
- lift over the cohort's Wide base rate and Wide-or-Narrow base rate;
- score/rating ordinal association;
- moat-rating distribution across run-derived score/assessability strata.

The benchmark's stored capture stratum is capture metadata only. Run-specific
strata MUST be derived from each run's own outputs. A separate baseline-fixed
stratum analysis may be reported for paired interpretation but must be labelled
as such.

## 7. Valuation comparison

### 7.1 Benchmark target

The primary benchmark target is uncertainty-adjusted:

- `Cheap`: benchmark price meets the required discount for the recorded
  uncertainty band;
- `Fair`: price is between the lower and upper uncertainty-adjusted boundaries;
- `Expensive`: price exceeds the upper uncertainty-adjusted boundary.

Simple price-above/below-point-fair-value direction is a secondary diagnostic.

### 7.2 Moat classification

Moat's owner-earnings DCF is classified conservatively using its three scenario
values at the comparison price:

- `Cheap`: price is at or below bear-case intrinsic value;
- `Fair`: price lies strictly between bear- and bull-case intrinsic values;
- `Expensive`: price is at or above bull-case intrinsic value;
- `Abstain`: required scenarios are missing, nonpositive or not correctly
  ordered.

The base-case margin of safety is reported as a continuous diagnostic and does
not override the categorical rule.

For each `owner_earnings_dcf` bear/base/bull row, v1 uses
`intrinsic_value_low` as that scenario's point intrinsic value. Under the
current schema `intrinsic_value_high` is expected to equal
`intrinsic_value_low`; the harness asserts this equality within numeric
precision. A material within-row difference is an unknown valuation shape and
causes that company to Abstain rather than allowing the harness to choose a
column opportunistically.

### 7.3 Price views

Two non-interchangeable analyses are required:

1. **As-run operational:** uses each persisted run's price-dependent outputs,
   retaining the baseline stale-price defect. This evaluates the pipeline as it
   operated.
2. **Price-normalised:** recomputes both runs' valuation classification using
   the same frozen benchmark-date price. This evaluates intrinsic-value
   agreement while holding price timing constant.

Committee verdicts remain as-run unless an explicitly named counterfactual
committee experiment is conducted. Operational and price-normalised metrics
must never be merged.

## 8. Committee evaluation

### 8.1 External-support categories for Investigate

- `Wide + Cheap`: strongly supported Investigate.
- `Narrow + Cheap`: acceptable but weakly supported Investigate.
- `None` at any valuation: contradicted Investigate.
- `Expensive` at any moat rating: contradicted Investigate.
- Other combinations, including Wide + Fair, are ambiguous and are not forced
  into correct/incorrect.

### 8.2 Metrics

The committee report contains:

- Investigate contradiction rate;
- Investigate support composition: strong, weak, ambiguous and contradicted;
- research-priority enrichment from Reject to Watch to Investigate;
- external-support trend within Watch as continuous overall score rises;
- committee coverage among eligible screen passers;
- paired score and status movement among companies present in both runs.

The continuous overall score remains important within statuses, particularly
Watch. Version 1 does not claim that Morningstar supplies the one correct
committee outcome.

The stored runs contain fewer than five Investigate verdicts per run. Under
the public disclosure rule, Investigate support and contradiction cells are
therefore expected to remain private or be suppressed in the public report.
This is an expected privacy consequence, not missing harness output. The public
report states that the analysis ran and was suppressed; it does not infer or
hint at the hidden cell values.

## 9. Tier C: fundamentals extraction

### 9.1 Benchmark sources and expected scope

The structured v1 source is the TTM fundamentals embedded in the normalized
Tier A screener file, `tier-a-screener.jsonl`. There is no separate structured
fiscal-year `tier-c-fundamentals.jsonl` in benchmark v1. Tier B records may
contain heterogeneous `tier_c_crosschecks`; those are corroborative manual
evidence only and do not form a common structured accuracy denominator.

Because Moat stores fiscal-year annual fundamentals and deliberately has no
quarterly series from which to construct TTM values, Tier C v1 is expected to
produce primarily:

- ratio comparisons;
- presence/absence and coverage comparisons;
- sign and order-of-magnitude diagnostics; and
- Inconclusive absolute-value comparisons.

A small Match/Probable-defect denominator is a valid finding about benchmark
comparability, not a harness failure. The report MUST lead with the outcome
distribution and comparable coverage rather than presenting a thin accuracy
rate without context.

### 9.2 Outcome taxonomy

Each field comparison receives exactly one outcome:

1. `Match`—comparable definition and period, within tolerance.
2. `Definitional difference`—a documented accounting/method difference with
   supporting rationale.
3. `Probable defect`—comparable definition and period, materially inconsistent,
   with no supported legitimate explanation.
4. `Inconclusive`—period, basis, denominator or source ambiguity prevents a
   sound judgment.
5. `Unavailable`—one side has no usable value.

Only Match and Probable defect enter the extraction-accuracy denominator.
Every category is reported separately.

### 9.3 Matching tolerances

After unit, sign and currency normalisation:

- monetary values and share counts: at most 2% symmetric relative difference;
- margins, ROE and ROIC: at most 1 percentage point absolute difference;
- EPS: at most the greater of 2% symmetric relative difference or the source's
  apparent rounding precision;
- near-zero values or sign differences: use an absolute and sign-aware rule,
  never percentage error alone.

The implementation MUST record the exact formula and normalized units.
Thresholds absorb rounding only; they do not turn accounting-definition
differences into matches.

For nonzero values `x` and `y`, symmetric relative difference is
`2 * abs(x - y) / (abs(x) + abs(y))`. When both values are zero the difference
is zero. When the denominator is zero or either value is sufficiently near zero
that the ratio becomes unstable, the comparison uses the sign-aware absolute
rule and records that rule in the private result.

### 9.4 Period and definition rules

- A fiscal year ending within four months of a TTM snapshot is a sensitivity
  cohort, not automatically like-for-like. Such absolute comparisons normally
  remain Inconclusive unless an exact reconciliation is available.
- Ratios may be analysed separately where period sensitivity is demonstrably
  limited, but must retain the period caveat.
- FCF and debt require documented reconciliation. Without it they are
  Definitional difference, not Match.
- Debt treatment is row-specific because Moat may use flagged lease-inclusive
  fallback tags even though its preferred definition excludes leases.
- D&A issue #10 is indirect/manual evidence because the structured benchmark
  lacks a directly comparable D&A field.
- ROE is tested as the hypothesis that average-equity calculation improves
  like-for-like fiscal-year agreement; it is not assumed in advance to match
  every Morningstar surface.

## 10. Tier B: qualitative theme coverage

### 10.1 Scope and claim

The primary automated result is **benchmark-theme recall**, not general claim
correctness. Until human review is incorporated it is labelled provisional
machine-judged theme coverage.

The private Tier B directory contains 26 company records: 24 analyst-covered
records eligible for narrative recall and two quantitative-only records that
are structurally unavailable for narrative scoring. Before judge execution,
the exact 24-company ticker list is sorted, frozen privately and recorded by
SHA-256 in the execution manifest. A different cohort requires a new manifest
and is not silently substituted.

An unmatched Moat claim is counted and disclosed but does not enter a precision
denominator because absence from Morningstar does not prove the claim false.
Claim precision and factuality remain explicitly unvalidated. A claim that
directly contradicts an explicit benchmark assertion is reported separately.

### 10.2 Benchmark-section to Moat-output mapping

The following mapping is fixed before judging:

| Benchmark section | Primary Moat input | Secondary Moat input |
|---|---|---|
| Moat sources and moat reasoning | asserted `ai_analysis` claims where `analysis_type = 'moat'` | none |
| Capital allocation / management | asserted `ai_analysis` claims where `analysis_type = 'management'` | committee Quality persona text, reported separately |
| Key risks | asserted `ai_analysis` claims where `analysis_type = 'risk'` | none |
| Bear case | committee Bear persona statements for the evaluated committee run | none |
| Bull case / affirmative thesis | asserted `ai_analysis` claims where `analysis_type = 'business_quality'` | committee `investment_thesis`, reported separately |

Primary and secondary inputs are never pooled into one recall denominator.
Committee prose is split into deterministic statement units with IDs derived
from run ID, ticker, section, statement order and text hash. If a required
committee verdict is missing, its Bear and secondary thesis sections Abstain;
they are not borrowed from another run.

AI-analysis packets are judged once per unique content hash. Where the same
claims feed baseline and candidate runs, the one frozen judgment is shared by
both and contributes no artificial pre/post change. Committee-derived Bear and
secondary thesis packets are judged per available run because their text may
differ. Differences from fresh committee calls are treated as stochastic
output differences unless a controlled experiment supports a stronger claim.

### 10.3 Atomic, one-to-one matching

- Themes and claims are atomic units with stable IDs.
- Each Moat claim may satisfy at most one benchmark theme.
- Each benchmark theme may receive credit from at most one Moat claim.
- The scorer uses a maximum one-to-one assignment; repeated or broad claims do
  not earn duplicate credit.
- Sections such as moat, bear, risk and bull/thesis are scored separately.
- A theme repeated across sections may be counted once in a declared combined
  total but remains visible in the section-specific totals.

### 10.4 Two-judge protocol

Two fresh Codex judge runs operate in separate contexts. Inputs are independently
shuffled. Neither judge sees:

- the other judge's decision;
- baseline/candidate identity;
- expected fix direction;
- aggregate results;
- committee status; or
- notes identifying a desired answer.

The immutable input packet contains only the rubric version, section, stable
theme and claim IDs, independently written theme, claim text and allowed output
schema. The judge cannot create, merge or rewrite benchmark themes.

Allowed decisions are `Match`, `No match` and `Unclear`, with a short structured
reason code. Agreement produces provisional consensus. Any disagreement or any
Unclear is `Unresolved` and receives no forced primary label. The report gives
lower and upper recall bounds by treating unresolved cases as misses and hits,
respectively.

The private audit record preserves judge model, prompt hash, input hash,
decision, reason, confidence if requested by the rubric, retry metadata and
timestamp.

### 10.5 Unit of reporting

Company-level macro recall and counts are primary. Themes are clustered within
companies and must not be treated as independent observations. Tier B at this
cohort size is reported as counts and worked methodology, not a precise
population percentage.

## 11. Future human validation

The sample is frozen before automated judge results are inspected:

1. complete review of all Tier B decisions for eight preselected companies,
   stratified across mega-cap contamination, thin coverage, sector-template,
   commodity, explicit-contradiction and ordinary cases;
2. every two-judge Unresolved decision outside those companies; and
3. a blinded 10% random sample of consensus Match and No match decisions outside
   the eight-company set.

Sampling uses a recorded fixed seed and occurs before aggregate judge results
are read. The exact private sample list and its hash are stored with the run
manifest; proprietary benchmark answers are not placed in this repository.

The audit expands to another eight companies if either:

- the human disagrees with more than 10% of audited consensus decisions; or
- the human finds a systematic rubric defect, regardless of percentage.

Human decisions are appended rather than overwriting machine decisions. The
report distinguishes automated results, human-reviewed subset results,
machine/human agreement, human-corrected decisions where available, and
outstanding unresolved cases.

## 12. Baseline/candidate comparison and attribution

The primary pre/post estimate uses the paired common cohort: companies eligible
and scored in both runs. Separate outputs show:

- full baseline snapshot;
- full candidate snapshot;
- entrants;
- exits;
- abstentions and coverage movement.

Raw rates from changing cohorts must not be presented as behavioral change.

The headline change is the **combined Phase 1 effect**. An individual fix may be
named as causal only when deterministic row-level or counterfactual evidence
shows that no other changed input could explain the outcome. Fresh LLM outputs
normally support association, not single-fix causation.

### 12.1 Committee stochastic-noise floor

Paired committee movement is not interpreted without an unchanged-input noise
floor. Before attributing committee score or status movement, execute one fresh
committee replicate over the candidate cohort with:

- the exact candidate quality, valuation and AI-analysis inputs;
- the same prompt/protocol and model identifier;
- the same available model parameters and runtime environment;
- cache reuse deliberately bypassed; and
- a distinct run ID recorded in the manifest.

The noise-floor report includes per-component score deltas, overall-score
deltas, status flips and Bear-severity changes between the stored candidate run
and the unchanged-input replicate. Small observed baseline/candidate movements
that fall within this empirical distribution are described as indistinguishable
from one-run model variation. One replicate estimates a practical noise floor
across companies; it does not fully characterise the model's sampling
distribution, and that limitation is stated.

If an equivalent model version or execution configuration is no longer
available, the noise-floor experiment is marked unavailable and committee
movement remains descriptive only.

### 12.2 Deterministic per-fix replay

Screen and valuation attribution should use deterministic replay where the
historical inputs allow it. Each claimed fix-specific effect is recomputed by
changing only the affected input or transformation while holding the other
baseline inputs fixed. The private trace records the before value, isolated
counterfactual value, downstream metric and final transition.

If the fixes cannot be isolated because inputs interact or historical state is
missing, no per-fix causal result is emitted. The combined Phase 1 comparison
still proceeds.

## 13. Statistical reporting

- Exact descriptive rates for the frozen 2026 universe are primary.
- Sector-stratified company bootstrap intervals are secondary estimates of
  generalisation to comparable companies or future universes.
- For a metric-specific resampling cohort, sectors with fewer than five
  eligible companies are pooled into one `Other-small-sectors` stratum before
  resampling. If the pooled stratum also contains fewer than five companies,
  that metric uses an unstratified company bootstrap and is labelled as such.
- Bootstrap intervals are not produced for metrics with a denominator below
  20 or only one observed outcome class; exact counts are shown instead.
- Baseline/candidate deltas use paired company-level resampling.
- Tier B resampling, if shown, occurs at company level rather than theme level.
- Random seeds and replicate counts are recorded in the manifest. The default
  is 10,000 replicates with seed `61001` unless a frozen amendment changes it
  before execution.
- Every metric states numerator, denominator, abstentions, exclusions and
  whether it is descriptive, generalisation-oriented or exploratory.

## 14. Persona influence and external alignment

Version 1 evaluates persona **influence and external alignment**, not persona
effectiveness.

The candidate run is the primary persona analysis because it represents the
shipped design. Baseline persona results are secondary.

For each persona the harness reports:

- weighted score-point contribution;
- status changes under removal;
- a **typical-output replacement** counterfactual, replacing each numerical
  component with that component's median in the evaluated run;
- a **mid-scale replacement** sensitivity analysis, replacing numerical
  components with 50;
- a **renormalized-removal** counterfactual, removing the persona's numerical
  weights and rescaling the remaining numerical weights to total 100%;
- Bear numerical risk contribution separately from its high-severity status
  cap; and
- association of persona components with the corresponding external target.

Typical-output replacement asks whether a persona adds company-specific
discrimination beyond its usual output. It is not called neutral evidence. The
component medians and their underlying cohort are recorded beside the result.
Mid-scale replacement measures distance from the mathematical scale midpoint;
it is not interpreted as typical or neutral.

Bear's two mechanisms are kept separate:

1. `risk_score` is analysed numerically through typical-output, mid-scale and
   renormalized-removal counterfactuals; and
2. the categorical severity cap is analysed by recomputing status with the cap
   disabled while holding the numerical score fixed.

No neutral categorical severity is invented. A full Bear-removal sensitivity
may remove the risk weight and disable the cap, but it must remain secondary to
the two mechanism-specific results.

The analysis must acknowledge the designed weights: Quality supplies 55% of the
weighted score, Valuation 40%, and Bear 5% plus its categorical cap. Arithmetic
ablation primarily measures this design and the observed score distribution; it
does not alone show that a persona adds valid information.

Before benchmark interpretation, the report records the candidate run's score
distributions and severity distribution as pipeline diagnostics. The observed
pre-evaluation fact that 76 of 117 candidate verdicts have `high` severity is
reported because it makes the Investigate cap close to a blanket rule; it is not
a benchmark-derived result.

## 15. Harness and execution requirements

The harness MUST:

- accept explicit baseline DB, candidate DB, benchmark directory, manifest,
  private-output directory and public-output path;
- refuse to use a private output path inside the repository;
- operate read-only on immutable database snapshots;
- verify hashes, run existence, lineage, uniqueness, expected stages and
  canonical identifiers before scoring;
- distinguish stage-specific partial completion from failure rather than
  assuming `pipeline_runs.status = complete`;
- use deterministic seeds;
- write full records atomically to the private output directory;
- create the public report only through an explicit aggregate-field allowlist;
- avoid printing proprietary rows or values in logs and exceptions;
- fail closed on unknown schema versions, fields, categories or aliases; and
- use only synthetic benchmark fixtures in repository tests.

The live `data/moat.db` is never an authoritative candidate input. A SQLite
snapshot/backup MUST be taken before further writes, closed consistently (not
copied with an uncheckpointed WAL), hashed, made read-only for eval purposes
and named in the manifest. Authoritative execution is blocked until that
candidate snapshot exists.

Development runs and authoritative runs are explicitly labelled. An
authoritative run requires this specification to be FROZEN and its hash to
match the manifest.

## 16. Proprietary-data boundary

Public artifacts may contain disclosure-safe aggregate methodology, rates,
counts, confusion matrices, cohort shape and snapshot dates. They MUST NOT
contain:

- company-level Morningstar-derived classifications or match results;
- captured benchmark values;
- analyst prose or lightly paraphrased themes;
- per-ticker examples that permit a benchmark rating to be reconstructed; or
- cells containing fewer than five benchmark-derived companies.

Cells below five are suppressed or combined. Detailed comparisons, judge
decisions, human adjudications and traces remain outside the repository.
Company-level discussion may describe Moat's own behavior only when it does not
reveal the corresponding proprietary benchmark answer.

## 17. Corrections and versioning

Frozen benchmark records and eval results are append-only. A discovered error
is corrected through a versioned overlay recording:

- original-record hash;
- corrected-record hash;
- reason;
- date;
- approver; and
- affected metrics.

The original result remains reproducible. Corrected results use a new benchmark
minor version or a new eval-spec execution. Expected answers are never edited
merely because Moat disagrees.

After this specification is frozen:

- editorial changes that do not change execution may be recorded as such;
- any changed cohort, target, tolerance, exclusion, matching rule, metric or
  interpretation boundary requires `eval-spec-v2` or an explicitly secondary
  sensitivity analysis;
- the primary v1 result is never replaced by a later, more favourable rule.

## 18. Required report structure

The authoritative report MUST include, in order:

1. execution manifest and reproducibility checks;
2. universe flow, canonical joins, exclusions and abstentions;
3. Tier A exact results and generalisation intervals;
4. valuation as-run and price-normalised results;
5. committee support, contradiction, ranking and coverage results;
6. Tier C outcomes by field and category;
7. provisional Tier B judge agreement, recall bounds and unresolved counts;
8. paired baseline/candidate changes and cohort movement;
9. persona influence and external alignment;
10. sensitivity analyses;
11. limitations, including pending human validation; and
12. claims that are supported, unsupported or still unresolved.

The report must use `combined Phase 1 effect`, `associated with`, `exploratory`,
`provisional` and `abstain` consistently with this specification.

## 19. Freeze approval

Freezing this specification confirms that Pete approves the evaluation
questions and rules above before the authoritative results are inspected. It
does not approve the harness implementation, benchmark correctness or eventual
results; those remain independently auditable.

**Owner approval:** Pete, 2026-10-06  
**Frozen at:** 2026-10-06  
**Frozen document SHA-256:** recorded in `docs/evals/eval-spec-v1.md.sha256`, computed over this file exactly as committed. A document cannot contain its own hash, so the digest lives beside it, and the execution manifest MUST match it.
