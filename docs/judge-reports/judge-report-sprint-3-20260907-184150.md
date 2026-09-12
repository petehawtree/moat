## Scope and evidence

Current scope is Sprint 3 plus completed Sprints 1–2.2. Sprints 4–6, FTSE/FX, batch completion, filing-refresh checks, and non-exact citation reanchoring are explicitly deferred. The repository conflicts with itself: [README.md](/Users/pete/moat/README.md:24) and the Sprint 3 plan say Sprint 3 is planned, while the pipeline presents `ai_analysis` as implemented. I assessed committed Sprint 3 acceptance criteria where they are not explicitly deferred.

Original MVP PRD reviewed: :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"}.

## Test execution

Ran the full discovered suite:

```text
.venv/bin/python -m pytest -q
113 passed, 1 warning in 0.41s
```

Warning: urllib3/OpenSSL LibreSSL compatibility. No skips or errors.

The suite does not exercise `run_analysis`, `run_ai_analysis_stage`, `call_sync`, batch retrieval, `cite.py --reanchor`, filing refresh, or amendment fallback. Its persistence tests use simplified schemas, so they miss real orchestration/FK behavior.

## Independent calculation checks

- FCF is implemented as `operating_cash_flow - capex`; missing capex yields `None`, not substituted OCF. Representative expected result: OCF 100, capex 30 ⇒ FCF 70; revenue 200 ⇒ FCF margin 35%. This behavior and the missing-capex edge are covered.
- Revenue CAGR: 100 to 121 over two years independently computes to 10.0%; share count 100 to 90 over two years computes to -5.1317%. The implementation returned those values.
- Debt/FCF: debt 400 and FCF 100 ⇒ 4.0x and passes the 5x floor; positive debt with zero/unknown FCF correctly becomes a determined fail, not unavailable.
- Sector percentile: for five ascending peers, top = 100%, bottom = 20%; implementation matched.
- Quality score: four passes, two fails, two unavailable ⇒ 4/6 = 66.67%; implementation matched. I independently recomputed the stored latest 505-company run: zero roll-up mismatches; 93 passed.
- ROIC/ROE/margin formulas are plausible (`NOPAT / invested capital`, `net income / equity`, income/revenue), but existing tests do not independently assert these calculations. Therefore this area is only partially verified.

