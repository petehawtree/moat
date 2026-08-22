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
  Confirmed at the raw-response level, not just via our parsed lookup
  dict: searched the raw file text directly for "american electric"
  (case-insensitive) and for AEP's CIK as a `cik_str` value (`4904`,
  unpadded, as SEC stores it) — zero matches for either, ruling out a
  parsing artifact on our end (case/whitespace mismatch, a duplicate-key
  overwrite, truncated download). Also confirmed this isn't a
  stale/transient snapshot: re-fetched the file fresh (no local cache) a
  few days after the initial finding — it had grown from 10,387 to 10,396
  entries in the interim (so it's actively maintained), and AEP still
  wasn't among them. SEC's own `submissions` API independently confirms
  CIK 4904 is "AMERICAN ELECTRIC POWER CO INC," ticker `AEP`, currently
  listed on Nasdaq. So: absent from this specific derived file at the
  byte level, confirmed by direct text search rather than a lookup
  artifact on our end — we can't see SEC's internal generation process,
  so we can't say *why* it's missing, only that it demonstrably is.
  `lookup_cik` now falls back to EDGAR's `browse-edgar` company search,
  which resolves tickers the bulk file misses.
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

**Where this generalizes:** the traditional path for handling disclosure
data like this is an SME or analyst defining the tagging/handling rules up
front, before any extraction code gets written. What happened in Sprint 1
inverted that - the agent ran directly against the golden source (SEC's
own EDGAR filings, not a summary or documentation) across a representative
sample spanning multiple industries, observed how the data actually
structures itself, and adjusted its logic to match evidence rather than
assumption. Not a replacement for that expertise - a way to compress what's
normally days of manual filing review into a validation loop that runs in
minutes and leaves a trail: one regression test per root cause, each
traceable back to the specific company and tag that prompted it. Full
write-up: [`docs/writeups/three-bugs-in-structured-financial-data.md`](writeups/three-bugs-in-structured-financial-data.md).

## A8. Deferred / explicitly not in Sprint 0-1

- FTSE 350 / UK data, FX normalization (→ later sprint, see A1)
- Sector-relative screening *implementation* (schema supports it now;
  logic lands in Sprint 2)
- Any alerting channel beyond a dashboard panel (email/push is a nice-to-have,
  not required for §11's success criteria)

**Added after the Sprint 2 review (§A10) — deferred beyond Sprint 2.1:**
- **Sector-specific metric configuration**, principally for banks and
  financial holding companies. `debt` (debt/FCF), `gross_margin` and
  `free_cash_flow` are close to meaningless for STT/USB/SPGI-type filers
  regardless of whether the data is present — a bank's balance sheet
  isn't a capital structure in the industrial sense. This extends §A7's
  bank-revenue-proxy note from a data problem to a *metric-definition*
  problem: the right fix is per-sector metric selection, not per-sector
  thresholds (which A2 already handles). Sprint 4-ish, own slice.
- **Distinguishing FAIL from UNAVAILABLE** in scoring (§A10). Cheap and
  separable from the ingest work — the data is already stored correctly,
  only the scoring collapses it. Sprint 2.2.

## A9. Sprint 2 — sector-relative screen implementation

A2 described the mechanism; this records the choices made turning it into
code (`moat/screen/quant_screen.py`, `moat/quality/quality_score.py`).

**Absolute floors are deliberately much looser than PRD §4's flat
criteria.** §4 lists e.g. "ROIC > 15%" as the flat bar; `config.py`'s
`ABSOLUTE_FLOORS` uses 0.0 for ROIC/ROE/operating margin. That's
intentional, not a bug: the old flat bar is where sector-relative
comparison now lives (a top-tercile bar within the company's own GICS
sector), and the absolute floor's only job is to disqualify outright
regardless of sector (e.g. negative free cash flow) per A2's own wording.

**Percentile formula:** for each metric, `compute_sector_percentile`
ranks a company against same-sector peers as "% of peers this company is
at least as good as" (ties favor the company — the "weak" percentile-rank
convention), direction-aware per metric (`debt` and `share_dilution` are
lower-is-better; everything else is higher-is-better). The sector-relative
bar is the 66.7th percentile — A2's "top tercile" made literal.

**Combining floor + sector-relative into `overall_pass`:** a metric passes
only if it clears *both* the absolute floor and the sector-relative bar,
falling back to floor-only when a sector comparison isn't available —
missing GICS sector, or a peer group smaller than
`MIN_SECTOR_PEER_GROUP` (5). 15 NASDAQ-100-only companies have no sector
at all (Wikipedia's NASDAQ-100 source doesn't carry GICS, flagged in
Sprint 1 — see `moat/ingest/universe.py`); those get floor-only scoring
rather than being silently failed for a comparison we can't compute (same
"flag, don't guess" rule as A4).

