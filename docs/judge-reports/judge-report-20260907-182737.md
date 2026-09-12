## Audit result

Current implementation fails Sprint 3’s operational requirements. The prior quantitative screen is substantially sound in code, but its formula test coverage is incomplete. Sprint 3 is presented as real by the pipeline, yet key refresh, extraction-fallback, batch, and recall paths do not meet its plan.

Scope ambiguity: README and the Sprint 3 plan still label Sprint 3 “Planned,” while `run_pipeline.py`, recent commits, and 113 Sprint-3-focused tests present it as implemented. I assessed Sprint 3 as current scope because callers are told it is real.

Deferred: UK/FTSE/FX; valuation; committee/brief; monitoring; proxy statements; multi-year narrative history; entailment checking.

Full suite executed successfully:

```text
.venv/bin/python -m pytest
113 passed, 0 failed, 0 skipped, 1 warning
```

The warning is urllib3’s LibreSSL compatibility warning.

## Independent calculation review

| Area | Requirement / implementation | Independent check | Test assessment |
|---|---|---|---|
| FCF | `FCF = operating cash flow − capex`; unknown if capex absent. | OCF 80, capex 20 gives FCF 60; with revenue 200, FCF margin is 30%. Code uses this sign convention. | Missing-capex behavior is tested; ordinary arithmetic is not directly regression-tested. |
| Debt | Debt/FCF is lower-is-better; debt with non-positive FCF is a determined fail. | Debt 240 / FCF 60 = 4x, under the 5x floor; debt 240 with FCF ≤ 0 must fail without computing a ratio. | The determined-failure edge case is covered. |
| Growth/dilution | CAGR uses earliest/latest annual values; share CAGR adjusts only filing-corroborated splits. | Revenue 100→121 over two years is 10% CAGR. Shares 100→121 is 10% dilution and fails the 1% cap. | Split gating is well targeted; ordinary CAGR/value-definition coverage is weak. |
| Sector percentiles | Weak percentile: percentage of peers at least as good; top tercile ≥66.7%. | Higher-is-better value 40 among 10/20/30/40/50 is 80%; lower-is-better debt 2 among 1–5 is also 80%. | Direction and boundary behavior are tested. |
| ROIC/ROE/margins | ROIC is NOPAT / (debt + equity − cash), with NOPAT = EBIT×79%; ROE uses ending equity; margins divide annual values by annual revenue. | EBIT 200, debt 100, equity 1,000, cash 50 gives ROIC 15.05%. Units are consistently annual USD in the extractor. | No direct numerical regression tests for these formulas. |

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US-only universe; UK/FX deferred | Current / deferred boundary | US constants and schema support it | Basic universe tests | PASS | Addendum A1/A8; `config.py` |
| Fundamentals provenance, FCF handling, share-basis gate | Current | Implemented | Focused offline regressions | PARTIAL | Tests cover known regressions, not a live full-universe rerun |
| Sector-relative eight-metric screen and quality score | Current | Implemented | Percentile, status, and synthetic E2E tests | PARTIAL | Core formulas are not all independently regression-tested |
| Financial-sector metrics must not produce misleading ranks | Current | Financials marked not applicable | No direct financial-sector test | PARTIAL | `quant_screen.py:298-300` |
| Normal refresh discovers the latest 10-K/10-K/A | Current Sprint 3 | Cache bypasses SEC discovery | No integration test | FAIL | `filing_fetcher.py:222-225` |
| Failed/short extraction produces a structured non-analysis; 10-K/A falls back to original | Current Sprint 3 | Invalid full text is analyzed; no amendment fallback exists | Synthetic extractor fixtures only | FAIL | `caller.py:142-166`; `filing_fetcher.py:240-288` |
| Native citation parsing and exact source anchors | Current Sprint 3 | Parser and byte equality are implemented | Mocked response tests only | CANNOT VERIFY | No real API citation proof/pilot |
| Atomic persistence of valid/invalid responses | Current Sprint 3 | Transactional write path implemented | Strong unit coverage | PASS | `persist.py:184-262` |
| Cache reuse retains an attempt/audit receipt and invalidates complete bundle | Current Sprint 3 | Cache-hit callers bypass attempt persistence | Unit test covers only unused helper path | PARTIAL | `persist.py:368-396`; `analyze.py:124-154` |
| Recall uses full resolution ladder and marks unresolved analyses stale | Current Sprint 3 | Exact-offset check only; no stale transition | No recall tests | FAIL | `cite.py:207-273` |
| Production combined Batch API is submitted, retrieved, validated, and persisted idempotently | Current Sprint 3 | Submission exists; no reachable retrieval/persistence flow | No batch tests | FAIL | `analyze.py:59-77`; `caller.py:348-395` is unused |
| Pilot, human review, and all in-scope structured outcomes | Current Sprint 3 | Not evidenced in repository | Not executable without API credentials/data | CANNOT VERIFY | Sprint-3 definition of done |
| Valuation, committee, brief, monitoring | Future | Stubs | N/A | DEFERRED | README sprint table; PRD roadmap |
| FTSE 350 and FX normalization | Future | Not implemented | N/A | DEFERRED | Addendum A1/A8 |

