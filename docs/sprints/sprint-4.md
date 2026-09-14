# Sprint 4 — Owner Earnings DCF + scenario valuation

**Status:** Done. Plan: [sprint-4-plan.md](sprint-4-plan.md). Decisions and
findings from execution: [`docs/PRD_ADDENDUM.md`](../PRD_ADDENDUM.md) §A16
(pre-build) and §A20 (found while running V5 against real data).

## What shipped

| # | Work | Result |
|---|---|---|
| V1 | Extend fundamentals ingest: D&A (3-tier merge), working-capital change (summary tag only, flag-don't-guess), `fetch_price_history` default window 2y → 10y | D&A populated 86/91, NWC 0/91 (flagged, not guessed) for the current pass set; verified against 8 real companies' raw XBRL, not just fixtures |
| V2 | `owner_earnings()` + `dcf_scenario()` — the real formula, two-stage DCF | 13 hand-computed tests incl. a real Coca-Cola FY2025 regression fixture that hand-verifies the NWC sign convention |
| V3 | `margin_of_safety()` sign-flip guard | Returns `None` (not a sign-flipped percentage) for non-positive `intrinsic_value_low`; verified against zero real false positives across all 91 companies' bear scenarios |
| V4 | Supporting methods: `fcf_yield`, `ev_ebit`, `annual_pe`/`pe_historical_range` | Same sign-flip-guard discipline as V3 applied to EV/EBIT and P/E; 20 new tests |
| V5 | `run_valuation()` wired into `run_pipeline.py`'s `valuation` stage, reading the latest `quality_scores` run (§A16.1's W7 pattern, applied from the start) | 90/90 companies (91 minus BKNG, excluded — see below) produce 6 valuation rows each; idempotent per `run_id`; one transaction per company |
| V6 | Dashboard: DCF bear/base/bull, margin of safety, FCF yield, EV/EBIT, P/E, per-company assumption drill-down | Verified end-to-end against the real database via `streamlit.testing.v1.AppTest` (no browser available in this environment) — zero exceptions, bear-case sign guard confirmed rendering distinctly (ABNB) vs. a normal case (AAPL) |

Full test suite: **218 passed, 1 skipped** (pre-existing, live-API opt-in),
up from 170 at the start of the sprint (48 new tests, all in
`tests/test_valuation_engine.py`).

## Growth-rate derivation — a decision the addendum didn't make

§A16.3 fixes the discount rate and terminal growth but never says where a
real company's DCF `growth_rate` comes from — V5 had to decide this to run
at all. **Decision (§A20):** the company's own trailing revenue CAGR
(capped to [-10%, +20%]), with bear/base/bull an *additive* ±4pp spread
around it — additive specifically because a multiplicative spread inverts
for a shrinking company (multiplying an already-negative rate by a bull
multiplier > 1 makes it more negative). Documented as a starting point,
same posture as `DISCOUNT_RATE`, not a final number.

## Three things found by actually running it (§A19.1's dry-run guardrail)

Per §A19.1 (dry-run the full candidate list for outliers before trusting
anything), V5's first real run against all 91 `passed_screen` companies
was read end-to-end before being called done. It surfaced three real
things — full detail in §A20:

1. **A stale-data bug, found and fixed.** NVDA's ingested `capex` only
   exists for FY2010-2012 (a pre-existing tag gap, not new). Without a
   recency filter, `run_valuation()` averaged those 15-year-old figures
   and divided by *today's* post-split share count, producing $0.71/share
   against a real ~$217 price. Fixed: the owner-earnings series is now
   restricted to the trailing 10 fiscal years, so a company whose *recent*
   data is gapped reports DCF as honestly unavailable instead of reaching
   backward. Companies with all-scenarios-unavailable went 6 → 8 as a
   direct, correct result of the fix, not a regression.
2. **A real methodology limitation, documented rather than hidden.**
   SHOP's 4-year owner-earnings average is dragged to ~$4.5M by a single
   real GAAP loss year (2022), producing a base-case DCF of $0.19/share
   (margin of safety -837%). Arguably a defensible ultra-conservative read
   of the data, arguably not — not changed without a deliberate review of
   averaging vs. median vs. weighting, which wasn't decided in advance and
   isn't decided here either. `key_assumptions` now carries the full
   trailing series (not just its average) so this is diagnosable from the
   dashboard rather than an unexplained outlier.
