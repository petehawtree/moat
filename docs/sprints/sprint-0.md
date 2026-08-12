# Sprint 0 — Scaffold

**Status:** Done | **Commit:** [`571c415`](https://github.com/petehawtree/moat/commit/571c415)

## Goal

Turn the PRD into a repo that has somewhere to put every later sprint's
work, without pre-building anything that later sprints would need to
change.

## What shipped

- **[`docs/PRD_ADDENDUM.md`](../PRD_ADDENDUM.md)** — formal record of the
  scoping decisions made before writing code: Sprint 1 is US-only (S&P 500
  + NASDAQ 100, FTSE 350 deferred), sector-relative screening instead of
  flat thresholds, mandatory citation enforcement on AI output, data
  confidence tiers, and the AI-caching rule that keeps LLM cost bounded.
  Addendum wins over the PRD wherever they'd disagree.
- **Full SQLite schema** (`moat/db/schema.sql`) covering the entire
  pipeline end to end — universe through watchlist — so later sprints
  don't need migrations for things already decided. E.g. `companies`
  carries `currency`/`exchange` columns now, for the future UK sprint.
- **Stubbed pipeline stages** (`ingest/`, `screen/`, `quality/`, `ai/`,
  `valuation/`, `committee/`, `monitor/`) — each function's docstring ties
  it to its PRD section and target sprint, and raises `NotImplementedError`
  rather than pretending to work.
- `scripts/run_pipeline.py` — runs stage by stage, writes a `pipeline_runs`
  row, stops cleanly at the first unimplemented stage.
- Streamlit dashboard skeleton, a passing schema smoke test, `.env.example`,
  `.gitignore`, `requirements.txt`.

## Key stat

0 lines of code touch real data yet — that was the point. Sprint 0 is
infrastructure only, so Sprint 1 could focus entirely on data correctness.

## Next up

[Sprint 1](sprint-1.md) — US universe + fundamentals/price ingestion.
