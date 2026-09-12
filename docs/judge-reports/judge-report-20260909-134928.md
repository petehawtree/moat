## Audit outcome

**FAIL.** The current quant screen has verified false positives from incomplete debt-tag extraction, and it still publishes scores for REITs using metrics the repository itself says are invalid for that sector. Deferred valuation, committee, monitoring, UK/FX, and the authorised 90-company AI run were not counted as failures.

Current scope is Sprint 3.1 items 1–5: US screening plus citation-enforced 10-K analysis. The PRD’s broader MVP is superseded in relevant areas by the addendum and sprint plans: FTSE/FX is deferred; Sprint 4+ is planned; the 90-company run requires separate approval.

Conflict/ambiguity: the original PRD targets ~850 US/UK names, while the addendum explicitly narrows the active scope to S&P 500 + NASDAQ 100. The addendum governs. Real Estate FFO/AFFO handling is not scheduled as shipped work, but the current screen still emits FCF/debt scores known to be invalid; under the requested rules, that is a current-scope defect.

### Test execution

- `.venv/bin/python -m pytest -q -ra`  
  Result: **154 passed, 1 failed, 1 warning**. The failed live Anthropic citation-shape test could not connect because network access is blocked.
- `ANTHROPIC_API_KEY='' .venv/bin/python -m pytest -q -ra`  
  Result: **154 passed, 1 skipped, 1 warning**. The skip was the intended credential-gated live test.
- Warning: urllib3 LibreSSL compatibility warning.

The live test is not safely isolated: importing project configuration loads `.env`, so a bare pytest run can activate the paid/network test when a developer has a key configured.

### Independent financial checks

| Area | Independent verification |
|---|---|
| FCF, FCF margin, debt/FCF | Apple FY2025: $111.482bn OCF − $12.715bn capex = **$98.767bn FCF**; $98.767bn / $416.161bn = **23.7329%**; $90.678bn debt / $98.767bn = **0.9181x**. All match stored values. |
| ROIC | Apple: NOPAT = $133.050bn × 79% = $105.110bn; invested capital = $90.678bn + $73.733bn − $35.934bn = $128.477bn; ROIC = **81.8119%**, matching storage. It is correctly tagged medium confidence due to the assumed tax rate. |
| Split-adjusted dilution | Walmart: 2008 shares 4.072bn × 3 split factor = 12.216bn on current basis; `(8.022 / 12.216)^(1/18) − 1 = −2.23%`, matching stored −2.2287%. |
| Real dilution edge case | TKO: `(194.011m / 82.808m)^(1/2) − 1 = 53.07%`; stored 53.0654%, correctly failing. |
| Quality roll-up | Independently recomputed every latest-run company from pass/fail statuses: **0 roll-up mismatches** across 505 companies. |
| Debt extraction | Failed materially; examples below show known SEC debt facts omitted or understated. |

### Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US-only S&P 500 + NASDAQ 100 universe | Current | Implemented | Synthetic dedupe test; local DB has 518 companies | PASS | [addendum](/Users/pete/moat/docs/PRD_ADDENDUM.md:8), [universe](/Users/pete/moat/moat/ingest/universe.py:106) |
| Annual EDGAR fundamentals, provenance, confidence | Current | Implemented but debt extraction incomplete | Tag-switch, duration, provenance tests; no debt-variant regressions | PARTIAL | [extractor](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:168) |
| Eight-metric sector-relative screen | Current | Produces false results when debt is absent/understated | Mostly synthetic metric tests | FAIL | [screen](/Users/pete/moat/moat/screen/quant_screen.py:200) |
| Distinguish fail, unavailable, and not applicable; score assessable metrics | Current | Implemented | Regression tests; 0 local roll-up mismatches | PASS | [quality score](/Users/pete/moat/moat/quality/quality_score.py:14) |
| Do not publish invalid sector metrics | Current | Financials excluded; Real Estate remains scored on invalid FCF/debt metrics | No REIT applicability test | FAIL | [addendum](/Users/pete/moat/docs/PRD_ADDENDUM.md:742), [screen config](/Users/pete/moat/moat/screen/quant_screen.py:298) |
| Ranked dashboard with per-metric explanations and confidence | Current | Renderer is present, but displays affected scores | No dashboard test | PARTIAL | [dashboard](/Users/pete/moat/moat/dashboard/app.py:85) |
| AI claims stored only with resolving citations | Current pilot | Exact write-path works, but uncited prose can be silently discarded and coverage remains 1.0 | Parser tests encode the incorrect ignore behavior | PARTIAL | [parser](/Users/pete/moat/moat/analysis/parser.py:261) |
| Durable citation recall and re-anchoring | Current | Exact anchors work; moved/fuzzy matching ignores saved context | Re-anchor tests omit duplicate-quote ambiguity | FAIL | [recall](/Users/pete/moat/scripts/cite.py:302) |
| Batch persistence, refresh, and cache lifecycle | Current | Implemented | Batch/fetcher/persistence unit tests | PASS | [Sprint 3.1](/Users/pete/moat/docs/sprints/sprint-3-1.md:15) |
| Live API citation-response compatibility | Current | Test exists but could not execute | Live test blocked by network | CANNOT VERIFY | [live test](/Users/pete/moat/tests/test_api_citation_shape.py:22) |
| Remaining 90-company AI run | Deferred | Held for separate approval | N/A | DEFERRED | [Sprint 3.1](/Users/pete/moat/docs/sprints/sprint-3-1.md:120) |
| DCF, valuation methods, margin of safety | Sprint 4 | Intentional stubs | N/A | DEFERRED | [Sprint 4 plan](/Users/pete/moat/docs/sprints/sprint-4-plan.md:1) |
| Committee and investment brief | Sprint 5 | Intentional stub | N/A | DEFERRED | [PRD](/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf) |
| Watchlist and monitoring | Sprint 6 | Intentional stub | N/A | DEFERRED | [README](/Users/pete/moat/README.md:111) |