## [HIGH] Normal runs silently reuse stale filings

**Location**

`moat/ingest/filing_fetcher.py:222-225`

**Requirement**

A normal Sprint 3 run must discover the latest filing first; only `--offline` may reuse the last cached filing without checking freshness.

**Observed behaviour**

A valid cached file returns immediately before CIK lookup or SEC submissions discovery.

**Why this matters**

New 10-Ks and 10-K/As do not change the document hash, cache key, or analysis. Users can receive apparently current analysis grounded in an obsolete filing.

**Evidence**

An isolated check inserted a valid cached filing, replaced `lookup_cik` with an assertion, and `run_for_ticker(..., offline=False)` returned `('old-10k', None)` without discovery. This conflicts with Sprint 3 W7.

**Recommended remediation**

On normal runs, fetch submissions metadata, select the latest accepted filing, then reuse immutable local bytes only when they match that selected accession and hash. Keep the current shortcut exclusively for offline mode.

## [HIGH] The declared production Batch workflow cannot complete

**Location**

`scripts/analyze.py:59-77`; `moat/analysis/caller.py:348-395`; `scripts/run_pipeline.py:241-254`

**Requirement**

Sprint 3 production is one combined request per company through the Batch API, with results independently retrieved, validated, persisted, and retried by `custom_id`.

**Observed behaviour**

`--batch` only submits and prints transient metadata. No command or pipeline path calls `retrieve_batch()`, and no submitted batch creates persisted attempts or analyses. The normal pipeline invokes synchronous `run_analysis()` instead.

**Why this matters**

The stated production architecture cannot produce research results after submission, undermining scalability, idempotency, cost controls, and auditability for the intended 93-company run.

**Evidence**

Repository-wide search found `retrieve_batch()` is defined but never called.

**Recommended remediation**

Persist batch request frames before submission, add retrieval/resume processing keyed by `custom_id`, atomically persist each result or failure, and wire that path into the production pipeline.

## [HIGH] Failed and implausibly short filings are analyzed instead of rejected

**Location**

`moat/analysis/caller.py:142-166`; `moat/ingest/filing_fetcher.py:240-288`

**Requirement**

A full fallback below 20,000 normalized characters or otherwise implausible must yield `document_extraction_failed`. A chosen 10-K/A that cannot yield acceptable Items 1/1A/7 must visibly fall back to the original 10-K.

**Observed behaviour**

Any full fallback is stored and supplied to the model. There is no 20,000-character check and no original-10-K fallback path.

**Why this matters**

A short amendment, malformed document, or non-filing can lead to apparently legitimate `INSUFFICIENT EVIDENCE` or narrative output instead of an explicit failure.

**Evidence**

A temporary 13,299-character HTML document with no Item headings was accepted by `prepare_sections()` as `{'full'}`. The real cached Apple FY2025 filing also falls to full fallback because the ToC-cluster rule rejects its real Item 1 candidate.

