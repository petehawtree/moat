## Result

**FAIL.** The completed Sprint 2.2 screen has a material share-dilution calculation error. In addition, the committed caller-facing Sprint 3 scripts cannot reliably persist or enforce cited analyses, despite the documentation still labelling Sprint 3 as planned.

Scope evidence: the addendum overrides the original US+UK PRD; Sprint 2.2 is done, while valuation, committee, monitoring, and UK/FX are deferred. Sprint 3 status is ambiguous: [README](/Users/pete/moat/README.md:25) and its plan say “planned,” but HEAD includes W1–W6 code and user-facing `analyze.py`/`cite.py`. I assessed those callable paths because they are presented as functional.

There is also a planning conflict: W1 says repeat runs should avoid refetching, while W7 requires normal runs to discover new filings first. [Sprint 3 plan](/Users/pete/moat/docs/sprints/sprint-3-plan.md:262)

## Test execution

The actual full-suite command is:

```text
.venv/bin/python -m pytest
```

It collected and passed **112 tests**, with **0 failed**, **0 skipped**, and **1 warning**: urllib3 warns that the interpreter uses LibreSSL. `python3 -m pytest` initially failed because the system interpreter lacks pytest; the repository virtual environment contains the declared dependencies.

The suite does not establish correctness sufficiently:

- It does not test the real post-migration schema; persistence tests construct permissive hand-written tables, hiding the production foreign-key failure.
- The split tests use idealised ratios and miss organic share-count movement around a real split.
- A parser test explicitly accepts uncited prose by ignoring it.
- No test exercises the documented cache-hit behavior through `scripts/analyze.py`, batch-result persistence, W2 trace persistence, or W6 reanchoring.

## Independent financial validation

For Apple FY2025, the stored fundamentals and non-dilution calculations agree with independent arithmetic:

- FCF = $111.482bn OCF − $12.715bn capex = **$98.767bn**; FCF margin = **23.73288223%**.
- Debt/FCF = $90.678bn / $98.767bn = **0.91810018**.
- Operating margin = $133.050bn / $416.161bn = **31.97079976%**.
- Gross margin = ($416.161bn − $220.960bn) / $416.161bn = **46.90516411%**.
- ROIC = $133.050bn × 79% / ($90.678bn + $73.733bn − $35.934bn) = **81.81191964%**; ROE = $112.010bn / $73.733bn = **151.91298333%**.
- Revenue CAGR, FY2007–FY2025, is **17.17368481%**.
- Its FCF-margin sector percentile is independently **34/62 = 54.8387%**, correctly below the 66.7% bar. Its composite arithmetic is correctly 6/8 = **75%**.

The edge handling for missing capex, non-positive FCF debt, unavailable values, and financial-sector exclusions is substantially correct. The dilution calculation is not.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US-only S&P 500 + NASDAQ-100 universe | Current | Implemented | Offline merge tests only | PARTIAL | [A1](/Users/pete/moat/docs/PRD_ADDENDUM.md:8); no live-source reproducibility test |
| SEC annual fundamentals, provenance, FCF/revenue integrity | Current | Implemented | Targeted unit tests | PARTIAL | FCF, margin, ROIC, ROE, and provenance independently checked on AAPL; several transformations lack direct regression tests |
| Eight-metric sector-relative screen, including dilution | Current | Implemented incorrectly | Tests miss real split arithmetic | FAIL | [A9/A12](/Users/pete/moat/docs/PRD_ADDENDUM.md:188), 104 dilution CAGR discrepancies in stored run |
| Assessable-metric score, coverage gate, financial exclusions | Current | Implemented | Meaningful unit and database checks | PASS | 75% AAPL calculation; 0 Financials passed latest run |
| Ranked dashboard and metric explanation | Current | Implemented | No UI/integration test | PARTIAL | Queries expose score/status/peer data, but underlying dilution results are wrong |
| Citation-enforced qualitative analysis | Planned, but caller-facing code exists | Cannot persist/enforce reliably | Unit tests do not use production schema | FAIL | [Sprint 3 W4–W6](/Users/pete/moat/docs/sprints/sprint-3-plan.md:265) |
| Valuation / owner-earnings DCF | Sprint 4 | Stub | N/A | DEFERRED | Explicit Sprint 4 scope |
| Committee and Investment Brief | Sprint 5 | Stub | N/A | DEFERRED | Explicit Sprint 5 scope |
| Watchlist monitoring | Sprint 6 | Stub | N/A | DEFERRED | Explicit Sprint 6 scope |
| FTSE 350 and FX normalization | Later | Not implemented | N/A | DEFERRED | [A1](/Users/pete/moat/docs/PRD_ADDENDUM.md:8) |

