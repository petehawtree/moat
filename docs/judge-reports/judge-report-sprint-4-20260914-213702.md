The current-scope implementation fails: the valuation pipeline can publish materially wrong results from inputs the repository already knows are invalid.

Original PRD reviewed: :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"}.

Scope findings: Sprint 4 is current, delivered scope. Sprint 5 investment briefs/committee and Sprint 6 monitoring are explicitly deferred. The addendum properly overrides the original US+UK universe with US-only scope. There is an unresolved conflict: Sprint 4’s plan says REITs are already excluded, while A14 says only Financials are excluded and the code values AMT/SBAC.

Full suite: `.venv/bin/python -m pytest -ra` collected 219 tests: 218 passed, 0 failed, 1 skipped. The skip is the opt-in live Anthropic citation-shape call. One unrelated environment warning was emitted: urllib3/OpenSSL LibreSSL compatibility.

Independent calculation checks:

| Area | Independent result | Assessment |
|---|---|---|
| Owner earnings | KO: 13.107bn + 1.050bn − 2.112bn − 7.208bn = 4.837bn | Formula/sign correct |
| DCF | Two-year example: PV = 1,475 using 10% growth, 10% discount, 2% terminal growth | Formula correct |
| Margin of safety | `(100−80)/100 = 20%`; non-positive intrinsic value returns `None` | Correct sign guard |
| FCF yield | 50m / 1bn = 5% | Formula correct |
| EV/EBIT | `(800+200−100)/100 = 9x` | Formula correct only with valid debt |
| P/E | 150 / 5 = 30x | Formula correct; history coverage inadequate |

## [HIGH] Missing debt is silently valued as zero

**Location**

`moat/ingest/fundamentals_edgar.py:217-218,703-705`; `moat/valuation/engine.py:207-229`.

**Requirement**

Debt/FCF screening and EV/EBIT must use valid total debt, or report the metric unavailable rather than inventing a zero-debt result.

**Observed behaviour**

Extraction omits `LongTermDebt` and `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`. `ev_ebit()` then converts missing debt to `0.0`.

**Why this matters**

This understates leverage and EV/EBIT, while also allowing companies to pass screening with debt unassessed.

**Evidence**

The live database has 18 passing companies with no latest stored debt. SEC cached facts show:

- AMT: stored debt $3.388bn; reported total debt including current maturities $37.220bn.
- AMT displayed EV/EBIT: 16.80x. Independently recalculated: `(79.478bn + 37.220bn − 1.475bn) / 4.846bn = 23.78x`.
- A: stored debt `NULL`; SEC tag reports $3.050bn. Displayed EV/EBIT is 27.60x rather than 29.66x.

The existing test explicitly asserts that missing debt defaults to zero, so it reinforces the defect.

**Recommended remediation**

Expand and validate debt-tag hierarchy, distinguish unknown from zero, re-ingest and re-screen affected companies, and make EV/EBIT unavailable when debt is incomplete.

## [HIGH] Default valuation run admits known-invalid companies

**Location**

`scripts/run_pipeline.py:173-211,647-649`.

**Requirement**

Current-scope valuation must not publish plausible but incorrect results for known-invalid price/share inputs or sectors outside the methodology.

**Observed behaviour**

Exclusions are optional command-line input. A disposable run of `run_valuation_stage()` with the real database and no exclusions valued 91/91 companies.

**Why this matters**

Known-invalid companies are presented as normal valuations:

- BKNG: current P/E 1.29x and FCF yield 130.8%, caused by a known price/share-basis mismatch.
- AMT and SBAC: receive FCF-based DCF, FCF-yield and debt metrics although repository documentation says REIT FFO/AFFO methodology is required.

**Evidence**

The repository documents BKNG as excluded, and Sprint 4 says 90/91 were valued. The normal CLI path still processed BKNG, AMT, SBAC, and all debt-gap candidates. This meets the stated exception for deferred work: it contaminates current functionality with plausible-looking wrong results.

**Recommended remediation**

Persist eligibility/validation status and enforce it in the valuation stage by default. Record excluded valuations as unavailable with a reason; do not rely on manual `--exclude` arguments.

## [MEDIUM] Historical P/E requirement is not met for the shipped data

**Location**

`moat/ingest/prices.py:73-85`; `moat/valuation/engine.py:575-596`.

**Requirement**

P/E must be compared with the company’s own 5–10 year historical range, with insufficient coverage clearly surfaced.

**Observed behaviour**

Changing the new-fetch default to ten years does not backfill existing histories. The method still produces a range from one or two observations.

**Why this matters**

The dashboard labels the method as a 5–10 year cross-check, but it is not currently useful as one.

