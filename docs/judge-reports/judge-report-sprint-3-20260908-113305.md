## Result

**FAIL.** The quant screen and citation write path were independently validated, but Sprint 3 does not record required structured outcomes for filing/extraction failures. Its real-filing extraction control also falls back on the available Apple 10-K without preserving the trace or enforcing the stated fallback budget rule.

Current scope is Sprints 0–2.2 plus active Sprint 3 W1–W7; Sprint 4–6 remain deferred. The original [MVP PRD](/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf) is broader, but the [addendum](/Users/pete/moat/docs/PRD_ADDENDUM.md:1) and [Sprint 3 plan](/Users/pete/moat/docs/sprints/sprint-3-plan.md:1) control current scope.

Ambiguities noted:

- README’s sprint table says Sprint 3 is “planned,” while its status section and Sprint 3 plan say W1–W7 are implemented and the pilot is pending.
- Sprint 3 requires normal runs to discover the latest filing, but explicitly defers the currently stale cache behavior to Sprint 4. Per the scope rule, I treated that as deferred, not a gate failure.

## Test execution

- Attempted `python3 -m pytest`: failed before collection because the system Python lacks pytest.
- Ran the repository’s usable full-suite command: `./.venv/bin/python -m pytest`
- Result: **113 passed, 0 failed, 0 skipped**, 1 `urllib3` LibreSSL compatibility warning, no test errors.

## Independent financial and logic checks

- Apple FY2025 independently recomputed: FCF = $111.482bn − $12.715bn = **$98.767bn**; FCF margin = **23.7329%**; debt/FCF = **0.9181x**; revenue CAGR (2007–2025) = **17.1737%**. These match stored screen values.
- Its six passed metrics out of eight produce **75.0%**, matching `quality_scores`; I independently recomputed all 505 stored composite scores from their pass/fail statuses with **zero mismatches**.
- Edge behavior is correctly represented in code/tests for missing capex → FCF unavailable, positive debt plus non-positive FCF → debt fail, small/no sector peer groups → floor-only comparison, and Financials’ three inapplicable metrics → unscreenable.
- All **32** persisted citations in the available Apple analysis exactly matched their stored section text, offsets, and hashes. The 30 asserted claims each had citations; four insufficient-evidence entries did not require them.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ-100 universe; FTSE/FX excluded | Current | Implemented | Offline universe tests only | PARTIAL | 518 active companies; live-source refresh not independently rerun |
| EDGAR annual fundamentals, provenance, FCF definition, validation | Current | Implemented | Targeted offline regressions | PASS | Apple calculation and `verify.py` provenance check matched stored data |
| Eight-metric sector-relative screen | Current | Implemented | Synthetic end-to-end and metric tests | PASS | Independent Apple calculations and 505-score reconciliation |
| Assessable-only quality score; Financials excluded rather than mis-ranked | Current | Implemented | Status and score tests | PASS | 93/505 current passes; Financials have insufficient coverage |
| Dashboard ranking, metric explanations, confidence display | Current | Implemented | No UI execution test | PARTIAL | Source queries expose score, status, metric detail, confidence |
| Citation-bound AI claims, durable anchors, atomic write/cached reuse | Current | Implemented for valid responses | Parser/persistence tests; one real Apple analysis | PASS | 32/32 current anchors exact; claim coverage 1.0 |
| Every in-scope AI company gets cited analyses or a persisted structured non-analysis outcome | Current | Incomplete | No orchestration/failure persistence test | FAIL | W1 and extraction failures are returned/printed without `analysis_attempts` rows |
| Observable extraction, real-filing regression coverage, controlled full fallback | Current | Incomplete | Seven synthetic fixtures only | FAIL | Available Apple 10-K incorrectly reaches full fallback; trace is not retained and no 10% guard exists |
| Three-filer / 12-analysis human-reviewed pilot | Current active Sprint 3 | Pending | Not automated | PARTIAL | Status says pilot analyses pending human read; database contains one ticker’s source analysis |
| FTSE 350, FX normalization, financial-sector metric redesign | Deferred | Intentionally deferred | N/A | DEFERRED | Addendum §§A1/A8/A14 |
| DCF, committee, investment brief, monitoring | Deferred | Intentionally stubbed | N/A | DEFERRED | Sprint 4–6 plan and explicit stubs |
| Fresh non-offline filing checks, batch retrieval, non-exact reanchoring | Deferred | Explicitly deferred to Sprint 4 | N/A | DEFERRED | Sprint 3 plan “Deferred to Sprint 4” section |

## [HIGH] Failed AI-stage companies are not persisted as structured outcomes

**Location**