A material edge remains: the quarantine detects only an implausible *latest* row. A flagged earlier row can remain in historical growth inputs and peer ranking. A temporary five-company check showed a company with an earlier `implausible_ratio` row receiving seven assessed metrics and multiple passes; the stored data also has passed EBAY, VRSN, and GEN records with historical implausibility flags.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ 100 universe | Current | Implemented | Basic universe tests | PASS | Addendum A1; 518 companies in stored run |
| SEC annual fundamentals, provenance, and core ratios | Current | Implemented, but historical bad rows insufficiently quarantined | FCF/lease/unit tests; no direct ROIC/ROE assertions | PARTIAL | `fundamentals_edgar.py`; independent formula checks |
| Sector-relative eight-metric screen | Current | Implemented | Percentile and end-to-end synthetic tests | PASS | Addendum A2/A9; direction-aware percentile verified |
| Assessable-metric quality score and six-metric gate | Current | Implemented | Direct tests and stored-run recomputation | PASS | `quality_score.py`; 0/505 recomputation mismatches |
| Ranked dashboard and metric explanation | Current | Implemented | No UI test | PASS | `dashboard/app.py` exposes rank, coverage, and per-metric status |
| W1 filing fetch and immutable receipt | Current | Mostly implemented; does not validate response content type | URL/cache tests only | PARTIAL | Sprint 3 W1 requires content-type validation; `fetch_filing_document()` omits it |
| W2 normalization, extraction, fallback and trace | Current | Implemented | Seven synthetic extraction fixtures | PASS | Sprint 3 extraction rules; real Apple filing safely used full fallback |
| W3 combined cited call and structured refusal outcome | Current | Refusal raises before structured handling | No orchestration test | FAIL | `caller.py`; temporary refusal run wrote zero attempts |
| W4 claim-level parsing and citation binding | Current | Multi-claim cited blocks collapse into one claim | Misleading “multi-claim” test uses separate blocks | FAIL | `parser.py`; adversarial response validated despite two claims becoming one |
| W5 atomic persistence/failure receipt | Current | Validation failures persist, API refusal does not | Unit persistence tests only | FAIL | Sprint 3 definition of done; refusal check produced no `analysis_attempts` row |
| W6 current exact citation recall | Current | Implemented | No direct tests; manually exercised existing Apple analysis | PASS | `cite.py`; 32/32 Apple anchors exact |
| W6 moved/fuzzy/renormalized reanchoring | Deferred | Exact rung only | No tests | DEFERRED | Sprint 3 plan’s “Deferred to Sprint 4” section |
| W7 select latest successful quality run | Current | Implemented | No orchestration test | PASS | `run_pipeline.py` selects complete/partial quality runs |
| W7 10-K/A fallback to original 10-K on extraction failure | Current | Not implemented | Selection-only tests | FAIL | Confirmed Sprint 3 Decision 3; no code path retains or retries original 10-K |
| Fresh filing discovery and end-to-end batch workflow | Deferred | Incomplete | No tests | DEFERRED | Explicitly deferred to Sprint 4 |
| Sprint 3 pilot, human review, and retrospective | Current release evidence | No Sprint 3 retrospective; only one filing/analysis in local data | Not verifiable | CANNOT VERIFY | Sprint 3 definition of done requires three pilots and human review |
| Valuation, committee, brief, monitoring | Deferred | Stubs | N/A | DEFERRED | Sprints 4–6 |
| FTSE 350, FX normalization | Deferred | Not implemented | N/A | DEFERRED | Addendum A1/A8 |

## [HIGH] Citation parser can merge multiple asserted claims into one record

**Location**

[parser.py](/Users/pete/moat/moat/analysis/parser.py:141)

**Requirement**

Sprint 3 W4 requires claims to be parsed independently, with citations bound to each asserted claim.

**Observed behaviour**

Any cited text block is treated as one claim. An adversarial cited block containing:

```text
First statement.
CLAIM: Second statement.
```

validated successfully with 100% coverage, but persisted as one business-quality claim containing both statements.

**Why this matters**

A citation can support only one statement while coverage incorrectly reports both as grounded. This defeats the claim-level binding that Sprint 3 was designed to enforce.

**Evidence**

The existing “multi-claim” test uses separate cited blocks, not a multi-claim block. The temporary adversarial check produced one claim, two citations, no validation errors, and 1.0 coverage.

**Recommended remediation**

Tokenize protocol markers inside cited blocks, assign citations to the appropriate parsed claim, and add API-shape fixtures with multiple claims in one cited block.

## [HIGH] 10-K/A extraction failure does not fall back to the original 10-K

**Location**

[filing_fetcher.py](/Users/pete/moat/moat/ingest/filing_fetcher.py:81), [persist.py](/Users/pete/moat/moat/analysis/persist.py:371)

**Requirement**

Sprint 3 Decision 3 requires selecting the amendment first, then falling back to the original 10-K if the amendment cannot yield plausible analysis sections.

**Observed behaviour**

`select_latest_10k()` selects the amendment, but no code retains the original candidate or retries it if section extraction/full-fallback validation fails.

**Why this matters**

The plan notes that many amendments are Part III-only. Companies with a usable original 10-K are instead recorded as extraction failures.

**Evidence**

A synthetic same-period 10-K/10-K/A submission selected the amendment as intended. Repository search found no original-10-K fallback path; tests cover only initial selection.

**Recommended remediation**

Return ordered filing candidates, attempt the selected amendment first, and retry the original 10-K on extraction failure while recording the chosen accession.

## [HIGH] API refusals are neither persisted nor isolated per company

**Location**

