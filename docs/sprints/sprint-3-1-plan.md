# Sprint 3.1 — citation/batch backlog + the authorized 90-company run (plan)

**Status:** Planned
**Implements:** the Sprint 3 deferred backlog (see
[sprint-3-plan.md § Deferred to Sprint
3.1](sprint-3-plan.md#deferred-to-sprint-31)), addendum
[§A16.1](../PRD_ADDENDUM.md#a161-sprint-4-is-now-two-sprints-not-one)

## Why this is its own sprint

Sprint 3's plan document labeled this backlog "Sprint 4." It isn't — none of
it touches valuation, and the PRD's Sprint 4 (the DCF/Owner Earnings engine)
doesn't depend on any of it. Renamed and split out 2026-09-08, the same way
Sprint 2's post-review fixes got `2.1`/`2.2` rather than being folded into
Sprint 3. See [§A16.1](../PRD_ADDENDUM.md#a161-sprint-4-is-now-two-sprints-not-one)
for the full reasoning.

## Goal

Close the five items Sprint 3's judge rounds and pilot surfaced but left
outside its definition of done, then run — and this is the actual point of
fixing the batch path — the deferred 90-company AI analysis pass that the
pilot's 3 companies were standing in for.

## Scope

**In** (full detail on each in
[sprint-3-plan.md § Deferred to Sprint
3.1](sprint-3-plan.md#deferred-to-sprint-31)):

| # | Item | Severity |
|---|---|---|
| 1 | Non-offline runs silently reuse stale filings — no freshness check against SEC submissions before trusting the cache | HIGH |
| 2 | Batch workflow has no end-to-end path — `retrieve_batch()` is unwired, nothing persists results or retries by `custom_id` | HIGH |
| 3 | Citation resolution ladder implements only the exact rung — moved/renormalized/fuzzy are stubbed | MEDIUM |
| 4 | Test coverage gaps — no test on `run_ai_analysis_stage`, `retrieve_batch`, `_reanchor`, refresh/amendment/batch round-trips, or a real citation-response fixture | MEDIUM |
| 5a | `toc_cluster` misreads a real IBR-stub Item 7 (JPM) as a table of contents | LOW — zero current impact, no Financials in `passed_screen` |
| 5b | Parser doesn't recognize the model's `---` section divider | LOW — cosmetic, citations unaffected |
| 6 | **Run the authorized 90-company batch** on the corrected extraction path and the (now-wired) `--batch` submission path | — the deliverable this backlog exists to unblock |

**Out:** anything from PRD §6 onward (valuation, committee, monitoring) —
those are Sprint 4+ and don't read this sprint's tables.

## Sequencing

Items 1–4 are prerequisites for item 6, not independent cleanup: running 90
companies unattended on a batch path that has never round-tripped, against a
stale-filing check that doesn't exist yet, is exactly the failure mode the
pilot was designed to catch before scaling past 3 companies. Fix order:
1 (freshness) → 2 (batch round-trip) → 4 (tests, written against 1–2 as they
land) → 3 (reanchor ladder — independent, can slot in anywhere) → 5a/5b (low
severity, zero current production impact) → 6 (the run).

## Cost

Item 6 runs at the production architecture Sprint 3 already priced: one
combined citation-enabled request per company through the Batch API,
~$0.30/company, **~$27.90 for the 90 remaining companies** — inside the
$35 production cap confirmed in Sprint 3's Decision 4. Items 1–5 make no
model calls.

## Definition of done

- All five backlog items resolved or explicitly re-deferred with a reason
  (not silently carried forward again).
- `retrieve_batch()` reachable end-to-end from `run_pipeline.py`, with a test
  exercising a real batch round-trip.
- The 90-company run completes under the $35 cap, with the same per-company
  transactional guarantee the pilot already has (four analyses or an
  `analysis_attempts` row, never partial).
- A human reads a sample of the 90 the way all 12 pilot analyses were read
  (full read of all 90 is not proposed as a gate — sample size and selection
  method are a discussion point, not decided here).
- `sprint-3-1.md` retrospective written, same convention as prior sprints.
