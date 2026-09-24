## Review result

Current completed scope (Sprints 0–4) is substantially correct. Sprint 5 is explicitly a partial pilot, not a completed release; Sprint 6 and UK/FTSE support are deferred. I found no new material calculation defect outside the allowlist.

Scope ambiguity: the original PRD calls UK coverage and monitoring MVP work, while the addendum explicitly overrides UK scope to US-only and defers monitoring. Sprint 5’s plan still says “planned,” but README calls it in progress and code/data contain a 21-company pilot. I treated it as in-progress, not a completed acceptance target.

The supplied database independently supported the core checks:

- 2,176 asserted AI claims all have citations; all 2,411 stored citation anchors join to an available source document and matched their stored quote byte-for-byte.
- AAPL independent DCF reconstruction: $222.217159/share and −49.5249% margin of safety, exactly matching persisted output. FCF yield independently calculated to 1.9810%; EV/EBIT to 37.8832x.
- AAPL screen independently recomputed to 6/8 assessable metrics passed = 75.0%.
- ADBE committee score independently recomputed as `92×.25 + 85×.20 + 82×.15 + 82×.10 + 55×.25 + (100−52)×.05 = 76.65`, matching persisted output.

Adversarial probes reproduced the known, allowlisted symptoms: negative owner-earnings DCF ordering (GitHub #5) and complex-number CAGR on a negative ending value (GitHub #6). These are informational only, as required.

## Test quality

The suite has strong deterministic coverage for parsing, citation anchoring, screen directionality, DCF arithmetic, edge guards, cache behavior, persistence, and dashboard rendering. Its main remaining weakness is that the one real Anthropic citation-shape test is opt-in and was skipped; default tests use constructed API responses.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US-only S&P 500 + NASDAQ 100 data foundation and provenance | Current | Implemented | Offline extractor, filing, schema, and provenance tests | PASS | Addendum A1/A11; `fundamentals_annual` provenance fields; 505-company data set |
| Sector-relative eight-metric quant screen and quality roll-up | Current | Implemented | Percentile, split, unavailable-status, and end-to-end tests | PARTIAL | Calculations verified; known informational exceptions remain for debt tags/REIT methodology (#1/#2) and unreachable negative-ending CAGR (#6) |
| Filing-grounded AI claims with durable citations | Current | Implemented | Parser, persistence, reanchor, and direct stored-corpus validation | PASS | 2,176/2,176 asserted claims cited; 2,411/2,411 anchors byte-exact |
| Owner-earnings DCF plus FCF yield, EV/EBIT, and historical P/E | Current | Implemented | Extensive unit/integration tests and independent AAPL reconstruction | PARTIAL | Positive-base calculations verified; known #5 DCF label inversion and #8 adjusted historical P/E remain informational |
| Committee weighted score, status, cache, and exclusions | Sprint 5 pilot | Implemented as pilot | Score, parser, persistence, cache, stage tests | PARTIAL | Current 21-company pilot; thresholds and full-universe completion remain explicitly unfinished |
| Ranked dashboard and Investment Brief | Sprint 5 pilot | Implemented as pilot | Streamlit AppTest coverage | PARTIAL | Brief rendering, citations, warnings, and provenance views tested; Sprint 5 DoD remains unchecked |
| FTSE 350, UK data, FX normalization | Deferred | Not implemented | N/A | DEFERRED | Addendum A1/A8 |
| Watchlist monitoring and alerts | Deferred | Stub/planned | N/A | DEFERRED | Sprint 6 / Sprint 5 scope exclusions |

## [LOW] Default suite does not exercise the live citation API contract

**Location**

[tests/test_api_citation_shape.py](/Users/pete/moat/tests/test_api_citation_shape.py:28)

**Requirement**

Citation parsing must remain compatible with the live Anthropic citation response shape required by the durable-anchor pipeline.

**Observed behaviour**

The only real API contract test is skipped unless `RUN_LIVE_API_TESTS=1`; this run skipped it. All default parser tests construct response objects manually.

**Why this matters**

An SDK or API response-shape change could leave the normal suite green while breaking production citation parsing.

**Evidence**

` .venv/bin/pytest -ra` reported one skipped test, specifically this opt-in live API test. Stored historical citation data validates existing output but cannot prove compatibility with a future API response.

**Recommended remediation**

Run this minimal test in a controlled scheduled/upgrade validation job with explicit credentials and spend authorization.

No tracked files were modified. The only worktree item remains the pre-existing untracked `moat-portfolio-handoff.md`.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/pytest -ra`
- Passed: 309
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 2
- PARTIAL: 4
- FAIL: 0
- NOT IMPLEMENTED: 0
- DEFERRED: 2
- CANNOT VERIFY: 0

### Findings
- Critical: 0
- High: 0
- Medium: 0
- Low: 1

### Overall confidence
89%

### Overall verdict
PASS WITH CONCERNS

### Top 5 actions
1. Run the opt-in live citation API test on a controlled schedule and after SDK upgrades.
2. Resolve known GitHub #1 debt-tag extraction gaps.
3. Resolve known GitHub #2 REIT metric-methodology gaps.
4. Resolve known GitHub #5 negative-owner-earnings DCF scenario labeling.
5. Complete and document Sprint 5 pilot validation before treating committee rankings as release-ready.
