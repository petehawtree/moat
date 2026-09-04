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

## A12. Sprint 2.1 execution notes (ingest integrity + provenance)

Implements §A10's fix and §A11's provenance layers.

**What was built:**
- **Raw `companyfacts` cached** under `FILINGS_CACHE_DIR` (502 files, ~2GB,
  gitignored). Re-running the full ingest dropped from **353s to 11s**, which
  is the point: verification and re-analysis are now cheap enough to actually
  do (§A11's "grounding has to be cheaper than guessing").
- **`share_basis_changes`** — restatement evidence captured at ingest, where
  every filing's version of a fact is still visible. **360 changes across 129
  companies**: 284 splits (108 companies) and 76 unit corrections (33).
- **Provenance columns** on `fundamentals_annual` (`accession_number`,
  `filed`, `quality_flags`) — 8,210 rows now traceable to a filing.
- **`filings` table populated**: 0 → **7,497 rows** with resolvable EDGAR
  URLs. This is what Sprint 3's §A3 citation enforcement resolves against.
- **`scripts/verify.py`** — one command to show a stored number beside every
  filing that reported it.
- **Screen gated on evidence**: a share-count jump is only adjusted when a
  filing actually restated that period.

**Result.** Holding data constant and toggling only the gate: **20 of 505
companies** get a different dilution verdict and **4 cross the screen
threshold** (CI, LNT leave; CPRT, GE enter). All five known false positives
now report their real dilution — TKO 6.2% → **53.1%**, CRWV 6.6% → **50.7%**,
ALAB 14.1% → **73.9%**, KHC −0.3% → **11.0%**, CHTR −4.5% → **3.1%** — while
genuine splits still adjust correctly (WMT −2.2%, AAPL −2.6%, CTAS −2.5%,
MA −2.1%: buybacks, as they should be).

**Two bugs found in the fix itself, both caught by `verify.py`** — worth
recording, because the tool earned its cost during the sprint that built it:

1. **Unit corrections must not drive rescaling.** Treating a 1000x
   "restatement" as a basis change assumes one clean switchover. But the bad
   unit can occupy the *middle* of a history: ConocoPhillips filed FY2010-2019
   in thousands with actual units either side. Rescaling everything before the
   boundary multiplied the already-correct FY2007-2009 rows by 1000, producing
   a -32.6%/yr dilution CAGR. Only `change_type='split'` now corroborates an
   adjustment; unit errors exclude the affected rows instead — §A10's "reject",
   not "normalise".
2. **A median-based outlier check inverts when bad rows are the majority.**
   Flagging share counts far from the company's own median flagged COP's and
   EG's *correct* rows, because ten bad years outnumbered nine good ones and
   dragged the median into the wrong unit. Replaced with a self-evidencing
   per-row test: `eps × shares / net_income` landing on a power of 1000 means
   the share count is in the wrong unit; any other miss means net income and
   the EPS numerator differ structurally (noncontrolling interests, preferred
   dividends). That distinction matters — an earlier version dropped TKO's
   share counts as "inconsistent" when its NCI structure was the cause,
   erasing the very merger dilution this sprint set out to restore.

**Validation split by cause:** 125 flagged rows — 66 `share_count_unit_outlier`
across 27 companies (genuinely unusable share counts), 59
`eps_shares_ni_mismatch` across 39 companies (EPS not comparable, share count
sound).

**Note on the regression method.** The first comparison against the Sprint 2
run showed 11 threshold crossings, but re-ingesting had also pulled fresh
filings — SanDisk's FY2026 10-K appeared in the interim and moved it 0 → 87.5
on its own. Isolating the fix (same data, gate on vs off) gives the honest
figure of 4. Worth remembering: a regression run against re-fetched data
measures the fix *and* the data drift together.

**Still open:** `SNDK`'s FY2026 figures (revenue $7.4B → $20.2B, net income
−$1.6B → +$11.4B post-spinoff) look like predecessor/combined reporting rather
than standalone results. Not investigated — flagged here rather than left to
be rediscovered.

## A13. Sprint 2.2 — data integrity (second external review)

A second external code review audited the shipped Sprint 2/2.1 system against
the PRD and this addendum. Its verdict — **"not investment-ready"** — was
correct, and every verifiable claim it made reproduced exactly against the
database. Findings and dispositions:

### Fixed in Sprint 2.2

