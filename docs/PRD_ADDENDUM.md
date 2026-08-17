# Project Moat — MVP PRD Addendum

Status: Draft for review | Date: 10 August 2026
Supplements `Project_Moat_PRD_MVP.pdf`. Where this addendum and the original PRD
disagree, this addendum wins — it exists to close ambiguities found while
scoping Sprint 0/1.

## A1. Sprint 1 universe narrowed to US-only

**Decision:** Sprint 1 covers **S&P 500 + NASDAQ 100 only** (deduplicated).
FTSE 350 is deferred to a later sprint.

**Why:** Free-tier UK fundamentals data is materially weaker than US (no
UK equivalent of SEC EDGAR's structured XBRL API), and mixing GBP- and
USD-denominated companies on one ranked dashboard requires an FX
normalization layer. Both problems are real but orthogonal to proving the
core pipeline. Solving them later, once the workflow is validated end-to-end
on cleaner data, is the more disciplined build order.

**Consequence:** No FX handling needed until the UK sprint. The `companies`
table still carries a `currency` and `exchange` column so adding FTSE 350
later doesn't require a schema migration — just a new ingest source and a
valuation-layer FX step.

## A2. Sector-relative screening

**Decision:** The quantitative screen (PRD §4) uses **sector-relative
thresholds**, not one flat bar for every company.

**Why:** A flat "ROE > 15%" applied uniformly under-selects capital-intensive
or structurally lower-margin sectors (industrials, capital goods, some
consumer staples) and over-selects nothing in return — it just produces a
universe skewed toward software/asset-light names, which isn't the same
thing as "exceptional business." Buffett's own criteria are read differently
per industry (see-through earnings on insurers vs. owner earnings on
consumer brands).

**Mechanism (Sprint 2 will implement this; documented here so the schema
supports it from Sprint 0):**
- Group companies by GICS sector (sourced with the universe list).
- For each metric in PRD §4, compute both:
  - an **absolute floor** (a bar low enough that failing it is disqualifying
    regardless of sector — e.g. persistently negative FCF), and
  - a **sector-relative bar** (e.g. top-tercile ROIC *within its own
    sector*).
- A company passes a metric if it clears the absolute floor AND its
  sector-relative percentile is stored alongside the pass/fail flag, so the
  dashboard can show *why* — "passed ROIC: sector-relative (78th percentile,
  Industrials)" rather than a bare checkbox.
- This means `quant_scores` stores the peer group and percentile per metric,
  not just a boolean — see schema in A5.

## A3. Evidence and citation requirement (hardened from PRD §1)

**Decision:** Every AI-generated claim in `ai_analysis` and the final
Investment Brief must carry a citation: source document (filing
accession number / URL) + the quoted excerpt it's grounded in. Analysis
without a resolvable citation is not stored — it fails validation and the
pipeline stage errors rather than silently accepting ungrounded prose.

**Why:** "Evidence over AI opinion" (PRD §1) is a principle until it's
enforced by a schema constraint. This is the single highest-leverage
addition for keeping the tool trustworthy at personal-research scale.

## A4. Data confidence tagging

**Decision:** Every fundamentals record carries a `confidence` field
(`high` / `medium` / `low`) and a `source` field. `high` = SEC EDGAR
structured XBRL. `medium` = derived/computed from EDGAR data with
assumptions (e.g. imputed owner earnings). `low` = yfinance-only or
otherwise unverified. The dashboard and briefs must surface this — a
brief built on `low`-confidence inputs should visibly say so.

## A5. AI cost control

**Decision:** AI analysis (business quality, moat, management, risk,
committee perspectives) is cached per company, keyed to a hash of the
filing/fundamentals version it was generated from. The pipeline only
re-runs AI stages when that hash changes (new 10-K/10-Q, or a material
fundamentals revision) — not on every scheduled pipeline run. This is the
mechanism that keeps §11's "material financial deterioration" trigger and
the caching rule the same signal.

## A6. Refresh cadence

