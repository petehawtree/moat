# Project Moat dashboard — UX discovery and prototype brief

Prepared for redesign review, 9 October 2026.

## Executive recommendation

Use **Company Workbench** as the core product model, with the **Decision Desk**
as its landing page and **Evidence Room** as a first-class view inside each
company. Keep **Monday Storyboard** as a presentation mode rather than the
everyday interface. The Opportunity Map is useful for discovery, but it should
not become the default because a scatter plot makes relative position look more
precise than the unvalidated committee thresholds warrant.

The governing information hierarchy should be:

1. **Decision:** status, score, price versus bear/base/bull value, confidence.
2. **Reason:** investment thesis, scorecard, strongest positive and negative.
3. **Change:** what to monitor and what would alter the conclusion.
4. **Proof:** claim → quote → filing, visible without leaving the workflow.
5. **Method:** assumptions, run IDs, definitions, and known limitations on demand.

## Confirmed Monday demo brief

- **Audience:** a potential employer evaluating the ability to build an
  institutional investment platform.
- **Desired memory:** the quality of the overall product build—not a single
  stock result.
- **Status treatment:** keep Investigate / Watch / Reject visible, but label
  them explicitly as **provisional** until Sprint 6 validates the thresholds.

The recommended demo path is therefore **Monday Storyboard → Company
Workbench → Evidence Room**. Start with the platform and its control model,
show one real decision as proof that the system works end-to-end, and finish by
opening the claim-level evidence. Do not lead with the opportunity ranking: for
this audience, the stronger signal is the judgment behind the workflow design.

The gallery uses the most complete non-failed committee batch, with recency as
the tie-breaker. This matters for the Sprint 6 paired-worker run: the later
`20260930T133537Z` batch is only the 49-verdict reverse-worker shard, while
`20260930T133528Z` is the authoritative 117-verdict candidate containing the
copied results from both workers (2 Investigate, 88 Watch, 27 Reject).

## What the existing Moat product uniquely has

The current dashboard already contains the ingredients most research products
struggle to join up:

- a 518-company S&P 500 / NASDAQ-100 universe;
- an eight-factor, sector-relative quality screen with explicit unavailable
  states;
- owner-earnings DCF scenarios and a bear-case margin of safety;
- three committee perspectives and a consolidated status;
- durable filing citations down to accession, section, and quoted text;
- visible data confidence, model assumptions, provenance, and known defects.

The weakness is not missing data. It is progressive disclosure. The current UI
presents the pipeline in implementation order and relies heavily on full-width
tables, long captions, and stacked prose. A new user has to learn the system
before they can read the decision.

## Market patterns worth borrowing

### 1. One-page company summary, then depth

Koyfin describes its financial-analysis summary as putting the key metrics on
one page, with graphical or column views and a customizable top line. TIKR puts
screening, company financials, transcripts, and valuation modelling in one
workflow. The common pattern is a stable company header plus task-oriented
subviews—not a long page containing the whole product.

Moat translation: persistent ticker context; a compact decision strip; tabs for
Decision brief, Quality, Valuation, Risks, and Sources.

