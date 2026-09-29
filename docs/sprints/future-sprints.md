# Future sprints

Work that is scoped in outline but not yet planned or scheduled. Each item
gets a proper `sprint-N-plan.md` when it is picked up. This file only
records what it is, why it was deferred, and what it waits on.

## MCP as the committee's tool/context layer

Replace the hand-assembled prompt context block each committee persona
receives with MCP-served tools and resources, so agents fetch filings,
fundamentals and valuations themselves rather than being handed a fixed
bundle.

- **Moved out of:** Sprint 6 (§A27), after §A26 first scoped it there.
- **Waits on:** Sprint 6's eval harness. Without it there is no way to say
  whether the change improved verdicts or only changed them.
- **Plan must answer:** does tool access change what the personas cite
  (§A3's citation guarantee has to hold for tool-fetched context too), and
  what it does to cost and the per-filing analysis cache.

## Alternative multi-agent workflows

Try other shapes against the committee's fixed sequential-and-independent
design: **debate** (personas see and respond to each other before a
verdict) and **supervisor-worker** (a fourth agent adjudicates
disagreement).

- **Moved out of:** Sprint 6 (§A27).
- **Waits on:** the harness, plus Sprint 6's persona-effectiveness result,
  which is the baseline these designs have to beat.
- **Plan must answer:** how many alternatives to run, how to hold inputs
  fixed so only the workflow varies, and what LLM spend a comparison run
  justifies.

## Trailing-twelve-month (TTM) fundamentals

Populate `fundamentals_quarterly` from 10-Q filings and derive TTM figures
alongside the existing fiscal-year series in `fundamentals_annual`.

**Why TTM matters for fundamental analysis**

- **Current view, not a stale one.** A fiscal-year figure can be up to 15
  months old by the time the next 10-K lands. TTM moves forward every
  quarter, so a company's economics are judged on its most recent four
  quarters, not on whenever its fiscal year happened to end.
- **Like-for-like across companies.** Fiscal years end in different months,
  so ranking companies on fiscal-year figures compares different periods. A
  sector-relative screen is exactly where that matters. TTM puts every
  company on the same trailing window.
- **Valuation ratios match the price.** P/E, EV/EBIT and FCF yield divide a
  price from today by an earnings figure. TTM keeps both sides from the same
  period, which is how practitioners and data vendors quote these ratios.
- **Turning points show up sooner.** Margin compression, a free-cash-flow
  swing or a debt build becomes visible within a quarter rather than
  waiting for the annual report. That matters for a bear case and for any
  future watchlist trigger.
- **External comparability.** Benchmarks and screeners report TTM. Without it,
  absolute comparisons measure timing, not accuracy. The Morningstar Tier C
  check was valid on only 29 of 516 companies because of this
  (`docs/evals/benchmark-prep-outcome.md`).

**What it does not replace.** Buffett/Graham analysis deliberately looks
through the cycle: multi-year averages of earnings and owner earnings, and
the 10-year DCF window. TTM belongs next to that as the *current* reading,
not in place of the *normalised* one. Any screen metric that moves to TTM
should be a deliberate choice per metric.

**Why it is a separate sprint.** 10-Qs report year-to-date figures, so
quarters have to be derived by subtraction. The 10-K has no Q4 filing, so Q4
is the full year minus nine months. Flow items (revenue, cash flow) sum
across quarters, while balance-sheet items (debt, cash, equity) are
point-in-time. Restatements, split adjustments (§A9) and the tag-merging
rules would all need extending to quarterly periods. That is a new ingest
path plus a schema-backed series, not a column change.

- **Deferred by:** §A27 (Sprint 6.0 Phase 0 decision).
- **Waits on:** nothing technical. Sprint 6's eval results should decide
  which metrics benefit enough to justify moving.
- **Plan must answer:** which screen metrics and valuation ratios switch to
  TTM and which stay fiscal-year or multi-year, how a company whose latest
  10-Q is missing or late is flagged (§A13 — unavailable is not zero), and
  whether the first cut is ingest-only (store quarters, derive TTM) before
  any scoring changes.

## Watchlist monitoring

`watchlist_events` and live re-run triggers on earnings, price and
management changes. `moat/monitor/watchlist.py` remains a documented stub.

- **Deferred by:** §A26.
- **Waits on:** validated `assign_status()` thresholds from Sprint 6, since
  monitoring re-surfaces that status.
