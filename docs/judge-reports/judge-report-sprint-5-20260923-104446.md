## Scope assessed

The addendum overrides the original MVP PRD: current completed scope is Sprints 0–4; Sprint 5 is an explicitly partial committee/brief pilot; Sprint 6 monitoring and FTSE/UK support are deferred. The PRD source reviewed: :codex-file-citation{path="/Users/pete/moat/docs/Project_Moat_PRD_MVP.pdf" purpose="source"}

Ambiguities resolved by repository evidence:

- Original US+UK scope conflicts with addendum A1; US-only wins.
- PRD calls monitoring MVP, while the roadmap explicitly puts it in unstarted Sprint 6; deferred.
- Sprint 5’s plan says “Planned,” while README/code/database show a 21-company pilot; treated as in-progress pilot work, not release-complete functionality.

## Test execution

Executed:

```bash
.venv/bin/pytest
```

Result: 307 collected; 306 passed, 1 skipped, 0 failed, 1 warning. The skipped test is the explicit opt-in live Anthropic API shape test. Warning: urllib3/OpenSSL compatibility warning under the local LibreSSL Python build.

Additional read-only checks:

- Verified all 2,411 persisted citations against their local source text, hashes, offsets, and quotes: 0 errors.
- Verified database foreign keys: 0 violations.
- Independently recalculated persisted AAPL screen, DCF, and committee values.

## Independent calculation checks

- Screen: AAPL ROIC is `0.818119...`; among 48 non-quarantined Information Technology peers, 45 are at or below it, yielding `45/48 × 100 = 93.75%`, matching the stored percentile. The unavailable/fail distinction for debt with non-positive FCF is correctly covered.
- Owner earnings: AAPL’s ten eligible annual values independently average to `$76.4462bn`, matching the persisted DCF base.
- DCF: applying the required 10-year discounted cash-flow plus Gordon terminal formula independently produced AAPL bear/base/bull values of `$151.274167`, `$222.217159`, and `$288.958007` per share, matching persistence to floating-point precision.
- Committee: AAPL’s stored `61.1` score matches `0.25Q + 0.20M + 0.15F + 0.10Mgmt + 0.25V + 0.05(100-Risk)`.
- Edge handling is generally sound for null capex/D&A, missing debt, non-positive EBIT/EPS, thin P/E history, and non-positive bear intrinsic values. The negative-owner-earnings scenario-ordering defect remains informational only because it exactly matches known GitHub issue #5.

## Test-quality assessment

The suite has meaningful hand-derived valuation and weighted-score assertions, end-to-end temporary SQLite tests, and citation/parser validation. It does not test the most important cache-invalidating path: a new bundle key must supersede every prior analysis version for the ticker. Existing cache tests only exercise a cache hit under the same bundle key, while committee tests manually clear old `is_current` rows. This omission allowed the finding below.

## Requirements traceability

