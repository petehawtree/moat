## Result

**FAIL.** The full automated suite is green, but current screen and valuation outputs remain materially unreliable for known debt-tag and REIT-methodology defects. The default valuation command can also publish those known-invalid candidates.

I reviewed the governing original PRD :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"} plus the addendum, sprint plans/retrospectives, schema, pipeline, source, tests, and current local database. No tracked files were changed.

## Scope and ambiguities

Current scope is through Sprint 4: US universe, SEC/yfinance ingest, sector-relative screen, citation-enforced AI analysis, and valuation. Sprint 5 committee/Investment Brief and Sprint 6 monitoring are deferred. FTSE/FX is explicitly deferred.

The debt and REIT fixes are not harmless deferred work: although their remediation is scheduled later, the defective screen is still published and feeds current valuation. They are current-scope failures.

Documentation conflicts:

- README says 90/91 companies received 540 valuation rows; Sprint 4’s retrospective says 70/70 and 420 rows after exclusions. The current latest run has 420 rows for 70 tickers.
- The P/E requirement calls for a 5–10-year own-history range, while the implementation accepts a one/two-year range with `low_confidence`; the plan leaves backfill unresolved.

## Test execution

I first confirmed `pytest` was not on `PATH` and system Python lacked pytest, then ran the repository environment’s complete suite:

`/Users/pete/moat/.venv/bin/python -m pytest -q`

Result: **219 passed, 0 failed, 1 skipped, 1 warning, 0 test errors**. The warning is urllib3’s LibreSSL compatibility warning. The skip is the credential-gated live Anthropic citation-response test.

Test quality is insufficient for several material paths:

- No regression fixture covers `LongTermDebt` or total-debt-including-current-maturities.
- No test excludes Real Estate’s invalid FCF/debt metrics.
- A parser test explicitly approves ignored uncited narrative.
- Amendment fallback is tested in preflight but not through real batch submission.
- Valuation integration uses only clean synthetic data; no default-stage eligibility test covers BKNG, REITs, or missing-debt issuers.
- The live API contract is unverified in this run.

## Independent calculation checks

| Area | Requirement / formula | Independent result | Verdict |
|---|---|---|---|
| Owner earnings | NI + D&A − capex − ΔNWC | KO: 13.107 + 1.050 − 2.112 − 7.208 = **$4.837bn** | PASS |
| DCF | Explicit discounted cash flows + Gordon terminal value | Base 100, 10% growth/discount, 2% terminal, two years = **1,475** | PASS |
| Margin of safety | (low intrinsic − price) / low intrinsic; non-positive low is non-numeric | (100−80)/100 = 20%; negative intrinsic returns `None` | PASS |
| FCF yield | FCF / market cap | 50m / 1bn = 5% | PASS |
| EV/EBIT | (market cap + debt − cash) / EBIT | Formula is correct, but AMT uses $3.388bn instead of $37.220bn debt | FAIL |
| Historical P/E | Price / positive EPS; own 5–10-year range | 150 / 5 = 30x, but latest output has 2 zero-year, 2 one-year, and 66 two-year ranges | PARTIAL |
| Quant score | Passed assessable metrics / assessable metrics | AMT’s displayed 5/8 = 62.5% is arithmetically correct but based on wrong debt | FAIL |
| Citation anchors | Every persisted claim must be cited and resolvable | All 2,411 stored anchors matched their file hash, offset, and quote; parser can still discard uncited prose | PARTIAL |

