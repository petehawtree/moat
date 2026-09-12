## Audit result: FAIL

The completed Sprint 3.1 scope has material defects in the batch analysis and citation-reanchoring paths. The persisted dashboard screening output is also stale relative to the current implementation. Deferred valuation, committee, monitoring, FTSE, and paid 90-company work were not treated as failures.

Scope evidence conflicts:

- The original PRD includes US and UK coverage; Addendum A1 explicitly overrides this with the US-only 518-company universe.
- Sprint 3.1 planning material labels work “planned,” while its retrospective and README state items 1–5 are complete. I evaluated those five items as current scope and the 90-company paid run as deferred.
- Sprint 4–6 modules are intentional stubs/planned work, not current-scope failures.

I left the repository unchanged.

### Test execution

Full configured suite:

```sh
.venv/bin/python -m pytest
```

Result: 149 collected; 148 passed, 1 failed, 0 skipped, 1 warning. The failure was the live Anthropic API citation-shape test, which attempted a real API call because an API key was present and failed on DNS/network connectivity (`anthropic.APIConnectionError`).

Diagnostic non-live execution:

```sh
ANTHROPIC_API_KEY='' .venv/bin/python -m pytest
```

Result: 148 passed, 0 failed, 1 skipped, 1 warning. This confirms local tests pass when the live API test is skipped, but does not validate the live API contract.

### Independent calculation checks

- AAPL FY2025: independently calculated FCF as $111.482bn OCF − $12.715bn capex = $98.767bn; FCF margin = 23.7329%; operating margin = 31.9708%; debt/FCF = 0.9181. These agree with stored calculation values.
- AAPL revenue CAGR, using the stored annual endpoints, independently produced 17.1737%, matching the screen value.
- WMT’s 2022/2023 share-history discontinuity was independently adjusted for the approximately 3:1 split/restatement basis. The resulting share CAGR, −2.2287%, matches the implementation.
- TKO’s large reported share increase is preserved as actual dilution rather than silently corrected as a split.
- Re-running the current screen/quality code against a temporary copy of the same database produced 91/505 passed companies, while the persisted current dashboard run says 93/505. Eight companies changed pass/fail status.

### Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US-only S&P 500/Nasdaq 100 universe and local data store | Current | Implemented | Broad unit/integration coverage | PASS | Addendum A1; database contains 518 companies |
| Fundamental ingestion, provenance, confidence and quarterly cache | Current | Implemented | Good targeted coverage, limited real-source regression | PARTIAL | Ingestion/schema tests; cached filing data present |
| Annual financial calculations, peer ranking, and dashboard output | Current | Implemented, but persisted result is stale | Formula tests plus independent AAPL checks | PARTIAL | Current code yields 91 passed; persisted output yields 93 |
| Unavailable versus failure semantics and financial-sector exclusion | Current | Implemented | Direct tests and data checks | PASS | Addendum A9/A14; financial metrics recorded `not_applicable` |
| Split correction versus genuine dilution | Current | Implemented | Targeted tests and independent WMT/TKO checks | PASS | WMT basis adjustment; TKO dilution retained |
| Filing selection, freshness and section extraction | Current | Partial | Mostly synthetic fixtures; amendment fallback not covered in batch | PARTIAL | Current known JPM extraction gap is explicitly deferred |
| Citation parsing, coverage and atomic persistence | Current | Partial | Strong parser/persistence tests, missing empty-analysis guard | PARTIAL | Empty “INSUFFICIENT EVIDENCE” response validates as success |
| Cache reuse and citation reanchoring | Current | Incorrect | No cached-analysis reanchor test | FAIL | Reanchoring examines only current-row claims, not reused source claims |
| Batch workflow, amendment fallback, and hard spend ceiling | Current | Incorrect | Batch tests omit cap and amendment-fallback scenarios | FAIL | Batch path neither applies the cost cap nor falls back from unusable 10-K/A |
| Live citation API response compatibility | Current | Cannot independently validate | Live test fails due to unavailable network | CANNOT VERIFY | Full suite API connection failure |
| Paid 90-company Sprint 3.1 production run | Deferred | Held for authorization | N/A | DEFERRED | Sprint 3.1 explicitly holds item 6 |
| Valuation, committee brief, monitoring, FTSE/FX | Deferred | Intentional stubs/planned work | N/A | DEFERRED | Addendum and Sprint 4–6 plans |

## [HIGH] Batch analysis bypasses the hard spend ceiling

**Location**

`scripts/run_pipeline.py:350-355`; `moat/analysis/caller.py` batch submission path.

**Requirement**

Addendum A15.12 and the Sprint 3 plan require a hard production-spend ceiling, with submissions and retrieval halted once recorded spend reaches the cap.

**Observed behaviour**

The synchronous path accepts a cost cap, but the batch branch calls batch submission/retrieval without passing or enforcing it. The configured production-cap constant is displayed but not used to constrain batch work.

**Why this matters**

A batch run can submit up to the full universe with a 64,000-token maximum per company and exceed the approved spend cap.

**Evidence**

Code inspection found no cost-cap, recorded-spend, or projected-cost check in `_run_batch_submission_and_retrieval`. Existing batch tests only test persistence/hydration.