| Requirement | Scope | Implementation | Test coverage | Verdict | Evidence |
|---|---|---|---|---|---|
| US S&P 500 + NASDAQ 100 ingest, provenance, confidence | Sprint 1 | SEC/XBRL ingest, cached source records, provenance fields | Regression and DB tests; live DB inspected | PASS | Addendum A1/A4/A11; `fundamentals_edgar.py`; FK check passed |
| Sector-relative eight-metric screen and assessable-metric quality score | Sprint 2/2.2 | Direction-aware percentiles, floors, unavailable/not-applicable states | Unit and temp-DB end-to-end tests; AAPL percentile recalculated | PASS | Addendum A2/A9/A14; `quant_screen.py`; stored AAPL result matched |
| Filing-grounded AI claims and durable citations | Sprint 3/3.1 | Claims/citations tables, immutable anchors, resolution events | Parser/persistence/re-anchor suite plus full persisted-anchor audit | PASS | Addendum A3/A15; 2,411/2,411 anchors valid; 2,176/2,176 asserted claims cited |
| AI cache invalidation must leave refreshed analysis current | Sprint 3/3.1, affects Sprint 5 | New bundle is persisted but old bundle remains current | Missing changed-bundle regression; isolated reproduction fails requirement | FAIL | `persist.py:92-98`; downstream current-row queries in committee/dashboard are unordered |
| Owner-earnings DCF, cross-checks, conservative margin of safety | Sprint 4 | Formula, scenario DCF, supporting methods, sign guards | Extensive hand-derived tests; AAPL independently recalculated | PASS | Addendum A16/A20; `engine.py`; known #5 is informational only |
| Committee ranking and Investment Brief | Sprint 5 partial pilot | 21-company pilot, scoring, brief rendering, default exclusions | Parser/persistence/dashboard tests; pilot rows inspected | PARTIAL | README/Sprint 5 plan explicitly say pilot is partial and thresholds are not final |
| FTSE 350, FX normalization | Later sprint | Not implemented | N/A | DEFERRED | Addendum A1/A8 |
| Watchlist alerts and monitoring | Sprint 6 | Intentional stubs | N/A | DEFERRED | README roadmap; `monitor/watchlist.py` |

## [HIGH] Refreshed AI analyses do not supersede prior versions

**Location**

[persist.py](/Users/pete/moat/moat/analysis/persist.py:92), [committee.py](/Users/pete/moat/moat/committee/committee.py:181), [app.py](/Users/pete/moat/moat/dashboard/app.py:82).

**Requirement**

When filing content, prompt, model, extraction, or normalization changes, A5/A15.7 require a fresh analysis. Callers requesting “current” analysis must receive that refreshed version only.

**Observed behaviour**

`_supersede_for_bundle()` clears `is_current` only for rows with the *same* `cache_key`. A changed filing necessarily changes that key, so the old four analysis rows remain current alongside the new four.

**Why this matters**

Committee and dashboard queries select all `is_current=1` rows without ordering. Their type-keyed dictionaries can therefore use stale claims/cache keys or mix versions. A refreshed filing can lead to a committee score or displayed brief grounded in obsolete analysis.

**Evidence**

An isolated reproduction with four old-bundle rows and four refreshed-bundle rows left both bundles current after invoking production `_supersede_for_bundle()`:

```text
[('new-bundle', 1, 4), ('old-bundle', 1, 4)]
```

The implementation predicates on `cache_key` at `persist.py:96`. Existing tests cover same-key cache forwarding but no changed-key supersession.

**Recommended remediation**

Supersede all prior current analysis rows for the ticker when a newly validated bundle becomes current, while retaining prior rows as historical records. Add regression coverage for a changed document hash and confirm committee/dashboard retrieve exactly one current row per analysis type.

## Already-triaged informational items

Known issues #1, #2, #3, #4, #5, #6, and #8 were observed in code/documentation or data but are allowlisted and not counted. In particular, the real negative-owner-earnings DCF ordering issue (#5) is present in pilot data but its margin-of-safety guard prevents the documented false-positive verdict.

## Judge Verdict

### Test execution
- Test command(s): `.venv/bin/pytest`
- Passed: 306
- Failed: 0
- Skipped: 1
- Test-suite verdict: PASS

### Requirements
- PASS: 4
- PARTIAL: 1
- FAIL: 1
- NOT IMPLEMENTED: 0
- DEFERRED: 2
- CANNOT VERIFY: 0

### Findings
- Critical: 0
- High: 1
- Medium: 0
- Low: 0

### Overall confidence
93%

### Overall verdict
FAIL

### Top 5 actions
1. Supersede all prior current AI analyses when a new bundle is persisted.
2. Add changed-filing, changed-prompt, and changed-model cache invalidation tests.
3. Enforce one current AI-analysis version per ticker and analysis type at query/schema level.
4. Resolve known issue #5 before using DCF scenario labels in a production ranking.
5. Resolve known issue #8 before expanding historical P/E price coverage.
