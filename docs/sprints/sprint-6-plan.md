# Sprint 6 — External-benchmark eval (plan)

**Status:** Planned — scope revised 2026-09-26. Supersedes the watchlist/
monitoring scope named for Sprint 6 since `sprint-0.md` and reaffirmed as
out-of-scope-for-Sprint-5 in `sprint-5-plan.md`'s "Out" list. See
[PRD_ADDENDUM.md §A26](../PRD_ADDENDUM.md) for the decision record.
Amended 2026-09-29 (§A27): Sprint 6.0 added as a data-baseline phase ahead
of the harness, and the MCP and multi-agent workflow experiments moved out to
[`future-sprints.md`](future-sprints.md). Sprint 6 is now eval only.

## Why the scope changed

Sprint 5 shipped the Investment Committee end to end, but its own retro is
explicit that what shipped isn't yet trustworthy in one specific place:
`assign_status()`'s 70/50 Investigate/Watch/Reject thresholds are starting
values, not pilot-validated (`sprint-5.md`'s "What the plan got wrong":
*"'Pilot-then-lock' named no standard to lock against... that is why it
moves to the eval rather than being ticked here"*). That external-benchmark
eval has been named as necessary in every Sprint 5 retro section since
("Carried forward") and in the last pre-push judge report's own top action
item, but nothing had actually scheduled it. Building watchlist monitoring —
live triggers that re-run the pipeline and re-surface a status — on top of a
status field that hasn't been validated would be automating a re-run around
a number that might itself be wrong.

Separately, this project's README states its purpose in two parts: the
investment-research output, and "the product-management and AI-engineering
lessons." The committee is this project's only multi-agent system (three
fixed personas, called sequentially, no tool use, no evaluation harness) and
it has never been benchmarked or tried any other way. That's a gap in the
second half of the project's own stated purpose, independent of the
watchlist question.

## Sprint 6.0 — eval baseline (added 2026-09-29, §A27)

The Morningstar capture (`docs/evals/benchmark-prep-outcome.md`) is measured
against screen run `20260923T122501Z`, which priced 427 of 518 tickers on
August closes (#15) and carries defects that move verdicts (#1, #16). Scoring
only that run would evaluate the pipeline as it was, not as it is. So a
narrow set of fixes lands first, and the harness scores **both** runs.

**Phase 0 — decisions, no code** (recorded 2026-09-29: §A27, and
`known-issues.md`'s design-decisions table)

- `free_cash_flow` stays operating cash flow minus capex. The 18% gap to the
  benchmark is definitional; Tier C scores it on direction and order of
  magnitude, not exact match.
- `total_debt` excludes lease obligations, documented as definitional. The
  NULLs are #1 and are fixed; the lease gap is not a defect.
- `fundamentals_quarterly` stays empty. Tier C uses the 29 period-matched
  companies plus ratios; TTM construction is a separate build, in
  [`future-sprints.md`](future-sprints.md).
- `roe` moves to average equity (#16).

**Phase 1 — fix, then re-ingest and re-run**

- #15 prices — detect empty responses, assert freshness, re-ingest.
- #1 re-scoped to the 128 affected companies — a debt-tag hierarchy
  replacing the 24-name `DEBT_TAG_GAP_TICKERS` exclusion.
- #16 average-equity ROE — persists `stockholders_equity`.
- #10 D&A split-tier and sign guard.
- Full pipeline re-run. Estimate committee re-score cost first: price
  changes move valuations, which invalidate committee cache entries.

Every changed field is diffed row by row against the baseline before the
re-run's output is used.

**Deliberately not fixed first:** #5, the single discount rate, and an
uncertainty-scaled margin of safety. They are valuation design questions and
run as eval experiments instead.

**Phase 2 — harness scores against run ids, not dates**

Tier A (contingency table, stratum gradient, lift over base rate), Tier B
(theme recall per `tier-b-reports/README.md`), Tier C (period-matched
subset). Both runs scored; the difference is each fix's measured effect.

**Phase 3 — scoring order:** SNA → NEE → EIX → ABNB + CRWD → WDC/LIN/EOG →
TPR → discount-rate spread, then the 70/50 thresholds below.

## Goal

Evaluate the pipeline as shipped, against the committee output from Sprint
6.0's re-run — no changes to `moat/ingest`, `moat/screen`, `moat/quality`,
`moat/analysis`, `moat/valuation` or `moat/committee` in scope here beyond
Sprint 6.0's list:

1. **Build the external-benchmark eval harness** the last three sprints have
   deferred to, and run the committee's `assign_status()` thresholds through
   it. This is the overdue Sprint 5 carry-forward item, not new scope.
2. **Measure the shipped committee design** with that harness — persona
   effectiveness: does each of Quality/Bear/Valuation Analyst change the
   final verdict in the direction and magnitude `compute_overall_score()`'s
   fixed weights assume, or do some personas dominate or get ignored in
   practice? This measures the current design; it does not try alternatives.

The harness this sprint builds is what the design experiments in
[`future-sprints.md`](future-sprints.md) will be scored with, so those are
planned once it exists.

## Out

- **Watchlist/monitoring** (`watchlist_events`, live re-run triggers) —
  deferred, not cancelled. `moat/monitor/watchlist.py` stays a documented
  stub (`NotImplementedError`); nothing was ever built against it, so
  nothing is stranded by deferring it further.
- **Changing `compute_overall_score()`'s shipped weights or
  `assign_status()`'s 70/50 thresholds** based on this sprint's findings —
  this sprint evaluates and experiments; deciding to change production
  scoring on the back of it is a follow-on decision, not this plan's DoD.
- **MCP as the committee's tool/context layer, and alternative multi-agent
  workflows** (debate, supervisor-worker) — each a sprint in its own right,
  moved to [`future-sprints.md`](future-sprints.md) (§A27).
- **FTSE 350 / UK universe** (§A1, still deferred).
- **Fixing any of the carried-forward Sprint 5 defects** (#11-#14), and any
  defect not on Sprint 6.0's list — separate from agent-design evaluation,
  tracked in `docs/known-issues.md` already.

## Definition of done (draft — refine once the harness exists)

- [ ] Sprint 6.0: Phase 0 decisions recorded; #15, #1, #16, #10 fixed with
  regression tests; re-run complete, with a row-level diff against
  `20260923T122501Z`.
- [ ] External-benchmark eval harness exists and scores both the baseline
  and the re-run, keyed by run id.
- [ ] `assign_status()`'s thresholds are either confirmed against the
  benchmark or replaced with a documented, evidenced alternative.
- [ ] Persona effectiveness measured for the shipped sequential design.
- [ ] Findings recorded in a retro regardless of outcome — an eval that
  shows the current design already holds up is a real result, not a null
  one.
