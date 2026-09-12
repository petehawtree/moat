Current scope is ambiguous in the documentation: README and the Sprint 3 plan say “planned,” but HEAD contains Sprint 3 W1–W7 commits and the live database contains a completed cited AAPL analysis. I treated Sprint 3 as active functionality, while treating valuation, committee, monitoring, UK/FX, proxy statements, and citation entailment as deferred.

The Sprint 2.2 financial screen is substantially verified. The live run `20260822T140041Z` has 505 companies, 93 passes, and zero independent quality-rollup mismatches. For Apple, independently derived FCF is $98.767bn (`$111.482bn OCF − $12.715bn capex`), FCF margin is 23.7329%, and debt/FCF is 0.9181—matching stored values. The share-dilution, unavailable-data, financial-sector exclusion, and provenance handling also behaved as documented in inspected records.

However, Sprint 3’s core evidence lifecycle is not correct after a cache hit, batch production is not wired, and the documented recall ladder is absent.

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ-100 universe and EDGAR ingestion | Current | Implemented | Basic unit coverage; live DB has 518 active companies and 505 with fundamentals | PASS | Addendum A1/A7; live DB |
| Annual financial definitions and data-integrity controls | Current | FCF = OCF − capex; lease income additive; provenance/flags retained | Regression tests and live spot checks | PASS | Addendum A10–A14; AAPL, TKO, CPT, FITB records |
| Sector-relative eight-metric screen | Current | Direction-aware percentiles, floors, coverage exclusions | Percentile and synthetic end-to-end tests | PASS | Addendum A2/A9; live rollup recomputation |
| Quality score and financial-sector suppression | Current | Percent of assessable metrics; minimum six metrics; Financials marked not applicable | Unit tests plus live status counts | PASS | Addendum A13/A14; zero rollup mismatches |
| Ranked dashboard and metric drill-down | Current | Present in Streamlit code | No automated UI test | PARTIAL | [app.py](/Users/pete/moat/moat/dashboard/app.py:86) |
| Filing retrieval, normalization, and section extraction | Active Sprint 3 | Works through full-document fallback, but rejects real Apple body headings | Fixtures only; no corpus calibration | PARTIAL | [section_extractor.py](/Users/pete/moat/moat/ingest/section_extractor.py:294) |
| Citation write path | Active Sprint 3 | Native citation anchors resolve to immutable normalized text | Parser/persistence tests; live AAPL run | PASS | 32/32 live citation byte anchors matched |
| Cache copy-forward and analysis lifecycle | Active Sprint 3 | Copies only `ai_analysis`; leaves claims/citations behind and prior rows current | Tests miss claim/citation lifecycle | FAIL | [persist.py](/Users/pete/moat/moat/analysis/persist.py:351) |
| Production Batch API and durable batch attempts | Active Sprint 3 | Batch submission exists, but production runner is synchronous and batch retrieval/persistence is not wired | No batch integration tests | NOT IMPLEMENTED | [run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:243), [analyze.py](/Users/pete/moat/scripts/analyze.py:59) |
| Citation re-anchoring ladder | Active Sprint 3 | Exact check only; all other prescribed rungs return unresolved | No re-anchoring tests | NOT IMPLEMENTED | [cite.py](/Users/pete/moat/scripts/cite.py:207) |
| Human pilot / entailment validation | Active Sprint 3 | Not evidenced; byte provenance is not semantic support | No human-review evidence | CANNOT VERIFY | Addendum A15.2 and Sprint 3 plan |
| Valuation, committee, investment brief, monitoring | Deferred | Stubs / later sprint work | Not required now | DEFERRED | PRD roadmap; README |
| UK/FTSE and FX normalization | Deferred | Not implemented | Not required now | DEFERRED | Addendum A1/A8 |
| Proxy statements and multi-year management assessment | Deferred | Explicitly excluded | Not required now | DEFERRED | Addendum A15.9 |

## [HIGH] Cache hits orphan the copied analysis from its claims and citations

**Location**

[persist.py](/Users/pete/moat/moat/analysis/persist.py:351), [persist.py](/Users/pete/moat/moat/analysis/persist.py:135), [analyze.py](/Users/pete/moat/scripts/analyze.py:124)

**Requirement**

A cached analysis must be explicitly reusable without fabricating new provenance, while retaining a complete claim-and-citation evidence trail and correctly distinguishing current from historical analyses.

**Observed behaviour**

On a cache hit, code inserts four new `ai_analysis` rows only. It neither copies nor associates `analysis_claims` and `citations`, and it never makes the source rows non-current.

The live database demonstrates this: run `20260907T134435Z` has four current AAPL analysis rows with `reused_from_run_id`, zero claims, zero citations, and four analysis rows without any claims. The source run remains current, yielding eight current AAPL analyses.

**Why this matters**

The latest visible analysis can have no claim-level evidence, while an older analysis is still presented as current. This breaks the sprint’s central evidence and historical-state model.

**Evidence**

Addendum A15.4/A15.7; Sprint 3 W5/W6 acceptance criteria; live SQLite queries.

**Recommended remediation**

Represent reuse as an explicit immutable association to the original analysis/claims/citations, or copy the complete immutable evidence graph transactionally. Set a single well-defined current analysis bundle per ticker.

## [HIGH] The promised production Batch workflow is not implemented

**Location**