**1. Operating cash flow was stored and scored as free cash flow.** When capex
was missing, `extract_annual_fundamentals` substituted OCF for FCF. That
inflates both FCF margin and debt/FCF, and inverts the metric's meaning for
exactly the capital-intensive businesses where capex matters most. It affected
**155 of 505** companies; 73 passed the FCF metric on the substituted figure
and **38 passed the whole screen**. FCF is now `None` when capex is unknown,
and `operating_cash_flow` is stored in its own column so the figure we do have
isn't lost.

**2. REIT revenue was understated by two orders of magnitude.** Rental income
is ASC 842 (leases); our candidate tags covered only ASC 606 (contracts with
customers). Camden Property Trust ingested **$12.967m** against a real
**~$1.6bn**, producing a 6,375% "FCF margin" that took the 100th percentile in
Real Estate. Because percentiles are relative, that one row shifted **all 30**
Real Estate peers and pushed one (KIM) across the top-tercile bar — a single
bad row was never contained to its own company. The two standards cover
mutually exclusive revenue streams, so the correct treatment is **additive**,
not precedence: total revenue = contract revenue + lease income, short-circuited
when the figure already came from a consolidated tag (`Revenues`). CPT now
reads $1,586,511,000.

**3. Unavailable data was scored as failure.** `_combine_pass` turned `None`
into `0` and `compute_quality_score` divided by all eight metrics, so
"we couldn't measure this" was indistinguishable from "this company did
badly" — mislabelling **257 ROIC, 202 gross-margin, 201 debt and 113
operating-margin** results as failures. `quant_scores.status` is now
`pass`/`fail`/`unavailable`, and the composite score is the percentage of
**assessable** metrics passed. Since that would let a company measured on one
metric score 100, `MIN_METRICS_ASSESSED = 6` is required to pass at all;
companies below it carry an explicit coverage note. **205 companies** are now
excluded on coverage rather than silently marked as failures.

**4. Quarantine for implausible rows.** A row whose income exceeds the revenue
it was supposedly earned on has a fragmentary revenue tag (DTE: $61m revenue
against $2.37bn operating income, real revenue ~$13bn; Fifth Third: $80m
against $2.52bn net income). Such rows are excluded from **every** peer group,
and their own metrics report as `unavailable` — values still stored so the
problem stays diagnosable.

**5. The cache never expired — a regression introduced by Sprint 2.1.** §A11's
provenance cache shipped with no TTL and no refresh path, so fundamentals were
frozen permanently while prices kept updating: a silent violation of §A6's
quarterly refresh. `FUNDAMENTALS_CACHE_MAX_AGE_DAYS = 90` now applies, with
`max_age_days=None` for verification reads (so re-checking a past claim isn't
disturbed by SEC having since revised the data). Worth noting how this
happened: the cache was added for *provenance*, measured on a 353s → 11s
speedup, and nobody asked what it did to *freshness*.

**6. Successful runs were marked `failed`.** Running into a deliberately
unbuilt stage raised `NotImplementedError` and marked the whole run failed —
so every successful screen was recorded as a failure, and the dashboard read
its results from runs labelled `failed`. Reaching a planned sprint boundary is
now `partial`; `failed` is reserved for real errors. The dashboard also
excludes failed runs, and `--init-db --init-only` creates the schema without
starting a full network pipeline.

### A false positive caught while fixing it

The first version of the plausibility rule flagged any margin outside ±100%.
That quarantined **Moderna** (−158% operating margin) and **MicroStrategy**
(−1141%) — both *genuine* losses on real revenue, not data errors. Excluding
distressed companies from peer comparison as "bad data" would be a worse
failure than the one being fixed, and would systematically flatter the
remaining peer group. The rule now keys on *positive* income exceeding
revenue, which separates a fragmentary revenue tag from a large real loss.

### Accepted but not fixed

- **Financial-sector metrics remain definitionally wrong** (§A8). FCF margin,
  debt/FCF and gross margin don't describe a bank. Sector-*relative* ranking
  cannot rescue an invalid metric *definition* — this needs per-sector metric
  selection, and is deferred rather than patched.
- **`filings.content_hash` / `local_path` are still NULL**, so §A5's
  filing-hash AI cache key is unavailable. The XBRL payload carries accessions
  and dates, not document text; fetching the documents themselves is Sprint 3
  work.