**Per-metric value definitions** (what's actually compared, not the raw
DB column — company size shouldn't dominate a peer comparison):
- `roic`, `roe`, `operating_margin`, `gross_margin`: the ratio as stored,
  latest fiscal year.
- `free_cash_flow`: FCF **margin** (FCF / revenue), not raw dollar FCF.
- `debt`: total debt / latest free cash flow. Debt outstanding against
  zero-or-negative FCF is an explicit absolute-floor fail (can't be
  serviced from operations), not a missing value.
- `revenue_eps_growth`: revenue CAGR across all available fiscal years;
  EPS trend (latest vs. earliest available) is a secondary floor-only
  gate, not part of the ranked value, since EPS is noisier and can be
  negative (CAGR undefined off a negative base).
- `share_dilution`: CAGR of diluted share count across available years
  (negative = buybacks = good; this is a lower-is-better metric).

**Bug found and fixed: stock splits misread as dilution/EPS collapse —
and a correction to how this was first diagnosed.** Running the real
screen surfaced Walmart failing both `share_dilution` and
`revenue_eps_growth` outright: `shares_diluted` jumps 2.85B → 8.42B
between our stored FY2021 and FY2022 rows, with EPS dropping 4.75 → 1.62
to match. The first pass at this write-up said "Walmart's 3-for-1 split
(FY2022)" — that's wrong on the date, and wrong in exactly the way §A7
warns about: pattern-matching a known corporate action ("WMT did a 3-for-1
split, this looks like one") without checking it against a primary
source. Walmart's real split was effective **Feb 23, 2024**, not FY2022.

What's actually happening, confirmed directly against SEC's raw XBRL
`filed` timestamps: a 10-K filed after a split restates its comparative
income statement (current year + ~2 priors) onto the post-split share
basis, and `_annual_entries`' "most-recently-filed value wins" rule
(added in Sprint 1 for ordinary restatements) picks those up. Walmart's
FY2024 10-K (filed 2024-03-15, after the split) restates FY2022 as its
oldest comparative — jumping that period's shares from the originally
filed 2.805B to 8.415B — but never touches FY2021, which falls outside
that filing's 3-year comparative window and stays on the pre-split basis
forever (no later filing's comparative window reaches back that far).
The jump we see is real, but it marks the edge of a restatement window,
not the split's actual date — confirmed with the same ~2-year offset on
Apple (real split Aug 2020; FY2020 10-K, filed 2020-10-30, restates the
FY2018 comparative from 5,000,109,000 to exactly 20,000,435,000 — a clean
4.0x — so the jump lands at FY2018, two years early) and Nvidia (real
splits 2021 and 2024; jumps land at FY2020 and FY2023 respectively, same
~2-year pattern both times).

This doesn't change the fix, only the explanation: `_detect_split_factors`
treats a ≥40%-in-one-year jump in diluted share count as a split/reverse-
split (real buybacks/issuance essentially never move that fast in a
single year) and rescales every year before the jump onto the latest
year's basis — shares multiplied by the detected ratio, EPS divided by it
(they move inversely). It doesn't need to know the real split date; it
only needs to find wherever the basis actually changes in the merged
series, which it does regardless of why the jump is positioned where it
is. After the fix, WMT's split-adjusted dilution CAGR is -2.2%/yr
(buybacks, correctly), and it now passes both metrics it previously
failed on the artifact alone. AAPL and NVDA both compute a small, real,
sub-3% dilution CAGR post-fix rather than a split-inflated number.
Trade-off, **as originally assessed — this assessment was wrong, see
§A10**: a real, non-split share issuance that happens to jump ≥40% in one
year (e.g. a large stock-funded acquisition) would be misread as a split
too and under-penalized — assumed rare in practice, and a false-negative
(missed dilution) rather than a crash, so accepted for Sprint 2 rather
than building full corporate-action tracking for it.

**Correction (measured after Sprint 2 shipped):** "rare in practice" was
asserted without measuring it. The detector fires on **189 of 505
companies (37.4%)**. It is not detecting stock splits; it is detecting
*any* large discontinuity in reported diluted share count, which turns
out to be three distinct phenomena with three different correct
treatments — genuine splits (adjust, as we do), real corporate events
such as IPOs and mergers (must not adjust), and unit-of-measure errors in
the source data (must be rejected at ingest, not silently normalized).
Full findings, evidence, and the fix in §A10.

**Quality score and pass threshold:** `compute_quality_score` is the
% of the 8 metrics a company's `overall_pass` cleared (0-100). The pass
threshold is `QUALITY_SCORE_PASS_THRESHOLD = 50.0` — at least half.
Against the real 505-company universe this produces **111/505 (22.0%)**
passing. That's a higher rate than PRD §13's "~50-100 out of ~850"
target, but §13's ~850-company figure was scoped for the full US+UK
universe including FTSE 350; S&P 500 + NASDAQ 100 is already a
pre-filtered set of large, established businesses, so a higher pass-through
rate here is expected rather than a sign the bar is too loose. Revisit
once FTSE 350 is added (A1) and the denominator grows to match §13's
original assumption.

**Known limitation, not fixed this sprint: ROIC noise for near-zero
invested capital.** Adobe (this run's #1, 100/100) computes ROIC = 111%.
Real cause, not a bug: Adobe carries zero debt and a large cash balance
against modest equity (years of buybacks), so ROIC's denominator
(`total_debt + equity - cash`, from `fundamentals_edgar.py`) is small
enough that the ratio becomes noisy — a real characteristic of Adobe's
balance sheet, not a miscalculation, but a company can land at the top of
the ranked list substantially on the back of one noisy metric. This is
exactly what A4's `confidence='medium'` tag on ROIC already exists to
flag (it's tagged medium precisely because it's "always an estimate"),
but the Sprint 2 screen doesn't yet *use* that tag — every fundamentals
value is compared identically regardless of confidence. Folding
confidence into the screen (e.g. discounting or footnoting medium/low
values rather than ranking them at face value) is reasonable Sprint 3+
scope, flagged here rather than silently left for a future session to
rediscover.

## A10. Sprint 2 post-review findings — the dilution metric is defective

An independent AI review of the Sprint 2 sample output against this
addendum and the PRD flagged the share-dilution metric as unreliable.
The claim was checked rather than accepted, and it holds. **Every
numeric assertion in that review reproduced exactly** against the
database — the nine dilution figures, the 12.5-points-per-metric
scoring, the 66.7% tercile boundary, and spot-checks of FCF margin,
debt/FCF, operating margin and revenue CAGR. Those parts of Sprint 2 are
confirmed working.

**Status: `share_dilution` should not be trusted until Sprint 2.1 lands.**
The other seven metrics are unaffected.

### What the detector actually detects

`_detect_split_factors` treats a ≥40% one-year jump in diluted share
count as a stock split. It fires on **189 of 505 companies (37.4%)** —
§A9's "rare in practice" was asserted without measurement and is wrong.

The jump is real; the *interpretation* is what fails. Three different
phenomena produce it, needing three different treatments:

| Phenomenon | Correct treatment | Currently |
|---|---|---|
| Genuine stock split | Adjust prior years to latest basis | ✅ correct |
| Real corporate event (IPO, merger, recap) | **Do not adjust** — dilution is real | ❌ adjusted away |
| Unit-of-measure error in source data | **Reject at ingest** | ❌ silently normalized |

### The decisive test: restatement

A genuine split **restates prior-period share counts across filings** —
the same period-end carries a different value in a later 10-K, because
the filer rebased its comparatives. A real share issuance restates
nothing: the count genuinely grew, and every filing agrees on every
period. This is directly checkable in the raw XBRL we already fetch.

Verified against the review's own nine examples:

- **Genuine splits** (restated; our adjustment is correct):
  SMCI `10.00x`, CTAS `4.00x`, MA `10.01x`, AME `1.50x`
- **Real dilution** (no restatement anywhere; false positives):
  TKO (WWE/UFC merger), CRWV (IPO), ALAB (IPO), CHTR (Time Warner Cable
  merger), KHC (Kraft-Heinz merger)

So the review was right that false positives exist, but wrong to
attribute all nine to corporate events — four are genuine splits, and
reverting to raw share counts (which its framing implies) would
*introduce* errors on those. The restatement signature separates the two
exactly, with no heuristic tuning, and is a stronger test than the
review's proposal (EPS moving inversely + price data + filing text),
which needs new data sources and still fuzzy-matches.

### The larger problem the review missed: unit errors

The most extreme detected "splits" are not splits or corporate events —
they are **unit-of-measure inconsistencies in the source data**:

- Southwest (LUV) FY2007: `768` diluted shares → FY2009: `741,000,000`
- Agilent (A) FY2007: `406` → FY2008: `371,000,000`
- Hershey (HSY) FY2009: `228,995` → FY2010: `230,313,000`
- Northern Trust (NTRS) FY2008: `224,053,430,000,000` — 224 *trillion*
  shares; a plainly corrupt value

The split detector **launders these into plausible-looking metrics**.
That is worse than either other failure mode: a corrupt input produces a
clean-looking output with nothing downstream able to detect it, which is
precisely the silent-deterministic-error class this project exists to
avoid (PRD §14 — "the numbers determine which companies deserve
attention").

A cheap internal-consistency check catches them at ingest:
`eps_diluted × shares_diluted ≈ net_income` flags **125 of 7,573 rows
(1.7%)**, including every case above. Intended as a *flag*, not an
auto-reject — some failures are legitimate (banks' preferred dividends
sit between net income and EPS; pre-IPO share structures).

### Materiality

Comparing current (split-adjusted) against raw-unadjusted dilution
across all 505 companies:

| | |
|---|---|
| Dilution pass/fail differs | **67 / 505** (36 adjusted-pass, 31 adjusted-fail) |
| Companies crossing the 50% screen threshold | **10** |
| Total passing the screen | **111 either way** |

Gaining screen pass under adjustment: BALL, CI, LNT, NEE, UNP.
Losing it: CTVA, DUK, GE, LLY, PEP.

**The identical headline (111) is a trap**, and worth recording as a
lesson in its own right: a check on the summary statistic alone would
conclude "no impact" while ten companies silently swap in and out of the
investable universe. Aggregate stability is not evidence of per-company
correctness.

### Fix (Sprint 2.1)

Detection moves to **ingest**, keyed on restatement rather than jump
size. The screen then consumes a known split factor instead of inferring
one. This requires provenance the pipeline currently destroys — see §A11.

Note that reverting to raw share counts is *not* the fix: raw is wrong
for genuine splits and wrong for unit errors. Both current worlds are
wrong, just differently.

## A11. Provenance and verification (extends A3)

**Decision:** derived fundamentals must retain a resolvable link back to
the filing they came from, and the raw source payload must be kept.

**Why:** §A10's defect went unnoticed, and was then *mis-diagnosed twice*
— once in §A9's original write-up (a stock split attributed to the wrong
year without checking filing dates) and once by the external review (four
genuine splits attributed to corporate events). Neither was carelessness.
Both had the same structural cause: **the pipeline destroys its own
evidence.**

As of Sprint 2:

- `fetch_company_facts()` fetches raw XBRL, uses it, and discards it — no cache
- `FILINGS_CACHE_DIR` is declared in `config.py` and never used
- the `filings` table has existed since Sprint 0 and holds **0 rows**
- `fundamentals_annual` carries no `accession_number` and no `filed` date
- the merge rule ("most recently filed wins") deletes the losing value —
  which is exactly the restatement evidence §A10 needs

Every number in the database is an orphan. Verifying any claim about one
required writing a throwaway script and re-fetching from SEC. **Verification
that costs a custom script gets skipped**, and confident prose fills the gap.

**Mechanism (Sprint 2.1):**
1. **Keep the receipt** — cache raw `companyfacts` JSON per CIK under
   `FILINGS_CACHE_DIR`, with retrieval date. Makes later checks offline,
   instant, and reproducible. SEC's data drifts (§A7: `company_tickers.json`
   grew mid-project), so a dated snapshot is what makes a claim
   re-checkable later.
2. **Provenance as a column** — `accession_number` + `filed` on every
   `fundamentals_annual` row. Both are already parsed from the XBRL and
   thrown away.
3. **Keep the contradictions** — record when filings disagree on the same
   period rather than silently resolving. This is what makes §A10's
   restatement test possible, and generalizes to ordinary restatements,
   which matter to a quality screen regardless.
4. **Populate the `filings` table** — already schema'd with `document_url`;
   once populated, any number joins to a real EDGAR URL.
5. **One-command verification** — `scripts/verify.py TICKER FIELD --year N`,
   printing every filing that reported that fact for that period with
   values, filed dates, accessions and URLs. Both misdiagnoses above would
   have died in seconds against that output. The point is not novelty:
   **grounding has to be cheaper than guessing, or guessing wins.**

**Scope extension to A3:** §A3 requires a resolvable citation for every
*AI-generated* claim. Both failures here were **quantitative** claims about
corporate events, which sat outside that fence entirely. A3's rule is
widened: any claim about *why a number looks the way it does* — written by
a model or a person, in code comments, sprint docs or this addendum —
needs an accession number. "WMT split in FY2022" should have been
unwriteable without a filing reference to hang it on.

**Sequencing note:** this is not merely Sprint 2.1 hygiene. Sprint 3's
citation enforcement (§A3) needs exactly the populated `filings` table and
`document_url` that step 4 provides. Doing this now **unblocks** Sprint 3
rather than delaying it — the requirement was always there; a quantitative
bug simply surfaced it before the AI work did.

**Supporting habits:**
- **Exports carry provenance.** The review analyzed a workbook with no
  accession columns, so its chain dead-ended at derived values and it
  inferred causes instead. Extracts should carry source columns.
- **Claims in docs get tests.** We already write one regression test per
  root cause; a claim like "this jump is a split" is testable against
  primary source. Same habit, applied to prose.

### Where this generalizes

§A7 drew the lesson: *run against the golden source, not a summary.*
Sprint 2's own analysis then ran against the database (a summary), and the
external review ran against an Excel extract (a summary of a summary). The
error rate rose at each remove. The architecture quietly encouraged exactly
what the documentation warned against — because the golden source was
fetched and then thrown away. Documented principles don't survive contact
with a pipeline that makes following them expensive.
