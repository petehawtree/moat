# Sprint 2.1 — Ingest data integrity + filing provenance

**Status:** Done

## Goal

Fix the `share_dilution` defect found in the [Sprint 2 review](sprint-2.md#sprint-review),
and remove the structural cause that let it go unnoticed and be misdiagnosed
twice: the pipeline was discarding the filing evidence needed to check it.

## What shipped

- **Restatement-keyed split detection** (`moat/ingest/fundamentals_edgar.py`).
  A share-count jump is now only treated as a stock split when a later filing
  actually **restated** that period. Genuine splits rebase prior-year
  comparatives; IPOs, mergers and recapitalisations don't.
- **Ingest validation.** `eps × shares ≈ net income` per row, with the *size*
  of any miss deciding which figure to distrust — a power of 1000 means the
  share count is in the wrong unit; anything else means net income and the EPS
  numerator differ structurally.
- **Provenance** — `accession_number`, `filed` and `quality_flags` on every
  fundamentals row; raw `companyfacts` cached to disk; the `filings` table
  populated for the first time.
- **`scripts/verify.py`** — one command to put a stored number next to every
  filing that reported it.
- 25 tests (9 new).

## Results

| | Before | After |
|---|---|---|
| Split detection basis | ≥40% jump (inferred) | filing restatement (evidence) |
| Companies "adjusted" | 189 (37.4%) | 129 with a recorded basis change |
| `filings` table | 0 rows | **7,497 rows** |
| Rows traceable to a filing | 0 | **8,210** |
| Full re-ingest | 353s | **11s** (cached) |

Real dilution restored on every known false positive:

| | Sprint 2 | Sprint 2.1 | |
|---|---|---|---|
| TKO | 6.2%/yr | **53.1%/yr** | WWE/UFC merger |
| CRWV | 6.6%/yr | **50.7%/yr** | IPO |
| ALAB | 14.1%/yr | **73.9%/yr** | IPO |
| KHC | −0.3%/yr | **11.0%/yr** | Kraft-Heinz merger |
| CHTR | −4.5%/yr | **3.1%/yr** | Time Warner Cable merger |

…while genuine splits still adjust correctly: WMT −2.2%, AAPL −2.6%,
CTAS −2.5%, MA −2.1% — buybacks, as they should read.

**Screen impact**, isolating the fix (same data, gate on vs off): **20 of 505**
companies get a different dilution verdict, **4 cross the threshold** — CI and
LNT leave, CPRT and GE enter. 111 companies still pass overall.

## What we found

**The fix had two bugs of its own, both caught by `verify.py`** — the tool
earned its cost inside the sprint that built it.

1. **Unit corrections must not drive rescaling.** ConocoPhillips filed
   FY2010–2019 diluted shares in thousands, with actual units either side.
   Treating that as a basis change rescaled the already-correct FY2007–2009
   rows by 1000, giving a −32.6%/yr dilution CAGR. Only genuine splits now
   corroborate an adjustment.
2. **A median-based outlier check inverts when the bad rows are the majority.**
   It flagged COP's and EG's *correct* rows, because ten bad years outvoted
   nine good ones. Replaced by the per-row power-of-1000 test — which also
   stopped an earlier version from dropping TKO's share counts as
   "inconsistent" when noncontrolling interests were the real cause, erasing
   the merger dilution this sprint existed to restore.

**On measuring the regression.** The first comparison against the stored
Sprint 2 run showed 11 threshold crossings — but re-ingesting had also pulled
fresh filings (SanDisk's FY2026 10-K landed in the interim and moved it 0 →
87.5 by itself). Isolating the fix gives the honest 4. A regression run against
re-fetched data measures the fix and the data drift together.

**Still open:** SNDK's FY2026 figures look like post-spinoff predecessor/
combined reporting rather than standalone results. Flagged, not investigated.

## Next up

Sprint 2.2 — distinguish FAIL from UNAVAILABLE in scoring, then Sprint 3
(AI analysis), which the populated `filings` table now unblocks for §A3
citation enforcement.
