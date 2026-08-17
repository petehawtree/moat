# Three data bugs an AI coding agent found by actually running the code

*Notes from Sprints 0-1 of Project Moat, a personal AI-assisted equity research tool built on Buffett/Graham fundamental investing principles.*

The goal was straightforward: pull real fundamentals for the S&P 500 + NASDAQ 100 (518 companies after deduplication) from SEC EDGAR's structured XBRL data, and build the deterministic screening layer everything else depends on. The interesting part wasn't writing that code - it was what showed up once it ran against all 518 companies instead of a handful of test tickers.

Three bugs, each a different flavor of "the data isn't what the docs imply it is."

## 1. Silent truncation from trusting the first matching tag

**Issue:** Apple's fundamentals history came back as 3 years instead of 19. No error - the code ran cleanly and returned data.

**Details:** SEC filers tag financial figures with standardized XBRL concept names (e.g. `Revenues`). Apple reported revenue under the `Revenues` tag through fiscal 2017, then switched to `RevenueFromContractWithCustomerExcludingAssessedTax` after adopting the ASC 606 accounting standard in fiscal 2018. The original extractor checked candidate tags in priority order and stopped at the first one present in a company's filings - which meant it only ever saw the pre-2018 `Revenues` entries, and quietly ignored the fact that the same company kept reporting revenue under a different tag afterward.

