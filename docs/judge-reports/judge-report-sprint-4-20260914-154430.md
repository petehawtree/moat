## Audit result

Current implemented scope is Sprint 1–3.1: US ingest, sector-relative screen/dashboard, and citation-enforced AI analysis. Sprint 4–6 remain explicitly deferred despite partial Sprint 4 code at HEAD; the README, sprint plan, and pipeline still mark valuation onward unimplemented. The original PDF’s wider US+UK MVP is overridden by the addendum’s US-only decision. I reviewed :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"}.

The implementation fails current-scope review because the live screen publishes materially wrong debt and REIT metrics.

### Independent calculation checks

| Area | Independent check | Result |
|---|---|---|
| FCF | `FCF = OCF − capex`; missing capex must yield unavailable, not OCF | Pass |
| REIT revenue | $12.967m contract revenue + $1.573544bn lease income = $1.586511bn | Pass |
| Sector percentile | Best of five peers = 5/5 = 100%; lower debt ranks inversely | Pass |
| Share dilution | TKO: \((194.0/82.8)^{1/2}-1 = 53.1\%\) | Pass |
| Debt/FCF | AMT stored: $3.3878bn / $3.7836bn = 0.895×; filing total: $37.2203bn / $3.7836bn = 9.837× | Fail |
| Citations | Read-only verification of 2,411 persisted anchors: file hashes, offsets, and quotes all matched | Pass |

### Test quality

The full suite is green, but it does not cover the two confirmed financial defects: common total-debt tags and Real Estate metric exclusion. Batch tests cover a plain 10-K submission and separately test amendment fallback in preflight, but not their combined path. The live Anthropic citation-shape test is intentionally skipped, so the external API contract was not verified in this run. Section-extraction tests are synthetic rather than real cached SEC fixtures.

### Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ-100 universe | Current | Implemented | Basic merge/normalization tests | PASS | Addendum A1; `universe.py` |
| FTSE 350 / FX normalization | Future | Not implemented | N/A | DEFERRED | Addendum A1/A8 |
| Annual SEC fundamentals, FCF, revenue, provenance | Current | Implemented | Regression tests cover FCF and lease revenue | PASS | `fundamentals_edgar.py`; tests |
| Sector-relative ranking | Current | Implemented, but dependent on bad debt inputs | Percentile tests only | PARTIAL | Addendum A2/A9; `quant_screen.py` |
| Total-debt / FCF metric | Current | Incorrect tag coverage | No debt-tag fixture | FAIL | Addendum A17; AMT cache and DB result |
| Real Estate screening validity | Current | Invalid FCF/debt metrics are still scored | No Real Estate applicability test | FAIL | Addendum A14/A17; AMT/SBAC screen rows |
| Assessable-metric quality score and minimum coverage | Current | Correct formula | Direct unit and integration tests | PASS | `quality_score.py`; tests |
| Ranked dashboard | Current | Displays contaminated screen output | No dashboard tests | PARTIAL | `dashboard/app.py`; debt/REIT findings |
| Citation-enforced persisted claims | Current | Durable anchors work; unprefixed prose is silently ignored | Parser/persistence tests; anchor audit | PARTIAL | Addendum A15.2; parser adversarial check |
| Live Anthropic citation response contract | Current integration | Opt-in test exists but was skipped | Credential-gated only | CANNOT VERIFY | `test_api_citation_shape.py` |
| Resumable batch analysis | Current | Normal path works; amendment fallback diverges | No combined regression | PARTIAL | `caller.py`; ad-hoc reproduction |
| Financial-sector alternative metrics | Future | Excluded rather than modeled | N/A | DEFERRED | Addendum A14 |
| IBR `toc_cluster` edge case | Future / explicitly re-deferred | Not fully solved | N/A | DEFERRED | Sprint 3.1 docs |
| Dual-class shared-CIK filing mapping | Explicitly deferred remediation | Manual exclusion used | N/A | DEFERRED | Addendum A18 |
| Valuation | Future Sprint 4 | Pipeline stage not wired | Future tests exist | DEFERRED | README; `run_pipeline.py` |
| Committee / Investment Brief | Future Sprint 5 | Not implemented | N/A | DEFERRED | README |
| Monitoring | Future Sprint 6 | Not implemented | N/A | DEFERRED | README |

## [HIGH] Debt extraction materially understates leverage

**Location**

`moat/ingest/fundamentals_edgar.py:217-218`, `:718-720`; `moat/screen/quant_screen.py:259-263`.

