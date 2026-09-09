# Sprint 3.1 — citation/batch backlog + the authorized 90-company run

**Status:** Items 1–5 done. **Item 6 (the 90-company run) not started** —
holds for a separate go-ahead before spending against the Batch API; see
[Next up](#next-up). **Plan:** [sprint-3-1-plan.md](sprint-3-1-plan.md)

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

## Next up

**Item 6 — the 90-company run — holds for a separate go-ahead.** Per
discussion at the start of this sprint: the run spends real money
(~$27.90, inside the $35 production cap) and submits every remaining
company's filing data to the Batch API, so it doesn't proceed on items 1–5
landing alone. Also still open, deferred to when the run happens rather
than decided in the abstract now: sample size and selection method for the
human read of the 90 analyses (the pilot's convention — reading all 12 of a
3-company pilot — doesn't scale directly, and the plan deliberately left
this a discussion point rather than deciding it sight-unseen).
