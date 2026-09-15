# Sprint 5 — Investment Committee + Investment Brief (plan)

**Status:** Planned — draft for review
**Implements:** PRD §7 (Investment Committee), §8 (MVP investment score), §9
(ranked dashboard — the columns not yet consolidated into one view), §10
(Investment Brief).
**Depends on:** Sprint 3/3.1's `ai_analysis`/`analysis_claims`/`citations`
and Sprint 4's `valuations` — the first sprint that reads *both* upstream
outputs together for the same company, which is exactly what §A15.9 named
as deferred to here ("joining the two into one evidence trail is Sprint
5's brief, not this sprint's").

## Goal

For every company with both a current AI analysis and a current valuation,
run three LLM persona perspectives (Quality Analyst, Bear Analyst, Valuation
Analyst — PRD §7), consolidate them into the PRD §8 weighted score and an
Investigate/Watch/Reject status, and assemble a one-page Investment Brief
(PRD §10) — persisted to the already-schema'd `committee_verdicts` table and
surfaced as the PRD §9 ranked dashboard's final form.

## Why this isn't a two-line implementation of the existing stub

`moat/committee/committee.py` already has `compute_overall_score()` fully
implemented and correct against PRD §8's weights, and stubs
`assign_status()`/`run_committee()` as `NotImplementedError` with an honest
`TODO (Sprint 5)`. Filling those in isn't the hard part. Three things are:

1. **The upstream join has real gaps a plan-time dry run already found**
   (below) — building persona prompts against an assumed "the pass set"
   universe without checking this first repeats exactly the mistake §A19.1
   exists to prevent.
2. **PRD §10's Investment Brief asks for content no current table stores.**
   `committee_verdicts` has three persona-view text columns and six score
   columns — good enough for PRD §7/§8. It has no column for "investment
   thesis," "bull case," "key things to monitor" or "AI conclusion," which
   §10 asks for by name. Quality/Bear/Valuation Analyst is not the same
   three-way split as Bull case/Bear case/Key monitors — mapping one onto
   the other is a real design decision, not a renaming exercise.
3. **§A19.6 already ruled that Sprint 5 must decide, before implementation,
   how citation entailment and "thin" analyses are handled in a
   committee-facing brief** — not inherit Sprint 3's citation-grounding
   (quote-exists, never checked-supports-claim) as if it were sufficient.
   This is a named precondition of this plan, not an open nice-to-have.

## Known join-set gap, found by a dry run before writing this plan (§A19.1)

Querying the live database (`is_current=1` on `ai_analysis`, latest
non-failed `valuations` run) rather than assuming numbers from prior
retros:

| Set | Count |
|---|---|
| `passed_screen` (latest quality_scores run) | 91 |
| Current `ai_analysis` (distinct tickers) | 72 |
| Current `valuations` (distinct tickers, latest run) | 70 |
| **Intersection — has both** | **69** |

The 4 mismatches, checked individually rather than assumed to be the same
kind of gap:

- **JPM, KO** — have `ai_analysis` (Sprint 3's original 3-company pilot)
  but no current `valuations` row, because neither currently passes the
  screen (JPM: Financials, only 3/8 metrics assessable, §A14; KO: 33%
  composite, below the 50% bar). Not a bug — pilot leftovers that predate
  the current pass set, and correctly absent from valuation.
- **BKNG** — passes the current screen but has no valuation row: the
  price/shares data inconsistency found in §A20, excluded operationally.
- **GOOGL** — has a valuation (doesn't need filing text) but no current
  `ai_analysis` (does need filing text): the dual-class-ticker filing
  collision, §A18, GitHub issue #3, confirmed still open, excluded from
  the citation-dependent stages only.

**Decided (2026-09-15, below):** pilot against the 69-company intersection
as-is; closing JPM/KO/GOOGL is separable, non-blocking work.

## Decisions confirmed 2026-09-15

| # | Decision | Resolution |
|---|---|---|
| 1 | Entailment/thin-labelling posture (§A19.6) | Surface the raw cited quote inline next to every inference in the brief UI; no second-pass entailment LLM/heuristic check. Cheapest option, ships fastest, matches PRD §14's "the human remains the investment committee" — the reader entailment-checks by eye while reading, same posture as every other "state it, don't hide it" decision in this codebase (§A4, §A13). |
| 2 | Committee input universe (C1) | Pilot/build against today's 69-company intersection as-is. JPM/KO (stale pilot leftovers, don't currently pass the screen) and GOOGL (§A18's dual-class bug, GitHub #3) are real but separable gaps — closing them doesn't gate this sprint's start, same reasoning Sprint 4 used to *not* let §A17's pre-existing gaps block its own work. |
| 3 | Brief synthesis content (C5) | Template-stitch "investment thesis"/"AI conclusion" from the three existing persona texts + `overall_score`/`status` — no 4th consolidation LLM call. No new LLM cost, no new citation-grounding/caching surface. Consequence: "bull case"/"bear case" in the brief UI are the existing `quality_analyst_view`+`valuation_analyst_view` / `bear_analyst_view` columns under PRD §10's section headers, not separately-generated content — see the Schema changes section below, now resolved rather than draft. |

These three are confirmed; C1's exact ticker list (re-derived from the live
DB, not copied from this plan), C4's status thresholds, and the spend cap
remain open — see "Open for discussion" below.

## Scope

**In:**
- Three persona LLM calls (Quality / Bear / Valuation Analyst, PRD §7),
  each grounded the same way Sprint 3 already enforces (§A3: every claim
  resolves to a citation; §A5: cached per company, not re-run every
  pipeline pass) — reusing `moat/analysis/`'s caller/pricing/prompt/persist
  pattern rather than building a second, parallel LLM-calling path.
- Consolidation: wire `compute_overall_score()` (done) into `run_committee()`;
  decide and implement `assign_status()`'s Investigate/Watch/Reject
  thresholds.
- Investment Brief content assembly (PRD §10) — joining `ai_analysis` +
  `citations` (qualitative evidence) with `valuations` + `fundamentals_annual`
  `accession_number` provenance (quantitative evidence) into one evidence
  trail per company, the specific gap §A15.9 named for this sprint.
- The entailment/thin-labelling decision (§A19.6) and its implementation.
- Dashboard: one consolidated PRD §9 ranked view (Company, Quality, Moat,
  Valuation, Overall Score, Status) replacing/sitting alongside the three
  separate Sprint 1/2/4 sections that exist today; click-through to a
  one-page Investment Brief (PRD §10) per company.
- Defaulting the known-bad-ticker exclusion for this stage (see C8 below)
  rather than leaving it opt-in — the committee's output is the actual
  investment recommendation, the highest-stakes place for a known-invalid
  input (BKNG's price/share mismatch, the 18 debt-tag-gap companies) to
  leak through unflagged.

**Out:**
- Sprint 6's watchlist/monitoring (`watchlist_events`, live triggers) —
  "key things to monitor" in the brief is *authored content* describing
  what to watch, not the live monitoring logic that watches it.
- FTSE 350 / UK universe (§A1, still deferred).
- DEF 14A proxy statements, multi-year filing history, 10-Qs (§A15.9,
  still deferred — a thin management analysis gets *labelled* thin per
  §A19.6's decision, not fixed by ingesting more documents here).
- Fixing the debt-tag extraction gap (§A17, GitHub #1) or REIT
  sector methodology (§A14) — pre-existing, filed, deliberately not fixed
  across three prior sprints; continuing to exclude operationally unless
  you want to reprioritize (see "Open for discussion").
- Backfilling price history for a fuller P/E range (§A21) — in scope here
  only as a decision (does the committee's `valuation_score` use or
  discount the P/E cross-check given 91/91 report `low_confidence`?), not
  necessarily as an ingest change.
- `moat/ai/analysis.py` — a pre-Sprint-3 stub (`ANALYSIS_TYPES`,
  `needs_refresh()`, all `NotImplementedError`) that nothing imports; the
  real implementation has lived in `moat/analysis/` since Sprint 3. Worth
  deleting as dead code in this sprint's cleanup, not treating as a second
  thing to implement.

## Schema changes

Additive only, same convention as Sprints 3 and 4. `committee_verdicts`
already covers PRD §7/§8 (three persona views, six weighted scores, overall
score, status, data_confidence). Per decision 3 above, `bull_case`/
`bear_case` need no new columns — the brief UI relabels the existing
`quality_analyst_view`+`valuation_analyst_view` / `bear_analyst_view`
columns under PRD §10's section headers. The remaining fields (genuinely
new content, not a relabelling) still need somewhere to live:

```sql
ALTER TABLE committee_verdicts ADD COLUMN investment_thesis TEXT;
ALTER TABLE committee_verdicts ADD COLUMN key_things_to_monitor TEXT;  -- JSON array
ALTER TABLE committee_verdicts ADD COLUMN ai_conclusion TEXT;
```

## Work breakdown

| # | Work | Acceptance |
|---|---|---|
| C1 | **Confirm the committee-eligible universe.** Decided: pilot the 69-company intersection as-is. | A documented, current list of tickers Sprint 5 actually runs against, re-derived from the live DB at build time (not copied from this plan, which will be stale by then — same lesson as sprint-4-plan's "93 as of the last screen run" going stale before V1 started). |
| C2 | **Implement the entailment/thin-labelling posture (§A19.6).** Decided: raw cited quote surfaced inline next to every inference in the brief UI, no second-pass check. | Every claim the brief surfaces shows its literal cited quote alongside it, verified against a sample brief, not just designed to. |
| C3 | **Three persona prompts** — Quality/Bear/Valuation Analyst, each reading the target company's `ai_analysis` (+ citations) and `valuations` rows, citation-enforced and cached the same way Sprint 3's stages already are. | Citation-grounding tests in the same style as `tests/test_analysis_parser.py`/`test_cite_reanchor.py`; cache hit on a re-run against unchanged upstream data (no API spend), cache miss when the source `ai_analysis`/`valuations` run_id changes. |
| C4 | **Consolidation** — wire `compute_overall_score()` into `run_committee()`; implement `assign_status()`. Thresholds are a judgment call (the function's own TODO already says so) — pilot against a handful of real companies before locking numbers, same posture as Sprint 4's discount rate. | `run_committee()` persists one `committee_verdicts` row per company with no `NotImplementedError`; a documented pilot read (5-10 companies spanning clear-pass/borderline/clear-reject) before thresholds are called final. |
| C5 | **Investment Brief content assembly** — company overview (`companies`), investment thesis + AI conclusion (template-stitched from the three persona texts + `overall_score`/`status`, decided above — no 4th LLM call), moat evidence (`ai_analysis` 'moat' + its `citations`), financial quality (`ai_analysis` 'business_quality' + `quant_scores`), valuation range + margin of safety (`valuations`, already correct from Sprint 4 — no recomputation here), bull/bear case (`quality_analyst_view`+`valuation_analyst_view` / `bear_analyst_view`, relabelled per decision 3), key things to monitor (new, authored — see Out: not live monitoring logic). | Every PRD §10 field has a defined source (existing column, join, or template rule) written down before C5 is built, not improvised per-field during it. |
| C6 | **Persist + wire the stage** — `committee` stage in `run_pipeline.py`, reading the latest successful `quality_scores`/`valuations`/`ai_analysis`, same "read whichever run is current" pattern as Sprint 3's W7 and Sprint 4's V5 (applied from the start, not found by a judge round a third time). | Idempotent per `run_id` (delete-then-reinsert, same reasoning as `valuations`' `_replace_valuations` — `committee_verdicts`' primary key has no nullable column so this one could actually use `ON CONFLICT`, but confirm before assuming); one transaction per company. |
| C7 | **Dashboard** — one consolidated PRD §9 ranked view; click-through one-page Investment Brief (PRD §10). | Ranked table matches PRD §9's exact column list; brief page renders every §10 field or an explicit "not available" per field, never a blank; a thin/low-confidence company (data_confidence ≠ high, or an entailment-flagged claim per C2) visibly reads as such, not identically to a high-confidence one. |
| C8 | **Default-exclude known-bad tickers for this stage.** The judge's own top action item: the 20+1-ticker exclusion (§A17/§A18/§A20) is `--exclude`, opt-in, on every stage that has it today — including a stage whose output is a ranked investment recommendation is the wrong place for that to stay optional. | Committee stage applies the exclusion list by default; `--include-excluded` (or similar) opts back in explicitly for someone who wants to see the excluded companies' verdicts anyway, rather than the current "forget the flag, get an unvetted number" default. |

## Risks

| Risk | Mitigation |
|---|---|
| **Upstream data-confidence doesn't automatically roll up into the committee's verdict.** A company with `medium`/`low` confidence fundamentals (§A4), or one of the 18 debt-tag-gap / REIT-excluded companies that slipped past C8's default exclusion some other way, could rank identically to a fully-clean company. | `committee_verdicts.data_confidence` already exists in the schema for exactly this — C4/C6 must actually populate and roll it up (min across contributing sources), not leave it NULL as a formality. |
| **Entailment gap (§A19.6).** Sprint 3's citation-grounding proves a quote exists, not that it supports the claim; some moat claims already extend past their literal cited excerpt (12-company eval finding, §A19.6). Un-decided, this becomes a committee-facing correctness gap, not an internal caveat. | C2 is a named, blocking work item specifically because §A19.6 already ruled it must be decided before implementation. |
| **`assign_status()` thresholds are unvalidated.** A wrong bar produces a confidently wrong Investigate/Watch/Reject label, the single most consequential number this sprint produces. | C4's pilot-then-lock approach, same discipline as Sprint 4's discount rate and the screen's 50.0/66.7th-percentile bars — a documented starting point, revisited against real output before being treated as final. |
| **Cost** — three LLM calls per company, at up to 69 companies, is a larger surface than Sprint 3's 3-company pilot. | Reuse Sprint 3's batch submission + spend-cap machinery (`moat/analysis/`) rather than a new path; pilot on a small, deliberately-varied subset of C1's 69 first (same as Sprint 3's AAPL/KO/JPM pilot) before a full run; set an explicit cap up front (see Cost below). |
| **Join-set churn** — the 69-company intersection will move every time `ai_analysis`, `valuations` or `quality_scores` refreshes, independently of each other. | C6 reads whichever runs are current at committee time, same "no hardcoded company list" posture as Sprint 3/4; C1's dry-run check is worth re-running immediately before any real (paid) batch, not just once at plan time. |

## Cost

Not $0 like Sprint 4 (deterministic arithmetic) — this sprint makes real LLM
calls, closer to Sprint 3/3.1's cost shape. Rough order of magnitude: 3
persona calls × 69 companies (C1's confirmed universe, no 4th call per
decision 3) is roughly 3x Sprint 3.1's 70-company/one-call-per-company batch
run ($9.32 batch spend, §A17/sprint-3-1.md) — call it a **$20-30 range**, to
be tightened once C1's exact ticker list is re-derived at build time.
Recommend an explicit spend cap (same mechanism as Sprint 3's `$15`/Sprint
3.1's `$35`) confirmed before any paid run, not assumed from this estimate.

## Open for discussion (not yet decided)

1. **Whether to fold in any of the judge's other standing findings** —
   debt-tag extraction (§A17), REIT methodology (§A14), price-history
   backfill (§A21) — into this sprint rather than continuing to defer them.
   *Recommendation:* continue deferring all three except C8 (which this
   plan already treats as in-scope, since it's specifically about this
   sprint's own stage) — consistent with every prior sprint's decision on
   the same three items, and none of them block C1-C7 from producing a
   correct verdict on the companies that aren't affected.
2. **Spend cap** for the persona LLM calls — needs a specific number before
   C3's pilot, not just the rough range above.
3. **`assign_status()`'s exact thresholds** — deliberately left for C4's
   pilot-then-lock step rather than guessed here, same posture as Sprint
   4's discount rate.

## Definition of done

- [ ] Every company in C1's confirmed universe has a `committee_verdicts`
  row with all six PRD §8 component scores, `overall_score`, `status`, and
  a populated `data_confidence` — or an explicit reason it doesn't, same
  per-method DoD discipline as Sprint 4.
- [ ] Every claim in a brief traces to a citation, and C2's chosen
  entailment/thin-labelling treatment is demonstrably applied, not just
  decided on paper.
- [ ] `assign_status()` thresholds are documented as pilot-validated, not
  guessed, with the pilot companies and reasoning recorded.
- [ ] The dashboard renders one consolidated PRD §9 ranked view and a
  PRD §10 one-page brief per company, verified end-to-end against the real
  database (same `streamlit.testing.v1.AppTest` approach Sprint 4 used, no
  browser in this environment).
- [ ] C8's default exclusion is active on the committee stage specifically
  (not assumed inherited from upstream stages).
- [ ] This retrospective's own sprint-5.md, including what this plan got
  wrong, same convention as Sprints 3/3.1/4.