[caller.py](/Users/pete/moat/moat/analysis/caller.py:238), [persist.py](/Users/pete/moat/moat/analysis/persist.py:447)

**Requirement**

A refusal must be recorded as `analysis_attempts.outcome='refused'`; every company must receive either analyses or a structured outcome.

**Observed behaviour**

`call_sync()` raises `RuntimeError` on refusal before `run_analysis()` can reach its refusal branch. The exception can fail the pipeline rather than allow remaining companies to proceed.

**Why this matters**

The system loses its audit receipt and breaks the required no-silent-skip behavior.

**Evidence**

In a temporary database with a mocked refusal, `run_analysis()` raised and left 0 pipeline runs, 0 analysis attempts, and 0 analyses.

**Recommended remediation**

Return a refusal `CallResult`, persist its attempt transactionally, and continue processing other tickers.

## [MEDIUM] Historical implausible rows are not quarantined

**Location**

[quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:375)

**Requirement**

Addendum A13 requires implausible revenue rows to be excluded from peer comparisons and not scored as trustworthy output.

**Observed behaviour**

Only `history[-1]` is checked for `implausible_ratio`. Earlier flagged rows remain in the history used for cross-period metrics.

**Why this matters**

A corrupted historical input can affect CAGR/trend-based metrics and peer rankings while the company appears assessable.

**Evidence**

Temporary test: an earlier bad row led to seven assessed metrics and several passes. The stored run contains 14 companies with historical implausibility flags but clean latest rows, including three current screen passes.

**Recommended remediation**

Exclude flagged historical observations from each affected time series and quarantine the company/metric when a valid comparable series cannot be formed.

## [MEDIUM] Filing response content type is never validated

**Location**

[filing_fetcher.py](/Users/pete/moat/moat/ingest/filing_fetcher.py:151)

**Requirement**

W1 explicitly requires status, content type, minimum length, parseability, and SHA-256 validation.

**Observed behaviour**

The fetcher checks status, length, and nonempty BeautifulSoup text, but not the HTTP `Content-Type`.

**Why this matters**

A large HTML-formatted error page or wrong resource can be accepted as a filing receipt.

**Evidence**

No response-header access or check exists; tests do not cover it.

**Recommended remediation**

Require an expected HTML/XHTML content type before accepting and storing bytes.

## [LOW] Scope documentation is stale and internally inconsistent

**Location**

[README.md](/Users/pete/moat/README.md:24), [sprint-3-plan.md](/Users/pete/moat/docs/sprints/sprint-3-plan.md:3), [run_pipeline.py](/Users/pete/moat/scripts/run_pipeline.py:11)

**Requirement**

Current scope must be clear enough to assess and operate the release.

**Observed behaviour**

Documents say Sprint 3 is planned, while the runner says the stage is real; the plan also appends deferred exceptions to earlier acceptance criteria.

**Why this matters**

It obscures whether failures are active release defects or intentionally deferred work.

**Evidence**

Direct contradiction among current repository materials.

**Recommended remediation**

Publish a Sprint 3 retrospective/status document and reconcile README, plan, and deferred backlog.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/python -m pytest -q`
- Passed: 113
- Failed: 0
- Skipped: 0
- Test-suite verdict: PASS

### Requirements
- PASS: 7
- PARTIAL: 2
- FAIL: 4
- NOT IMPLEMENTED: 0
- DEFERRED: 4
- CANNOT VERIFY: 1

### Findings
- Critical: 0
- High: 3
- Medium: 2
- Low: 1

### Overall confidence
91%

### Overall verdict
FAIL

### Top 5 actions
1. Fix claim parsing so every protocol claim has its own citation binding.
2. Persist refusal/API-error attempts and continue processing unaffected companies.
3. Implement the specified 10-K/A-to-original-10-K extraction fallback.
4. Prevent historical implausible rows from affecting trend metrics or peer groups.
5. Reconcile Sprint 3 scope/status documentation and add end-to-end orchestration tests.
