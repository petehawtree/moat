## Outcome

**PASS WITH CONCERNS.** The shipped data, screening, valuation, and pilot committee paths are substantially correct within their documented scope. I found two new Low-severity cache/allowlist defects. Sprint 5 is explicitly an unfinished pilot and Sprint 6 is future scope, so neither is a gate failure.

I reviewed the original PRD, [addendum](/Users/pete/moat/docs/PRD_ADDENDUM.md), README, sprint plans/retros, glossary, schema, code, tests, and [known-issues allowlist](/Users/pete/moat/docs/known-issues.md).

Scope ambiguities resolved by repository evidence:

- The PRD says US + UK; addendum A1 explicitly overrides this to US-only.
- Sprint 5’s plan says “planned,” while README says “in progress”; README is the current status, and explicitly says its pilot/thresholds are not final.
- Monitoring is MVP roadmap Sprint 6 but explicitly unimplemented future scope.
- The FCF-floor prose says both “non-negative” and “positive”; implementation uses strict `> 0`, matching the PRD’s “Positive.”

## Test execution and quality

The system Python lacks pytest, but the repository’s committed virtual environment contains the declared test setup.

- Preliminary: `python3 -m pytest` → could not import pytest.
- Full suite: `./.venv/bin/python -m pytest` → 301 collected; **300 passed, 1 skipped, 0 failed**, one LibreSSL/urllib3 environment warning.

The skipped test is an intentionally opt-in, credentialed Anthropic citation-response contract test. Parser, persistence, SQLite, pipeline, valuation, and dashboard tests otherwise run locally. The test suite is strong on deterministic calculations and persistence; its main residual gap is that the live third-party API contract is not exercised in routine CI.

## Independent calculation checks

- Screen: ADBE’s persisted annual FCF margin independently computes as `$9.852bn / $23.769bn = 41.4489%`; debt/FCF is `0`, and 7 passed of 8 assessed metrics correctly gives `87.5%`. Its 64.79th revenue-growth percentile correctly fails the 66.67th-percentile gate.
- DCF: ADBE’s stored base-case owner-earnings average, growth, discount rate, terminal growth, and 427m shares produce an independent two-stage DCF value of **$310.5199/share**, exactly matching persistence.
- Supporting methods: independently derived normal cases are correct: FCF yield `50/1,000 = 5%`, EV/EBIT `(800+200−100)/100 = 9x`, and P/E `150/5 = 30x`. Negative/zero earnings and non-positive intrinsic value are correctly rejected rather than sign-flipped.
- Committee score: `80, 70, 60, 50, 90, risk 40` yields `73.5`, using the required risk inversion: `25%×80 + 20%×70 + 15%×60 + 10%×50 + 25%×90 + 5%×(100−40)`.
- Citation data: the read-only database check found 2,176 current asserted claims and zero without a citation.

Known filed defects #1–#6 were not counted, per the allowlist. I also independently reproduced #5’s negative-owner-earnings DCF label inversion and #6’s complex-number CAGR edge case; both match their filed symptoms.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US universe, EDGAR/yfinance ingestion, provenance | Current shipped scope | Implemented | Unit/integration-style SQLite tests; provenance CLI spot-check | PASS | A1, A4, A6–A7; `scripts/verify.py ADBE revenue --year 2025` traced the stored figure to its SEC accession |
| Sector-relative 8-metric screen | Current shipped scope | Implemented | Direction, percentile, split-basis, unavailable-status, and end-to-end tests | PASS | A2/A9/A13; independent ADBE calculation |
| Quality roll-up and coverage gate | Current shipped scope | Implemented | Roll-up and insufficient-coverage tests | PASS | 50% threshold plus minimum 6 assessed metrics |
| Citation-grounded AI analysis | Current shipped scope | Implemented | Extensive parser/persistence tests; live API-shape test skipped | PARTIAL | A3; current asserted claims all have citations, but live API contract is opt-in only |
| Owner-earnings DCF and supporting valuation methods | Current shipped scope | Implemented | 51 valuation tests, including hand-derived DCF values and edge cases | PASS | A16/A20; independent ADBE DCF matches stored value |
| Committee score, brief, ranking dashboard | Explicit in-progress pilot | Partially implemented and pilot-run | Persona/parser/persistence/dashboard tests | DEFERRED | README states 21/69 pilot and unvalidated thresholds; allowlist design decision confirms this is not release-ready |
| P/E historical-range backfill | Explicitly deferred | Low-confidence state is surfaced | Tests cover low-confidence signaling | DEFERRED | A21; no fabricated 5–10 year range |
| UK/FTSE universe and FX normalisation | Later scope | Not implemented | N/A | DEFERRED | A1/A8 |
| Watchlist monitoring | Sprint 6 future scope | Stubbed | N/A | DEFERRED | PRD §11 roadmap; README/Sprint 6 |