**Fix:** Merge entries across *every* candidate tag for a metric rather than taking the first match - [`_merged_annual_entries`](https://github.com/petehawtree/moat/blob/main/moat/ingest/fundamentals_edgar.py#L173), used from [`extract_annual_fundamentals`](https://github.com/petehawtree/moat/blob/main/moat/ingest/fundamentals_edgar.py#L244).

**Test:** [`test_revenue_merges_across_tag_switch`](https://github.com/petehawtree/moat/blob/main/tests/test_fundamentals_edgar.py#L27) - constructs a synthetic filer that reports under one tag pre-2019 and another from 2019 on, and asserts both years survive extraction.

A companion bug lived in the same function: a 10-K's XBRL data can carry a *quarterly* duration figure under the same tag used for annual figures (e.g. footnote tables of quarterly financial data). Filtering only by form type (`10-K`) wasn't enough - duration also has to fall within 350-380 days to count as a full fiscal year. Covered by [`test_quarterly_footnote_entries_excluded_from_annual`](https://github.com/petehawtree/moat/blob/main/tests/test_fundamentals_edgar.py#L54).

## 2. The government's own canonical reference file has gaps

**Issue:** American Electric Power (AEP) - an S&P 500 utility that's filed 10-Ks for decades - failed ingestion with "no CIK found."

**Details:** SEC publishes `company_tickers.json` as the documented, canonical ticker-to-CIK mapping. It's meant to be comprehensive. It doesn't contain AEP. Confirmed directly against SEC's `submissions` API, which resolves CIK 4904 and confirms `AEP` as its current ticker - the company is unambiguously registered and filing; it's just absent from the bulk lookup file that's supposed to be the source of truth for exactly this kind of lookup.

**Re-verified before publishing this:** confirmed at the raw-response level, not just via a parsed lookup dict - searched the file's raw text directly for "american electric" (case-insensitive) and for CIK 4904 as a value, both zero matches, ruling out a parsing bug on our end (case mismatch, duplicate-key overwrite, truncated download). Also re-fetched the file fresh a few days later: it had grown from 10,387 to 10,396 entries, and AEP still wasn't among them. What this confirms is that AEP is absent from this specific file at the byte level - not why SEC's process produced that result, which we can't see into.

**Fix:** Added a fallback lookup through EDGAR's `browse-edgar` company search, which accepts a ticker directly and resolves the CIK when the bulk file misses it - [`_lookup_cik_via_browse_edgar`](https://github.com/petehawtree/moat/blob/main/moat/ingest/fundamentals_edgar.py#L79), wired into [`lookup_cik`](https://github.com/petehawtree/moat/blob/main/moat/ingest/fundamentals_edgar.py#L110).

**Test:** this one's a live-data edge case rather than something worth mocking - it's called out explicitly in [`docs/sprints/sprint-1.md`](https://github.com/petehawtree/moat/blob/main/docs/sprints/sprint-1.md) as a data-source finding, and the fallback path is exercised every time the pipeline runs against the full universe (AEP resolves correctly in the Sprint 1 ingest run).

## 3. "Revenue" isn't one tag - it's a dozen tags depending on your industry

**Issue:** More than a dozen large, unambiguous companies - Goldman Sachs, CrowdStrike, PNC, Kraft Heinz, Fox Corp, APA, Alexandria Real Estate, and others - failed extraction with "no usable revenue/income tags," despite obviously having full 10-K financials.

**Details:** The initial tag candidate list (`Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`) covers a generic industrial/consumer company. It doesn't cover:
- Broker-dealers, who report `RevenuesNetOfInterestExpense` instead of a plain revenue line (Goldman Sachs)
- Filers using the `Including`-assessed-tax variant of the ASC 606 tag rather than `Excluding` (CrowdStrike, Kraft Heinz, APA, Alexandria RE)
- Filers who tag net income as `ProfitLoss` instead of `NetIncomeLoss` on their 10-K specifically, even though `NetIncomeLoss` exists elsewhere in their facts under 10-Q filings (PNC, Fox Corp)

**Fix:** Expanded the [`TAG_CANDIDATES`](https://github.com/petehawtree/moat/blob/main/moat/ingest/fundamentals_edgar.py#L132) mapping for both `revenue` and `net_income` to include these variants, discovered by inspecting each failing company's actual XBRL facts rather than guessing.

**Test:** [`test_restatement_picks_most_recently_filed`](https://github.com/petehawtree/moat/blob/main/tests/test_fundamentals_edgar.py#L79) and [`test_year_dropped_without_revenue_or_income`](https://github.com/petehawtree/moat/blob/main/tests/test_fundamentals_edgar.py#L99) pin the surrounding logic (restatement handling, and refusing to half-fill a fiscal year that's missing both top-line figures) that this tag expansion depends on.

## What was left alone, deliberately

13 of 518 companies (2.5%) still don't resolve, and they're not further bugs to chase - 8 are foreign private issuers filing Form 20-F instead of 10-K (out of scope for a US 10-K-based extractor), 2 are banks whose GAAP presentation has no single "revenue" line at all, and 3 trade under tickers now mapped to newly-restructured holding-company CIKs with thin post-reorg filing history. Full breakdown in [`docs/PRD_ADDENDUM.md` §A7](https://github.com/petehawtree/moat/blob/main/docs/PRD_ADDENDUM.md).

## Where this generalizes

There's a broader implication here for anyone doing this kind of work in capital markets, where "structured data" so often turns out to have real complexity embedded inside it - different disclosure conventions by industry, tagging judgement calls, occasional gaps in reference data that's supposed to be canonical. The traditional path is an SME or analyst defining the handling rules up front, before any extraction code gets written. What happened here inverted that: the agent ran directly against the golden source - not a summary, not documentation, the actual filings - across a representative sample spanning multiple industries, observed how the data actually structures itself, and adjusted its logic to match evidence rather than assumption. Not a replacement for that expertise - a way to compress what's normally days of manual filing review into a validation loop that runs in minutes and leaves a trail: one regression test per root cause, each traceable back to the specific company and tag that prompted it.

The same pattern held for the AEP finding above: rather than trust a single fetch, re-verify against the golden source directly, at the raw-data level, before publishing a claim about it.

None of this showed up from reading SEC's documentation or from testing against two or three well-known tickers. It only showed up from running the full pipeline against the full universe, treating every failure as something to explain rather than something to log and move past, and writing a regression test for each root cause rather than just patching the symptom.

Repo: [github.com/petehawtree/moat](https://github.com/petehawtree/moat)