[moat/analysis/persist.py](/Users/pete/moat/moat/analysis/persist.py:378), [scripts/run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:214), and [moat/db/schema.sql](/Users/pete/moat/moat/db/schema.sql:262).

**Requirement**

For every in-scope company, Sprint 3 requires either four cited analyses or a structured outcome explaining the failure. Validation or extraction failures must leave an auditable failure record rather than silence.

**Observed behaviour**

A missing filing returns `{"outcome": "error"}` with no write. Extraction failures similarly return `document_extraction_failed` without a write. W1 errors are only printed and excluded from later processing. `analysis_attempts.outcome` cannot store `document_extraction_failed`.

**Why this matters**

A completed pipeline can conceal companies that received neither analysis nor a durable non-analysis result. This breaks the cited pipeline’s completeness and auditability guarantee.

**Evidence**

In an isolated temporary database, `run_analysis("MISS", ...)` returned `no cached filing — run W1 first` while leaving `analysis_attempts=0` and `ai_analysis=0`. This contradicts Sprint 3’s definition of done and W5 acceptance criteria.

**Recommended remediation**

Persist W1, extraction, and API-pre-call failures in a durable outcome model; reconcile every screened ticker at run completion and mark the run incomplete when any ticker lacks either four analyses or a recorded non-analysis outcome.

## [MEDIUM] Full-fallback extraction is neither observable nor budget-controlled

**Location**

[moat/analysis/caller.py](/Users/pete/moat/moat/analysis/caller.py:147), [moat/ingest/section_extractor.py](/Users/pete/moat/moat/ingest/section_extractor.py:170), and [section rules](/Users/pete/moat/docs/sprints/sprint-3-section-extraction-rules.md:194).

**Requirement**

Extraction must retain candidate/selection evidence. If more than 10% of companies need full-document fallback, processing must stop for extractor remediation.

**Observed behaviour**

The fallback row stores `extraction_trace=NULL`; no run-level fallback rate or 10% stop condition exists. On the repository’s cached Apple 2025 10-K, the true Item 1 candidate at character 22,385 is rejected as `toc_cluster` because the trailing table-of-contents headings remain within the ±3,000-character window. The filing therefore falls back to the 221,343-character full document.

**Why this matters**

The initial real-filing sample is already a false positive fallback. The system cannot diagnose it from its stored receipt and can scale an expensive fallback pattern without enforcing its own budget/quality control.

**Evidence**

Direct extraction of the cached filing produced `full_fallback`; its sole `filing_documents` row has no trace. Existing fixtures are synthetic and deliberately separate the body heading from the table of contents by over 3,000 characters.

**Recommended remediation**

Persist the full extractor trace for fallback outcomes, add real normalized SEC fixtures, correct the ToC-cluster rule, and enforce/report the fallback percentage before production submission.

## [MEDIUM] Incorporated-by-reference handling sends the full filing and omits the required gap label

**Location**

[moat/analysis/caller.py](/Users/pete/moat/moat/analysis/caller.py:118) and [moat/analysis/caller.py](/Users/pete/moat/moat/analysis/caller.py:147).

**Requirement**

For `sections_partial`, analyze the remaining reliable sections and label the incorporated-by-reference gap.

**Observed behaviour**

The caller saves usable sections and also adds `full`, causing the prompt to receive duplicated material plus the unavailable section. The prompt has no extraction-gap label.

**Why this matters**

The model can treat an unavailable section as present, contrary to the documented transparent partial-analysis behavior.

**Evidence**

`sections_partial` enters both branches in `prepare_sections`; the existing test only verifies extractor classification, not prompt contents or user-visible labelling.

**Recommended remediation**

For `sections_partial`, send only the reliable sections and an explicit gap notice; add an integration test asserting both document selection and output labelling.

## Judge Verdict

### Test execution
- Test command(s): `python3 -m pytest` (environment error: pytest unavailable); `./.venv/bin/python -m pytest`
- Passed: 113
- Failed: 0
- Skipped: 0
- Test-suite verdict: PASS

### Requirements
- PASS: 4
- PARTIAL: 3
- FAIL: 2
- NOT IMPLEMENTED: 0
- DEFERRED: 3
- CANNOT VERIFY: 0

### Findings
- Critical: 0
- High: 1
- Medium: 2
- Low: 0

### Overall confidence
87%

### Overall verdict
FAIL

### Top 5 actions
1. Persist and reconcile a structured outcome for every screened ticker, including W1 and extraction failures.
2. Fix the real-filing ToC false-positive fallback and retain fallback extraction traces.
3. Enforce and report the 10% full-fallback limit before any scaled AI run.
4. Correct `sections_partial` prompting and add an integration test for its visible gap label.
5. Complete the three-filer human-reviewed pilot before authorising the remaining companies.
