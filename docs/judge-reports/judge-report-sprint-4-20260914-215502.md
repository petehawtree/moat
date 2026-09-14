## Independent audit result: FAIL

Current scope is Sprints 1–4. The original PRD’s US+UK scope is overridden by the addendum’s US-only decision; Sprint 5 committee/brief and Sprint 6 monitoring are explicitly deferred. Formal PRD reviewed: :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"}

The core valuation arithmetic is sound. However, the normal valuation command can publish valuations for candidates the repository already knows are methodologically invalid. That is a current-scope correctness failure, not a penalty for deferred sector-specific work.

The requirements are ambiguous in two places:

- PRD §6 asks for a 5–10 year P/E comparison, while Sprint 4 accepts thin-history output if labelled. All current results are labelled low-confidence, but none has five usable years.
- Documentation says valuation uses the “same 21-ticker” exclusion as AI analysis, but the latest 70-company valuation run includes GOOGL and excludes BKNG instead.

### Test execution

I first found `pytest` absent from PATH, then identified and used the repository virtual environment.

- Full suite: `.venv/bin/pytest`
- Result: 220 collected; 219 passed, 1 skipped, 0 failed.
- Warning: one `urllib3` LibreSSL/OpenSSL compatibility warning.
- Separate read-only Streamlit `AppTest` against the existing database: 0 exceptions; it emitted five Streamlit deprecation warnings for `use_container_width`.

### Independent financial verification

Using live AAPL data from the latest valuation run:

| Calculation | Independent result | Stored result |
|---|---:|---:|
| Owner earnings FY2025 | $112.010bn + $11.698bn − $12.715bn − $0 = **$110.993bn** | $110.993bn in DCF series |
| Base DCF/share | **$222.217159115110** | $222.217159115110 |
| FCF yield | **1.981041%** | 1.981041% |
| EV/EBIT | **37.883161×** | 37.883161× |
| Current P/E | **44.540213×** | 44.540213× |

The DCF independently used the documented 10-year projection, 9.5% discount rate, 2.5% terminal growth, and stored 17.1737% base growth assumption. Dollar inputs, share count, and per-share output were consistent.

The sign guards also behave correctly: a naive margin of safety for intrinsic value −$50 and price $10 would misleadingly yield +120%; implementation returns no numeric verdict. Negative EBIT likewise returns unavailable rather than a misleading negative multiple.

Citation enforcement also held in the stored live data: 2,176 asserted claims, zero without a citation; 2,411 joined citation anchors had zero missing files and zero quote-offset mismatches; all 288 current analysis rows had 100% claim coverage.

### Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US-only S&P 500 + NASDAQ-100 ingestion, SEC provenance, price ingestion | Current | Implemented | Offline extraction, universe, database tests | PASS | Addendum A1/A4/A7; live DB contains 505 companies with fundamentals |
| Sector-relative eight-metric screen and quality score | Current | Implemented, but known debt/REIT inputs contaminate candidates | Good synthetic metric tests; no protection test for affected live candidates | PARTIAL | [quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:292), A17 |
| Citation-enforced AI analysis | Current | Implemented with claim/citation validation and atomic persistence | Parser, persistence, batch, reanchor tests; live anchor audit | PASS | 0 asserted claims without citations; 0 persisted offset mismatches |
| D&A/NWC ingestion, flag-don’t-guess, and provenance | Current | Values and flags implemented; source accession is only revenue/net-income anchor | D&A tier and NWC tests; no per-input provenance test | PARTIAL | [fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:715) |
| Owner Earnings DCF, scenarios, conservative margin of safety | Current | Implemented correctly | Hand-derived unit tests, integration tests, independent live recomputation | PASS | [engine.py](/Users/pete/moat/moat/valuation/engine.py:36) |
| FCF yield, EV/EBIT, and own-history P/E cross-check | Current | Formulae correct; P/E history is only 0–2 years in current data | Strong pure-function tests; no current-data history adequacy test | PARTIAL | [engine.py](/Users/pete/moat/moat/valuation/engine.py:593), Sprint 4 lines 87–93 |
| Value every valid passed-screen company, otherwise persist an explicit reason | Current | Unsafe candidates are only skipped when caller supplies exclusions | No valuation-stage exclusion/default-path test | FAIL | [run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:173), A17/A20 |
| Valuation dashboard, including negative-bear rendering | Current | Implemented and rendered successfully | No dashboard test in pytest; independently AppTest-checked | PASS | [app.py](/Users/pete/moat/moat/dashboard/app.py:154) |
| FTSE 350 / FX normalization | Deferred | Not implemented | N/A | DEFERRED | Addendum A1 |
| Investment Committee and Investment Brief | Deferred | Stubs only | N/A | DEFERRED | Sprint 5 |
| Monitoring/watchlist | Deferred | Stubs only | N/A | DEFERRED | Sprint 6 |

## [HIGH] Default valuation execution publishes known-unreliable results

**Location**

[scripts/run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:173), [dashboard/app.py](/Users/pete/moat/moat/dashboard/app.py:174)

**Requirement**

Sprint 4 requires every current passed-screen company to receive a valid valuation or an explicit persisted reason it cannot. Known-invalid candidates must not receive plausible valuation output.

