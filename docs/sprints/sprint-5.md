# Sprint 5 — Investment Committee + Investment Brief

**Status:** Done, with one deliberate exception: `assign_status()`'s 70/50
thresholds are **not** pilot-validated. They move to the external-benchmark
eval being built next (`docs/evals/`, on the unmerged
`eval-morningstar-benchmark` branch) rather than being locked
on this sprint's evidence (see [Thresholds](#thresholds--deferred-to-the-eval-not-validated-here)).
Plan: [sprint-5-plan.md](sprint-5-plan.md). Decisions and findings:
[`docs/PRD_ADDENDUM.md`](../PRD_ADDENDUM.md) §A22–§A25, and
[`docs/known-issues.md`](../known-issues.md) for everything carried forward.

## What shipped

| # | Work | Result |
|---|---|---|
| C1 | Committee-eligible universe, re-derived from the live DB at run time | `ai_analysis ∩ latest valuations − COMMITTEE_KNOWN_EXCLUDED_TICKERS`. 69 companies at the plan's snapshot; **81** in the final run, after GitHub #9's fix moved the screen pass set from 91 to 109 (below) |
| C2 | Entailment posture (§A19.6): the raw cited quote shown inline, with no second-pass check | Every Quality/Bear STATEMENT's `[refs: N]` resolves to its 10-K quote in an expander; an unreferenced one carries an explicit "no citation" caption. Investment thesis and AI conclusion are labelled "synthesized, not individually cited". Each brief links to its source filing on EDGAR |
| C3 | Three persona prompts (Quality / Bear / Valuation Analyst) | Validated per persona: 4–8 STATEMENTs, numeric refs only (Quality/Bear), and every ref a real asserted claim id. Cached on a content hash of everything the prompt renders (A5) |
| C4 | Consolidation: `compute_overall_score()` wired in, `assign_status()` implemented | PRD §8 weights, with risk inverted once (it had been *added*, a real polarity bug the judge caught). A high bear-case severity caps Investigate at Watch but never vetoes to Reject. **Thresholds 70/50 are starting values, not validated** |
| C5 | Investment Brief content (PRD §10) | Every field has a defined source: thesis and conclusion are template-stitched (no 4th LLM call, decision 3); moat evidence, financial quality and valuation range come straight from their tables; "not available" is shown explicitly, never a blank |
| C6 | `committee` stage in `run_pipeline.py` | One transaction per company, no partial writes. Each verdict records the exact valuation, quant and AI-claims runs it was scored on, and a transient API failure skips only that company (both added 2026-09-23, below) |
| C7 | Dashboard | Overview / Investment Committee / Universe & Screening tabs; a ranked committee table with brief and SEC-filing link columns; a one-page brief per company with a shareable `?brief=TICKER` link. Warns when newer inputs exist than the verdict used |
| C8 | Known-bad tickers excluded by default on this stage | `COMMITTEE_KNOWN_EXCLUDED_TICKERS` is applied unless `--include-excluded` is passed. Now 29 tickers (#1 debt-tag gap: 24; #2 REITs: 3; #3 GOOGL; BKNG) |

**Final run:** `20260923T153351Z`, 81 companies: **2 Investigate** (ADBE
75.8, REGN 70.6), **53 Watch**, **26 Reject**. All 81 carry input
provenance. Data confidence: 73 medium, 8 high.

Full test suite: **329 passed, 1 skipped** (the opt-in live-API test), up
from 221 at the end of Sprint 4.

## Spend

| | Cost |
|---|---|
| Committee pilot, 21 companies (2026-09-15/18) | ~$1.90 |
| Full committee run on refreshed prices, 69 companies (2026-09-23 am) | $4.33 |
| Full committee run after the #9 fix, 80 companies | $6.47, of which **~$1.70 was a duplicate run** (below) |
| CHD committee verdict | $0.07 |
| AI analysis for the 14 newly-passing companies, batch | $2.07 + $0.22 (CHD's retry) |
| **Sprint 5 total** | **~$15.05** |

Against the plan's **$20–30** estimate. Measured per-company committee cost
is ~$0.063 (3 Sonnet calls, ~2.7k input and ~840 output tokens each),
roughly a fifth of what the plan assumed. The pre-run estimate for the 69-company
run was $4.29 (input tokens exact via `count_tokens`, output from the
pilot's real response lengths); the actual was $4.33.

**The ~$1.70 duplicate:** stopping a stalled run, a `pkill -f` pattern
matched the shell wrapper rather than the Python process (its script came
in on stdin, so the pattern never appeared on its command line), and a
`pgrep` with the same pattern falsely confirmed it had stopped. The resumed
run and the orphan scored the same 27 companies in parallel for ~28
minutes. Process-control error, not a code defect; it did produce the
repeat-scoring evidence in [Thresholds](#thresholds--deferred-to-the-eval-not-validated-here).

## Found by running it

**The pilot (2026-09-15, ~$0.37 of crash-truncated runs).** A quant block
crashing on a metric with no comparable number; `[refs: ]` crashing the
parser; non-numeric refs like `[refs: roic]` slipping *past* validation into
the statement text; **no committee cache at all** (every retry re-billed
all 3 personas, against A5); the Valuation Analyst's descriptive refs
failing otherwise-valid output (it is grounded by the context block's
figures, never a claim id, so those refs are now dropped, not rejected).
All fixed before the 21-company pilot was kept.

**Judge review of the pilot output (2026-09-15/18).** Risk was *added* to
the weighted score rather than inverted (AMAT: 50.3 Watch under the bug,
47.5 Reject fixed; all 21 recomputed in place, no re-spend). Uncited
statements looked identical to cited ones. PRD §10's moat, financial and
valuation sections didn't exist. A ticker missing 3 of 4 analysis types
could still produce a complete-looking verdict. The cache key ignored
`key_assumptions`, quant scores and data confidence (FCF yield 3%→12% gave
the same key). A 1-statement response passed validation. All fixed; this
also produced the first non-FAIL judge report the project has had, once
the known-issues allowlist (§A24) stopped known, filed defects being
re-counted every run.

**Taking the pilot to the full universe (2026-09-22/23).**

1. **Briefs could pair a verdict with evidence it never saw** (judge,
   HIGH). The dashboard re-queried the *latest* valuation, quant and AI runs.
   Verdicts now persist `valuation_run_id`, `quality_run_id` and
   `ai_claims_run_ids`, and the brief renders those. Pre-provenance
   verdicts say so explicitly.
2. **A refreshed AI analysis never superseded the old one** (judge, HIGH).
   It only retired rows with the *same* cache key, and a new filing always
   changes the key; the fresh-analysis path never retired anything at all. It
   now retires every prior current row for the ticker. No live duplicates
   existed, so no data cleanup was needed.
3. **GitHub #9: 7 committee companies had no DCF because capex/D&A were
   never extracted.** NVDA held an Investigate verdict with no DCF and no
   FCF yield. The causes were untried XBRL tags (`PaymentsToAcquireProductiveAssets`;
   an oil-and-gas capex split needing a sum; `DepreciationAndAmortization`)
   and extension-only tags companyfacts never returns (NEE). The fix is checked
   against all 502 cached companyfacts files: 0 existing values change;
   126 companies gain capex, 94 gain D&A. Knock-on effects:
   - The screen pass set went **91 → 109**. 7 of the 21 new passers only
     pass via already-filed defects (6 × #1 null debt, CCI as #2's REIT
     case), so the exclusion lists were extended rather than
     letting them through.
   - With a DCF, six of the seven now value well below price (NVDA bear $69
     vs $227). **NVDA moved Investigate → Watch** and CASY/WAT Watch → Reject.
     REGN, a new passer, is the one new Investigate (bear DCF $1,097 vs
     $797).
4. **One transient API error aborted the whole stage, three different
   ways.** A mid-stream `overloaded_error` arrives as an `APIStatusError`
   carrying the stream's HTTP 200. A mid-stream connection reset is a raw
   `httpx.ReadError`, not wrapped by the SDK. A stalled stream blocked for
   the SDK's 600s default read timeout. Now: 5/20/60s retries on anything
   transient; a per-company `api_error` after that; a halt after 3
   consecutive failures; a 120s read timeout.
5. **A batch retry could orphan a paid batch at Anthropic.** The batch
   `custom_id` was ticker + prompt hash, so retrying CHD after a validation
   failure collided with its failed row's UNIQUE `custom_id`. But the batch
   is submitted *before* its rows are written, so it was left running
   untracked (cancelled before it billed). IDs are now unique per
   submission.

## Thresholds — deferred to the eval, not validated here

The plan's DoD asked for `assign_status()`'s thresholds to be "documented
as pilot-validated, not guessed." **That box stays unchecked, on purpose.**
Nothing this sprint produced is a standard to validate *against*: a
threshold is only right relative to an external view of which companies
deserve Investigate, and building that view is the eval's job, not
something to improvise from the model's own output.

What this sprint does contribute is evidence about how *precise* any
threshold can be:

- **Repeat-scoring noise.** The accidental duplicate run scored 27
  companies twice on identical inputs: mean |Δ| **1.4** points, max
  **4.2**, and **3 status flips** (REGN 68.5 Watch / 70.6 Investigate; NTAP
  and NSC either side of 50). A status within ~2 points of a threshold is
  not a stable result.
- **15 of 81** final verdicts sit within ±2 of 50 or 70.
- **The high-severity cap never binds in the final run**: no company
  scoring ≥70 had a high bear case, so that rule is untested on real data.
- A first external spot check (ADBE vs Morningstar's published multiples)
  found stale prices and a 2-year historical P/E window, not a threshold
  problem. That is the kind of comparison the eval should systematize.

## Definition of done

- [x] Every company in C1's universe has a `committee_verdicts` row with
  all six component scores, `overall_score`, `status` and a populated
  `data_confidence`, or an explicit reason it doesn't. 81/81 in the final
  run. Two failed validation first and persisted nothing: ITW's committee
  verdict (malformed `[refs:` brackets) and CHD's AI analysis (an uncited
  claim). Both passed on retry.
- [x] Every claim traces to a citation, and C2's posture is applied, not
  just decided. Verified on real briefs, including the uncited-statement
  flag and source-filing links.
- [ ] ~~`assign_status()` thresholds documented as pilot-validated~~.
  **Deferred to the eval**, above.
- [x] Consolidated ranked view and one-page brief, verified against the
  real database via `streamlit.testing.v1.AppTest` (ADBE, NVDA, REGN)
  and in a browser for the link columns and `?brief=` deep link.
- [x] C8's default exclusion active on the committee stage specifically.
- [x] This retrospective.

## What the plan got wrong

- **The cost estimate was ~5–7× too high:** $20–30 planned for one pass
  over 69 companies, against a real $4.33. Every committee run this sprint
  *plus* the new-passers' AI analysis came to ~$15 combined. The plan
  priced by analogy to Sprint 3.1's batch run without measuring the persona
  prompts. They are ~2.7k tokens, not filing-sized. `count_tokens`
  on real prompts would have given the number up front.
- **"Reuse Sprint 3's batch machinery" didn't happen, and that was right.**
  The committee is synchronous (sequential calls, easy to cap per call).
  But it meant none of Sprint 3's retry/robustness came along, which is
  why three separate transient-error paths had to be found live.
- **C3's cache acceptance ("miss when the source run_id changes") was
  the wrong shape.** Run ids change on every re-run even when nothing did.
  The key had to be content-based, and it took two judge rounds to make
  it cover everything the prompt actually renders. It still doesn't quite
  (see Carried forward).
- **Input provenance wasn't in the plan at all.** C6 said "read whichever
  run is current", which is right for the *stage* and wrong for the
  *brief*. The brief must show what the verdict saw, not what ran since.
- **C1's "69" was stale within the sprint**, the same lesson as Sprint 4's "93".
  A data fix upstream (#9) moved the universe to 81, and it will move again.
- **"Pilot-then-lock" (C4) named no standard to lock against.** A pilot
  read can find a threshold that *looks* sensible, but can't show it is
  *right*. That is why it moves to the eval rather than being ticked here.

## Judge review

Pre-push judge runs this sprint, all archived in
[`docs/judge-reports/`](../judge-reports/):

- **2026-09-18:** the first **PASS WITH CONCERNS** in the project's
  history ([report](../judge-reports/judge-report-sprint-5-first-clean-run-20260918-143831.md)),
  once the known-issues allowlist existed.
- **2026-09-22, FAIL** ([report](../judge-reports/judge-report-sprint-5-20260922-184711.md)):
  one HIGH (the brief mixing input runs, fixed as item 1 above) and two
  MEDIUMs, **both still open** (see Carried forward).
- **2026-09-23 10:44, FAIL** ([report](../judge-reports/judge-report-sprint-5-20260923-104446.md)):
  one HIGH (analyses not superseded, fixed as item 2).
- **2026-09-23 11:41, PASS WITH CONCERNS** ([report](../judge-reports/judge-report-sprint-5-20260923-114138.md)):
  0 Critical/High/Medium, 1 Low (the live citation test only runs opt-in).
- **2026-09-23 17:09, PASS WITH CONCERNS**, after the #9 merge
  ([report](../judge-reports/judge-report-sprint-5-20260923-170924.md)):
  0 Critical/High/Medium, 2 Low. One is the cache-key sector gap from
  09-22, now rated Low. The other is new: `scripts/metrics.py --check`
  fails when only the render date differs. Its own top actions include
  "complete and document Sprint 5 threshold validation before calling
  rankings final", which is the deferral above, stated independently.

## Carried forward

- **Thresholds**: to the external-benchmark eval, as above.
- **Two MEDIUM judge findings (2026-09-22), not yet filed:**
  - "Key things to monitor" keeps only the Bear statements' text, dropping
    their refs, so monitor items render with neither citations nor the
    no-citation flag.
  - The committee cache key omits fields the prompt renders
    (`sector_peer_group`, company name/sector), so a sector reassignment
    can serve a stale verdict.
- **`metrics.py --check` date false positive** (Low, 09-23 17:09): the
  advisory check fails every day after rendering, even with identical
  values.
- **GitHub #10** (D&A extracted *wrong*, e.g. NEE 101× too small; CBRE's
  verdict affected) and **#11** (the DCF's trailing-window length is never
  surfaced: NVDA's 5-year and WAT's 3-year base look like 10-year ones).
  Both were found during #9's follow-through.
- **Committee spend isn't persisted** (`committee_verdicts` has no cost
  column), so the committee figure in `docs/metrics.json` stays manual.
- **README prose and screenshots still show the 21-company pilot** (the "21
  committee briefs" funnel); they need re-capturing against the final run.
- Sprint 6 (monitoring) is untouched; "key things to monitor" is authored
  brief content, not live monitoring.