**Recommended remediation**

Enforce the full-document plausibility floor before request construction, persist `document_extraction_failed` when it fails, and attempt the original 10-K when a selected amendment fails section extraction.

## [MEDIUM] Citation recall implements only one of the required resolution rungs

**Location**

`scripts/cite.py:207-273`

**Requirement**

Recall must attempt offsets, moved-in-section, moved-across-filing, normalization-insensitive, and fuzzy-context matching; unresolved citations must make their analysis stale.

**Observed behaviour**

The implementation explicitly performs exact offsets only and records all other situations as `unresolved`; it does not mark the associated analysis stale.

**Why this matters**

Anchors do not remain durable across expected re-normalization or document movement, defeating a central Sprint 3 citation requirement.

**Evidence**

The code comment says the remaining rungs are “Sprint 4 scope,” conflicting with current Sprint 3 W6 and A15.5.

**Recommended remediation**

Implement the documented resolution ladder with immutable events, ambiguity controls, and an explicit stale-analysis state.

## [MEDIUM] Cache-hit paths omit the required audit receipt

**Location**

`moat/analysis/persist.py:368-396`; `scripts/analyze.py:124-154`

**Requirement**

Cache reuse must be explicit and auditable alongside analysis attempts.

**Observed behaviour**

Both public cache-hit paths copy `ai_analysis` rows directly and return without inserting `analysis_attempts`.

**Why this matters**

The audit trail cannot distinguish a cache reuse from a missing or failed invocation, despite the database and helper function supporting this record.

**Evidence**

An isolated temporary database run returned `{'outcome': 'cache_hit'}` and then reported `attempts 0`. The lower-level `persist_result()` cache branch is tested, but neither live caller uses it.

**Recommended remediation**

Route cache hits through one persistence path that always writes the reuse attempt and include a separate extraction-version component in the bundle key.

## [MEDIUM] Tests do not establish material financial arithmetic or production integrations

**Location**

`tests/test_quant_screen.py`; `tests/test_section_extractor.py`; absent tests for pipeline refresh, batch retrieval, recall, and live citation shape.

**Requirement**

Material calculations and current-scope workflows need independent, error-detecting coverage.

**Observed behaviour**

Tests strongly cover several historical regressions, but the end-to-end financial test uses one annual period and does not verify ordinary FCF margin, debt/FCF, revenue CAGR, ROIC, ROE, or gross-margin arithmetic. Section fixtures are synthetic and did not expose the Apple fallback. Batch, refresh, recall, and real API citation behavior are untested.

**Why this matters**

The suite can pass while formula changes, real SEC formatting, or operational Sprint 3 failures break current research outputs.

**Evidence**

113 tests pass, but no test invokes `run_ai_analysis_stage`, `retrieve_batch`, `_reanchor`, normal cached filing refresh, or a real citation-enabled API response.

**Recommended remediation**

Add deterministic formula fixtures with independent expected values, real anonymized SEC fixtures, normal/offline refresh tests, amendment fallback tests, batch round-trip tests, and a credential-gated integration test for the actual API response shape.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/python -m pytest`
- Passed: 113
- Failed: 0
- Skipped: 0
- Test-suite verdict: PASS

### Requirements
- PASS: 2
- PARTIAL: 4
- FAIL: 4
- NOT IMPLEMENTED: 0
- DEFERRED: 2
- CANNOT VERIFY: 2

### Findings
- Critical: 0
- High: 3
- Medium: 3
- Low: 0

### Overall confidence
84%

### Overall verdict
FAIL

### Top 5 actions
1. Make normal runs discover the latest filing before cache reuse.
2. Implement persisted Batch retrieval, validation, retry, and pipeline wiring.
3. Reject implausible full fallbacks and implement 10-K/A-to-original fallback.
4. Implement the citation resolution ladder and stale-analysis handling.
5. Add formula, real-filing, refresh, batch, recall, and API integration tests.
