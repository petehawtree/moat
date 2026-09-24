## Outcome

**PASS WITH CONCERNS.** Core arithmetic, scoring, evidence anchors, and the active Sprint 5 pilot work as implemented. I found two new Low issues.

Current scope is Sprints 1–4 plus the explicitly partial Sprint 5 committee pilot. The original US+UK PRD is overridden by Addendum A1’s US-only scope; Sprint 5 is “planned” in its plan but “in progress” in README, with README/code/data supporting the latter. The original PRD was inspected here: :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"}

Full suite: `.venv/bin/pytest` collected 330 tests: 329 passed, 1 skipped, 0 failed. The skipped test is the intentionally opt-in live Anthropic API contract test. One LibreSSL/urllib3 environment warning occurred; no test errors.

Independent checks included:

- Latest live data: 505 screened, 109 passed; 109 valued; 81 latest committee verdicts.
- ADBE FCF margin independently calculated as `(OCF − capex) / revenue = 41.448946%`, matching storage.
- ADBE base DCF independently recomputed to `$310.519920/share`, matching storage.
- All 321 persisted DCF scenario rows, 106 FCF-yield rows, 77 EV/EBIT rows, 505 quality roll-ups, and 81 committee weighted scores/statuses matched independent calculations.
- All 2,883 citation anchors used by the latest committee briefs matched their document SHA-256, offsets, and quoted text exactly.

Known filed items #1, #2, #3, #4, #5, #6, #8, #10, and #11 were treated as informational only. The latest committee run correctly excluded the configured known-bad tickers. In particular, #8 affects historical P/E, and #10/#11 limit some DCF inputs/confidence; these are not new findings.

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US universe, EDGAR ingestion, provenance | Current | Implemented | Unit and SQLite integration tests | PASS | 518 companies; 505 with fundamentals; filing/citation receipts present |
| Sector-relative screen and quality coverage gate | Current | Implemented | Direction, percentile, split-basis, unavailable-status tests | PARTIAL | Independent roll-up check: 505/505 match; known #1/#2 remain informational |
| Citation-grounded AI analysis | Current | Implemented | Parser/persistence tests; live contract test opt-in | PARTIAL | 2,883/2,883 current brief citation anchors verified; routine live API contract not exercised |
| Owner-earnings DCF and supporting methods | Current | Implemented | Extensive hand-derived valuation tests | PARTIAL | 321 DCF and supporting-method calculations matched; known #5/#8/#10/#11 apply informationally |
| Committee weighted score, risk polarity, status cap | Sprint 5 pilot | Implemented | Parser, persistence, stage tests | PASS | 81/81 current verdicts match independent weighted-score and status checks |
| Dashboard ranking and one-page brief evidence display | Sprint 5 pilot | Implemented | Streamlit AppTest coverage | PASS | Current source quotes and input provenance resolve correctly |
| FTSE 350 / FX normalisation | Deferred | Not implemented | N/A | DEFERRED | Addendum A1/A8 |
| Watchlist monitoring | Sprint 6 future work | Stubbed | N/A | DEFERRED | PRD roadmap and Sprint 5 plan |

## [LOW] Committee cache omits rendered sector metadata

**Location**

[committee.py](/Users/pete/moat/moat/committee/committee.py:247), `compute_committee_bundle_key`; [prompt.py](/Users/pete/moat/moat/committee/prompt.py:175).

**Requirement**

A5 cache reuse must not preserve a verdict when persona-visible input changes.

**Observed behaviour**

The prompt renders company name, sector, and `sector_peer_group`, but the cache hashes neither sector field nor company metadata.

**Why this matters**

A sector reclassification can display a verdict generated under different peer-group context as a current cache hit.

**Evidence**

Changing only `sector_peer_group` from `Information Technology` to `Industrials` produced the identical bundle SHA-256. Existing cache tests mutate values but not rendered sector metadata.

**Recommended remediation**

Hash a canonical complete context block, or include every rendered field, with regression tests for company-sector and peer-group changes.

## [LOW] Metrics freshness check fails solely because the date changed

**Location**

[metrics.py](/Users/pete/moat/scripts/metrics.py:59), [metrics.py](/Users/pete/moat/scripts/metrics.py:280), [metrics.py](/Users/pete/moat/scripts/metrics.py:322).

**Requirement**

The documented pre-push metrics check should detect stale metrics, not fail without metric changes.

**Observed behaviour**

`metrics.py --check` failed because rendering uses today’s date while README says it was generated on 2026-09-19; the metric values themselves were identical.

**Why this matters**

The advisory check is noisy every day after rendering, weakening its value as a freshness control.

**Evidence**

The only generated-block diff was `2026-09-19` versus `2026-09-23`.

**Recommended remediation**

Use the metrics record’s `as_of` date for rendering, or exclude the generated-date line from comparison.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/pytest`
- Passed: 329
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 3
- PARTIAL: 3
- FAIL: 0
- NOT IMPLEMENTED: 0
- DEFERRED: 2
- CANNOT VERIFY: 0

### Findings
- Critical: 0
- High: 0
- Medium: 0
- Low: 2

### Overall confidence
89%

### Overall verdict
PASS WITH CONCERNS

### Top 5 actions
1. Hash all rendered committee context in the cache key.
2. Fix the date-driven metrics-check false positive.
3. Run the opt-in live API contract test in a controlled release check.
4. Resolve known DCF issues #10 and #11 before relying on borderline verdicts.
5. Complete and document Sprint 5 threshold validation before calling rankings final.