- **No-sector companies still get floor-only scoring** (§A9). Disclosed, and
  their scores remain less comparable than sector-ranked ones.

### Disputed

The review listed the unimplemented AI, valuation, committee, brief and
monitoring stages as **High severity** while acknowledging they are declared
future sprints. They are Sprints 3–6 of a documented six-sprint plan, and
grouping them with live calculation defects overstates the position: the
honest statement is "at Sprint 2.2 of 6," not "High severity issue." The same
applies to its "missing tests" for unbuilt features, and to FTSE/FX (deferred
by §A1). Separately, `pytest>=8.2` is in `requirements.txt` and the suite runs
clean — the reviewer's inability to execute it was an environment that hadn't
installed requirements, not a repository defect.

### Result

**98 of 505** companies now pass, against 111 before. The fall is the point:
38 were passing on substituted OCF, and 205 are now correctly reported as
insufficiently measurable rather than as failures.

## A14. Sprint 2.2 follow-up — status inversion and sector applicability

A code review of Sprint 2.2 itself found two issues. Both verified exactly.

### 1. A determined failure was relabelled "unavailable"

Sprint 2.2 introduced `pass`/`fail`/`unavailable` precisely to stop
"we couldn't measure this" being scored as "this company did badly." It then
inverted that distinction in the one case where a **failure is expressed
without a value**.

A company carrying debt with no positive free cash flow to service it fails
`debt` outright — `_absolute_floor_pass` returns 0, disqualifying regardless
of sector. But debt/FCF is deliberately `None` there, because dividing by
non-positive cash flow is meaningless. `_metric_status` tested `value is None`
*first*, so the explicit failure became `unavailable`, was dropped from the
denominator, and effectively vanished.

**Impact:** 109 rows; 17 companies that passed the screen were affected and
**9 should not have passed at all** (CBRE, DLTR, ETR, FCX, KMB, PEP, ROK, URI,
VLO — all now 42.9 and correctly excluded).

**Fix:** status keys on `absolute_floor_pass`, not on `value`. A verdict was
reached or it wasn't; whether it can be expressed as a comparable ratio is a
separate question. The lesson generalises — an "unknown value" and an
"unknown verdict" are different things, and conflating them is what caused
both this defect and the one Sprint 2.2 was written to fix.

### 2. Deferring a fix is not the same as continuing to emit the output

§A8 deferred sector-specific metric definitions, and §A13 restated that
sector-*relative* ranking cannot rescue an invalid metric *definition*. Both
are still true. But the shipped screen went on scoring financials with those
metrics and publishing the resulting ranks — which is what the review objected
to, correctly. Deferring the *fix* is a scheduling decision; continuing to
emit a number computed from inputs we have documented as meaningless is a
correctness decision, and we had quietly made the second one by not deciding.