## [CRITICAL] Schema migration destroys `ai_analysis` constraints

**Location**

[connection.py](/Users/pete/moat/moat/db/connection.py:81)

**Requirement**

Sprint 3 must atomically store four analyses, claims, and citations with referential integrity.

**Observed behaviour**

`_retire_legacy()` recreates `ai_analysis` with `CREATE TABLE ... AS SELECT`. SQLite drops primary keys, `NOT NULL`, defaults, and foreign keys in this form.

A fresh temporary database initialized with the repository function had no `ai_analysis` foreign keys or composite primary key. With foreign keys enabled, inserting an `analysis_claims` row failed:

```text
foreign key mismatch - "analysis_claims" referencing "ai_analysis"
```

**Why this matters**

A valid cited analysis cannot be persisted against the real schema. It also permits duplicate/incomplete analysis rows.

**Evidence**

The schema requires `analysis_claims` to reference `(run_id, ticker, analysis_type)` on `ai_analysis`, but the migration removes that key. Existing persistence tests use custom tables rather than `init_db()`.

**Recommended remediation**

Rebuild the migration using explicit replacement DDL that preserves every key, constraint, index, and foreign key; copy data only after creating that schema. Add a test that initializes a fresh production schema and persists a complete cited result.

## [HIGH] Split rebasing uses the wrong factor

**Location**

[quant_screen.py](/Users/pete/moat/moat/screen/quant_screen.py:111)

**Requirement**

The screen must consume the filing-restatement split factor, not infer a factor from a year-to-year share jump. [A12](/Users/pete/moat/docs/PRD_ADDENDUM.md:505)

**Observed behaviour**

The code records only corroborated years, then multiplies by `v_next / v_prev`. That ratio includes real issuance/buybacks occurring between fiscal years.

For AAPL, filing evidence gives exact 7× and 4× split factors, so the FY2007 count should be multiplied by 28. The implementation instead uses adjacent raw ratios of approximately 7.065× and 3.808×. It reports dilution of **−2.5593%/yr**; independent calculation using the filing ratios gives **−2.7747%/yr**.

**Why this matters**

It misstates dilution and split-adjusted EPS trends, which feed both a floor and a sector-relative ranking. On run `20260822T140041Z`, recalculation with stored restatement ratios changed:

- 104 dilution CAGR values
- 15 dilution pass/fail decisions
- 2 overall screen outcomes: DLTR would enter and NEE would leave

**Evidence**

`verify.py AAPL shares_diluted --year 2018` showed the cached SEC facts changing from 5,000,109,000 to 20,000,435,000 shares: exactly 4×. The screen never reads `share_basis_changes.ratio`.

**Recommended remediation**

Pass and use the recorded `ratio` per restated period, preserving the adjacent-year jump only as a detection condition. Add regression fixtures where real buybacks/issuance coincide with a split.

## [HIGH] Citation coverage can be bypassed by uncited prose

**Location**

[parser.py](/Users/pete/moat/moat/analysis/parser.py:159)

**Requirement**

Every asserted claim must have a resolving citation; uncited connective assertions must fail validation. [Sprint 3 W4](/Users/pete/moat/docs/sprints/sprint-3-plan.md:265)

**Observed behaviour**

