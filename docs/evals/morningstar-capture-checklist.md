# Morningstar benchmark — capture checklist

Working checklist for the 7-day Morningstar trial that produces
`morningstar_benchmark_v1`, the one-off external benchmark Project Moat's
future eval runs are scored against.

**Status:** not started. Tick Tier 0 first — two later tiers depend on it.

## Licensing boundary

This file describes *what to capture and where it maps*. It contains no
Morningstar data: no ratings, no fair values, no analyst prose. Field names
and rating taxonomies (Economic Moat, the five moat sources, Uncertainty,
Capital Allocation) are Morningstar's published methodology, referenced
here as factual description. That is safe to commit publicly.

What is **not** safe to commit, and must never enter this repo:

- Captured values of any kind — a fair value estimate, a moat rating, a
  price/fair-value ratio, a per-year fundamentals figure
- Analyst narrative: bull/bear bullets, moat reasoning, risk commentary,
  verbatim or lightly paraphrased
- Bulk exports (CSV/XLSX) from the screener or financials tabs

Morningstar's research is proprietary and may not be reproduced without
consent. Captured data lives **outside the repo entirely**:

```
~/.moat-private/benchmark/v1/        # set MOAT_BENCHMARK_DIR to point here
    tier-a-screener.jsonl
    tier-c-fundamentals.jsonl
    tier-b-reports/<TICKER>.json
```

Outside the repo, not `.gitignore`d inside it — a gitignore is a
convenience, not a boundary, and one `git add -f` or a mistyped path puts
proprietary data in a public history permanently. The repo may publish
*derived agreement metrics* (rates, counts, confusion matrices) and the
benchmark's shape (n, sectors, snapshot date). It may not publish the
inputs those were computed from.

Capture Morningstar's qualitative reasoning as **your own structured
labels and summaries**, never as copied text.

## Tier 0 — access checks

Do these before capturing anything. Two of the later tiers die without them.

| # | Check | If missing |
|---|---|---|
| 0.1 | Is **Economic Moat** a visible column/filter in the stock screener list view? | Tier A collapses to per-company lookup; cut n from ~200 to ~60 |
| 0.2 | Is there a **CSV/Excel export** from the screener or a watchlist? | Roughly triples Tier A capture time; plan manual entry |
| 0.3 | Do you get **full analyst reports** (bull/bear narrative, capital allocation, FVE assumptions) or ratings only? | **Tier B is dead.** Redirect the whole trial to A + C |
| 0.4 | Is there a **Financials / Key Ratios** tab with >=5 years of history per company? | Tier C is dead |
| 0.5 | Does coverage extend to mid-caps? Check `FIX`, `CASY`, `SNA`, `PNR`, `APP`, `SNDK` | Contamination probe weakens; record which names are uncovered, substitute |
| 0.6 | Is there a **rate limit or report-view cap** on the trial tier? | Governs whether 24 reports in Tier B is realistic |

## Tier A — screener list view

**Whole universe if export works, ~200 stratified if not · cheap per
company · this is the durable regression suite.**

Moat's quant screen runs on all 505 companies, so a Morningstar No-Moat
name has a comparable Moat output (`passed_screen = 0`). The negative-control
idea works at this stage and only at this stage.

The screener is a **retrieval** tool here, not a selection tool. Moat's
universe already determines the list; the screener only looks up
Morningstar's verdict on it.

| Morningstar field | Moat counterpart | Priority |
|---|---|---|
| Ticker, company name | `companies.ticker` | Join key |
| Sector / industry | `companies.sector` | High — also fills a real gap, see below |
| **Economic Moat** (Wide/Narrow/None) | `quality_scores.passed_screen` | **Primary target** |
| Moat Trend (Positive/Stable/Negative) | none | Medium |
| **Star Rating** (1-5) | `committee_verdicts.status` | High — ordinal agreement |
| **Uncertainty Rating** | `committee_verdicts.data_confidence`, `risk_score` | High |
| **Capital Allocation** (Exemplary/Standard/Poor) | `committee_verdicts.management_score` | High |
| **Fair Value Estimate** | `valuations.intrinsic_value_low/high` (`owner_earnings_dcf`, base) | High |
| Price/Fair Value | `valuations.margin_of_safety_pct` | Medium — sign agreement only |
| **Last price + as-of date** | `valuations.current_price` | **Blocking** — see below |
| Market cap | none | High — drives coverage/contamination strata |
| Analyst name, report date | provenance | High |

