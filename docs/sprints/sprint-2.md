# Sprint 2 — Sector-relative quant screen + ranked dashboard

**Status:** Done

## Goal

Turn the `quant_scores`/`quality_scores` stubs from Sprint 0 into a real
deterministic screen: score every company on PRD §4's 8 metrics against
its own GICS sector (not one flat bar for every company, per
[PRD_ADDENDUM.md §A2](../PRD_ADDENDUM.md#a2-sector-relative-screening)),
roll that up into one ranked quality score, and surface it on the
dashboard so a company's pass/fail is explainable, not a bare checkbox.

## What shipped

- **`moat/screen/quant_screen.py`** — for each of the 8 PRD §4 metrics
  (ROIC, ROE, FCF margin, revenue/EPS growth, operating margin, debt/FCF,
  share dilution, gross margin), computes a sector-comparable value, an
  absolute-floor pass, a percentile within the company's own GICS sector,
  and a sector-relative pass (top tercile) — combined into `overall_pass`
  per metric, written to `quant_scores`.
- **`moat/quality/quality_score.py`** — rolls a company's 8 `quant_scores`
  rows into one `composite_score` (0-100, % of metrics passed) and a
  `passed_screen` flag (>= 50), written to `quality_scores`.
- `scripts/run_pipeline.py`'s `screen` and `quality` stages are now real;
  `ai_analysis` onward still stubbed for Sprint 3+.
- Dashboard: a ranked table (composite score, sector, pass/fail) plus a
  per-ticker drill-down showing every metric's value, floor pass, sector
  percentile, and sector-relative pass — the "why" A2 asked for.
- 16 tests: 8 new for the screen (percentile direction-awareness, the
  missing-sector fallback, an end-to-end synthetic run) plus a regression
  test for the stock-split bug below.

## Results

Run against the full Sprint 1 universe (505 companies with fundamentals):

| | |
|---|---|
| Companies scored | 505 / 505 |
| Passed the screen (composite_score ≥ 50) | **111 (22.0%)** |
| Top-scoring company | Adobe (100/100 — every metric cleared) |

Higher than PRD §13's "~50-100 out of ~850" — but that figure was scoped
for the full US+UK universe; S&P 500 + NASDAQ 100 is already a pre-filtered
set of large, established businesses, so a richer pass-through rate here
is expected. Full reasoning in
[PRD_ADDENDUM.md §A9](../PRD_ADDENDUM.md#a9-sprint-2--sector-relative-screen-implementation).

Spot-checking the top of the ranked table against names a Buffett-style
screen should surface: Adobe, Mastercard, Meta, Moody's, MSCI, Nvidia and
Verisk all land in the top 15 — recognizable moat businesses, not noise.

## What we found

**Bug: stock splits misread as dilution and an earnings crash — and a
correction to the first diagnosis of it.** The first real run failed
Walmart on both `share_dilution` and `revenue_eps_growth` outright:
`shares_diluted` jumps 2.85B → 8.42B between our stored FY2021 and FY2022
rows. The first version of this write-up called that "Walmart's 3-for-1
split (FY2022)" — wrong on the date (the real split was Feb 2024), and
wrong by pattern-matching a known corporate action instead of checking a
primary source. What's actually happening: a 10-K filed after a split
restates its comparative income statement (current year + ~2 priors) to
the post-split basis, and our "most-recently-filed value wins" merge rule
picks that up — so the visible jump marks the edge of a restatement
window, landing ~2 years *before* the real split, not at it. Confirmed
directly against SEC's raw XBRL filing timestamps, and the same ~2-year
offset shows up on Apple (real split Aug 2020, jump at FY2018) and Nvidia
(real splits 2021/2024, jumps at FY2020/FY2023).

That doesn't change the fix, only the explanation: a ≥40%-in-one-year
jump in diluted share count is treated as a split (real buybacks/issuance
essentially never move that fast in a single year), and every year before
the jump is rescaled onto the latest year's basis — shares multiplied by
the detected ratio, EPS divided by it. It doesn't need to know the real
split date, only where the basis actually changes. Post-fix, Walmart's
real dilution trend reads as -2.2%/yr (buybacks, correctly), and it now
passes both metrics. AAPL and NVDA both compute a small, real, sub-3%
dilution CAGR instead of a split-inflated number. Full write-up:
[PRD_ADDENDUM.md §A9](../PRD_ADDENDUM.md#a9-sprint-2--sector-relative-screen-implementation).

**Known limitation, not fixed this sprint:** Adobe's #1 ranking is
partly a noisy ROIC (111%) — real cause is a small invested-capital
denominator (zero debt, large cash pile), not a calculation error, but the
screen doesn't yet discount ROIC's existing `confidence='medium'` tag
when ranking. Flagged for Sprint 3+ rather than fixed now — full reasoning
in [PRD_ADDENDUM.md §A9](../PRD_ADDENDUM.md#a9-sprint-2--sector-relative-screen-implementation).

**Known gap, not a bug:** 15 NASDAQ-100-only companies have no GICS sector
(flagged in Sprint 1 — Wikipedia's NASDAQ-100 source doesn't carry it) and
fall back to absolute-floor-only scoring rather than being silently
failed for a sector comparison we can't compute.

## Next up

Sprint 3 — AI business/moat/management/risk analysis, citation-enforced
(PRD §5, [PRD_ADDENDUM.md §A3](../PRD_ADDENDUM.md#a3-evidence-and-citation-requirement-hardened-from-prd-1)).
