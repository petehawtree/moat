## Assessment

**FAIL.** The citation-enforced AI pilot is substantively verified, but the current quantitative screen publishes materially wrong candidate results: debt is understated or omitted, and REITs are ranked on metrics the repository itself identifies as invalid.

I made no repository changes.

Current scope is Sprints 1–3.1. Valuation, committee/brief, monitoring, FTSE/FX, proxy filings, and citation-entailment checking are explicitly deferred. Sprint naming is slightly inconsistent: README stops at Sprint 3 while Sprint 3.1 records shipped work. I treated all live, shipped screen outputs as current scope; the operational `--exclude` does not repair their persisted scores or dashboard display.

Full suite:

- `.venv/bin/python -m pytest`
- 159 passed, 1 skipped, 0 failed, 1 LibreSSL/urllib3 warning.
- The skipped test is the opt-in live Anthropic citation-shape test. I also re-parsed the three persisted pilot responses and independently checked all 99 citation hashes, offsets, and quotes against local filing receipts: all were valid; 91/91 asserted claims had citations.

Independent financial checks:

- Debt rule: total debt / latest annual FCF must be at most 5x. AMT’s FY2025 stored debt is $3.3878bn and FCF $3.7836bn, producing the displayed 0.895x pass. Its cached SEC facts contain $37.2203bn under `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`; $37.2203bn / $3.7836bn = **9.837x**, an absolute-floor failure.
- REIT rule: the addendum explicitly identifies FFO/AFFO—not FCF margin and debt/FCF—as the appropriate basis. Yet AMT and SBAC pass the live screen with 62.5% and 50.0%, respectively, using those invalid metrics.
- Other checked formulas behave as specified: FCF is OCF minus capex and remains unavailable when capex is absent; percentile directionality is correct; quality score uses pass/assessed metrics with a six-metric coverage floor.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ 100 universe; FTSE/FX deferred | Current / deferred | US-only ingestion and schema support | Offline universe tests | PASS | Addendum A1; README scope |
| Annual fundamentals with provenance, FCF = OCF − capex, and confidence tags | Current | Implemented, but debt extraction is incomplete | FCF/revenue tests; no debt-tag hierarchy test | PARTIAL | [fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:169) |
| Debt screen: total debt / annual FCF, max 5x | Current | Uses only non-current/current tag pair; omits common total concepts | No regression for omitted debt tags | FAIL | [fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:217), [A17](/Users/pete/moat/docs/PRD_ADDENDUM.md:1228) |
| Sector-relative eight-metric screen without publishing definitionally invalid metrics | Current | Only Financials are excluded; Real Estate remains scored | No REIT applicability test | FAIL | [quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:298), [A17](/Users/pete/moat/docs/PRD_ADDENDUM.md:1240) |
| Distinguish pass/fail/unavailable and enforce minimum coverage | Current | Correctly implemented | Direct unit and end-to-end synthetic tests | PASS | [quality_score.py](/Users/pete/moat/moat/quality/quality_score.py:14) |
| Ranked dashboard with metric-level explanation | Current | Renders current `quality_scores` and `quant_scores` | No UI test; inputs are materially wrong | PARTIAL | [app.py](/Users/pete/moat/moat/dashboard/app.py:112) |
| Latest 10-K/10-K/A retrieval, extraction, amendment fallback | Current | Implemented | Good offline tests for selection, freshness, fallback, extraction | PASS | [filing_fetcher.py](/Users/pete/moat/moat/ingest/filing_fetcher.py:309) |
| Every persisted asserted AI claim has a durable, resolvable citation | Current | Implemented with exact write-path validation and immutable anchors | Parser/persistence tests; persisted pilot independently verified | PASS | [parser.py](/Users/pete/moat/moat/analysis/parser.py:276) |
| Batch persistence, caching, re-anchoring, and cost cap | Current | Implemented | Strong mock/offline coverage; live API test skipped | PARTIAL | [persist.py](/Users/pete/moat/moat/analysis/persist.py:713) |
| DCF/owner earnings valuation | Deferred—Sprint 4 | Intentional stubs | Not applicable | DEFERRED | [engine.py](/Users/pete/moat/moat/valuation/engine.py:20) |
| Committee and Investment Brief | Deferred—Sprint 5 | Intentional stubs | Not applicable | DEFERRED | README sprint plan |
| Watchlist monitoring | Deferred—Sprint 6 | Intentional stubs | Not applicable | DEFERRED | README sprint plan |
| FTSE 350 and FX normalization | Deferred | Not implemented by design | Not applicable | DEFERRED | Addendum A1 |
| Claim-entailment review and DEF 14A management evidence | Explicitly deferred | Not implemented by design | Not applicable | DEFERRED | Addendum A15.9 |