**Price and as-of date are blocking.** Both Morningstar's P/FV and Moat's
margin of safety move with price. Without the benchmark-date price, a
re-run in six months shows "drift" that is really just the market, and
price-dependent metrics can't be recomputed against the frozen baseline.

**Sector capture has a side benefit.** 15 NASDAQ-100 names carry no GICS
sector in Moat (`ALAB, ALNY, ARM, ASML, CCEP, CRWV, FER, MELI, MSTR, NBIS,
PDD, RKLB, SHOP, SPCX, TRI`). Four pass the screen on absolute floors
alone, never facing a sector-relative bar — they clear a weaker test than
every other passer. Morningstar's sector field closes that gap as a
by-product. Capture it even though it is not an eval target.

### Screener criteria

**Never filter on Economic Moat.** That selects on the dependent variable —
choosing companies by the answer you are trying to predict — and makes any
agreement rate uninterpretable. The moat label must be an observed outcome
on a sample drawn independently of it. (This is why the original plan's
three Wide/Narrow/None watchlists were dropped.)

**Primary — take the whole universe, do not sample.** If 0.1 and 0.2 both
pass, this costs the same as 200 names and makes the regression suite far
stronger:

```
Index membership  =  S&P 500  OR  Nasdaq-100
```

That is the entire filter. No market-cap floor, no sector filter, no rating
filter — the weak names matter as much as the strong ones, because Moat's
failures are half the contingency table.

Columns to add to the view before exporting: ticker · name · sector ·
industry · Economic Moat · Moat Trend · Star Rating · Uncertainty ·
Capital Allocation · Fair Value · Price/Fair Value · Last Close · **price
as-of date** · Market Cap · Analyst · Report Date.

**Fallback if there is no index filter:** `Country = United States` +
`Exchange = NYSE, Nasdaq` + `Market cap > $5B` (the S&P 500's smallest
constituents sit around $5-7B), then intersect against Moat's ticker list
and discard non-members. Expect to over-capture 700-1000 names to recover
most of the 505.

### Stratified sample — only if export is unavailable

Stratify on **Moat's output**, which is free for all 505, not on
Morningstar's label, which is the expensive unknown. Populations are from
screen run `20260923T122501Z`:

| Stratum | Population | Capture | Why this stratum |
|---|---:|---:|---|
| **Pass** | 109 | **109** | Everything downstream runs on these; take all |
| Fail — boundary (score 33.3-42.9) | 68 | 35 | Discrimination is tested at the boundary, not in the tails |
| Fail — clear (score <30) | 164 | 20 | Confirms the screen is not rejecting obvious Wide moats |
| Unassessable — definitional (Financials 74, Real Estate 22) | 96 | 15 | Quantifies the §A14 design exclusion |
| **Unassessable — data gap (all other sectors)** | 68 | 25 | **Quantifies what extraction gaps cost in coverage** |
| | **505** | **204** | |

Weight each stratum by population / captured when reporting rates, and
fill each failure stratum proportionally by sector.

**The boundary stratum has no 45-49.9 band.** `composite_score` is a
discrete lattice (% of assessable metrics passed), so the highest failing
scores are 42.9 (n=12), 37.5 (n=28) and 33.3 (n=28). Sample across all
three; there is nothing between 42.9 and the 50.0 pass threshold.