## [HIGH] Debt extraction creates false screen passes

**Location**

[fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:217), [fundamentals_edgar.py](/Users/pete/moat/moat/ingest/fundamentals_edgar.py:650).

**Requirement**

Debt must be total debt / latest FCF, with a 5x absolute ceiling and sector-relative comparison.

**Observed behaviour**

The candidate list omits common debt concepts including `LongTermDebt` and `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`.

In the current local run (`20260909T124824Z`):

- Agilent (`A`) stores debt as NULL despite its cached SEC FY2025 10-K reporting `LongTermDebt = $3.050bn`. Its FCF is $1.152bn, so debt/FCF is **2.6476x**. Among 34 Health Care peers, 20 are worse/equal: **58.82%**, below the 66.7% bar. It currently passes at 3/6 = 50%; with this failed debt metric it is **3/7 = 42.86%**, therefore should fail.
- Autodesk (`ADSK`) similarly stores debt as NULL despite `LongTermDebt = $2.500bn`; 2.500 / 2.409 = **1.0378x**, 62.22nd percentile among 45 Technology peers. It also changes from a passing 3/6 to **3/7 = 42.86%**.
- American Tower (`AMT`) stores only $3.388bn current debt, omitting $33.833bn long-term debt. It reports **0.895x** debt/FCF and passes; SEC’s explicit debt-including-current-maturities fact is $37.202bn, yielding **9.832x**, an absolute-floor failure.

**Why this matters**

The screen’s stated purpose is determining which companies deserve attention. At least two companies are current false positives, and AMT has a materially false debt verdict.

**Evidence**

The raw cached SEC facts identify the missing values and accessions:

- A: `0001090872-25-000087`
- ADSK: `0000769397-26-000015`

Existing tests cover tag switching generally, but none exercise these debt concepts.

**Recommended remediation**

Add a non-overlapping debt hierarchy: prefer explicitly “including current maturities” total-debt tags; otherwise sum validated non-current and current variants. Add real-fact regression fixtures for A, ADSK, and AMT, then invalidate and rerun affected screens.

## [HIGH] REITs remain ranked using metrics documented as invalid

**Location**

[quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:298), [PRD addendum](/Users/pete/moat/docs/PRD_ADDENDUM.md:742).

**Requirement**

Current published scores must not use metrics known not to describe the business model.

**Observed behaviour**

Only Financials are marked `not_applicable`. Real Estate continues to receive FCF-margin and debt/FCF scores even though the addendum says REIT capex and debt/FCF misdescribe them and FFO/AFFO is the appropriate basis.

The current run has two passing Real Estate companies: AMT (62.5) and SBAC (50.0).

**Why this matters**

This is not merely unimplemented future FFO/AFFO work: the dashboard presents current plausible-looking rank and pass results from metrics the project knows are invalid.

**Evidence**

