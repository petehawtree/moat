# Project Moat

AI-assisted equity research tool built on Buffett/Graham fundamental
investing principles. Personal research tool — not investment advice, and
not a substitute for the human "investment committee" (you).

Full spec: `docs/Project_Moat_PRD_MVP.pdf` (original PRD) +
[`docs/PRD_ADDENDUM.md`](docs/PRD_ADDENDUM.md) (scoping decisions made
during Sprint 0 — read this first, it overrides the PRD where they differ).

What actually happened each sprint — results, bugs found, scope calls — is
in [`docs/sprints/`](docs/sprints/). Longer-form lessons are in
[`docs/writeups/`](docs/writeups/): [Sprint 0-1 data bugs](docs/writeups/three-bugs-in-structured-financial-data.md)
and [what two code reviews found](docs/writeups/what-two-code-reviews-found.md).

## Architecture

<img src="docs/img/architecture.svg" alt="Project Moat pipeline: three free data sources feed an ingestion stage that writes into one shared SQLite store; five stages read and write that same store in sequence, with a citation-enforcement rule at the AI Analysis stage; the output reaches a human who makes the final call, while a separate watchlist monitor loops back to re-trigger ingestion on its own." width="100%">

Green = shipped (Sprint 0–1). Dashed = planned (Sprint 2–6, still stubs).
Gold = the one thing no sprint replaces.

## Status

**Sprint 2.2 — data integrity. Done.**
- Acted on a second external review that found the system not
  investment-ready. Fixed: operating cash flow being scored as free cash
  flow (155 companies), REIT revenue missing ASC 842 lease income (Camden
  read $13m against a real ~$1.6bn), and unavailable data being scored as
  failure (773 nulls).
- Metrics now report **pass / fail / unavailable**, and the score is the
  % of *assessable* metrics — a company we couldn't measure is no longer
  indistinguishable from one that did badly.
- **93/505** pass, down from 111: 38 were passing on substituted cash flow,
  and financials are now excluded as unscreenable rather than mis-ranked on
  metrics that don't describe a bank (§A14).
- 31 tests passing. See [sprint-2-2.md](docs/sprints/sprint-2-2.md).

**Sprint 2.1 — ingest data integrity + filing provenance. Done.**
- Stock-split detection now keyed on **filing restatement** rather than
  inferred from a jump — fixes real dilution being erased at IPOs and
  mergers (TKO 6.2% → 53.1%/yr, CRWV 6.6% → 50.7%/yr).
- Ingest validation catches share counts filed in the wrong unit.
- Every fundamentals row is traceable to a filing; `filings` table
  populated (0 → 7,497 rows), raw SEC payloads cached (re-ingest 353s → 11s).
- `python scripts/verify.py WMT shares_diluted` shows any stored number
  beside every filing that reported it.
- 25 tests passing. See [sprint-2-1.md](docs/sprints/sprint-2-1.md).

**Sprint 2 — sector-relative quant screen + ranked dashboard. Done.**
- Screen: all 8 PRD §4 metrics scored against each company's own GICS
  sector, not a flat bar — see [`docs/PRD_ADDENDUM.md`](docs/PRD_ADDENDUM.md) §A2/§A9.
- Results: 111/505 passed at the time (93 after the Sprint 2.1/2.2 data
  fixes). Top of the ranked
  table is recognizable moat businesses (Adobe, Mastercard, Meta,
  Moody's, MSCI, Nvidia, Verisk) — see
  [`docs/sprints/sprint-2.md`](docs/sprints/sprint-2.md).
- Dashboard: ranked table + per-company metric breakdown showing *why* a
  company passed or failed each metric.
- 16 tests passing.
- A `share_dilution` defect found by external review has been fixed in
  Sprint 2.1 below.

**Sprint 1 — data foundation and company universe. Done.**
- Universe: 518 unique US companies (S&P 500 + NASDAQ 100, deduplicated).
- Fundamentals: 505/518 (97.5%) via SEC EDGAR XBRL. The 13 gaps are
  explained, not bugs — see [`docs/PRD_ADDENDUM.md`](docs/PRD_ADDENDUM.md) §A7.
- Prices: 518/518 (100%) via yfinance.

Run `python scripts/run_pipeline.py --init-db` (add `--init-only` to just create the schema) then
`python scripts/run_pipeline.py --from-stage screen` to reproduce.

Sprints 3-6 (ai/valuation/committee/monitor) are still documented stubs —
see the sprint table below.

## Scope for now

- **Universe:** S&P 500 + NASDAQ 100 only (US). FTSE 350 is deferred —
  see addendum §A1.
- **Data:** free/near-free sources only — SEC EDGAR (structured, high
  confidence) + yfinance (prices, supplementary). See addendum §A4 for
  how confidence is tracked per record.
- **Screening:** sector-relative, not one flat threshold for every company
  — see addendum §A2.
- **Stack:** Python, SQLite (single local file, no infra), Streamlit
  dashboard, Claude API for the qualitative/valuation/committee stages
  (Sprint 3+).

## Sprint plan

| Sprint | Scope | Summary |
|---|---|---|
| 0 | Repo scaffold, schema, pipeline skeleton — **done** | [sprint-0.md](docs/sprints/sprint-0.md) |
| 1 | Universe + price/fundamentals ingestion (US only) — **done** | [sprint-1.md](docs/sprints/sprint-1.md) |
| 2 | Sector-relative quant screen + ranked dashboard — **done** (one metric defective, see 2.1) | [sprint-2.md](docs/sprints/sprint-2.md) |
| 2.1 | Ingest data integrity + filing provenance — **done** | [sprint-2-1.md](docs/sprints/sprint-2-1.md) |
| 2.2 | Data integrity: FCF, REIT revenue, FAIL vs UNAVAILABLE — **done** | [sprint-2-2.md](docs/sprints/sprint-2-2.md) |
| 3 | AI business/moat/management/risk analysis (citation-enforced) | |
| 4 | Owner Earnings DCF + supporting valuation methods | |
| 5 | Investment Committee + one-page Investment Brief | |
| 6 | Watchlist monitoring | |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in MOAT_CONTACT_EMAIL (required by SEC EDGAR) and ANTHROPIC_API_KEY
python scripts/run_pipeline.py --init-db --init-only
pytest
```

## Repo layout

```
moat/
  ingest/       # universe, price, fundamentals fetchers
  screen/       # deterministic quant screen (sector-relative)
  quality/      # pre-AI deterministic quality score
  ai/           # Claude-based qualitative analysis (citation-enforced)
  valuation/    # Owner Earnings DCF + supporting methods
  committee/    # 3-persona consolidation + final weighted score
  monitor/      # watchlist diffing and triggers
  db/           # SQLite schema + connection helper
  dashboard/    # Streamlit app
scripts/        # run_pipeline.py and future one-off scripts
docs/           # PRD + addendum
tests/
```

## Running the dashboard

```bash
streamlit run moat/dashboard/app.py
```
