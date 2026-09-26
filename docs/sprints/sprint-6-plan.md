# Sprint 6 — Investment Committee eval + multi-agent R&D (plan)

**Status:** Planned — scope revised 2026-09-26. Supersedes the watchlist/
monitoring scope named for Sprint 6 since `sprint-0.md` and reaffirmed as
out-of-scope-for-Sprint-5 in `sprint-5-plan.md`'s "Out" list. See
[PRD_ADDENDUM.md §A26](../PRD_ADDENDUM.md) for the decision record.

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

## Goal

Two threads, both against the existing Sprint 5 committee output —
no changes to `moat/ingest`, `moat/screen`, `moat/quality`, `moat/analysis`,
or `moat/valuation` in scope here:

1. **Build the external-benchmark eval harness** the last three sprints have
   deferred to, and run the committee's `assign_status()` thresholds through
   it. This is the overdue Sprint 5 carry-forward item, not new scope.
2. **Evaluate and experiment with the committee's own agent design**, using
   that harness as the scoring mechanism:
   - Persona effectiveness — does each of Quality/Bear/Valuation Analyst
     change the final verdict in the direction and magnitude
     `compute_overall_score()`'s fixed weights assume, or do some personas
     dominate/get ignored in practice?
   - MCP as the tool/context layer for committee agents, in place of the
     current hand-assembled prompt context block.
   - Alternative multi-agent workflow shapes — debate (personas see and
     respond to each other before a verdict), supervisor-worker (a fourth
     agent adjudicates disagreement) — evaluated against the fixed
     sequential-and-independent design Sprint 5 shipped.

## Out

- **Watchlist/monitoring** (`watchlist_events`, live re-run triggers) —
  deferred, not cancelled. `moat/monitor/watchlist.py` stays a documented
  stub (`NotImplementedError`); nothing was ever built against it, so
  nothing is stranded by deferring it further.
- **Changing `compute_overall_score()`'s shipped weights or
  `assign_status()`'s 70/50 thresholds** based on this sprint's findings —
  this sprint evaluates and experiments; deciding to change production
  scoring on the back of it is a follow-on decision, not this plan's DoD.
- **FTSE 350 / UK universe** (§A1, still deferred).
- **Fixing any of the carried-forward Sprint 5 defects** (#10-#14) — separate
  from agent-design evaluation, tracked in `docs/known-issues.md` already.

## Definition of done (draft — refine once the harness exists)

- [ ] External-benchmark eval harness exists and runs against the current
  81-company committee output.
- [ ] `assign_status()`'s thresholds are either confirmed against the
  benchmark or replaced with a documented, evidenced alternative.
- [ ] At least one MCP-based prompt/context experiment run against the
  harness, with a documented result (better, worse, or inconclusive —
  all three are valid outcomes to record).
- [ ] At least one alternative multi-agent workflow (debate or
  supervisor-worker) run against the harness and compared to the shipped
  sequential design.
- [ ] Findings recorded here or in a retro regardless of outcome — an
  experiment that shows the current design already holds up is a real
  result, not a null one.