Sources: [Koyfin equity research](https://www.koyfin.com/for-investors/equity-research/),
[Koyfin functionality](https://www.koyfin.com/help/topic/functionality/),
[TIKR](https://www.tikr.com/).

### 2. Screening is a queue, not the conclusion

Koyfin exposes large screens, watchlists, configurable columns, percentile
ranks, and reusable views. Streamlit's dataframe supports row selection,
progress, chart, and link columns, so a credible ranked queue is possible
without rebuilding a grid component.

Moat translation: the landing view should be the research queue, with Quality,
Committee, Bear MoS, Confidence, and Call visible. Selecting a row should open
the company workbench. Avoid treating ranking as a buy list.

Sources: [Koyfin functionality](https://www.koyfin.com/help/topic/functionality/),
[Streamlit dataframe design](https://docs.streamlit.io/develop/concepts/design/dataframes),
[Streamlit row selection](https://docs.streamlit.io/develop/tutorials/elements/dataframe-row-selections).

### 3. Moat, fair value, and uncertainty belong together

Morningstar explicitly links competitive-advantage analysis to future cash
flows and fair value, while uncertainty determines the margin of safety needed.
That is a strong conceptual fit for Moat's data, even though Project Moat uses a
different model and must not mimic Morningstar's ratings.

Moat translation: never show the moat conclusion as a free-standing badge.
Place it alongside price versus bear/base/bull value, risk, and data confidence.

Sources: [Morningstar methodology documents](https://www.morningstar.com/business/insights/research/methodology-documents),
[Morningstar economic-moat methodology](https://www.morningstar.com/en-gb/business/insights/blog/equity-economic-moat-ratings).

### 4. Evidence should remain adjacent to the claim

BamSEC centers workflows on search, link-to-text, filing comparison, watched
companies, and fast return to original documents. AlphaSense describes
cited, decision-ready output and an integrated research workflow. Both support
the same usability principle: provenance works best in context, not in a
separate compliance appendix.

Moat translation: every important claim gets a nearby source affordance. The
Evidence Room then provides a ledger for audit, source coverage, and challenge.
Keep the existing warning that citation resolution does not prove entailment.

Sources: [BamSEC features](https://www.bamsec.com/features),
[BamSEC getting started](https://help.bamsec.com/hc/en-us/articles/45645740053011-Getting-Started-Guide),
[AlphaSense](https://www.alpha-sense.com/).

### 5. Monitoring closes the research loop

Watchlists, alerts, filing feeds, and news monitoring recur across Koyfin, TIKR,
BamSEC, and AlphaSense. The useful mental model is not “publish a static brief”
but “hold a view until defined evidence changes.”

Moat translation: rename “Key things to monitor” to “What would change the
view?” and display it as a small, actionable checklist. A future watchlist can
attach events to those specific conditions.

### 6. Streamlit can support the hierarchy—with restraint

Streamlit provides sidebars for persistent controls; columns for compact
comparisons; tabs and expanders for secondary detail; popovers for settings;
and interactive dataframes for selection. Tabs compute all their content by
default, so expensive evidence and assumption queries should eventually be
loaded only after user intent or cached centrally.

Source: [Streamlit layouts and containers](https://docs.streamlit.io/develop/concepts/design/layouts-and-containers).

## Five prototype hypotheses

| Concept | Primary user question | Best use | Main risk |
|---|---|---|---|
| Decision Desk | What deserves attention? | Daily landing page / triage | Ranking can imply false precision |
| Company Workbench | Should I spend more time on this company? | Core analysis workflow | Dense if tabs are not disciplined |
| Opportunity Map | Where are the outliers? | Idea generation / sector review | Scatter plots overstate comparability |
| Evidence Room | Can I defend this claim? | Diligence / audit / trust | Too slow as the default landing view |
| Monday Storyboard | Can I explain the product in five minutes? | Live demo / portfolio narrative | Not efficient for repeat analysis |

Open the gallery with:

```bash
streamlit run moat/dashboard/prototypes.py
```

## Validation plan

For the Monday review, ask each observer to complete three tasks without
guidance:

1. Find the highest-priority company and explain why it is not an automatic buy.
2. Decide whether its valuation offers a bear-case margin of safety.
3. Open one important claim and identify the source language supporting it.

Record task success, time, and one confidence score (“How sure are you that you
understand why the system reached this call?”). Then ask which concept they
would choose for (a) daily use, (b) deep research, and (c) a client/internal
demo. The winning redesign should optimize task performance, not visual taste.

## Remaining decisions to validate

- Does the user need a dark terminal aesthetic, a light editorial research
  aesthetic, or a presentation mode offering both?
- Which actions are genuinely needed next: watchlist, notes, export, compare,
  or rerun? Adding inactive action chrome would make the prototype look more
  mature while making the product less honest.
