# Sprint 2 — Sector-relative quant screen + ranked dashboard

**Status:** Done, with one metric since found defective — see
[Known defects](#known-defects-found-after-this-sprint-shipped) below.

## Sprint review

Sprint 2's output was validated **externally** — an independent AI review
checked the sample data against the PRD and the addendum. The result was
mixed in a useful way, so it's recorded rather than summarised away.

| Area | Verdict |
|---|---|
| Sector-relative architecture (§A2) | ✅ Sound — stays as designed |
| FCF margin, debt/FCF, operating margin, revenue CAGR | ✅ Verified correct |
| Percentile direction, 66.7% tercile, scoring, 50% threshold | ✅ Verified correct |
| `share_dilution` | 🔴 Defective — do not trust until Sprint 2.1 |
| Missing data treated as failure | ⚠️ Design limitation — Sprint 2.2 |
| Bank/financial metrics | ⚠️ Needs sector-specific definitions — deferred |

**What held.** Every numeric claim in the review reproduced exactly against
the database. Seven of the eight metrics are confirmed working, and the
core design decision this sprint existed to test — sector-relative bars
instead of flat thresholds — came through unchallenged.

**What didn't.** The split detector fires on **189/505 companies (37.4%)**,
not the "rare" case assumed when it shipped, and conflates genuine splits
with IPOs/mergers and with unit errors in the source data. **67 companies'**
dilution result depends on the treatment and **10 cross the screen
threshold** — while the headline total stays 111 either way.

**Where the review was itself wrong.** Four of its nine examples (MA, CTAS,
SMCI, AME) are genuine splits that we handle correctly; reverting to raw
share counts, as its framing implied, would have introduced new errors. It
had analysed an Excel extract rather than the filings, so it inferred causes
it couldn't check — the same mistake made in this sprint's own first
write-up of the Walmart bug.

**Root cause of both.** The pipeline discards its own evidence: raw XBRL is
never cached, the `filings` table is empty, and no fundamentals row records
which filing it came from. Verification required a throwaway script both
times, and verification that costs a script gets skipped
([§A11](../PRD_ADDENDUM.md#a11-provenance-and-verification-extends-a3)).

**Next.** Sprint 2.1 fixes detection at ingest (keyed on restatement, not
jump size), adds an `EPS × shares ≈ net income` sanity check, retains filing
provenance, and ships a one-command `verify` tool. It also unblocks Sprint 3,
which needs the same `filings` table for §A3 citation enforcement.

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
| Passed the screen (composite_score ≥ 50) | **111 (22.0%)** ⚠️ |
| Top-scoring company | Adobe (100/100 — every metric cleared) |

⚠️ These rankings are affected by the dilution defect below — 10 companies
sit on the wrong side of the threshold because of it.

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

## Known defects (found after this sprint shipped)

`share_dilution` is not currently trustworthy — summary in
[Sprint review](#sprint-review) above. Mechanically, `_detect_split_factors`
treats any ≥40% one-year jump in diluted share count as a stock split, but
three different things produce that jump and each needs different handling:

| Phenomenon | Correct treatment | Currently |
|---|---|---|
| Genuine stock split | Rebase prior years | ✅ correct |
| Real corporate event (IPO, merger) | **Don't adjust** — dilution is real | ❌ adjusted away |
| Unit-of-measure error in source data | **Reject at ingest** | ❌ silently normalised |

The separator is *restatement*: a genuine split rebases prior periods across
filings (SMCI `10.00x`, CTAS `4.00x`, MA `10.01x`, AME `1.50x`), while real
issuance restates nothing (TKO, CRWV, ALAB, CHTR, KHC). Sprint 2.1 keys
detection on that signature at ingest, adds an `EPS × shares ≈ net income`
sanity check, and retains the filing provenance needed to check either.
Full detail:
[PRD_ADDENDUM.md §A10](../PRD_ADDENDUM.md#a10-sprint-2-post-review-findings--the-dilution-metric-is-defective)
and [§A11](../PRD_ADDENDUM.md#a11-provenance-and-verification-extends-a3).

## Next up

**Sprint 2.1 — ingest data integrity + provenance** (§A10, §A11), which
also unblocks Sprint 3: the populated `filings` table it adds is exactly
what §A3's citation enforcement needs.

Then Sprint 3 — AI business/moat/management/risk analysis, citation-enforced
(PRD §5, [PRD_ADDENDUM.md §A3](../PRD_ADDENDUM.md#a3-evidence-and-citation-requirement-hardened-from-prd-1)).