**Observed behaviour**

`run_valuation_stage()` values every `passed_screen=1` ticker unless the caller manually supplies `--exclude`. The dashboard itself instructs users to run `python scripts/run_pipeline.py --from-stage valuation` without exclusions.

The live database proves this path was used: runs `20260914T202556Z` and `20260914T202859Z` valued all 91 candidates, including A, ADSK, AMT, SBAC, BKNG, and GOOGL. AMT was given EV/EBIT 16.80× despite its stored debt being $3.39bn where repository material identifies roughly $37.2bn. BKNG was given EV/EBIT 0.96× despite the documented price/share-basis anomaly.

**Why this matters**

The known debt-tag and REIT-methodology work is deferred, but the resulting invalid values are not safely deferred: callers can generate and view them now. This contaminates current valuation output with plausible but incorrect financial results.

**Evidence**

A17 explicitly identifies 18 null-debt candidates, AMT’s materially understated debt, and AMT/SBAC’s invalid REIT metrics. A20 identifies BKNG’s cross-source price/share inconsistency. The current code makes the exclusion opt-in, while the latest correct 70-company run only exists because exclusions were manually supplied.

**Recommended remediation**

Enforce the documented exclusion set by default until source defects are fixed, persist an unavailable/excluded valuation row with a reason, and add integration tests for default eligibility and the no-exclusion CLI path.

## [MEDIUM] P/E “5–10 year” cross-check is not currently usable

**Location**

[engine.py](/Users/pete/moat/moat/valuation/engine.py:269), [app.py](/Users/pete/moat/moat/dashboard/app.py:241)

**Requirement**

PRD §6 calls for P/E against the company’s own historical valuation range; Sprint 4 describes a 5–10 year range, with thin history visibly qualified.

**Observed behaviour**

The code correctly labels fewer than five years as low confidence. But all 70 current dashboard valuations are low confidence; the broader 91-company run had 83 companies with two years, five with one, and three with zero usable years.

**Why this matters**

The label prevents concealment, but the advertised supporting cross-check cannot presently serve its intended purpose for almost the entire current universe.

**Evidence**

The dashboard itself reports 70/70 low-confidence results. Existing tests use a synthetic six-year range and do not test the actual incremental-price-history/backfill state.

**Recommended remediation**

Decide and implement the explicitly open historical-price backfill policy, then add an integration test asserting actual usable-history coverage and clear display of the low/high historical P/E range.

## [MEDIUM] D&A/NWC values lack input-specific provenance

**Location**

[fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:715)

**Requirement**

A16.2 requires D&A and working-capital inputs to carry source, confidence, and accession provenance.

**Observed behaviour**

D&A and NWC are independently extracted, but the stored `accession_number` and `filed` fields are selected from the revenue/net-income anchor, not the D&A or NWC fact.

**Why this matters**

A value may be traceable through the raw cache, but its persisted record does not identify the filing and tag that supplied that valuation input. This weakens auditability of the primary DCF inputs.

**Evidence**

Lines 715–722 select the row anchor before separately reading D&A/NWC. Tests validate values and flags but do not assert their source accession/tag.

**Recommended remediation**

Persist per-input provenance or a structured provenance map for D&A and NWC, and test a case where their source filing differs from the revenue anchor.

## [LOW] Negative ending revenue can crash scenario growth derivation

**Location**

[engine.py](/Users/pete/moat/moat/valuation/engine.py:321)

**Requirement**

Missing, zero, and non-positive financial inputs must produce an explicit safe outcome rather than a malformed valuation or pipeline failure.

**Observed behaviour**

`historical_revenue_cagr()` filters truthy revenue rather than positive revenue. A positive start and negative ending revenue over two or more years produces a complex CAGR; `scenario_growth_rates()` then raises `TypeError`.

**Why this matters**

No current 70-company valuation hit this path, but a future qualifying issuer can abort valuation processing instead of receiving an unavailable/flat-growth result.

**Evidence**

Ad-hoc check: revenue 100 in 2023 and −10 in 2025 raises `TypeError: '<' not supported between instances of 'complex' and 'float'`. Existing tests cover missing revenue and a negative one-year CAGR only, not a negative endpoint over multiple years.

**Recommended remediation**

Require positive endpoint revenue before CAGR calculation and add regression tests for zero and negative ending revenue across multi-year spans.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/pytest`
- Passed: 219
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 4
- PARTIAL: 3
- FAIL: 1
- NOT IMPLEMENTED: 0
- DEFERRED: 3
- CANNOT VERIFY: 0

### Findings
- Critical: 0
- High: 1
- Medium: 2
- Low: 1

### Overall confidence
86%

### Overall verdict
FAIL

### Top 5 actions
1. Make known-invalid valuation exclusions enforced by default and persist explicit exclusion reasons.
2. Fix debt extraction and Real Estate screening/valuation applicability before restoring those candidates.
3. Backfill price history and establish a measurable minimum P/E-history coverage target.
4. Store input-specific D&A/NWC provenance.
5. Add valuation-stage eligibility, dashboard, and negative-revenue regression tests.