[run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:243), [persist.py](/Users/pete/moat/moat/analysis/persist.py:380), [analyze.py](/Users/pete/moat/scripts/analyze.py:59)

**Requirement**

Production should use one combined Batch API request per company, preserve each request frame durably, process results independently, and retry idempotently by `custom_id`.

**Observed behaviour**

The pipeline calls `run_analysis`, which always calls synchronous `call_sync`. `--batch` merely submits a batch and prints request metadata; it neither retrieves results nor persists attempts, claims, citations, errors, or retry state. `retrieve_batch` is unused.

**Why this matters**

The operational production path does not meet the documented cost-control, auditability, and idempotency requirements. A process interruption after submission loses the sole durable request frame.

**Evidence**

Sprint 3 W5/W7 and Addendum A15.10–A15.12; no tests cover submission/retrieval/persistence integration.

**Recommended remediation**

Make Batch the production pipeline path; write submission attempts and request frames before submission; add a retrieval command/job that persists every result atomically and retries by durable `custom_id`.

## [HIGH] Citation re-anchoring ladder is absent

**Location**

[cite.py](/Users/pete/moat/scripts/cite.py:207)

**Requirement**

Citation recall must progress through offsets, same-section quote search, whole-filing search, normalization-insensitive search, fuzzy contextual match, then unresolved—recording an event for the selected rung.

**Observed behaviour**

`--reanchor` performs exact byte-offset validation only. Every non-exact anchor is written as `unresolved`; the code itself labels the missing rungs as Sprint 4 work.

**Why this matters**

A harmless filing re-fetch or normalization change makes otherwise recoverable citations appear permanently unresolved, contrary to Sprint 3’s stated durable-anchor design.

**Evidence**

Addendum A15.5; Sprint 3 W6. The code’s Sprint 4 comment conflicts with those requirements.

**Recommended remediation**

Implement and test all documented resolution rungs before presenting `--reanchor` as the recall surface.

## [MEDIUM] The extractor falsely rejects real Apple section headings

**Location**

[section_extractor.py](/Users/pete/moat/moat/ingest/section_extractor.py:294)

**Requirement**

Real Item 1, Item 1A, and Item 7 headings should be extracted when confidence rules permit; fallback should be an exception monitored against the 10% threshold.

**Observed behaviour**

The supplied AAPL 2025 10-K is classified `full_fallback`: real Item 1 and Item 7 headings are rejected as `toc_cluster` because the ±3,000-character window also includes nearby table-of-contents headings.

**Why this matters**

The only real filing exercised takes the expensive, broad full-document path. The fixtures did not detect this realistic false positive, so the documented fallback-rate control has not been validated.

**Evidence**

`data/filing_docs/.../aapl-20250927.htm`: 221,343 normalized characters; Item 1 and Item 7 rejected despite visible body headings; `filing_documents` records one `full_fallback`.

**Recommended remediation**

Refine ToC detection to identify an actual contents cluster rather than any nearby item headings, then calibrate against a representative filing corpus before scale-up.

## [MEDIUM] AI stage does not select the latest successful quality run

**Location**

[run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:147)

**Requirement**

The AI stage must select the latest successful `quality_scores` run.

**Observed behaviour**

The selector groups `quality_scores` and orders lexicographically by `run_id`; it does not join `pipeline_runs` or exclude failed runs.

**Why this matters**

A run that completed scoring but subsequently failed can become the selected AI spend gate.

**Evidence**

Sprint 3 W7 explicitly requires successful-run selection. Current live loose and status-aware selectors happen to agree, but only incidentally.

**Recommended remediation**

Join `pipeline_runs`, require an accepted completion state, and order by the recorded run timestamp.

## [LOW] Public cache default contradicts the 90-day freshness requirement

**Location**

[fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:137)

**Requirement**

Fundamentals cache should default to the 90-day refresh period.

**Observed behaviour**

`fetch_company_facts(..., max_age_days=None)` defaults to never expiring, although its docstring says the default is `FUNDAMENTALS_CACHE_MAX_AGE_DAYS`. The main ingest caller passes 90 explicitly.

**Why this matters**

External callers can silently freeze fundamentals indefinitely.

**Evidence**

Addendum A13; function signature versus docstring.

**Recommended remediation**

Use the configured 90-day value as the public default and reserve `None` for explicit archival verification reads.

## Judge Verdict

### Test execution
- Test command(s): `./.venv/bin/python -m pytest --collect-only -q`; `./.venv/bin/python -m pytest -q` (112 collected; 1 urllib3/LibreSSL warning; no errors)
- Passed: 112
- Failed: 0
- Skipped: 0
- Test-suite verdict: PASS

### Requirements
- PASS: 5
- PARTIAL: 2
- FAIL: 1
- NOT IMPLEMENTED: 2
- DEFERRED: 3
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 3
- Medium: 2
- Low: 1

### Overall confidence
91%

### Overall verdict
FAIL

### Top 5 actions
1. Repair cache copy-forward so current analyses retain complete immutable claim/citation provenance.
2. Implement durable Batch submission, retrieval, result persistence, and idempotent retries as the production path.
3. Implement the full citation re-anchoring ladder and regression tests.
4. Fix and calibrate section extraction against representative real filings before scaling beyond pilot use.
5. Add integration tests for pipeline run selection, lifecycle status, batch handling, cache reuse, and recall.
