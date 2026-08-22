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

## Data provenance — what actually changed

This is the part with consequences beyond one metric, so it's worth stating
plainly.

**Why it matters here specifically.** This project's product *is* trust. PRD
§1 says "evidence over AI opinion"; §A3 hardens that into "analysis without a
resolvable citation is not stored." But a citation rule is only as good as the
data layer underneath it. Through Sprint 2, every number in the database was
an **orphan** — it said `source = 'sec_edgar'`, which names a family of
documents, not a document. Nothing recorded *which filing* a figure came from,
and the raw payload was thrown away after parsing.

That had a measurable cost: the dilution defect was misdiagnosed **twice** —
once in our own §A9 write-up (a split attributed to the wrong year), once by
the external reviewer (four genuine splits called corporate events). Neither
was carelessness. Checking a single number meant writing a throwaway script
and re-hitting SEC's API, so in practice nobody checked, and confident prose
filled the gap.

### Before → after

| | Sprint 2 | Sprint 2.1 |
|---|---|---|
| Raw SEC payload | fetched, parsed, **discarded** | **cached** (502 files, ~2GB, gitignored) |
| Which filing a number came from | not recorded | `accession_number` + `filed` on **100% of 8,210 rows** |
| `filings` table | **0 rows** (schema'd Sprint 0, never populated) | **7,497 rows**, 502 companies, 2009–2026 |
| Link from a number to an EDGAR document | none | **0 broken chains** — every cited accession resolves |
| Disagreements between filings | silently discarded by "latest wins" | preserved in `share_basis_changes` (360) |
| Data-validation state | invisible | `quality_flags` on the row (125 flagged) |
| Cost to verify one number | write a script + network call | `python scripts/verify.py TICKER FIELD` |
| Cost to re-run all ingest | 353s | **11s** |

### The chain of custody

```
SEC 10-K  ──►  cached companyfacts  ──►  fundamentals_annual  ──►  quant_scores  ──►  dashboard
(EDGAR URL)     (data/filings/)          (+accession, filed,       (metric value)     (rank, why)
      ▲                                    quality_flags)
      └──────────────── verify.py resolves any link, in either direction ───────────────┘
```

Every hop is now traversable. Given a number on the dashboard you can reach
the filing; given a filing you can find every number derived from it.

### What it looks like in practice

The claim that took two wrong diagnoses to settle is now one command:

```bash
python scripts/verify.py WMT shares_diluted --year 2022
```

```
  period ending 2022-01-31  <-- RESTATED
         2,805,000,000  filed 2022-03-18  0000104169-22-000012
         2,805,000,000  filed 2023-03-17  0000104169-23-000020
         8,415,000,000  filed 2024-03-15  0000104169-24-000056
```

Three filings, one period. The figure holds steady across two years, then the
first 10-K filed after the February 2024 split restates it 3.00x — the filer
saying so, not us inferring it. Each line carries its EDGAR URL, so the claim
is checkable by a human in one click.

Run the same command against TKO and it reports **no restatement at all**,
which is exactly why its 53%/yr dilution is now allowed to stand.

### Scope — what is and isn't covered

Covered: US annual fundamentals from SEC EDGAR (the figures the screen runs
on), and the share-count restatement history behind the dilution metric.

**Not yet covered**, so the claim isn't overstated:
- **Prices** (yfinance) carry no equivalent provenance — no document exists to
  cite, and they don't feed the screen. They will matter at valuation
  (Sprint 4).
- **Derived metrics** (ROIC, FCF margin, debt/FCF) trace to their *inputs*,
  which are cited; the computation itself is documented in code and §A9 rather
  than carried as a per-row citation.
- **`quality_flags` are advisory** — they mark rows as untrustworthy, they
  don't yet feed the confidence tiers in §A4. Wiring those together is
  Sprint 2.2 scope, alongside FAIL-vs-UNAVAILABLE.

### What this unblocks

Sprint 3 requires every AI claim to carry a resolvable citation (§A3). That
needs a populated `filings` table with real document URLs — which is precisely
what shipped here. The requirement was always in the plan; a quantitative bug
just surfaced it two sprints early, which is the cheaper way to find out.

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