## [LOW] Committee cache omits rendered company and peer-group context

**Location**

[committee.py](/Users/pete/moat/moat/committee/committee.py:272), `compute_committee_bundle_key`; [prompt.py](/Users/pete/moat/moat/committee/prompt.py:151) and [prompt.py](/Users/pete/moat/moat/committee/prompt.py:175).

**Requirement**

A5 requires a committee result to be regenerated when inputs that affect the persona context change.

**Observed behaviour**

The cache key includes metric, value, status, and percentile, but excludes `sector_peer_group`; it also excludes the company name and sector rendered in the context header. An ad-hoc check changing only `sector_peer_group` from “Information Technology” to “Industrials” produced the identical SHA-256 key.

**Why this matters**

A sector reclassification or peer-group metadata update can reuse a verdict generated from different displayed context, presenting stale analysis as current.

**Evidence**

The rendered prompt includes those fields, while the hash payload does not. Existing cache tests mutate metric values and the composite score, but not rendered sector/company metadata.

**Recommended remediation**

Hash the canonical complete context block, or include every rendered context field in the cache payload, and add cache-miss tests for sector and peer-group changes.

## [LOW] The known-issues allowlist still marks fixed GitHub #7 behaviour as unfixed

**Location**

[known-issues.md](/Users/pete/moat/docs/known-issues.md:18), GitHub #7 row; [committee.py](/Users/pete/moat/moat/committee/committee.py:277); [test_committee_persist.py](/Users/pete/moat/tests/test_committee_persist.py:457).

**Requirement**

The allowlist says an entry is removed when its issue is fixed, so it does not suppress a future recurrence.

**Observed behaviour**

The #7 row says quant/quality inputs are absent from the committee cache key. Current code hashes quant values, statuses, percentiles, and quality score; its dedicated regression test confirms a metric/quality change forces recomputation.

**Why this matters**

The stale allowlist can suppress a reintroduced screen-only cache defect during future judging and misstates the repository’s current behavior.

**Evidence**

Commit `9b9f928` explicitly says the change closes #7; the implementation and regression test confirm it. The allowlist remains unchanged.

**Recommended remediation**

Confirm the issue’s tracker status, then remove or update the #7 allowlist row in the same change that closes it.

## Judge Verdict

### Test execution
- Test command(s): `python3 -m pytest` (environment lacked pytest); `./.venv/bin/python -m pytest`
- Passed: 300
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 4
- PARTIAL: 1
- FAIL: 0
- NOT IMPLEMENTED: 0
- DEFERRED: 4
- CANNOT VERIFY: 0

### Findings
- Critical: 0
- High: 0
- Medium: 0
- Low: 2

### Overall confidence
88%

### Overall verdict
PASS WITH CONCERNS

### Top 5 actions
1. Include all rendered committee context in the cache key.
2. Correct the stale GitHub #7 allowlist entry after confirming tracker status.
3. Run the opt-in live citation API contract test before release.
4. Resolve known issue #5 before treating negative-owner-earnings DCF ranges as decision-ready.
5. Complete and document Sprint 5’s threshold-validation pilot before calling committee output final.
