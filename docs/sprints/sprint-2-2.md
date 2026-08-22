# Sprint 2.2 — Data integrity

**Status:** Done

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

30 tests (5 new).

## Results

**98 of 505** companies pass, against 111 before — and the fall is the point:
38 were passing on substituted operating cash flow, and **205** are now
correctly reported as insufficiently measurable rather than silently marked as
failures.

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

## Still open

- **Financial-sector metrics are definitionally wrong** (§A8). FCF margin,
  debt/FCF and gross margin do not describe a bank, and sector-*relative*
  ranking cannot rescue an invalid metric *definition*. Needs per-sector
  metric selection.
- **`filings.content_hash` / `local_path` are NULL**, so §A5's AI cache key is
  unavailable — the XBRL payload carries accessions, not document text.
- **No-sector companies** still get floor-only scoring (§A9).

## Next up

Sprint 3 — AI business/moat/management/risk analysis, citation-enforced
(§A3), which the populated `filings` table unblocks.