AMT’s displayed FCF is $5.464bn − $1.680bn = $3.784bn and is used in screen calculations, despite the documented REIT limitation. The screen configuration contains no Real Estate exclusion.

**Recommended remediation**

Until FFO/AFFO and a REIT-specific leverage definition exist, mark Real Estate FCF and debt metrics `not_applicable` and prevent insufficiently assessed REITs from being ranked.

## [MEDIUM] Re-anchoring can resolve a citation to the wrong repeated quote

**Location**

[cite.py](/Users/pete/moat/scripts/cite.py:302).

**Requirement**

Stored prefix/suffix disambiguate repeated quotes; a citation must not silently change what it points to.

**Observed behaviour**

The moved, moved-section, renormalized, and fuzzy paths search only for the quote. They never inspect `prefix` or `suffix`.

A temporary read-only adversarial check stored the intended second occurrence of “competition is intense”; the resolver returned the first occurrence as `moved`. The local JPM pilot corpus already contains three citations whose quoted text appears twice in the same document.

**Why this matters**

A post-normalization or re-extraction event can report an apparently successful resolution while pointing to different evidence.

**Evidence**

The requirement explicitly calls prefix/suffix the repeated-quote disambiguator in [A15.3](/Users/pete/moat/docs/PRD_ADDENDUM.md:843) and requires quote-plus-context fuzzy matching in [A15.5](/Users/pete/moat/docs/PRD_ADDENDUM.md:900).

**Recommended remediation**

Evaluate all quote matches against both saved context selectors; resolve only a unique contextual match, otherwise record `unresolved`. Add duplicate-quote tests for every non-exact rung.

## [MEDIUM] Citation coverage masks uncited prose

**Location**

[parser.py](/Users/pete/moat/moat/analysis/parser.py:219), [test_analysis_parser.py](/Users/pete/moat/tests/test_analysis_parser.py:223).

**Requirement**

Citation validation must enforce coverage with no uncited prose.

**Observed behaviour**

Non-protocol prose without a pending `CLAIM:` is silently ignored. The corresponding test asserts that this is correct, so an asserted sentence can disappear from parsed analysis while claim coverage remains 1.0.

**Why this matters**

The coverage metric can report complete grounding while the raw model response violated the no-uncited-prose rule.

**Evidence**

[A15.2](/Users/pete/moat/docs/PRD_ADDENDUM.md:798) explicitly requires “no uncited prose”; the code’s behavior conflicts with that documented acceptance criterion.

**Recommended remediation**

Treat any non-whitespace, non-protocol response text as validation failure, except explicitly permitted control text. Replace the ignore test with rejection coverage.

## [LOW] Bare pytest can unexpectedly run a paid live test

**Location**

[config.py](/Users/pete/moat/moat/config.py:13), [test_api_citation_shape.py](/Users/pete/moat/tests/test_api_citation_shape.py:22).

**Requirement**

The full automated suite should be reproducible without accidental network spending.

**Observed behaviour**

The live test says it is skipped unless an environment key is set, but project imports load `.env` during collection. A normal configured developer environment can therefore run the network test on `pytest`; here it failed due blocked DNS.

**Why this matters**

The suite is non-deterministic across developer environments and cannot presently establish live API compatibility.

**Evidence**

Normal full-suite execution failed with `anthropic.APIConnectionError`; explicitly setting `ANTHROPIC_API_KEY=''` yielded 154 passes and one intended skip.

**Recommended remediation**

Require an explicit integration-test marker or opt-in variable that configuration loading cannot activate implicitly; keep default pytest offline.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/python -m pytest -q -ra`; `ANTHROPIC_API_KEY='' .venv/bin/python -m pytest -q -ra`
- Passed: 154
- Failed: 1
- Skipped: 1
- Test-suite verdict: CANNOT VERIFY

### Requirements
- PASS: 3
- PARTIAL: 3
- FAIL: 2
- NOT IMPLEMENTED: 0
- DEFERRED: 4
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 2
- Medium: 2
- Low: 1

### Overall confidence
84%

### Overall verdict
FAIL

### Top 5 actions
1. Correct debt-tag extraction, add real SEC regression fixtures, and rerun the screen.
2. Exclude Real Estate from invalid FCF/debt scoring until FFO/AFFO metrics exist.
3. Make re-anchoring require unique prefix/suffix context matches.
4. Reject, rather than ignore, uncited response prose.
5. Make the live API test explicit opt-in and rerun it with network access.
