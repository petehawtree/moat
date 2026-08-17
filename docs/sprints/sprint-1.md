# Sprint 1 — Data foundation and company universe

**Status:** Done | **Commit:** [`1e23a25`](https://github.com/petehawtree/moat/commit/1e23a25)

## Goal

Build the real US universe and pull real fundamentals/price data for it —
turn Sprint 0's stubs into something that actually fetches and stores data.

## What shipped

- **`moat/ingest/universe.py`** — S&P 500 (Wikipedia, with GICS sector +
  CIK) merged with NASDAQ 100, deduplicated to **518 unique US tickers**.
- **`moat/ingest/fundamentals_edgar.py`** — SEC EDGAR XBRL extraction,
  with confidence tagging (`high`/`medium`/`low`) ready for the
  sector-relative screening work in Sprint 2.
- **`moat/ingest/prices.py`** — yfinance, incremental daily refresh.
- `scripts/run_pipeline.py`'s `universe` and `ingest` stages are now real;
  `screen` onward still stubbed for Sprint 2+.
- Dashboard shows real ingest coverage instead of a placeholder.
- 8 tests: a schema smoke test plus offline regression tests for two bugs
  found below.

## Results

| | Coverage |
|---|---|
| Fundamentals (SEC EDGAR) | 505 / 518 companies (97.5%) |
| Prices (yfinance) | 518 / 518 companies (100%) |

## What we found

Sprint 1 was as much about validating against real filings as writing the
ingestion code — three real bugs turned up, and the coverage gap after
fixing them turned out to be fully explainable rather than a mystery.

**Bugs fixed:**

1. **XBRL tags must be merged across every candidate, not just the first
   match.** Companies switch tags over time — Apple reported revenue under
   `Revenues` through fiscal 2017, then switched to
   `RevenueFromContractWithCustomerExcludingAssessedTax` after adopting
   ASC 606. Taking the first matching tag silently truncated Apple's
   history to 3 years; merging recovered the full 19-year history.
2. **SEC's own canonical ticker→CIK file is incomplete.** American
   Electric Power (AEP) — an S&P 500 utility that's filed 10-Ks for
   decades — is simply absent from `company_tickers.json`. Confirmed at
   the raw-response level (direct text search for "american electric" and
   for CIK `4904` as a value — zero matches for either), ruling out a
   parsing bug on our end. Re-verified with a fresh fetch days later (no
   cache): the file had grown from 10,387 to 10,396 entries, confirming
   it's actively maintained, and AEP still wasn't in it — a standing gap
   in that specific file, confirmed by direct text search rather than a
   lookup artifact on our end. Added a
   fallback lookup via EDGAR's `browse-edgar` company search, which
   resolves tickers the bulk file misses.
3. **Revenue/net-income tags vary more than expected.** Broker-dealers use
   `RevenuesNetOfInterestExpense` (Goldman Sachs); several large-caps use
   `RevenueFromContractWithCustomerIncludingAssessedTax` instead of the
   `Excluding` variant (CrowdStrike, Kraft Heinz, APA, Alexandria RE); some
   filers tag net income only as `ProfitLoss`, not `NetIncomeLoss` (PNC,
   Fox Corp). All added as candidates — this alone recovered coverage on
   more than a dozen large, obviously-real companies that were failing.

**Remaining 13 gaps (2.5%) — real limitations, not bugs, documented in
[PRD_ADDENDUM.md §A7](../PRD_ADDENDUM.md#a7-sprint-1-execution-notes-added-after-running-the-real-pipeline):**

- **8 foreign private issuers** (ASML, PDD, ARM, CCEP, NBIS, TRI, FER,
  SPCX) file Form 20-F, not 10-K — correctly out of scope for a US-only,
  10-K-based extractor.
- **2 banks** (SYF, TFC) whose GAAP income statements lead with interest
  income/expense rather than a single revenue line, and don't tag a
  consolidated revenue figure at all. Fixing this needs a bank-specific
  revenue proxy — reasonable Sprint 2+ scope, tied to the sector-relative
  screening work anyway since banks need different ratio treatment
  regardless.
- **3 recently-restructured entities** (XOM, APA, HONA) trade under
  tickers now mapped to newer holding-company CIKs whose XBRL facts only
  go back through 10-Q filings. Tracking corporate restructuring events to
  chain back to the pre-reorg CIK isn't worth the complexity for 3
  companies; flagged as thin data rather than silently dropped.

## Next up

Sprint 2 — sector-relative quant screen + ranked dashboard.
