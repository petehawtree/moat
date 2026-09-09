# Sprint 3.1 — citation/batch backlog + the authorized 90-company run

**Status:** Items 1–5 done, and independently judge-reviewed twice (below).
**Item 6 not started** — holds for a separate go-ahead, and its scope is
now **70 companies, not 90** (see [Judge review](#judge-review) and
[Next up](#next-up)). **Plan:** [sprint-3-1-plan.md](sprint-3-1-plan.md)

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

Run twice via `./judge.sh` against this branch (reports in `.judge/`, not
committed). **First pass** (`judge-report-20260909-131716.md`) found three
real Highs, all confirmed by direct code inspection before fixing, all in
this sprint's own code:

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

**Second pass** (`judge-report-20260909-134928.md`), against the fixes and
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

## Next up

**Item 6 — the 70-company run — holds for a separate go-ahead.** Per
discussion at the start of this sprint: the run spends real money and
submits filing data to the Batch API, so it doesn't proceed on items 1–5
landing alone. Scope corrected from the plan's "90" to **70**: the fresh
screen (91, not 93) minus AAPL (already analyzed) minus the 20 tickers
§A17 excludes for known debt/REIT data-quality gaps. Also still open,
deferred to when the run happens rather than decided in the abstract now:
sample size and selection method for the human read of the analyses (the
pilot's convention — reading all 12 of a 3-company pilot — doesn't scale
directly, and the plan deliberately left this a discussion point rather
than deciding it sight-unseen).
