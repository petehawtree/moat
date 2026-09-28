# Morningstar benchmark — capture complete

Records what the external benchmark capture produced, what it already proved,
and what is safe to conclude from it. The method is in
`morningstar-capture-checklist.md`; this is the outcome.

**Licensing boundary.** Nothing here reproduces captured Morningstar values or
analyst prose. What follows is derived agreement metrics, benchmark shape, and
findings about Moat's own behaviour — the categories the checklist marks as
publishable. The captured data itself lives outside this repo and always will.

## What exists now

| Tier | Scope | State |
|---|---|---|
| A — screen vs moat rating | 516 of 518 companies, all seven strata | complete |
| B — AI reasoning vs analyst reasoning | 26 companies, verbatim frozen | complete |
| C — fundamentals accuracy | 516 companies, field-level | complete, with a period caveat |

Snapshot 2026-09-28. Two companies could not be captured (no match in
Morningstar's security master), so the denominator is 516, not 518. Quote both
when reporting coverage.

Of the 516, **59 are quantitatively rated rather than analyst-covered**. They
carry no analyst reasoning and a different star-band formula, so they are
excluded from reasoning and margin-of-safety comparisons. The split is
detectable mechanically two independent ways that agree on 59 of 60 cases.

## Tier A — the screen discriminates, less dramatically than it first appeared

| Moat screen | has a moat |
|---|---:|
| PASS (109) | **93.6%** |
| FAIL (392) | 71.4% |
| Universe (501 rated) | 76.2% |

Three quarters of this universe already carries a moat, so passing the screen is
a **+17.4pp lift** over the base rate. Real, but modest — and invisible until the
failure strata existed. The pass cohort alone suggested a near-perfect result.

**The ranking is the stronger finding.** Moat probability falls monotonically
with the screen's own ordering, without exception:

```
pass 93.6%  >  fail-boundary 85.3%  >  fail-clear 74.4%
           >  unassessable 67.6%    >  definitional 59.6%
```

`composite_score` ordering tracks an independent expert judgement all the way
down. That is a stronger validation of the scoring than the pass/fail threshold.

**The screen is specific, not sensitive.** It captures 33% of the strongest
businesses and leaks 6% of the weakest. For a conservative strategy that is
arguably the right trade, but it is now a measured property rather than an
assumption, and it belongs in the README's claims about what the screen does.

## What the extraction gaps cost

Of the 68 companies the screen cannot assess for data reasons, **68% carry a
moat and 18 are top-rated**. Eighteen high-quality businesses are invisible to
the pipeline because of extraction failures alone — not because they were judged
and rejected.

That is the clearest available statement of what #1, #9 and #10 cost in
coverage, and it is a more useful number than another point of label agreement.

## GitHub #1 is roughly five times larger than catalogued

| | |
|---|---:|
| `total_debt IS NULL` where the benchmark has a figure | 177 |
| sector-excluded from the debt metric by design | 49 |
| **genuinely affected** | **128** |
| aggregate borrowing invisible where the metric applies | **~$2.0tn** |
| currently passing the screen | 24 |

`config.DEBT_TAG_GAP_TICKERS` holds 24 names. That is complete for the passers
but covers 19% of affected companies. **#1 should be re-scoped.**

## Tier C — extraction is exact where periods align

The benchmark reports trailing twelve months; `fundamentals_annual` stores
fiscal years, and `fundamentals_quarterly` is empty, so absolute figures are not
directly comparable in general.

For the 29 companies whose fiscal year ended within four months of the snapshot
the two periods nearly coincide, and the comparison becomes valid. Deviation
scales monotonically with how stale the fiscal year is (0.00% → 3.2% → 4.7% →
8.2%), which confirms period as the sole cause.

**On that period-matched subset, median deviation is 0.00% for revenue, net
income, operating cash flow, capital expenditure, cash and gross margin.**
Extraction is correct.

Three fields do NOT reconcile even when period-matched, so the gap is
definitional rather than an error:

| Field | Median deviation | Likely cause |
|---|---:|---|
| `free_cash_flow` | 18% | Moat uses operating cash flow minus capex; the benchmark subtracts more |
| `total_debt` | 16% | lease obligations likely included externally |
| `operating_margin` | 2% | reported versus adjusted |

**Settle these three before scoring.** Free cash flow and debt both feed screen
metrics, and a systematic 16% gap on debt is large enough to move verdicts by
itself. Treating a definitional difference as an extraction defect is the
mistake #16 was nearly filed as.

## A borrowable idea: uncertainty-scaled margin of safety

The benchmark's star thresholds are computed mechanically from fair value and an
uncertainty rating, with no variance across 24 analyst-covered companies and
confirmed on 456 of 516:

| Uncertainty | Required discount before "cheap" |
|---|---:|
| Low | 20% |
| Medium | 30% |
| High | 40% |
| Very High | 50% |

This is a ratio and a method, not captured data, so it is safe to record here.

Moat computes `margin_of_safety_pct` but does not scale the *required* margin by
`data_confidence` or `risk_score`. Adopting a confidence-scaled hurdle is a
small change with a clear external precedent, and it is the single most
transferable idea the capture produced.

## Also worth checking, cheaply

The benchmark's discount rates span **5.8% to 10.0%** across eight companies
that publish one — regulated utilities at the bottom, consumer discretionary at
the top. If Moat applies a single rate across the whole universe, low-risk names
are systematically undervalued and high-risk names overvalued relative to an
external view. Worth confirming before the valuation comparison runs.

## Status

The benchmark's half is frozen and will not move. Moat's half is regenerated per
eval run by design, so the capture never waits on the pipeline being in a
particular state — and open defects are not a reason to delay, since capturing
before a fix is what makes the fix verifiable.

Remaining known gap: **depreciation and amortisation is unavailable from any
structured route**, so #10 cannot be validated directly against external data.
The arithmetic case stands instead — a company with the capital programme that
one carries cannot have the D&A currently recorded.