**Prioritise the data-gap unassessables.** These 68 companies are the most
revealing in the universe. **20 of them were assessed on zero metrics** —
`COO, DTE, EBAY, GEN, GIS, HSY, LHX, VRSN` among them. Moat has no opinion
on any of them at all. If Morningstar rates a large share Wide or Narrow,
that is a clean, quantified statement of what the extraction gaps
(GitHub #1, #9, #10) cost in universe coverage — a more useful finding
than another point of moat-label agreement.

Generate the capture list:

```sql
SELECT q.ticker, c.name, c.sector,
  CASE WHEN q.passed_screen=1 THEN 'pass'
       WHEN q.metrics_assessed<6 AND c.sector IN ('Financials','Real Estate')
            THEN 'unassessable-definitional'
       WHEN q.metrics_assessed<6 THEN 'unassessable-datagap'
       WHEN q.composite_score>=33 THEN 'fail-boundary'
       ELSE 'fail-clear' END AS stratum
FROM quality_scores q JOIN companies c ON c.ticker=q.ticker
WHERE q.run_id='20260923T122501Z' ORDER BY stratum, c.sector, q.ticker;
```

Re-point `run_id` at the current screen run before generating — the
populations above shift with each re-ingest.

**Scoring:** distribution of Morningstar moat labels among the screen's
passers vs. its failures. Report a confusion matrix, not a single accuracy
figure.

## Tier C — financials / key ratios

**109 screen passers · numeric only · no analyst report needed.**

Maps column-for-column onto `fundamentals_annual`. Capture 5-10 fiscal
years per company. This tier was not in the original plan and is the
cheapest, highest-value part of the trial: it targets the failure class
behind three of the project's five worst bugs, and needs no LLM judge.

| Morningstar field | `fundamentals_annual` column | Tests |
|---|---|---|
| Revenue | `revenue` | baseline |
| Gross margin | `gross_margin` | screen metric |
| Operating income / margin | `operating_income`, `operating_margin` | screen metric |
| Net income | `net_income` | baseline |
| Diluted EPS | `eps_diluted` | baseline |
| Diluted shares outstanding | `shares_diluted` | dilution screen metric |
| Operating cash flow | `operating_cash_flow` | the Sprint 2.2 OCF-as-FCF bug class |
| **Capital expenditure** | `capex` | GitHub #9 |
| **Free cash flow** | `free_cash_flow` | screen metric + DCF input |
| **D&A** | `depreciation_amortization` | GitHub #9, GitHub #10 |
| **Total debt** | `total_debt` | GitHub #1 — the 18-ticker NULL gap |
| Cash & equivalents | `cash_and_equiv` | EV calculation |
| ROIC | `roic` | screen metric |
| ROE | `roe` | screen metric |

**Scoring:** flag any field deviating >2% from Moat's EDGAR-derived value.
Report deviation counts by field and by ticker, not a single pass rate.

**Priority cohorts** — where breakage is already suspected, and where
Morningstar can quantify an error SEC companyfacts structurally cannot
(it returns standard taxonomies only, never a filer's extension namespace):

- GitHub #9 tickers: `NVDA, CASY, EOG, FTNT, LRCX, NEE, WAT`
- GitHub #10 tickers: `NEE, AES` plus every ticker with negative D&A
- `DEBT_TAG_GAP_TICKERS` (GitHub #1): `A, ADSK, ALAB, ALNY, DDOG, DECK,
  DXCM, GRMN, LULU, MNST, NOW, PLTR, PM, RMD, ROL, SHOP, VRTX, WSM`
- All Utilities and Energy names
- Asset-light names whose low D&A may be legitimate rather than defective:
  `VICI, FOXA, FOX, CHRW, EXPD, NFLX, CBRE`. Moat cannot currently tell
  these apart from a real extraction gap. Tier C resolves it.

## Tier B — full analyst report

**24 companies · one-off audit · only if 0.3 passes.**

Score as **recall and counts, not precision or percentages.** Moat emits
free-text, citation-anchored claims, not labels — there is no Wide/Narrow/None
field and no five-source taxonomy anywhere in the pipeline. Scoring
classification accuracy would mean building an LLM extractor whose own
error rate is indistinguishable from the pipeline's.

| Morningstar field | Moat counterpart | Scored as |
|---|---|---|
| **Moat sources** (Network Effect, Switching Costs, Cost Advantage, Intangible Assets, Efficient Scale) | `ai_analysis` where `analysis_type='moat'` | Recall only |
| Moat reasoning (own summary, <=5 bullets) | same | Qualitative diff |
| Stated moat duration (20yr / 10yr) | none | Context |
| **Bear case bullets** | `analysis_type='risk'` claims, `bear_analyst_view` | **Risk recall — strongest single metric available** |
| Key risks | same | Risk recall |
| Bull case bullets | `investment_thesis`, `business_quality` claims | Thesis coverage |
| Capital allocation commentary | `management` claims, `management_score` | Qualitative diff |
| **FVE assumptions**: revenue growth, operating margin, WACC/discount rate, terminal growth, projection horizon | `valuations.key_assumptions` JSON | **Input-level diff** |
| Analyst, report date, FVE as-of date | provenance | Blocking for reproducibility |

**FVE assumptions are the field to fight hardest for.** Moat stores its DCF
inputs explicitly (`discount_rate`, `growth_rate`, `terminal_growth`,
`projection_years`, `base_owner_earnings_avg`). Diffing *inputs* says why
two fair values disagree; diffing outputs says only that they do. Given
GitHub #5 is an owner-earnings-base defect, `base_owner_earnings_avg` is
the most diagnostic number on this list.

### Cohort — 24 companies, stratified by Moat's own output

Drawn from the committee cohort, covering all 9 sectors that reach
committee. **Lock this against the post-#9 committee re-run, not the
earlier one.**

| Stratum | Tickers | Why |
|---|---|---|
| Investigate (both) | `ADBE, NVDA` | Only two; strongest positive calls |
| Reject tail | `ORCL, MRK, UNH, EIX, WDC` | Strongest negative calls — nearest available negative control |
| High-score Watch | `AAPL, GOOG, META, COST, PG` | Mega-cap; doubles as the contamination probe |
| Mid Watch | `VRSK, PAYX, LIN, ITW, ZTS` | Bulk of the distribution |
| Known-defect controls | `ABNB, CRWD` | GitHub #5 affected — if the eval does not flag these, the eval is broken |
| Thin-coverage probe | `CASY, FIX, SNA, PNR, TPR` | Low analyst attention; honest test of capability vs. recall |
| Thin-sector probe | `EOG, NEE` | Energy/Utilities, smallest n |

**Note on the contamination probe.** Keeping Morningstar out of the
generating model's context is necessary but not sufficient — moat
commentary on `AAPL`, `GOOG` and `META` is in the model's training data.
Agreement on mega-caps is partly recall, not pipeline capability. Report
the thin-coverage stratum's agreement rate separately.

## Deliberately excluded

| Field / metric | Why |
|---|---|
| Moat rating as a classification target for the AI stage | No ordinal label exists in the pipeline; scoring it needs an unvalidated extractor that confounds the result. Keep the rating in Tier A (vs. the screen), drop it as an AI-stage metric |
| Moat-source **precision** | Not answerable against free-text claims. Recall is |
| "Within one category" agreement | On a 3-point scale only Wide<->None is more than one apart, so it is ~95% by construction |
| 10/10/10 moat stratification | Ten No-Moat names yield no AI-stage output — the screen removes them before any AI stage runs. Stratify Tier A by Morningstar label across all 505; stratify Tier B by Moat's own output |
| Fair-value direction agreement as a *regression* metric | Currently re-measures GitHub #5 (`ABNB, CRWD, EIX, PEG, UBER`) and #8. Keep as a one-off observation, exclude from the repeatable suite until those close |

## Sample size

Tier A at n=505 (whole universe) or n=204 (stratified fallback) can detect
a meaningful regression; the stratified version needs its rates weighted
back to population proportions before they mean anything. Tier B at n=24
cannot — report it as counts and worked examples, never as a percentage
with an implied confidence. The original plan's n=30 split 10/10/10 would
have needed roughly a 20-point degradation before a future run could be
distinguished from noise.

## Freeze rules

- Snapshot date, source provider and analyst/report date recorded per record
- Expected answers are never edited because Moat disagrees — disagreements
  become investigations
- Price-dependent metrics are always recomputed against the benchmark-date
  price, never today's