## [HIGH] Debt extraction understates leverage and creates false screen passes

**Location**

[fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:217), [fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:650)

**Requirement**

The current debt metric is total debt divided by latest annual FCF, with a 5x maximum.

**Observed behaviour**

Only `LongTermDebtNoncurrent`, `LongTermDebtCurrent`, and `DebtCurrent` are considered. Common total-debt concepts are omitted.

**Why this matters**

AMT is shown as 0.895x debt/FCF and passes. Its cached FY2025 SEC fact gives total debt of $37.2203bn including current maturities; against $3.7836bn FCF, leverage is 9.837x and should fail. Eighteen of the 91 screen-pass companies have null debt, removing the debt metric from their score denominator.

**Evidence**

The missing tags and AMT discrepancy are independently reproduced from the local cached SEC payload and current database, matching [A17](/Users/pete/moat/docs/PRD_ADDENDUM.md:1228).

**Recommended remediation**

Implement an explicit, non-double-counting debt-tag hierarchy; re-ingest, re-screen, invalidate affected AI eligibility/results, and add real-fact regression fixtures for AMT and null-debt candidates.

## [HIGH] REITs are published as passing using invalid FCF and debt metrics

**Location**

[quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:298), [app.py](/Users/pete/moat/moat/dashboard/app.py:112)

**Requirement**

A screen must not publish plausible rankings using metrics that do not describe the company’s business model. The addendum identifies FFO/AFFO as the proper REIT basis.

**Observed behaviour**

`SECTOR_INAPPLICABLE_METRICS` excludes only Financials. Real Estate is still evaluated on FCF margin and debt/FCF. AMT and SBAC pass the current screen.

**Why this matters**

The dashboard and AI-selection source present unsuitable REITs as current candidates. `--exclude` only suppresses a future AI batch; it leaves the stored score and dashboard result intact.

**Evidence**

Current database results: AMT passes 5/8 (62.5%); SBAC passes 4/8 (50.0%). The repository expressly documents this as invalid but unfixed in [A17](/Users/pete/moat/docs/PRD_ADDENDUM.md:1240).

**Recommended remediation**

Until FFO/AFFO metrics exist, mark the affected Real Estate metrics not applicable and make REITs unscreenable under the coverage gate; then rerun and republish screening results.

## [MEDIUM] Tests do not protect the two material screen failures

**Location**

[test_fundamentals_edgar.py](/Users/pete/moat/tests/test_fundamentals_edgar.py:27), [test_quant_screen.py](/Users/pete/moat/tests/test_quant_screen.py:159)

**Requirement**

Material calculations require tests able to detect incorrect implementations and financial false positives.

**Observed behaviour**

The fundamentals tests cover tag merging, duration filtering, and restatements, but no debt-tag hierarchy. The end-to-end screen fixture uses only a generic Technology peer set and never tests Real Estate applicability.

**Why this matters**

The suite passed while both material defects remained live.

**Evidence**

159 tests pass despite AMT’s 0.895x versus 9.837x discrepancy and two invalid REIT passes.

**Recommended remediation**

Add extraction fixtures for `LongTermDebt` and total-including-current-maturities tags, plus end-to-end screen tests proving REITs cannot pass on FCF/debt metrics before sector-specific metrics are implemented.

## [LOW] README reports stale screen totals and omits Sprint 3.1 status

**Location**

[README.md](/Users/pete/moat/README.md:57)

**Requirement**

Repository status should accurately describe the current shipped release.

**Observed behaviour**

README reports 93/505 passing, while the current persisted screen run contains 91/505; Sprint 3.1 is also absent from its sprint table.

**Why this matters**

It makes current scope and candidate counts harder to assess.

**Evidence**

Current database run `20260909T124824Z` has 91 passes; [A17](/Users/pete/moat/docs/PRD_ADDENDUM.md:1258) records the same change.

**Recommended remediation**

Update the README status/sprint table after the screen repair and clearly distinguish historical run totals from current results.

## Judge Verdict

### Test execution
- Test command(s): `python3 -m pytest` (system interpreter lacked pytest); `.venv/bin/python -m pytest`
- Passed: 159
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 4
- PARTIAL: 3
- FAIL: 2
- NOT IMPLEMENTED: 0
- DEFERRED: 5
- CANNOT VERIFY: 0

### Findings
- Critical: 0
- High: 2
- Medium: 1
- Low: 1

### Overall confidence
94%

### Overall verdict
FAIL

### Top 5 actions
1. Fix debt-tag extraction, re-ingest, and rerun the screen.
2. Remove REIT FCF/debt screening until FFO/AFFO-based metrics exist.
3. Add real-fact debt and Real Estate regression tests.
4. Invalidate or visibly mark affected dashboard and AI-candidate results.
5. Update README scope and current screen totals.