**Recommended remediation**

Apply the same cap accounting to batch preparation, submission, and retrieval; persist spend atomically and add tests proving no additional batch is submitted after the cap is reached.

## [HIGH] Batch analysis does not apply required 10-K/A fallback

**Location**

`moat/analysis/caller.py:357-370`; `moat/analysis/caller.py:458-509`.

**Requirement**

Sprint 3 Decision 3 requires use of the latest 10-K/A, with fallback to the original 10-K for the same fiscal period when the amendment cannot provide required sections.

**Observed behaviour**

The batch path selects only the latest local filing. When a short Part III amendment cannot produce usable sections, it returns an extraction error rather than trying the original filing. The synchronous path does implement this fallback.

**Why this matters**

Eligible companies can be skipped from batch analysis despite having a usable original filing.

**Evidence**

In a temporary database with a short amendment and valid original filing, batch bundle discovery returned an extraction error while synchronous dry-run analysis selected the original filing successfully.

**Recommended remediation**

Centralize filing selection/fallback so synchronous and batch paths use identical logic, then add an end-to-end batch regression test.

## [HIGH] Cached analyses cannot be reanchored

**Location**

`moat/analysis/persist.py:512-553`; `scripts/cite.py:357-366`.

**Requirement**

Sprint 3.1 requires all reanchoring rungs to operate on current analyses. Cached reuse is an approved normal workflow.

**Observed behaviour**

Cached analysis rows reference a source analysis but do not copy its claims/citations. Reanchoring joins only claims belonging to the current analysis row, so it sees zero citations for cached analyses.

**Why this matters**

A stale or invalid cached citation remains presented as current and is never corrected by the required reanchor process.

**Evidence**

A temporary reproduction with a reused analysis and an unresolvable source citation reported zero citations checked and made no stale-state change.

**Recommended remediation**

Resolve claims through `reused_from_run_id` during reanchoring, or materialize immutable claim/citation copies for the reused row. Add a cached-analysis reanchor test.

## [MEDIUM] Persisted screening output does not match current code

**Location**

`data/moat.db` latest screening run; `moat/screen/quant_screen.py`; `moat/quality/quality_score.py`.

**Requirement**

The current dashboard and downstream analysis should reflect screening results produced by the delivered calculation logic.

**Observed behaviour**

The persisted run reports 93/505 passed companies. Re-running the current code on a temporary copy of the same input data produced 91/505, with eight threshold crossings.

**Why this matters**

Users and downstream AI selection can consume an outdated passed set.

**Evidence**

Old-pass/current-fail examples include EBAY, GEN, KVUE, VLTO, and VRSN; old-fail/current-pass examples include AZO, CRWD, and SBAC.

**Recommended remediation**

Regenerate and version the screening output after calculation changes, surface calculation-version metadata, and add a deterministic snapshot regression check.

## [MEDIUM] Citation validation accepts an empty analytical result

**Location**

`moat/analysis/parser.py:366-397`; `moat/analysis/prompt.py`.

**Requirement**

The analysis prompt requires 5–8 claim lines per section, with cited affirmative claims or explicit evidence limitations.

**Observed behaviour**

A response containing only one `INSUFFICIENT EVIDENCE` line for each required section validates successfully with 100% coverage, zero asserted claims, and no citations.

**Why this matters**

The system can persist a superficially valid “success” that provides no analysis or verifiable evidence.

**Evidence**

An ad-hoc parser check using four required headers, each containing only `INSUFFICIENT EVIDENCE`, returned valid with coverage `1.0`.

**Recommended remediation**

Require a minimum number of substantive claims and/or a bounded evidence-insufficiency ratio before success can be persisted.

## [LOW] Full-suite live API test is not reliably isolated

**Location**

`tests/test_api_citation_shape.py:22-40`.

**Requirement**

The automated suite should be repeatable and clearly separate optional live-provider validation from offline regression testing.

**Observed behaviour**

The test runs whenever an API key is present and fails on unavailable network connectivity. The repository’s environment loads the key, so the default full suite is not reliably offline.

**Why this matters**

A clean local regression run can fail due to infrastructure availability, obscuring product-test status and potentially incurring API usage.

**Evidence**

The full configured suite failed only on this live test; clearing the key produced 148 passes and one intended skip.

**Recommended remediation**

Mark the test explicitly as opt-in live integration testing and exclude it from default pytest execution unless a dedicated flag is supplied.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/python -m pytest`; diagnostic: `ANTHROPIC_API_KEY='' .venv/bin/python -m pytest`
- Passed: 148
- Failed: 1
- Skipped: 0
- Test-suite verdict: FAIL

### Requirements
- PASS: 3
- PARTIAL: 4
- FAIL: 2
- NOT IMPLEMENTED: 0
- DEFERRED: 2
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 3
- Medium: 2
- Low: 1

### Overall confidence
88%

### Overall verdict
FAIL

### Top 5 actions
1. Enforce the hard spend cap throughout batch submission and retrieval.
2. Share amendment-fallback logic between synchronous and batch analysis.
3. Make reanchoring follow cached-analysis provenance.
4. Recompute and publish the current screening run before using it downstream.
5. Reject citation responses with no substantive analytical claims.
