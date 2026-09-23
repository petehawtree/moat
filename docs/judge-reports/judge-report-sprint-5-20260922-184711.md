I found one High and two Medium current-scope defects. The financial formulas and full automated suite are otherwise strong, but the Sprint 5 brief can present evidence and valuation data from runs other than those used for its recommendation.

I inspected the original PRD :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"}, addendum, all sprint material, glossary, allowlist, implementation, tests, and live local database.

Scope: Sprints 0–4 are shipped; Sprint 5 is the active, explicitly partial pilot. Sprint 6 monitoring and UK/FTSE coverage are deferred. The original PRD calls monitoring an MVP feature, while the later roadmap and Sprint 5 plan defer it to Sprint 6; the addendum/active plans resolve this in favour of deferral. The apparent A3 “every claim cited” conflict is also settled: uncited Quality/Bear statements may be shown only with an explicit warning; valuation statements are grounded by visible quantitative context.

Known issues #1–#6 and #8 were checked and not counted. In particular, the negative-owner-earnings DCF scenario inversion was reproduced in the live DB for ABNB/CRWD/EIX/PEG/UBER, but it matches allowlisted GitHub #5.

Test execution: `.venv/bin/python -m pytest -ra` collected 304 tests: 303 passed, 1 skipped, 0 failed. The skip is the intentional live Anthropic citation-shape check; one LibreSSL/urllib3 warning was emitted.

Financial validation:

| Area | Independent check |
|---|---|
| Quant screen | Implements sector-relative direction-aware percentile plus absolute floor. Missing data is excluded from the denominator; debt with non-positive FCF is a real fail. Tests cover direction, floor-only fallback, split handling, and unavailable status. |
| Quality score | Correctly computes `100 × passed / assessed`, requiring at least 6 assessed metrics and score ≥50. |
| Owner earnings / DCF | Live AAPL FY2025 owner earnings independently reconciles to `$112.010bn + $11.698bn − $12.715bn = $110.993bn`. Using the stored 10-year average `$76.4462bn`, 17.1737% base growth, 9.5% discount rate, and 2.5% terminal growth yields `$222.217159/share`, exactly matching the persisted base DCF. |
| Supporting valuation | AAPL FCF yield independently computes to `98.767bn / 4.9856tn = 1.9810%`; EV/EBIT using `$90.678bn` debt, `$35.934bn` cash and `$133.050bn` EBIT gives `37.8832x`, matching stored results. |
| Committee score | Correctly applies PRD weights and inverts risk once: for scores 80/70/60/50/40 and risk 90, expected score is 58.5. Boundary and severity-cap tests are present. |

The pure-calculation tests are materially better than average: they use hand-derived expectations and cover nulls, signs, non-positive denominators, stale DCF inputs, and status boundaries. Gaps remain around multi-run brief provenance and cache invalidation; those tests can all pass while the defects below remain.

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ 100 universe, provenance and confidence | Shipped | Implemented | Extraction/provenance tests; live AAPL verification | PASS | A1/A4/A11; `scripts/verify.py AAPL` |
| Eight-metric sector-relative screen | Shipped | Implemented | Direction, floor, split, unavailable, end-to-end tests | PASS | A2/A9/A14; `quant_screen.py` |
| Quality roll-up and coverage guard | Shipped | Implemented | Assessed-denominator and 6/8 threshold tests | PASS | A13/A14; `quality_score.py` |
| DCF, margin-of-safety guard, FCF yield, EV/EBIT and P/E range | Shipped | Implemented | Hand-derived unit and persistence tests | PASS | A16/A20; independent AAPL reconciliation |
| Citation-backed AI analysis against the live API | Shipped | Implemented | Parser/persistence tests pass; live API test skipped | CANNOT VERIFY | `test_api_citation_shape.py` requires opt-in paid call |
| PRD §8 weighted committee score and C8 exclusions | Sprint 5 | Implemented | Score, severity, persistence, stage-exclusion tests | PASS | `committee.py`, `test_committee_score.py` |
| Committee cache invalidates for every prompt input | Sprint 5 | Incomplete | No peer-group/company-header invalidation test | FAIL | A5; cache omits rendered fields |
| Brief uses one coherent upstream evidence/version set | Sprint 5 | Incomplete | Dashboard tests use only one upstream run | FAIL | C5–C7; latest-run queries replace scored inputs |
| Every surfaced inference has inline support or explicit caveat | Sprint 5 | Incomplete | No test for monitor items retaining/rendering references | FAIL | C2/C7; monitor list discards statement refs |
| Full 69-company committee completion and pilot-validated thresholds | Sprint 5 pilot | Deliberately partial | Pilot/documentation only | PARTIAL | README and known-issues design decision |
| Watchlist/monitoring | Sprint 6 | Stubbed/deferred | N/A | DEFERRED | PRD roadmap; Sprint 5 “Out” |
| FTSE 350 / FX normalisation | Later | Deferred | N/A | DEFERRED | A1 |

