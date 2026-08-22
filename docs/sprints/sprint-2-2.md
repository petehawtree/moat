# Sprint 2.2 — Data integrity

**Status:** Done

## Sprint review

Two external code reviews drove this sprint: one that audited the shipped
Sprint 2/2.1 system, and a second that reviewed the fixes themselves. **Every
verifiable claim in both reproduced exactly against the database** — the
155/73/38 FCF counts, Camden's $12.967m, the 773 mislabelled nulls, and the
follow-up's 109/17/9 debt figures.

| Finding | Disposition |
|---|---|
| OCF scored as free cash flow (155 companies) | 🔴 **Fixed** |
| REIT revenue missing ASC 842 lease income | 🔴 **Fixed** |
| Unavailable data scored as failure (773 nulls) | 🔴 **Fixed** |
| Implausible rows contaminating peer percentiles | 🔴 **Fixed** |
| Cache never expiring (froze §A6 refresh) | 🔴 **Fixed** |
| Successful runs marked `failed`; `--init-db` side effects | 🟠 **Fixed** |
| Debt failures relabelled `unavailable` (109 rows, 9 false passes) | 🔴 **Fixed** (§A14) |
| Financials scored on definitionally invalid metrics | 🟠 **Excluded from screen** (§A14) — not solved |
| `filings.content_hash` NULL, so §A5 AI cache key unavailable | ⚪ Accepted — Sprint 3 work |
| No-sector companies get floor-only scoring | ⚪ Accepted, disclosed (§A9) |
| Unimplemented AI/valuation/committee/monitoring stages | ⚫ **Disputed** — see below |

**The verdict "not investment-ready" was correct**, and the reason is the
data-integrity defects above rather than the unbuilt sprints.

### Where the reviews were challenged

The first review graded the unimplemented AI, valuation, committee, brief and
monitoring stages as **High severity** while acknowledging they are declared
future sprints. They are Sprints 3–6 of a documented six-sprint plan; grouping
them with live calculation defects overstates the position. The honest
statement is "at Sprint 2.2 of 6," not "High severity issue." The same applies
to its list of missing tests for unbuilt features, and to FTSE/FX (deferred by
§A1). Separately, its note that the test suite was "not independently
reproducible" reflected an environment that hadn't installed requirements —
`pytest>=8.2` is in `requirements.txt` and the suite runs clean.

Recorded in [§A13](../PRD_ADDENDUM.md#a13-sprint-22--data-integrity-second-external-review)
so the distinction between *disputed* and *accepted* survives the sprint.

## Goal

Act on a second external code review, which audited the shipped system against
the PRD and addendum and concluded — correctly — that it was **not
investment-ready**. Every verifiable claim it made reproduced exactly against
the database.

## What shipped

| Defect | Before | After |
|---|---|---|
| OCF stored and scored as free cash flow | **155** companies, 38 passing the screen on it | FCF is `None` when capex is unknown; OCF kept in its own column |
| REIT revenue (ASC 842 lease income missing) | CPT: **$12.967m** vs a real ~$1.6bn | additive ASC 606 + ASC 842 → **$1,586,511,000** |
| Unavailable data scored as failure | **773** nulls counted as fails | `status` = pass/fail/**unavailable**; score over assessable metrics only |
| Implausible rows contaminating peers | CPT shifted **all 30** Real Estate peers | quarantined from every peer group |
| Fundamentals cache | **never expired** (froze §A6 refresh) | 90-day TTL, `max_age_days=None` for verification |
| Successful runs | marked **`failed`** | `partial`; dashboard ignores failed runs |
| `--init-db` | started a full network pipeline | `--init-only` creates schema and exits |

31 tests (6 new).

## Results

**93 of 505** companies pass, against 111 before — and the fall is the point.
38 were passing on substituted operating cash flow, 9 on debt failures that
had been relabelled "unavailable", and **187** companies are now reported as
insufficiently measurable rather than silently marked as failures (69 of those
because metrics don't apply to their sector at all).

Metric verdicts across the run: **994 pass, 2,038 fail, 801 unavailable, 207
not applicable** — four outcomes where Sprint 2 had two.

The dashboard now shows `passed / assessed` alongside the score, so "6/6
assessable" and "3/8 assessable" are no longer indistinguishable.

## What we found

**A fix that was about to make things worse.** The first version of the
plausibility rule flagged any margin outside ±100%. That quarantined
**Moderna** (−158% operating margin) and **MicroStrategy** (−1141%) — both
*genuine* losses on real revenue. Excluding distressed companies from peer
comparison as "bad data" would have been a worse defect than the one being
fixed, and would have quietly flattered every remaining peer group. The rule
now keys on *positive* income exceeding revenue, which cleanly separates a
fragmentary revenue tag (DTE, Fifth Third) from a large real loss.

**A regression I introduced in Sprint 2.1.** The provenance cache shipped with
no expiry, freezing fundamentals permanently while prices kept refreshing — a
silent violation of §A6. It was added for provenance, measured on a 353s → 11s
speedup, and nobody asked what it did to freshness. Caching and staleness are
the same change viewed from two directions; the sprint only looked from one.

**Percentiles have blast radius.** A single nonsense row doesn't just mis-score
its own company. Because a percentile is relative, CPT's bad revenue moved all
30 of its sector peers and pushed one across the pass bar. That's why bad rows
are now quarantined from peer groups rather than merely flagged.

## Follow-up review of this sprint

A code review of Sprint 2.2 itself found two issues, both verified exactly
([§A14](../PRD_ADDENDUM.md#a14-sprint-22-follow-up--status-inversion-and-sector-applicability)):

**The fail/unavailable distinction was inverted in one case.** A company with
debt and no positive cash flow to service it fails `debt` outright, but the
debt/FCF *ratio* is deliberately `None` — and the status keyed on the value,
not the verdict. 109 rows; **9 companies passed that should not have** (CBRE,
DLTR, ETR, FCX, KMB, PEP, ROK, URI, VLO). Status now keys on whether a verdict
was reached. An unknown *value* and an unknown *verdict* are different things —
conflating them caused both this bug and the one this sprint was written to fix.

**Deferring a fix is not the same as continuing to emit the output.** §A8
deferred sector-specific metrics for financials; the screen kept scoring banks
on them anyway. Metrics that don't describe a business model are now marked
`not_applicable` — a definitional exclusion, separate from "couldn't measure."
**All 74 financials now fall out of the screen** (15 previously passed), total
passing 98 → 93. The tool does not have a valid bank screen, and now says so
instead of producing a number.

## Still open

- **Financial-sector metrics** still need real definitions (book value
  growth, net interest margin, efficiency ratio, tangible common equity).
  Financials are now excluded from the screen rather than mis-ranked (§A14),
  but excluded is not solved.
- **Real Estate is the next candidate** for the same treatment — REIT capex
  is property acquisition, so FCF and debt/FCF misdescribe them; FFO/AFFO is
  the right basis.
- **`filings.content_hash` / `local_path` are NULL**, so §A5's AI cache key is
  unavailable — the XBRL payload carries accessions, not document text.
- **No-sector companies** still get floor-only scoring (§A9).

## Next up

Sprint 3 — AI business/moat/management/risk analysis, citation-enforced
(§A3), which the populated `filings` table unblocks.
