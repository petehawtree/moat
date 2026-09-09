# Sprint 4 — Owner Earnings DCF + scenario valuation (plan)

**Status:** Planned — draft for review
**Implements:** PRD §6, addendum
[§A16](../PRD_ADDENDUM.md#a16-sprint-4--valuation-architecture-implements-prd-6)
**Independent of:** [Sprint 3.1](sprint-3-1-plan.md) — this stage reads
`fundamentals_annual` and `quality_scores`, never `ai_analysis`/`citations`,
so it does not wait on the citation/batch backlog. See
[§A16.1](../PRD_ADDENDUM.md#a161-sprint-4-is-now-two-sprints-not-one).

## Goal

For every company that clears the quant screen, compute an intrinsic-value
range (bear/base/bull) via Owner Earnings DCF, cross-checked against three
supporting methods, and a margin of safety against the current price — PRD
§6's output spec, persisted to the already-schema'd `valuations` table.

## Why this isn't a two-line implementation of the existing stub

`moat/valuation/engine.py` has stood since Sprint 0 with `owner_earnings`,
`dcf_scenario` and `run_valuation` all raising `NotImplementedError`. The
temptation is to treat this sprint as "fill in the function bodies." Checking
the stub's own formula against the schema says otherwise: `owner_earnings()`'s
docstring is `net income + D&A − maintenance capex − ΔNWC`, and neither D&A
nor working-capital change exists in `fundamentals_annual` or anywhere in
`fundamentals_edgar.py`'s candidate tags. This is Sprint 3's Build-A/Build-B
shape recurring — a data layer has to be built before the math that was
"just a formula" can run at all. Full reasoning in
[§A16.2](../PRD_ADDENDUM.md#a162-owner-earnings-needs-data-this-pipeline-has-never-ingested).

## Scope

**In:**
- Owner Earnings DCF (primary method, PRD §6), three scenarios (bear/base/
  bull), for every company in the current `quality_scores` pass set (93 as of
  the last screen run — this will move as fundamentals refresh; the stage
  reads whichever `quality_scores` run is current, the same pattern W7 used
  for AI analysis).
- Supporting methods: FCF yield, EV/EBIT, P/E vs. its own historical range.
- Margin of safety, computed off the conservative (low) end of the range per
  PRD §1, with the sign-inversion guard from
  [§A16.4](../PRD_ADDENDUM.md#a164-two-findings-folded-in-before-build).
- New ingest: D&A, working-capital change (flagged, not reconstructed, when
  unavailable — §A16.2), extended price-history depth for the historical P/E
  range.
- Persistence to `valuations` (schema already exists, zero rows currently)
  and a dashboard column/panel showing the range, current price and margin
  of safety per company.

**Out:**
- Company-specific discount rates (CAPM/WACC) — fixed rate per
  [§A16.3](../PRD_ADDENDUM.md#a163-discount-rate-fixed-and-conservative-not-company-specific-wacc).
- Financials/banks and (per §A14, still-deferred) REITs — already excluded
  from the quant screen as unscreenable; a bank or REIT that later gets a
  valid sector-specific screen still needs a valuation methodology suited to
  it (FCF-based DCF misdescribes both), which is not this sprint's problem to
  solve pre-emptively.
- Anything consuming `ai_analysis` (bull/bear narrative, moat evidence) —
  that join is Sprint 5's committee/brief, per §A15.9.
- Sprint 3.1's backlog — see the independence note above.

## Decisions confirmed 2026-09-08

| # | Decision | Resolution |
|---|---|---|
| 1 | Sprint numbering | Split from the "Sprint 4" label Sprint 3 had used for its own backlog — see [§A16.1](../PRD_ADDENDUM.md#a161-sprint-4-is-now-two-sprints-not-one) and [sprint-3-1-plan.md](sprint-3-1-plan.md). |
| 2 | Owner Earnings fidelity | Extend ingest for true D&A + ΔNWC rather than a FCF-proxy shortcut — [§A16.2](../PRD_ADDENDUM.md#a162-owner-earnings-needs-data-this-pipeline-has-never-ingested). |
| 3 | Discount rate | Fixed conservative rate (9–10%, starting point) across all companies and scenarios; scenarios differ by growth/margin assumptions, not discount rate — [§A16.3](../PRD_ADDENDUM.md#a163-discount-rate-fixed-and-conservative-not-company-specific-wacc). |

These three are confirmed; the work breakdown, exact starting parameters, and
company-list scoping below are the draft this document exists to put in front
of you.

## Schema changes

Additive only, same convention as Sprint 3.

```sql
-- fundamentals_annual gains two nullable columns, same provenance shape
-- as the existing free_cash_flow/capex columns.
ALTER TABLE fundamentals_annual ADD COLUMN depreciation_amortization REAL;
ALTER TABLE fundamentals_annual ADD COLUMN working_capital_change REAL;
-- (quality_flags already exists and gains a new flag value:
--  'nwc_unavailable_treated_as_zero')
```

`valuations` needs no changes — it already carries `method`, `scenario`,
`intrinsic_value_low/high`, `current_price`, `margin_of_safety_pct` and a JSON
`key_assumptions` column, which is where the discount rate, growth rate and
terminal growth used for each row get recorded (so a stored valuation is
self-describing without a second table).

## Work breakdown

| # | Work | Acceptance |
|---|---|---|
| V1 | **Extend fundamentals ingest** — D&A (merge-across-candidates: `DepreciationDepletionAndAmortization` → `DepreciationAmortizationAndAccretionNet` → `Depreciation`+`AmortizationOfIntangibleAssets`) and working-capital change (`IncreaseDecreaseInOperatingCapital` only; `NULL` + quality flag otherwise, per §A16.2's flag-don't-guess rule). Extend `fetch_price_history`'s default window past 2y for historical-range support (§A16.4). | New columns populated for the current 93-company set with `source`/`confidence`/`accession_number` provenance matching existing columns; a spot-check against 3–5 companies' raw XBRL (same method §A7/§A9 used) before trusting the merge across the full set; `quality_flags` shows `nwc_unavailable_treated_as_zero` rather than a silently-summed wrong number for filers without the summary tag. |
| V2 | **Owner Earnings + DCF core** — implement `owner_earnings()` against the real formula now that its inputs exist; `dcf_scenario()` for one scenario, run three times with bear/base/bull growth and terminal-growth assumptions at a fixed discount rate. | Unit tests with hand-computed expected values (independent arithmetic, not derived from the implementation — same standard as §A12's regression tests) for at least one capital-light and one capital-intensive company. |
| V3 | **Margin of safety, with the sign guard** — fix the existing formula so a negative or zero `intrinsic_value_low` produces an explicit non-numeric verdict, never a sign-flipped percentage (§A16.4). | A test asserting a negative-bear-case company does *not* report a positive margin of safety; dashboard renders that case distinctly (e.g. "bear case: negative — not investable on this basis") rather than a number. |
| V4 | **Supporting methods** — FCF yield (already-stored FCF ÷ market cap), EV/EBIT (market cap + debt − cash, over operating income), P/E vs. its own 5–10yr range (needs V1's extended price history). | All three computed and persisted per company; P/E-range method reports its own confidence/coverage when fewer than N years of price history are available for a recently-listed company, rather than silently truncating the range. |
| V5 | **Persist + wire the stage** — `valuation` stage in `run_pipeline.py`, one transaction per company into `valuations`, reading the latest successful `quality_scores` run (same pattern as Sprint 3's W7 fix, applied from the start this time rather than found by a judge round). | A re-run against an unchanged `quality_scores`/fundamentals pair either recomputes cheaply (deterministic, no cache-key machinery needed — unlike AI analysis, there's no API cost to avoid) or is explicitly idempotent per run_id; no partial company writes. |
| V6 | **Dashboard** — Company/Quality/Moat columns already exist (PRD §9); add Valuation range, current price, margin of safety. | Ranked dashboard shows all four PRD §9 columns plus the new ones; a company with a negative-bear-case flag (V3) renders visibly differently from a normal range, not as a malformed number. |

Target test count and fixture style match Sprint 3's convention (hand-derived
expected values, not implementation-derived) — a specific number isn't set
here since V1's tag-variance surface is the unknown that determines it, the
same way Sprint 3 couldn't size its own test count until section extraction's
real failure modes were found.

## Risks

| Risk | Mitigation |
|---|---|
| **Working-capital tag fragmentation** — unlike D&A, most filers don't report one canonical ΔNWC line; guessing which fragmented tags to sum risks a confident wrong number, the same class of defect as §A10's split detector. | Flag-don't-guess (§A16.2): only the summary tag is trusted; everything else is `NULL` + flagged, never summed from fragments. |
| **Negative bear-case owner earnings** — not an edge case, it's what a bear case is for. The existing `margin_of_safety()` formula silently inverts sign on it. | V3 is a named work item specifically because this was found before, not after, a company reached the dashboard with a false margin of safety. |
| **Discount-rate defensibility** — a flat rate doesn't reflect a leveraged industrial vs. a debt-free software company's actual risk. | Accepted per §A16.3: the bear/base/bull range is where that uncertainty is meant to live under PRD §1's own framing. Revisit if pilot output (a handful of companies, read by a human, same posture as Sprint 3's pilot) shows the flat rate producing obviously wrong rankings. |
| **Historical P/E range for recently-listed companies** — extending price history to 5–10y doesn't help a company that IPO'd 18 months ago. | V4 reports reduced confidence/coverage rather than a range built on insufficient history, matching A4/A13's established "state the limitation, don't hide it" pattern. |
| **Company-list churn** — the 93-company `passed_screen` set will change as fundamentals refresh quarterly (§A6) and as Sprint 3.1/future sector work changes screen eligibility. | V5 reads whichever `quality_scores` run is current at valuation time, same as Sprint 3's AI-analysis stage; no hardcoded company list. |

## Cost

**$0.** This stage is deterministic arithmetic over already-ingested (plus
V1's small extension) data — no LLM calls, no API spend. Worth stating
plainly given Sprint 3's cost section was most of that plan: this sprint's
entire cost surface is engineering time, not a spend cap.

## Open for discussion (not yet decided)

- **Exact discount-rate and terminal-growth starting numbers** — §A16.3
  proposes 9–10% / 1.5–3% as a starting point, explicitly to be tuned against
  real output rather than locked in advance, same posture as the quality
  score's 50.0 threshold. Worth eyeballing the pilot output before treating
  these as final.
- **Sample size and method for the human-read gate** — Sprint 3 read all 12
  pilot analyses. Whether Sprint 4 needs an equivalent human-read checkpoint
  before trusting the ranked valuations (and if so, how many companies /
  which ones) isn't decided here.
- **Whether V1's price-history extension should also backfill for the full
  505-company universe or only the 93 that pass the screen** — the screen is
  already the cost gate for AI analysis; whether it should gate ingest depth
  too is a smaller question but worth a deliberate answer rather than
  defaulting either way.

## Definition of done

- Every company in the current `quality_scores` pass set has either a full
  valuation (four methods, three DCF scenarios) or an explicit reason it
  doesn't (insufficient price history, missing D&A/NWC inputs beyond what
  V1's flagging tolerates).
- No company can show a positive-looking margin of safety on a negative
  intrinsic value (V3's guard, tested).
- `valuations` is populated and the dashboard renders it per PRD §9/§10.
- `sprint-4.md` retrospective written, same convention as prior sprints —
  including what this plan got wrong, per that convention.