The documented null-NWC-to-zero treatment is an explicit Sprint 4 methodology decision, so I did not count it as a separate defect.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ 100 universe | Current | Implemented | Basic universe tests | PASS | 518 active companies; addendum A1 |
| FTSE 350 and FX normalization | Deferred | Not implemented | N/A | DEFERRED | Addendum A1/A8 |
| SEC annual data, FCF, provenance | Current | Revenue/FCF/provenance implemented | FCF and revenue regressions | PASS | FCF correctly remains null without capex |
| Sector-percentile and quality-score mechanics | Current | Direction-aware percentile; assessable denominator | Unit and synthetic integration tests | PASS | Best of five peers = 100%; score formula verified |
| Total debt / debt-to-FCF | Current | Incomplete debt tag hierarchy | No affected-tag regression | FAIL | AMT total debt is understated by $33.832bn |
| Valid sector metrics for Real Estate | Current | REITs still score FCF/debt | No REIT test | FAIL | AMT/SBAC are passed-screen despite invalid methodology |
| Ranked screen/dashboard | Current | Displays contaminated outputs | No dashboard test | PARTIAL | Current score rows include defective debt/REIT results |
| Citation-enforced qualitative claims | Current | Durable anchors work; parser loses plain uncited text | Parser/persistence tests miss rejection case | PARTIAL | Adversarial response validated at 1.0 coverage |
| Live Anthropic citation contract | Current integration | Opt-in test exists | Skipped | CANNOT VERIFY | `RUN_LIVE_API_TESTS` not enabled |
| Resumable batch workflow | Current | Main path works; amendment path diverges | No combined fallback/submission test | PARTIAL | Preflight fallback succeeds; submission fails |
| Owner-earnings calculation | Current | Correct formula and accepted NWC treatment | Hand-derived and KO tests | PASS | Independent KO calculation |
| Bear/base/bull DCF and margin safety | Current | Correct two-stage DCF and sign guard | Hand-derived/edge tests | PASS | Independent 1,475 calculation |
| FCF yield and EV/EBIT cross-checks | Current | FCF correct; EV/EBIT depends on bad debt input | Clean-input tests only | PARTIAL | Missing debt safely returns unavailable, but partial debt can be wrong |
| P/E versus own historical range | Current | Low-confidence flag exists | Pure-function tests | PARTIAL | All 70 current ranges are below five years |
| Valuation-stage eligibility | Current | Manual `--exclude` only | No default eligibility test | FAIL | Default cloned-data run valued all 91, including BKNG/AMT/SBAC |
| Committee and Investment Brief | Deferred | Stubs | N/A | DEFERRED | Sprint 5 |
| Monitoring | Deferred | Stubs | N/A | DEFERRED | Sprint 6 |

## [HIGH] Debt extraction materially understates leverage

**Location**

[fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:217), [quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:259)

**Requirement**

Debt must reflect total debt relative to positive FCF; debt above 5× FCF fails the absolute floor.

**Observed behaviour**

Extraction only considers non-current and current components. It omits common total-debt concepts including `LongTermDebt` and `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`.

**Why this matters**

Companies can appear lightly leveraged or have debt removed from the score denominator.

**Evidence**

AMT’s latest raw cached 10-K fact reports total debt of **$37.2203bn**. The application stores only **$3.3878bn** current debt. With FCF of **$3.7836bn**, the correct ratio is **9.84×** and fails; the displayed ratio is **0.895×** and passes. A and ADSK have missing stored debt despite raw `LongTermDebt` facts.

**Recommended remediation**

Implement and test a validated total-debt hierarchy; re-ingest, re-screen, and revalue affected companies.

## [HIGH] Real Estate is still ranked on metrics the repository defines as invalid

**Location**

[quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:298)

**Requirement**

The repository identifies REIT FCF/debt metrics as invalid; FFO/AFFO is the appropriate basis.

**Observed behaviour**

Only Financials receive `not_applicable` exclusions. Real Estate still receives all eight metrics.

**Why this matters**

The dashboard and valuation stage publish plausible but methodologically invalid REIT results.

**Evidence**

AMT passes the screen at 62.5%; SBAC passes at 50.0%. SBAC’s FCF metric passes and contributes to its threshold result despite the documented invalidity.

**Recommended remediation**

Until a valid REIT configuration exists, mark the invalid metrics `not_applicable` and exclude the sector through the existing coverage rule.

## [HIGH] Default valuation runs bypass known-invalid-candidate exclusions

**Location**

[run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:595), [run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:647)

**Requirement**

Known-invalid inputs must not be presented as normal current-scope valuations.