## [HIGH] Investment Brief can mix a prior recommendation with newer, unscored evidence

**Location**

[committee.py](/Users/pete/moat/moat/committee/committee.py:428), [schema.sql](/Users/pete/moat/moat/db/schema.sql:313), and [app.py](/Users/pete/moat/moat/dashboard/app.py:188).

**Requirement**

Sprint 5 C5–C7 requires one Investment Brief that joins the AI analysis, citations, quant screen, and valuation inputs that support the committee verdict.

**Observed behaviour**

`run_committee()` reads explicit `valuation_run_id` and `quality_run_id`, but persists neither. The dashboard later fetches the newest non-failed quant and valuation runs, plus current AI analysis, independently of the selected committee verdict.

**Why this matters**

After an upstream refresh or a separate valuation/AI run, a user can see a prior Investigate/Watch/Reject decision beside newer DCF, margin-of-safety, screen, moat evidence, and filing links that the personas never saw. This creates a plausible but incorrect evidence trail for an investment recommendation.

**Evidence**

The dashboard calls latest-run queries at `app.py:193–203` and `216–226`; source filings use current AI rows at `app.py:85–99`. `committee_verdicts` has no fields for the source quality, valuation, or AI-analysis run IDs. Dashboard tests seed only one run and cannot expose divergence.

**Recommended remediation**

Persist immutable upstream run/version identifiers with each verdict, including AI claim-source runs. Render every brief section from those identifiers; mark the brief stale when a newer upstream input exists.

## [MEDIUM] “Key things to monitor” silently loses citation references

**Location**

[committee.py](/Users/pete/moat/moat/committee/committee.py:498) and [app.py](/Users/pete/moat/moat/dashboard/app.py:702).

**Requirement**

Sprint 5 C2 requires literal source support alongside each inference in a brief, or the established explicit uncited-statement warning.

**Observed behaviour**

`_stitch_key_things_to_monitor()` serialises only `ParsedStatement.text`, discarding `refs`. The dashboard renders those strings as plain bullets, with neither citation expanders nor an uncited warning.

**Why this matters**

The brief duplicates Bear Analyst claims as monitoring guidance while removing the evidence controls that apply in the Bear case section.

**Evidence**

In the live AAPL pilot, 6 of 8 Bear statements have claim IDs and stored citations, but all 8 corresponding monitor items contain text only. No test asserts that monitor items retain or render references.

**Recommended remediation**

Persist monitor items as objects containing text and claim IDs, then reuse the persona citation renderer. Explicitly flag items with no references.

## [MEDIUM] Committee cache omits fields rendered in its prompt

**Location**

[committee.py](/Users/pete/moat/moat/committee/committee.py:247) and [prompt.py](/Users/pete/moat/moat/committee/prompt.py:151).

**Requirement**

A5 requires committee analysis to rerun when its source inputs change.

**Observed behaviour**

The cache key omits `quant_scores.sector_peer_group`, although the prompt renders it whenever percentile data exists. It also omits company name and sector from the prompt header.

**Why this matters**

A sector reassignment can alter persona context while returning a cached verdict made under the old sector. This is the same stale-input class that the cache is intended to prevent.

**Evidence**

An ad-hoc check produced the identical cache hash when `sector_peer_group` changed from `Information Technology` to `Health Care` with all hashed fields held constant. Existing cache tests mutate valuation figures, quant values, and confidence, but not these rendered fields.

**Recommended remediation**

Build the cache key from a canonical representation of the complete prompt context, or include every rendered company and quant field explicitly. Add regression tests for sector-peer-group and company-sector changes.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/python -m pytest --collect-only -q`; `.venv/bin/python -m pytest -ra`
- Passed: 303
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 5
- PARTIAL: 1
- FAIL: 3
- NOT IMPLEMENTED: 0
- DEFERRED: 2
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 1
- Medium: 2
- Low: 0

### Overall confidence
91%

### Overall verdict
FAIL

### Top 5 actions
1. Persist and render the exact upstream run/version set used for every committee verdict.
2. Retain and render citations for “Key things to monitor,” with explicit warnings for unsupported items.
3. Derive the committee cache key from the complete rendered context.
4. Add multi-run dashboard and cache-invalidation regression tests.
5. Run the opt-in live citation API contract test in a controlled environment.