**Evidence**

In the latest 90-company valuation run, all 90 P/E ranges are low confidence: 82 have two years, five have one year, and three have zero.

**Recommended remediation**

Backfill price history or mark the P/E-range valuation unavailable below the defined five-year minimum.

## [MEDIUM] DCF coverage is not surfaced for very short owner-earnings histories

**Location**

`moat/valuation/engine.py:513-550`; `moat/dashboard/app.py:220-224`.

**Requirement**

The DCF’s trailing owner-earnings smoothing should be transparent enough to distinguish a meaningful history from thin evidence.

**Observed behaviour**

Any non-empty owner-earnings series produces a normal DCF without a coverage flag.

**Why this matters**

A one-year or two-year average is materially less reliable than the intended multi-year smoothing.

**Evidence**

Among 83 computed DCFs, one uses one year, one uses two, one uses three, and three use four years. Only seven are explicitly unavailable.

**Recommended remediation**

Require a documented minimum history or add a visible low-confidence state and prevent thin DCFs from being treated as comparable to ten-year cases.

## [LOW] Valuation tests miss real-stage input validity

**Location**

`tests/test_valuation_engine.py`; `tests/test_run_pipeline_exclude.py`.

**Requirement**

Tests should detect incorrect production valuation inputs and default-stage behaviour.

**Observed behaviour**

Valuation integration tests use a clean synthetic company. The exclusion tests cover only AI analysis, not valuation. A unit test codifies missing debt as zero.

**Why this matters**

The suite passes while the default real-data run publishes known-invalid valuation rows.

**Evidence**

The disposable default-run check reproduced 546 rows for 91 companies, including BKNG, AMT, SBAC, A and ADSK.

**Recommended remediation**

Add real-fact regression fixtures for debt tags, price/share-basis mismatch, REIT exclusion, and default valuation-stage eligibility.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ 100 universe; UK/FX deferred | Current | US-only configuration implemented | Universe tests | PASS | Addendum A1 overrides original PRD |
| Annual fundamentals, provenance and refresh | Current | SEC extraction and provenance implemented, but debt hierarchy incomplete | Offline extraction tests omit affected debt tags | PARTIAL | 18 current passing companies have missing latest debt |
| Sector-relative quantitative screening and assessable-metric quality score | Current | Percentile and coverage formulas implemented | Good synthetic coverage | FAIL | Debt defects and REIT methodology contaminate current screened candidates |
| Citation-enforced qualitative analysis | Current | Parser, anchoring and persistence are extensively tested | Live API shape test skipped | CANNOT VERIFY | Native API integration was not independently exercised |
| Owner-earnings formula and source treatment | Current | Net income + D&A − capex − ΔNWC, with documented zero fallback for absent NWC | Hand-derived and real-KO fixture tests | PASS | Independent KO result: $4.837bn |
| Bear/base/bull DCF and margin-of-safety guard | Current | Two-stage Gordon DCF, fixed discount rate, non-positive guard | Hand-derived DCF and edge tests | PASS | Independent two-year result: 1,475 |
| FCF yield and EV/EBIT cross-checks | Current | Formulas implemented | Unit tests only clean/missing-as-zero debt inputs | FAIL | EV/EBIT materially understated for AMT and debt-gap companies |
| P/E against own 5–10 year historical range | Current | Low-confidence flag exists | Pure-function tests only | PARTIAL | 90/90 shipped rows below five years |
| Valuation persistence and dashboard | Current | Six rows/company and dashboard rendering implemented | Synthetic persistence tests; no valuation-stage eligibility test | PARTIAL | Default run values known-invalid candidates |
| Investment committee and one-page brief | Deferred Sprint 5 | Stubs only | Not applicable | DEFERRED | README sprint plan |
| Monitoring/watchlist alerts | Deferred Sprint 6 | Stubs only | Not applicable | DEFERRED | README sprint plan |
| FTSE 350, FX normalization | Deferred | Not implemented | Not applicable | DEFERRED | Addendum A1 |

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/python -m pytest -ra`
- Passed: 218
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 3
- PARTIAL: 3
- FAIL: 2
- NOT IMPLEMENTED: 0
- DEFERRED: 3
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 2
- Medium: 2
- Low: 1

### Overall confidence
91%

### Overall verdict
FAIL

### Top 5 actions
1. Fix debt extraction and prohibit missing debt from becoming zero.
2. Enforce persisted valuation exclusions/validity checks by default.
3. Backfill price history before presenting historical P/E ranges.
4. Flag or withhold DCFs with insufficient owner-earnings history.
5. Add real-data regression and default-stage integration tests.