3. **A real, isolated data anomaly — excluded, not fixed.** BKNG's
   EV/EBIT (0.96×) and current P/E (1.29×) are implausible for a
   profitable company (next-lowest P/E in the same run: CMCSA at 4.76×,
   so not a systemic pattern). Traced to `market_cap = price ×
   shares_diluted`: SEC's `shares_diluted` (32.6M, internally consistent —
   no restatement in `share_basis_changes`) paired with a `price_history`
   series ($135-215) inconsistent with BKNG's known multi-thousand-dollar
   share price. Most likely a stock split yfinance's price series has
   already adjusted for that the latest ingested 10-K hasn't caught up to
   — not confirmed, only the inconsistency is. No existing mechanism
   catches a cross-source (SEC vs. yfinance) inconsistency like this;
   `detect_share_basis_changes` only compares SEC's own filings against
   each other. Excluded via the same `--exclude` mechanism as §A17's
   debt/REIT list, pending a real fix (a plausibility guard on implied
   P/E or market-cap/revenue).

**Also confirmed, not new:** all 91 companies' P/E-range method reports
`low_confidence = True` (83 with only 2 years of matched price history).
This is V1's own flagged-as-open backfill gap — the price-window default
change only applies to new fetches, and the incremental refresh path never
backfilled tickers that already had ~2 years stored. The method is
correctly reporting its own thin coverage (this sprint's own acceptance
bar), but it isn't yet a *useful* 5-10yr range for almost anyone.

## Definition of done

- [x] Every company in the current `quality_scores` pass set has either a
  full valuation or an explicit reason it doesn't, **per method** (not
  just per company) — a company missing D&A/capex gets a row with
  `key_assumptions.status = "unavailable"` and a stated reason, never a
  silent gap.
- [x] No company shows a positive-looking margin of safety on a negative
  intrinsic value — checked directly against all 91 companies' real
  output (9 real negative-bear-case companies, 0 guard failures), not
  just the unit tests.
- [x] `valuations` is populated (540 rows, 90 companies) and the dashboard
  renders it per PRD §9/§10, verified end-to-end (no browser in this
  environment — verified via `streamlit.testing.v1.AppTest` running the
  real script against the real database instead).
- [x] This retrospective, including what the plan got wrong (below).

## What the plan got wrong

- **The growth-rate question wasn't visible in the plan at all.** §A16.3
  reads as if discount rate and terminal growth were the only DCF
  assumptions needing a decision; `growth_rate` — arguably the assumption
  that moves a DCF result the most — wasn't named as an open question
  anywhere in sprint-4-plan.md. It surfaced only because V5 tried to
  actually call `dcf_scenario()` against real companies and the parameter
  had nowhere to come from.
- **"Full valuation... or an explicit reason it doesn't" (V1's plan
  acceptance) undersold how granular that needs to be.** The plan's
  framing reads company-level ("a company... has a full valuation or an
  explicit reason"); the real shape that ended up shipping is per-method
  (a company can have a working DCF *and* an unavailable P/E range in the
  same row set), which is the more useful shape but not what the
  acceptance language literally described.
- **V4's acceptance said "computed and persisted" — persistence is V5's
  job.** Read literally, V4's own row implies persistence was in scope for
  V4, while V5's row is titled "persist + wire the stage" as if that were
  a separate concern. Treated as the same split V2 already established
  (compute in one item, wire/persist in another) rather than re-litigated;
  worth tightening the wording if this plan format gets reused.
- **The plan's "93 as of the last screen run" was already stale before V1
  started** — the real current pass set was 91 (§A17's already-known
  debt-tag/REIT exclusions). Not a Sprint 4 defect; inherited from
  Sprint 3.1's own already-documented finding, but worth a reminder that a
  plan's snapshot numbers age the moment a screen re-runs.

## Judge review

[Pending — run after this retro is committed.]