Non-cited text not preceded by `CLAIM:` is silently ignored. An adversarial response containing an extra uncited statement produced `claim_coverage = 1.0`; the extra statement was absent from parsed claims.

**Why this matters**

The persisted coverage metric can claim 100% while user-visible model prose contains an unsupported assertion.

**Evidence**

The test suite codifies this behavior in `test_parse_uncited_connective_ignored`, rather than rejecting it.

**Recommended remediation**

Reject any non-whitespace response prose that is not a recognised header, `CLAIM:`, `INSUFFICIENT EVIDENCE:`, or permitted management label. Add the adversarial case as a validation-failure test.

## [HIGH] Cache-hit path spends on a new API call and omits evidence

**Location**

[analyze.py](/Users/pete/moat/scripts/analyze.py:76), [persist.py](/Users/pete/moat/moat/analysis/persist.py:136)

**Requirement**

An unchanged bundle must make zero API calls and explicitly preserve/reuse its analysis, claims, and citations. [Sprint 3 W5](/Users/pete/moat/docs/sprints/sprint-3-plan.md:266)

**Observed behaviour**

`scripts/analyze.py` calls `call_sync()` before checking the cache. On a hit, `persist_result()` copies only `ai_analysis` rows; it does not copy or associate `analysis_claims` and `citations`.

**Why this matters**

It violates spend control and produces a new “current” analysis with no claim/citation rows.

**Evidence**

The code order is direct; existing cache tests assert only copied `ai_analysis` rows.

**Recommended remediation**

Calculate the bundle key and check cache before invoking the API. Model reuse as an explicit association to immutable original claims/citations, or copy them transactionally with clear provenance.

## [MEDIUM] Section-extraction audit trace is always stored as NULL

**Location**

[caller.py](/Users/pete/moat/moat/analysis/caller.py:121)

**Requirement**

W2 requires persisted candidates, chosen spans, lengths, and rules for observable extraction. [Sprint 3 W2](/Users/pete/moat/docs/sprints/sprint-3-plan.md:263)

**Observed behaviour**

The trace is stored with `extraction.trace.get(section_id)`, but section traces live under `extraction.trace["sections"][section_id]`. Thus successful section rows receive NULL trace data.

**Why this matters**

The main diagnostic evidence for a bad section extraction is lost.

**Recommended remediation**

Persist the full immutable trace or the correctly nested per-section trace, and test the DB row rather than only the extractor return object.

## [MEDIUM] Reanchoring implements only exact offsets

**Location**

[cite.py](/Users/pete/moat/scripts/cite.py:207)

**Requirement**

Sprint 3 requires a resolution ladder: offsets, quote-in-section, quote-in-filing, normalization-insensitive, fuzzy, then unresolved.

**Observed behaviour**

`--reanchor` labels every non-exact citation as `unresolved`; moved, renormalized, and fuzzy paths are absent.

**Why this matters**

A durable anchor is incorrectly marked broken after harmless text movement, undermining the stated recall guarantee.

**Recommended remediation**

Implement and test every specified resolution rung, including duplicate quotes and immutable event history.

## Judge Verdict

### Test execution
- Test command(s): `python3 -m pytest` (system interpreter lacked pytest); `.venv/bin/python -m pytest`
- Passed: 112
- Failed: 0
- Skipped: 0
- Test-suite verdict: PASS

### Requirements
- PASS: 1
- PARTIAL: 3
- FAIL: 2
- NOT IMPLEMENTED: 0
- DEFERRED: 4
- CANNOT VERIFY: 0

### Findings
- Critical: 1
- High: 3
- Medium: 2
- Low: 0

### Overall confidence
94%

### Overall verdict
FAIL

### Top 5 actions
1. Repair the `ai_analysis` migration so production-schema citation persistence works.
2. Rebase shares with recorded filing-restatement ratios and rerun the screen.
3. Reject all uncited non-protocol prose before persistence.
4. Check cache before API calls and preserve claim/citation provenance on reuse.
5. Persist extraction traces and implement the required citation-resolution ladder.
