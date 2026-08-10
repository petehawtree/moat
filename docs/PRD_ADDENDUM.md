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

## A7. Deferred / explicitly not in Sprint 0-1

- FTSE 350 / UK data, FX normalization (→ later sprint, see A1)
- Sector-relative screening *implementation* (schema supports it now;
  logic lands in Sprint 2)
- Any alerting channel beyond a dashboard panel (email/push is a nice-to-have,
  not required for §11's success criteria)