- **Prices:** daily.
- **Fundamentals:** quarterly, plus an earnings-triggered refresh (new
  10-Q/10-K filed) rather than waiting for the next scheduled quarterly run.
- **AI analysis / valuation / committee:** only re-run when A5's cache key
  changes, or on manual request from the dashboard.

## A7. Sprint 1 execution notes (added after running the real pipeline)

Running ingestion against the full 518-company universe surfaced real data
issues worth recording rather than re-discovering later.

**Coverage achieved:** 505/518 companies (97.5%) with usable annual
fundamentals; 518/518 (100%) with price history.

**Bugs found and fixed:**
- XBRL tags must be **merged across every candidate**, not just the first
  one present. Companies switch tags over time — e.g. Apple reported
  revenue under `Revenues` through fiscal 2017 and switched to
  `RevenueFromContractWithCustomerExcludingAssessedTax` after adopting
  ASC 606. Taking the first matching tag silently truncated Apple's
  history to 3 years; merging recovered the full 19-year history.
- SEC's `company_tickers.json` — the documented canonical ticker→CIK
  file — is not actually complete. American Electric Power (AEP), an
  S&P 500 utility that has filed 10-Ks for decades, is absent from it.
  Confirmed this isn't a stale/transient snapshot: re-fetched the file
  fresh (no local cache) a few days after the initial finding — it had
  grown from 10,387 to 10,396 entries in the interim (so it's actively
  maintained), and AEP still wasn't among them. SEC's own `submissions`
  API independently confirms CIK 4904 is "AMERICAN ELECTRIC POWER CO INC,"
  ticker `AEP`, currently listed on Nasdaq — so this is a standing gap in
  a file SEC represents as authoritative, not a glitch. `lookup_cik` now
  falls back to EDGAR's `browse-edgar` company search, which resolves
  tickers the bulk file misses.
- Revenue and net-income XBRL tags vary more than the original candidate
  list assumed: broker-dealers use `RevenuesNetOfInterestExpense` (Goldman
  Sachs), several filers use `RevenueFromContractWithCustomerIncludingAssessedTax`
  instead of the `Excluding` variant (CrowdStrike, Kraft Heinz, APA,
  Alexandria RE), and some tag net income only as `ProfitLoss` rather than
  `NetIncomeLoss` on their 10-K (PNC, Fox Corp). All added as candidates.

**Remaining gaps (13 companies, ~2.5%) — real data limitations, not bugs:**
- **8 foreign private issuers** (ASML, PDD, ARM, CCEP, NBIS, TRI, FER,
  SPCX): file Form 20-F, not 10-K. Correctly out of scope for a 10-K-based
  extractor — see §A1's US-only decision. Nothing to fix here; flagging so
  it doesn't look like an oversight.
- **2 banks/financial holding companies** (SYF, TFC): US bank GAAP income
  statements lead with interest income/expense rather than a single
  "revenue" line, and neither company tags a consolidated revenue XBRL
  figure at all. A proper fix is a bank-specific revenue proxy (interest
  income + noninterest income) — reasonable Sprint 2+ scope, tied to the
  sector-relative screening work anyway since banks need different ratio
  treatment regardless (§A2).
- **3 recently-restructured entities** (XOM, APA, HONA): each trades under
  a ticker now mapped to a newer holding-company CIK (post-reorganization
  or recent spinoff) whose XBRL facts only go back through 10-Q filings —
  the pre-restructuring operating company's longer 10-K history exists
  under a different CIK. Resolving this requires tracking corporate
  restructuring events, which isn't worth the complexity for 3 companies;
  flagged as thin/insufficient data (§A4) rather than silently dropped.

## A8. Deferred / explicitly not in Sprint 0-1

- FTSE 350 / UK data, FX normalization (→ later sprint, see A1)
- Sector-relative screening *implementation* (schema supports it now;
  logic lands in Sprint 2)
- Any alerting channel beyond a dashboard panel (email/push is a nice-to-have,
  not required for §11's success criteria)