**Requirement**

Debt must be total debt relative to latest positive FCF, with debt above 5× failing the absolute floor.

**Observed behaviour**

The tag list only uses `LongTermDebtNoncurrent` plus current debt. It omits common total concepts including `LongTermDebt` and `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`.

**Why this matters**

The published debt metric can show low leverage where the issuer is highly leveraged, affecting scores and rankings.

**Evidence**

AMT’s cached latest 10-K reports $37.2203bn including current maturities; the application stores only $3.3878bn. With FCF of $3.7836bn, the correct ratio is 9.837× and fails the 5× floor; the application reports 0.895× and passes. This independently reproduces Addendum A17.

**Recommended remediation**

Implement a tested debt-tag hierarchy: prefer total-debt tags including current maturities; otherwise sum validated current and non-current components. Add real-fact regressions for AMT and issuers using `LongTermDebt`.

## [HIGH] Real Estate companies are ranked using metrics the repository defines as invalid

**Location**

`moat/screen/quant_screen.py:298-300`, `:413-431`.

**Requirement**

The repository states that REIT capex, FCF margin, and debt/FCF misdescribe the sector; FFO/AFFO is the correct basis.

**Observed behaviour**

Only `Financials` receive `not_applicable` metrics. Real Estate remains in all eight screen metrics. AMT and SBAC are currently passed-screen companies.

**Why this matters**

The dashboard presents scores and pass/fail results that are not meaningful for these businesses.

**Evidence**

Addendum A14 identifies the issue; A17 confirms AMT and SBAC pass using the invalid metrics. The current database shows AMT passing debt and SBAC passing FCF.

**Recommended remediation**

Until a valid REIT configuration exists, mark the affected metrics `not_applicable` and enforce the minimum-coverage exclusion, matching the Financials approach.

## [MEDIUM] Batch submission bypasses the amendment fallback

**Location**

`moat/analysis/persist.py:457-502`; `moat/analysis/caller.py:357-370`.

**Requirement**

Sprint 3 requires a defined 10-K/A fallback rather than analyzing a part-III-only amendment stub.

**Observed behaviour**

Batch preflight correctly selects the original 10-K after a bad amendment. `submit_batch()` then re-queries filings and selects the amendment again, aborting before batch creation.

**Why this matters**

One part-III-only amendment can prevent a batch from being submitted despite a usable original filing being available.

**Evidence**

A temporary reproduction yielded `preflight_accession=0000-01`, followed by `ValueError: full_fallback for 0000-02 is implausibly short` from `submit_batch()`.

**Recommended remediation**

Pass the preflight-resolved accession, content, document map, and prompt hash into submission; do not repeat filing selection in the batch function. Add an end-to-end amendment-fallback batch regression.

## [MEDIUM] Citation coverage can ignore uncited output

**Location**

`moat/analysis/parser.py:219-267`.

**Requirement**

Citation validation must enforce coverage with no uncited prose.

**Observed behaviour**

Plain, uncited text not following `CLAIM:` or `INSUFFICIENT EVIDENCE:` is ignored. A response containing such an assertion plus four cited claims validates successfully with 1.0 coverage; the assertion disappears from persisted analysis.

**Why this matters**

The system reports complete citation coverage while silently discarding nonconforming model output, losing audit completeness and potentially material analysis content.

**Evidence**

The temporary adversarial response returned `is_valid=True`, four claims, and no errors despite containing an uncited assertion. This conflicts with Addendum A15.2’s explicit warning about uncited prose in separate blocks.

**Recommended remediation**

Reject any substantive nonprotocol text, or parse it as an asserted uncited claim so validation fails. Add a regression asserting that this response cannot persist.

## Judge Verdict

### Test execution
- Test command(s): `pytest` (not found on PATH); `.venv/bin/pytest`
- Passed: 203
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 3
- PARTIAL: 4
- FAIL: 2
- NOT IMPLEMENTED: 0
- DEFERRED: 7
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 2
- Medium: 2
- Low: 0

### Overall confidence
91%

### Overall verdict
FAIL

### Top 5 actions
1. Fix total-debt tag extraction and add real-filing regression fixtures.
2. Exclude Real Estate from invalid FCF/debt metrics pending an FFO/AFFO screen.
3. Make batch submission use the amendment-fallback result from preflight.
4. Reject uncited nonprotocol AI prose instead of silently discarding it.
5. Add an opt-in CI job for the live citation API contract and real SEC document fixtures.