**Mechanism:** `SECTOR_INAPPLICABLE_METRICS` marks a metric
`not_applicable` for a sector — a *definitional* exclusion, distinct from
`unavailable` (we tried to measure it and couldn't). For `Financials`:

- `gross_margin` — a bank has no cost of goods sold.
- `free_cash_flow` — a bank's operating cash flow mixes operating and
  financing activity, so FCF/revenue is not an operating margin. The data bore
  this out with "margins" of 49x (FITB) and 29x (RF).
- `debt` — debt is a bank's raw material, not a solvency signal. Scoring
  debt/FCF penalises the balance sheet a bank is supposed to have.

Not-applicable metrics are excluded from peer groups and from the score
denominator. With 3 of 8 metrics gone, financials fall below
`MIN_METRICS_ASSESSED` and are reported as **unscreenable**, carrying an
explicit note, rather than being mis-ranked.

**Consequence, stated plainly:** **all 74 financials now fall out of the
screen** (15 previously passed). Total passing drops 98 → 93. That is a real
reduction in coverage of the largest sector in the universe, and it is the
correct position: the tool does not currently have a valid screen for banks,
insurers or brokers, and should say so rather than produce a number. Restoring
them requires financial-sector metrics (book value growth, net interest
margin, efficiency ratio, loan-loss provisioning, tangible common equity) —
still deferred, now visibly rather than silently.

**Real Estate is the obvious next candidate** for the same treatment: REIT
capex is property acquisition, so FCF and debt/FCF misdescribe them too, and
FFO/AFFO is the right basis. Not done here — flagged rather than quietly
extended beyond what the review evidenced.

## A15. Sprint 3 — citation architecture (implements A3, extends A11)

§A3 required every AI claim to carry a resolvable citation. This section
records *how* that requirement is met, because the naive implementation —
ask the model for a JSON list of `{accession_number, quote}` and check the
quotes — solves a smaller problem than the one Sprint 3 actually has.

### A15.1 What Sprint 3 is missing before it starts

`filings` holds 7,497 rows of accession numbers and EDGAR index URLs.
`local_path` and `content_hash` are NULL on every one of them, because the
rows were derived from the XBRL `companyfacts` payload, which carries
**numbers and the accessions they were filed under — no document text.**

Business quality, moat, management and risk are narrative judgements. They
live in Item 1, Item 1A and Item 7 of the 10-K, none of which the pipeline
has ever fetched. So Sprint 3 is two builds stacked: a **document layer**
(fetch, normalize, section, hash, cache the actual filing text) and then the
analysis on top of it. Treating it as one build is the main schedule risk;
the document layer is where the defects will be.

### A15.2 Decision: use the API's native citations, not model-authored citation JSON

**Decision:** filing text is supplied as `document` content blocks with
`citations: {"enabled": true}`, and citations are read from the API response
rather than parsed out of model-written JSON.

**Why:** the two failure modes §A3 exists to prevent are a claim with no
citation and a citation that was invented. Native citations remove the second
one *structurally* rather than by detection:

- the model can only cite documents supplied in that request, so it cannot
  cite a filing it never saw;
- `cited_text` is extracted by the API from the supplied document, not
  generated by the model, so a quote cannot be a paraphrase, a hallucination,
  or a real quote from the wrong filing;
- each returned text block carries its own citation list, which is far
  better than the stub's original design — one prose blob plus a bag of
  citations — where nothing says which claim a citation belongs to.

**Corrected after external review:** an earlier draft of this section said
block boundaries give claim-level granularity *for free*. They do not. A text
block is an artefact of how the API split the response, not a claim grammar:
one block can carry two propositions, and uncited connective prose can appear
in its own block. "One claim per block" is a request, not a guarantee, and
enforcement cannot rest on it. Claims are therefore **parsed by us** from a
constrained textual protocol into an `analysis_claims` table with a stable
`claim_id`, and each parsed asserted claim is then required to carry a
resolving citation. The API still supplies the evidence; it does not define
the unit the evidence attaches to.

`validate_citations()` therefore changes job. It was written to catch
fabrication. Fabrication is now prevented upstream, so validation enforces
**coverage** (no uncited prose) and **durability** (see A15.3).

**Consequence:** structured outputs (`output_config.format`) are unavailable
— the API rejects the combination with a 400, because citations interleave
citation blocks with text. Response structure comes from a documented text
protocol (one claim per block; the literal prefix `INSUFFICIENT EVIDENCE:`
for a block that deliberately declines to assert) rather than a JSON schema.
That is the right trade: a schema would guarantee the *shape* of citations,
which is the part we no longer need guaranteed.

**What this still does not verify: entailment.** A citation proves the quote
is real and was supplied. It does not prove the quote *supports* the claim
attached to it. That fence is stated here rather than left implied, because
§A11's lesson is that unstated fences get read as coverage. Support checking
is a judgement problem (an LLM-judge pass, or a human read), and it is out of
scope for Sprint 3.

### A15.3 Decision: request-local coordinates are resolved to durable anchors at call time

**This is the central decision of the sprint.**

The API returns a citation as `document_index`, `start_char_index`,
`end_char_index`, `cited_text`. Those coordinates are **request-local**:
`document_index: 0` means "the first document block in that one request",
and the character offsets index the exact string sent in it. The moment the
request is over, that frame of reference is gone.

Persisting the API's citation object as-is would store coordinates whose
frame we threw away — **precisely the §A11 failure**, in a new place. A
number that cannot be resolved back to its source is an orphan whether it is
a share count or a character offset.

**Decision:** every citation is translated, at the moment of the call and
while the request frame is still in hand, into an anchor that means something
on its own:

| Field | Purpose | Durability |
|---|---|---|
| `accession_number` | the filing | permanent (SEC-assigned) |
| `section_id` (`item_1`, `item_1a`, `item_7`) | which part of it | stable across re-fetch |
| `doc_sha256` | the exact normalized text we sent | pins the offsets |
| `norm_version` | which normalizer produced that text | makes A15.6 detectable |
| `start_char` / `end_char` | *position selector* | valid only for that `doc_sha256` |
| `quote` + `quote_sha256` | *quote selector* | survives re-chunking |
| `prefix` / `suffix` (48 chars each) | disambiguates a repeated quote | survives re-normalization |

**Why both a position selector and a quote selector.** Position is exact and
free to resolve, and worthless the moment the document is re-fetched or the
normalizer changes. A quote plus surrounding context can be re-found in a
document that has shifted underneath it. Storing only one of the two means
either fast-but-brittle or robust-but-ambiguous (a quote like "competition is
intense" occurs many times in one 10-K, which is what `prefix`/`suffix` fix).
This is the W3C Web Annotation selector pattern, and it is the specific thing
that lets a citation outlive the text it was taken from.

### A15.4 Decision: claims and citations are tables, not a JSON column

The raw API response is kept verbatim as an audit record and never queried
for logic. The durable structures are three tables, not one:

- **`analysis_claims`** — one row per parsed claim, with a stable `claim_id`,
  `claim_text`, `claim_order` and an `assertion_status` of `asserted` or
  `insufficient_evidence`. This is the unit citations attach to (§A15.2).
- **`citations`** — immutable anchors, each with a required FK to its claim.
- **`citation_resolution_events`** — one row per re-anchoring attempt.

**Why not JSON in a column:** it cannot answer the three questions that
"future recall" actually means.

1. **"Which claims rest on this filing?"** — asked every time a new 10-K
   supersedes an old one. Without it, Sprint 6's monitoring cannot tell which
   conclusions just went stale, and A5's cache key silently protects
   conclusions that should have been re-examined.
2. **"Does this citation still resolve?"** — a sweep across every stored
   citation, not a per-document read.
3. **"What evidence did the brief I read in March actually stand on?"** —
   which requires the anchor written in March to still say what it said in
   March.

**Corrected after external review:** an earlier draft called citation rows
"append-only" while also giving them `resolved_status`, `resolved_score`,
`last_verified_at` and `superseded_at` — fields that change on every
re-anchoring sweep. Those two statements contradict each other, and the
mutable version loses exactly the history the third question needs. The
anchor is now immutable once written; every resolution attempt appends to
`citation_resolution_events`, and current status is *derived* from the latest
event. Supersession is likewise a relationship between analysis versions
(`is_current`, `superseded_by_run_id`) rather than a date stamped onto every
citation of a superseded analysis.

### A15.5 Decision: accepting a citation and recalling one have different tolerances

They are different jobs and must not share a matcher.

- **Accepting (write path):** exact only. `cited_text` must equal
  `normalized_text[start_char:end_char]` byte-for-byte. This is not
  defensive theatre against the API — it is an assertion against **our own**
  document-index→accession mapping, which is the one place in this design
  where a silent off-by-one attaches real quotes to the wrong company. A
  mismatch fails the stage.
- **Recalling (read path):** a resolution ladder, tried in order, recording
  which rung succeeded:

  1. offsets, when `doc_sha256` still matches → `exact`
  2. exact quote search within the same `section_id` → `moved`
  3. exact quote search across the whole filing → `moved_section`
  4. whitespace/normalization-insensitive search → `renormalized`
  5. fuzzy match on quote + `prefix`/`suffix` above a similarity floor →
     `fuzzy` (recorded with its score)
  6. nothing above the floor → `unresolved`

Each attempt writes a `citation_resolution_events` row — which rung answered,
the score if fuzzy, and the span it landed on. The anchor itself is never
touched.

**Rules that do not bend:** an `unresolved` result is *recorded and marks its
analysis stale* — the citation is never deleted, and the anchor is never
re-pointed at a different span that happens to score well. A citation that
quietly changes what it points at is worse than one that admits it is broken.
Recording the outcome as an event rather than an overwrite is what makes that
rule enforceable instead of merely stated.

### A15.6 Decision: normalization is versioned

Offsets index normalized text (whitespace collapsed, unicode dashes/quotes
folded, HTML entities and non-breaking spaces resolved), never raw HTML. The
normalizer carries `norm_version`, stored on every anchor.

**Why:** the normalizer *will* change — it always does, once a filing with an
unusual encoding shows up. Without a version, that change silently invalidates
every stored offset while leaving them syntactically valid: the citation still
resolves, to the wrong 60 characters. With a version, the mismatch is
detectable, and the quote selector (A15.3) is what carries the citation across
it.

### A15.7 Cache key (extends A5)

§A5 keys the AI cache on "the filing/fundamentals version it was generated
from". That is necessary and not sufficient: a changed prompt or a changed
model invalidates an analysis exactly as much as a changed filing does.

**Decision:** the key covers an *analysis bundle*, not one analysis type —
one request now produces all four (§A15.10), so keying on `analysis_type`
would describe a unit the pipeline no longer generates:

`cache_key = sha256(bundle_protocol_version ‖ prompt_content_hash ‖ model_id
‖ norm_version ‖ extraction_version ‖ sorted(doc_sha256...))`

Every component is stored in its own column too. Note `prompt_content_hash`
rather than a hand-maintained `prompt_version` string: a version number
someone forgets to bump is a cache that serves an analysis the current prompt
would never produce. Storing only the digest makes "why did this regenerate?"
unanswerable, and an unexplained regeneration is how cost control quietly
stops working.

### A15.8 Section extraction is the sprint's real failure surface

The trap, stated before it happens: a naive `Item 1A` regex matches the
**table of contents** entry first, yielding a 200-character "Risk Factors
section" that reads `Item 1A. Risk Factors .......... 23`. The model, behaving
correctly, replies `INSUFFICIENT EVIDENCE`. Validation passes — the analysis
is honest, cited where it can be, and completely wrong about the company.

This is the §A7/§A10 pattern once more: **a data defect that presents as a
modest, well-behaved answer.** Countermeasures, all of which are cheaper than
finding this later:

- per-section plausibility bounds (an Item 1A under ~2,000 characters is a
  failed extraction, not a company without risk factors);
- a stored `section_confidence` and the extraction method used;
- on a doubtful extraction, **flag and skip the company** — or fall back to
  the whole document, at a stated cost multiple. Never proceed on a section
  we do not believe.
- regression tests against real cached filings, including one with a
  trapping table of contents and one inline-XBRL document.

### A15.9 Explicitly not solved in Sprint 3

- **Entailment** — that a quote supports the claim it is attached to (A15.2).
- **Quantitative claims** — §A11 widened A3 to cover claims about *why a
  number looks the way it does*. Sprint 3 grounds narrative claims in filing
  text; the screen's metrics remain grounded by `accession_number` on
  `fundamentals_annual`. Joining the two into one evidence trail is Sprint 5's
  brief, not this sprint's.
- **DEF 14A proxy statements** — where executive compensation, incentive
  structure and board independence actually live. A "management quality"
  analysis grounded only in the 10-K is **thin, and should be labelled as
  thin** rather than presented as a full assessment. Adding the proxy is a
  second fetcher and a second document type; deferred, visibly.
- **Filing history** — Sprint 3 grounds on the latest 10-K only. Management
  track record ("what they said in 2021 vs. what happened") needs several
  years and is a natural Sprint 3.1.
- **10-Qs** — quarterly narrative adds little to a moat assessment at the
  cost of 4x the corpus.

### A15.10 Architecture and cost mechanics

**Decided: production is one combined, citation-enabled request per company,
submitted through the Message Batches API.** Not four calls. The documents go
in once (1.00×D against 1.55×D for four cached calls), batch takes 50% off
every token, and — the part an earlier draft got wrong — the two are not
independently stackable levers. A batch processes its requests
**independently**, so it cannot guarantee that calls two, three and four
start after call one's cache entry becomes readable. "Four cached calls" and
"batched" were listed as separate free levers a reader could take together;
they are mutually exclusive, and the combined request makes the question
moot because it needs no cache hit to be cheap.

Costed at D = 80k: **$0.30/company, ~$28 for 93.** Four calls batched with
writes but no hits would be ~$1.10/company (~$102) — worse than not caching
at all under batch (~$0.90, ~$84), which is the clearest possible argument
for the combined request.

**Consequence: in-request prompt caching is irrelevant to the production
path.** One request per company has no prefix to reuse. The cache-key and
copy-forward machinery of §A5 stays — that is *cross-run* reuse and it is the
lever worth more than every other combined — but the 5-minute TTL,
sequential-call and prompt-assembly-order mechanics apply only to the
four-call form, which survives as a synchronous evaluation path on the
three-ticker pilot and nothing more.

**Retracted: the refusal sentinel is not a stop sequence.** An earlier draft
proposed registering `INSUFFICIENT EVIDENCE:` as a stop sequence to avoid
paying for an explanation. A stop sequence ends the **entire generation** at
its first occurrence, so in a combined request one insufficient sub-point
would truncate the other three analyses — silently, and in a way that looks
like a short answer rather than a failure. It stays a literal, parseable
claim status; verbosity is capped by the prompt and by `max_tokens`.

**Decided: context is not tailored per analysis type.** Under the four-call
design it cost more (2.00×D against 1.55×D) by breaking the shared prefix;
under one combined request the question dissolves. Uniformly dropping a
section for every type is a different and legitimate lever if measured
sections turn out large.

**Deferred, and blocked on something this project does not have: effort and
model tier.** Both trade capability for cost, and both are meant to be swept
against an eval. Citation validation checks *groundedness*, not judgement —
§A15.2 leaves entailment unverified — so there is no automated signal for
whether a moat analysis is any good, only a human reading it. The plan
therefore runs Opus 5 at its default `high` effort with adaptive thinking,
which is the top of the cost curve, and books the saving as unbanked rather
than unavailable: published sweeps on research and knowledge work, the shape
of this task, show nearly flat curves. Effort is swept by hand on the pilot,
before the model is touched.

One escalation pattern survives the missing eval, because a partial failure
signal exists: **run at low effort and re-run only the companies whose
citations fail validation.** It buys groundedness rather than judgement, but
groundedness is the property this sprint actually enforces.

**Recorded so it is not rediscovered:** there is no long-context premium on
these models; the Files API does not reduce cost, since document content
bills per request whether inlined or referenced by `file_id`; and `D` is not
portable between models, because tokenizers differ by up to ~35% — a
cross-model comparison re-counts rather than scaling one number.

**Budget is a control, not an estimate.** `max_tokens = 64,000` does not cost
$64,000 but it *permits* that much billable output: a combined batched Opus
request that actually emitted 64k would cost $1.00/company, $93 for 93 —
three times the estimate. So the estimate is paired with an enforced ceiling
(§A15.12) rather than trusted.

### A15.11 The request frame is a receipt, and receipts get kept

§A15.3 resolves `document_index` into a durable anchor at call time, using
the map from document slot to accession. An earlier draft held that map only
in process memory for the duration of the call.

**Decision:** every attempt persists its full frame — request id, batch id and
`custom_id`, model id, prompt content hash, protocol and extraction versions,
the complete slot→document mapping, the usage fields, and the raw response
JSON — in an `analysis_attempts` table, written whether the attempt succeeded
or failed.

**Why:** this is §A11's lesson arriving at the API boundary. The pipeline
already destroyed its own evidence once by fetching XBRL, using it, and
discarding it; holding the slot map in memory is the same mistake at a
smaller scale. Without the stored frame, a citation that fails its byte-check
cannot be diagnosed after the fact — the only record of what slot 0 pointed
at died with the process. Failed attempts are the ones most worth keeping,
because a failure with no preserved frame is unreproducible by construction.

### A15.12 Spend control, and what "no partial writes" means

**Decided: a hard spend ceiling, enforced in code.** Cost is read from each
response's usage fields and accumulated; submission and retrieval stop once
recorded spend reaches the cap. The pilot runs under a $10–15 evaluation cap
(three tickers, deliberately varied: a conventional filer, an inline-XBRL
filer, and a long risk-factors filer), and the 90-company run is authorised
only after measured p50/p95 input and output — thinking tokens included —
land inside a pre-agreed production cap of $35. An estimate that nothing
enforces is a wish.

**Decided: a company's analyses are written in one transaction, or not at
all.** One combined request produces four analyses; validating them
individually and writing as they pass would leave a company half-analysed
with no marker saying so. The full response is validated in memory, then one
SQLite transaction writes the analyses, claims, citations and the attempt
record together. A validation failure writes the attempt row — with reason
and usage — and no analysis rows at all.

**Decided: coverage is measured over claims, not characters.** An earlier
draft defined `citation_coverage` as cited prose ÷ total prose, which any
short quote wrapped around a long uncited assertion will flatter. The
acceptance metric is `asserted claims with ≥1 resolving citation ÷ asserted
claims`, and it must equal **1.0** — character coverage survives only as a
diagnostic. A gate that can be gamed by the thing it is gating is not a gate.