**Observed behaviour**

The 21 exclusions are optional CLI arguments. A read-only clone of the real database run without `--exclude` valued all 91 passed-screen companies.

**Why this matters**

BKNG, AMT, SBAC, and debt-gap issuers receive normal-looking valuation rows by default.

**Evidence**

The cloned run wrote 546 rows. BKNG showed EV/EBIT **0.96×**, FCF yield **130.8%**, and P/E **1.29×**, despite the documented price/share inconsistency. AMT and SBAC received FCF-based DCF/cross-check outputs.

**Recommended remediation**

Persist eligibility/invalidity state and enforce it by default; store explicit unavailable/excluded reasons rather than relying on manual flags.

## [MEDIUM] Citation coverage can silently discard uncited assertions

**Location**

[parser.py](/Users/pete/moat/moat/analysis/parser.py:261), [test_analysis_parser.py](/Users/pete/moat/tests/test_analysis_parser.py:223)

**Requirement**

Every AI-generated claim must be cited; uncited prose must not be silently accepted.

**Observed behaviour**

Plain uncited text without `CLAIM:` is ignored, not rejected.

**Why this matters**

The system can report 100% citation coverage while dropping substantive model output.

**Evidence**

An adversarial four-section response containing “This is an uncited material assertion” returned `is_valid=True`, four persisted claims, and coverage 1.0.

**Recommended remediation**

Reject substantive non-protocol text or parse it as an uncited asserted claim so validation fails.

## [MEDIUM] Batch submission does not reuse amendment-fallback resolution

**Location**

[persist.py](/Users/pete/moat/moat/analysis/persist.py:517), [caller.py](/Users/pete/moat/moat/analysis/caller.py:357)

**Requirement**

A part-III-only 10-K/A must fall back to the usable original 10-K in all analysis paths.

**Observed behaviour**

Preflight resolves the original filing, but `submit_batch()` reselects the amendment and re-runs extraction.

**Why this matters**

One amendment stub can abort an otherwise valid batch.

**Evidence**

A temporary reproduction produced `preflight accession: 0000-01-000001`, followed by `ValueError: full_fallback for 0000-02-000002 is implausibly short`.

**Recommended remediation**

Pass the preflight-resolved accession, content, document map, and prompt hash into submission; add an end-to-end regression.

## [MEDIUM] Shipped P/E ranges do not meet the stated historical horizon

**Location**

[prices.py](/Users/pete/moat/moat/ingest/prices.py:73), [engine.py](/Users/pete/moat/moat/valuation/engine.py:593), [app.py](/Users/pete/moat/moat/dashboard/app.py:133)

**Requirement**

P/E should be compared with the company’s own 5–10-year historical range, with inadequate history clearly surfaced.

**Observed behaviour**

The ten-year default applies only to new histories. Existing histories were incrementally refreshed from their prior two-year window, and the application still presents a one/two-year range.

**Why this matters**

The P/E cross-check is not currently useful as the promised historical valuation range.

**Evidence**

Latest output: 70/70 P/E ranges are low confidence—66 have two years, two have one year, and two have none.

**Recommended remediation**

Backfill price history or mark P/E-range valuation unavailable below the five-year minimum.

## Judge Verdict

### Test execution
- Test command(s): `pytest -q` (not found); `python3 -m pytest -q` (pytest unavailable); `.venv/bin/python -m pytest -q` (full suite; 1 urllib3 LibreSSL warning)
- Passed: 219
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 6
- PARTIAL: 5
- FAIL: 3
- NOT IMPLEMENTED: 0
- DEFERRED: 3
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 3
- Medium: 3
- Low: 0

### Overall confidence
94%

### Overall verdict
FAIL

### Top 5 actions
1. Fix total-debt extraction, then re-ingest, re-screen, and revalue affected companies.
2. Enforce REIT and known-invalid valuation exclusions by default.
3. Reject uncited non-protocol AI prose.
4. Make batch submission reuse the amendment-fallback preflight result.
5. Backfill price history or withhold historical P/E ranges below five years.
