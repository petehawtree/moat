# Sprint 3.1 — citation/batch backlog + the authorized 90-company run

**Status: done.** Items 1–5 closed and independently judge-reviewed three
times (below). **Item 6 ran**: 70 companies now have current analyses (not
90 — see [Judge review](#judge-review)), for **$11.13 total** real spend
($1.80 mini-pilot + $9.32 batch — the batch came in under its own $12.15
projection), well under the $35 cap. A live batch-submission bug was found
and fixed on the first real attempt (below) — no money was lost. **Plan:**
[sprint-3-1-plan.md](sprint-3-1-plan.md)

## Goal

Close the five backlog items Sprint 3's judge rounds and pilot surfaced but
left outside its definition of done, as prerequisites for — not independent
of — the deferred 90-company AI analysis run. Fix order followed the plan's
sequencing: 1 (freshness) → 2 (batch round-trip) → 4 (tests, against 1–2 as
they landed) → 3 (reanchor ladder) → 5a/5b → 6 (the run, held).

## What shipped

- **Item 1 — freshness (`moat/ingest/filing_fetcher.py`).** Non-offline
  runs now always fetch SEC submissions and select the latest accepted
  10-K/10-K/A before touching the cache; the primary-document download is
  skipped only when the cached accession matches that selection exactly
  (`_cached_filing_for_accession`). A new filing or amendment filed after
  the initial fetch is no longer invisible. `offline=True` is unchanged —
  zero network calls, same as before.
- **Item 2 — batch workflow, end-to-end (`moat/analysis/persist.py`).**
  `submit_batch()` was a dead end: nothing persisted its results or ever
  called `retrieve_batch()`. Now:
  - `submit_and_persist_batch()` writes a `pending` `analysis_attempts` row
    per ticker immediately after submission — the durable record retrieval
    reads back, not any in-memory state.
  - `run_batch_retrieval()` reconstructs its work list from those `pending`
    rows, hydrates them via `retrieve_batch()`, and persists each item
    through the same `persist_result()` every other path uses — four
    analyses or an `analysis_attempts` row, never partial. A second call
    for the same `batch_id` only touches whatever is still `pending`, so a
    crash mid-retrieval loses nothing.
  - `analysis_attempts.outcome` gained a `pending` state (CHECK constraint
    widened via a guarded table-recreate migration, `custom_id` UNIQUE)
    and an `accession_number` column, so the exact filing a batch item was
    built against survives round-trip.
  - `find_ticker_bundle()` / `persist_cache_hit()` give the batch path the
    same cache check the sync path has inline — `submit_batch()` has none
    of its own, so without this every re-run of an already-analyzed
    universe would pay for the batch anyway.
  - `scripts/analyze.py --batch` now persists before returning and gained
    `--retrieve-batch BATCH_ID`; `scripts/run_pipeline.py`'s `ai_analysis`
    stage gained `--batch` (submit → poll → retrieve in one invocation) and
    `--retrieve-batch-id` (resume a poll that timed out or was killed) —
    the "reachable end-to-end from `run_pipeline.py`" acceptance bar.
- **Item 3 — citation resolution ladder (`scripts/cite.py`).** All six
  rungs from §A15.5 are implemented and tried in order per citation:
  `exact` → `moved` (same section, quote moved) → `moved_section` (quote
  found elsewhere in the filing) → `renormalized` (whitespace/unicode-
  punctuation-insensitive search) → `fuzzy` (longest-common-run similarity,
  ≥0.60, score recorded) → `unresolved`. Every attempt still only writes a
  `citation_resolution_events` row — the anchor itself is never touched,
  matching §A15.5's non-negotiable. New `ai_analysis.stale_analysis` column
  is set on any `(run_id, ticker, analysis_type)` whose citations resolved
  at `fuzzy` or `unresolved`; smoke-tested against the real pilot data
  (AAPL/KO/JPM, 99 citations) — all still resolve `exact`, as expected
  since nothing about those filings has changed.
- **Item 4 — test coverage.** 34 new tests (114 → 148, +1 credential-gated):
  `run_for_ticker`'s freshness behavior (including the exact regression the
  bug describes — a stale cached accession must trigger a re-download, not
  a silent skip); `retrieve_batch()`'s hydration of succeeded/errored/
  refused/missing batch items; the batch persistence round-trip and its
  resumability; the amendment→original-10-K fallback in `run_analysis()`;
  all six reanchor rungs plus `stale_analysis` flagging; the `---`-divider
  parser fix (below); and one credential-gated integration test
  (`tests/test_api_citation_shape.py`, skipped without a live API key) that
  makes one real, minimal call and checks the citation response shape
  `parser.py` assumes actually holds. Not added: a dedicated fixture-based
  test for `run_ai_analysis_stage`/the `--batch` pipeline wiring itself —
  it's script-level orchestration over already-tested pieces
  (`find_ticker_bundle`, `submit_and_persist_batch`, `run_batch_retrieval`),
  and exercising it meaningfully means either mocking the Anthropic client
  end-to-end inside `run_pipeline.py` or running item 6 itself; deferred to
  when item 6 actually runs rather than built as a parallel simulation of it.
- **Item 5a — `toc_cluster` IBR-stub false positive: re-deferred, not
  fixed.** Re-examined rather than fixed. The theoretically correct fix
  (treat a candidate as immune once an earlier primary section has already
  resolved at high confidence) needs the extraction pass restructured into
  two passes — `_rejection()` runs during candidate collection, before any
  assignment is chosen — and a prior *distance-based* fix attempt already
  regressed AAPL/KO once (Sprint 3 Round 6). Zero current production impact
  stands (no Financials in `passed_screen`). Re-deferring a real structural
  fix rather than risking that regression for a currently-excluded sector
  was the judgment call here; documented in-code
  (`section_extractor.py::_is_toc_cluster`) so it isn't silently dropped
  again.
- **Item 5b — parser `---`-divider: fixed.** The divider routinely lands
  inside the *same* cited content block as a section's last claim (the
  model doesn't reliably start a new block at the divider), which is why
  adding `---` to `_SPLIT_RE` — the literal suggestion in the backlog —
  would have been the wrong fix: that regex drives cited- vs. plain-block
  *dispatch*, and the compound-block branch it would have routed into has
  no handling for a claim body that arrives without an explicit `CLAIM:`
  prefix in the same block, which would have silently dropped the claim
  instead of just leaving junk on it. Fixed instead at `_add()` — the one
  place all claim text is finalized — stripping a trailing standalone `---`
  regardless of which branch produced the text. Regression tests mirror the
  pilot's actual shape (mid-stream and end-of-stream).

## Migration

Two schema changes, both additive/guarded, applied via `moat/db/connection.py`'s
existing `_migrate()` pattern (no drops, no backfill):
- `analysis_attempts.outcome` CHECK widened to include `'pending'`, plus a
  new `accession_number` column — table-recreate migration (SQLite can't
  ALTER a CHECK in place), guarded on the constraint text and dynamic on
  whichever columns the old table actually has, so it's correct whether or
  not a partial upgrade already ran. Verified against a copy of the real
  `data/moat.db`: all 3 pilot `analysis_attempts` rows preserved.
- `ai_analysis.stale_analysis` — plain additive column, defaults to 0.
  Verified against the same copy: all 12 `ai_analysis` rows and 99
  `citations` rows preserved; `--reanchor` against the real pilot data
  (AAPL/KO/JPM) resolves all 99 at `exact`, as expected.

## Judge review

Run three times via `./judge.sh` against this branch. `.judge/` itself is
gitignored — judge.sh diffs `git status` before/after each run to catch
unintended writes, and a tracked report would show up as a new file on
every subsequent run and false-positive that check — so the three reports
are archived as tracked copies under
[`docs/judge-reports/`](../judge-reports/). **First pass**
([`judge-report-20260909-131716.md`](../judge-reports/judge-report-20260909-131716.md)) found
three real Highs, all confirmed by direct code inspection before fixing,
all in this sprint's own code:

- Batch mode had no spend-cap enforcement — `_run_batch_submission_and_
  retrieval()` never received `cost_cap_usd` at all. Fixed with a
  preflight projection (free `count_tokens()` calls, same assumed-output
  formula `--dry-run` uses) that caps how many tickers are ever submitted.
- The batch cache-precheck (`find_ticker_bundle()`) had no amendment
  fallback, unlike the sync path. Fixed by factoring
  `_resolve_sections_with_amendment_fallback()` out of `run_analysis()` so
  both paths share it and can't drift again.
- `_reanchor()` joined `analysis_claims` straight on the current
  `ai_analysis.run_id`, but a cache-forward row stores no claims of its
  own (they live under `reused_from_run_id`) — every cached/reused
  analysis silently reanchored zero citations. Fixed to join through
  `COALESCE(reused_from_run_id, run_id)`.

That pass also flagged the persisted 93-company `passed_screen` set as
stale relative to current code (it predates commit `e8a824f`). Re-running
screen+quality (no API cost) confirmed it exactly: **91/505, not 93** —
EBAY/GEN/KVUE/VLTO/VRSN drop out, AZO/CRWD/SBAC newly pass, and KO/JPM (2
of the pilot's 3 already-analyzed companies) no longer pass at all.

**Second pass** ([`judge-report-20260909-134928.md`](../judge-reports/judge-report-20260909-134928.md)), against the fixes and
the fresh screen data, confirmed all three Highs resolved and surfaced two
more issues in this sprint's own code — a MEDIUM (fixed) and a LOW (fixed,
second time it was raised):

- The `moved`/`moved_section`/`renormalized` rungs accepted the *first*
  exact-content match with no check it was the right one — confirmed real:
  the JPM pilot corpus already has citations whose quote appears twice in
  the same document. Fixed with `_disambiguate()`: a repeated match is
  only accepted when exactly one occurrence's surrounding text matches the
  citation's own stored prefix/suffix (§A15.3); otherwise the rung falls
  through. `fuzzy` (rung 5) deliberately still doesn't disambiguate — a
  documented, bounded limitation, not folded into this fix.
- The live API test's `ANTHROPIC_API_KEY`-presence gate meant a bare
  `pytest` in this repo's own configured environment could make a real
  network call and fail non-deterministically. Now requires
  `RUN_LIVE_API_TESTS=1` explicitly.

It also surfaced two Highs that are **not** Sprint 3.1 scope — real,
independently confirmed, but in already-shipped Sprint 2.1/2.2 code
(`fundamentals_edgar.py` debt-tag extraction; `quant_screen.py` scoring
Real Estate on metrics §A14 already flagged as invalid for that sector,
still unfixed). Written up as [PRD_ADDENDUM.md
§A17](../PRD_ADDENDUM.md#a17-sprint-31s-judge-pass-found-two-pre-existing-sprint-2-defects--excluded-from-item-6-not-fixed-there)
rather than fixed on this branch. Decision: exclude the 20 affected
tickers from item 6 via `run_pipeline.py`'s new `--exclude` flag (an
operational filter, not a change to the persisted screen data) and track
the real fix as its own future sprint slot.

160 tests now (114 at the start of this sprint).

**Third pass** ([`judge-report-20260909-143940.md`](../judge-reports/judge-report-20260909-143940.md)), after the exclude
mechanism and the README/addendum updates below, confirmed a clean state
on everything this sprint owns: **159 passed, 0 failed, 1 skipped
(the opt-in live test) — test-suite verdict PASS**. The judge additionally
re-parsed all three persisted pilot responses and independently
re-verified all 99 citation hashes, offsets, and quotes against local
filing receipts (all valid, 91/91 asserted claims cited). The two Highs
are the same already-documented §A17 items, restated once more (a MEDIUM
about test coverage for those same two issues, and a LOW that the README
was stale — both addressed: README now has a Sprint 3.1 status block and
sprint-table row, and this retro's own §A17 cross-reference already
covered the test-coverage gap by naming the fix as its own future sprint's
job rather than this one's).

## Dry run (free) — cost established, one more bug found

`--batch --dry-run` was extended (this sprint) to run the real preflight
cost projection — free `count_tokens()` calls, no generation, no spend —
rather than stopping before it. First run, against the 71-ticker candidate
list (91 passed, minus §A17's 20 exclusions):

- **AAPL**: cache hit, $0 (already analyzed in the pilot).
- **GOOGL**: `no_filing` error — see below.
- **69 tickers**: real per-ticker projections. **Total projected: $17.351.**

**GOOGL's `no_filing` error is a real bug, not a fluke** — see
[§A18](../PRD_ADDENDUM.md#a18-dual-class-tickers-sharing-a-cik-silently-orphan-the-second-tickers-filing-row)
and [GitHub issue #3](https://github.com/petehawtree/moat/issues/3). GOOG
and GOOGL share one CIK and file identically; whichever ticker's W1 fetch
runs first claims the shared `filings` row, silently orphaning the other's
own ticker-keyed lookups. Excluded via `--exclude` alongside §A17's 20,
same reasoning: a correct fix (resolve via CIK, not `filings.ticker`,
across several call sites) isn't something to rush right before a live
spend for the one company it currently affects.

## Mini-pilot (real spend, $1.80) — found a systemic extraction bug

The dry run's per-ticker token counts ran 39k (LIN) to 279k (MRK) — a 7x
spread worth stress-testing the way JPM stress-tested the pilot. Ran
`scripts/analyze.py --tickers MRK PEG LIN` (sync, real spend) on the three
biggest outliers plus the smallest:

- **PEG, LIN**: `persisted`, coverage 1.0.
- **MRK**: `validation_failed` — one asserted claim (a debt figure) had no
  citation. Correctly handled: only an `analysis_attempts` row was
  written, no partial analysis.

Both MRK and PEG had fallen back to `full_fallback` — sending the *entire*
695k/792k-char document instead of targeted sections. Checking the
extraction trace found why, and it wasn't a fluke: `last_toc_cluster_pos`
was computed as the single latest `toc_cluster` rejection *anywhere in the
document*, including the ordinary Item 7A/8/9/9A/9B cluster — short,
tightly packed headings in essentially every normal 10-K, not just JPM's
IBR-stub case. **56 of the 69 real candidates (81%) hit this** — item 5a's
"zero current impact" framing was true only for the narrower thing it
measured (Financials on the stale 93-set), not for the actual production
universe. Fixed (see the code-history commit and
[sprint-3-section-extraction-rules.md](sprint-3-section-extraction-rules.md)'s
criterion-6 amendment): a `toc_cluster` rejection only counts toward
`last_toc_cluster_pos` when it sits at or before 20% of document length —
a threshold chosen from a measured, clean gap in the real data (7
companies at 8.8–13.3%, everything else at 30.1%+).

Re-extracting the affected companies under the fix (55 cleared; PEG's
cache was left alone — it already has real citations from the mini-pilot)
and re-running the same dry run:

| | Before fix | After fix |
|---|---|---|
| full_fallback rate | 56/69 (81%) | 21/69 (30%) |
| Total projected cost | $17.351 | **$12.149** |

The remaining 21 are confirmed different, unrelated causes (checked
AMGN/MO: tie-break ambiguity between multiple valid assignments; ZTS/UNH:
no consistent assignment found at all; NFLX: one section under its length
floor) — a more heterogeneous set than the one root cause this fix
addresses. Tracked, not chased further here:
[GitHub issue #4](https://github.com/petehawtree/moat/issues/4).

## Item 6 — the run

Submitted for real: 67 companies via the Batch API (AAPL, LIN, PEG
resolved as $0 cache hits — already analyzed via the pilot/mini-pilot).

**First attempt crashed before spending anything.** `submit_batch()`
referenced `anthropic.types.MessageCreateParamsNonStreaming`, which the
installed SDK (`anthropic` 0.121.0) no longer re-exports at that path —
an `AttributeError` on the very first request, before
`client.messages.batches.create()` was ever called. No batch was
submitted, no partial state was written. Root cause: every batch test
before this mocked `submit_batch()`/`caller.submit_batch` itself, so
nothing had ever exercised its request construction against the real
installed package. Fixed by building the request dict directly instead of
through the SDK's type-checking-only construction path (which is what can
silently move between SDK versions); a new test — mocking only the
network call, not `submit_batch()` itself — was confirmed to fail with the
identical error before the fix, pass after.

**Second attempt succeeded completely.** Batch `msgbatch_01DHyAxaXzgmuhZaFjfSqo9M`:
67 submitted, 67 persisted, 0 pending, 0 failed. Real cost **$9.324**
(under its own $12.149 projection — batch discount plus real output
running under the 8k-token assumption). MRK, which had failed citation
validation in the mini-pilot on `full_fallback`'s 279k-token full
document, persisted cleanly this time at 107k tokens under the fixed,
targeted extraction — one data point, but the right direction.

One harmless side effect of the crash-and-retry, cleaned up: the crashed
attempt's cache-hit resolution for AAPL/LIN/PEG had already written their
`analysis_attempts` audit rows before the crash; the successful retry
re-resolved and wrote a second set (cache hits aren't keyed by `custom_id`
the way batch items are, so nothing deduplicated them). No cost or data
impact — `ai_analysis` itself is correctly deduplicated by `(run_id,
ticker, analysis_type)` regardless — just doubled audit rows, deleted
manually (kept the retry's).

**Total real spend for item 6: $11.128** ($1.804 mini-pilot + $9.324
batch), against the plan's original ~$27.90 estimate and the $35 cap.
**70 companies now have current analyses** (280 `ai_analysis` rows, 2,312
citations).

## Next up

**The human read.** Per the plan's own definition of done, sample size and
selection method were left a discussion point rather than decided
sight-unseen — now there's real output to choose from, not just
token-count proxies. Sample chosen (confirmed 2026-09-09): **12 companies,
48 analyses** — one per GICS sector, all cleanly targeted-extraction, plus
three deliberate `full_fallback` stress picks (issue #4's still-unresolved
group):

- **Baseline** (one per sector, targeted extraction): META, ORLY, COST,
  EQT, GILD, VRSK, ADBE, CRH, NEE
- **High-risk** (`full_fallback`, issue #4): NFLX, UNH, BALL

**Done (2026-09-12).** Read via `python scripts/cite.py TICKER` for each,
same as the pilot's 12-analysis read, then evaluated by an LLM against the
PRD and this addendum, and that eval checked against the database. Full
report: [`judge-report-sprint-3-1-citation-sample-read-2026-09-12.md`](../judge-reports/judge-report-sprint-3-1-citation-sample-read-2026-09-12.md).

Coverage and citation-grounding hold exactly (401 claims: 361 asserted with
397 citations between them, 40 correctly marked `insufficient_evidence`,
zero uncited assertions, zero cited insufficiency claims). Three open gaps
carried forward, none of them Sprint 3.1 blockers:

- Management analyses aren't labelled "thin" per §A15.9, though they're
  10-K-only as designed.
- Entailment (quote *supports* the claim) remains unverified per §A15.2 —
  explicitly out of scope for Sprint 3, needs a human/entailment pass later.
- `companies.exchange` is `NULL` for BALL, CRH, EQT, NEE, UNH, VRSK —
  doesn't block anything yet but weakens auditing the US-only universe
  constraint (§A1).
