# Dashboard glossary

Every value shown in `moat/dashboard/app.py`, plus the inputs behind it that
aren't shown directly. Source of truth for formulas: `moat/screen/quant_screen.py`
(screen), `moat/quality/quality_score.py` (roll-up), `moat/valuation/engine.py`
(valuation), `moat/db/schema.sql` (storage). Constants below are current as of
Sprint 4 — see `moat/config.py` and `moat/valuation/engine.py` if they've moved.

## Sprint 1 — ingest coverage

| Value | Meaning |
|---|---|
| `years_of_fundamentals` | Count of `fundamentals_annual` rows for the ticker (distinct fiscal years ingested). |
| `confidence` | `high` (SEC EDGAR XBRL) / `medium` (derived w/ assumptions) / `low` (yfinance-only) — highest confidence across the ticker's rows. |
| `latest_price_date` | Most recent date in `price_history` for the ticker. |
| `universe` | `sp500` / `nasdaq100` (comma-joined if both). |
| `(no sector)` filter option | 15 NASDAQ-100-only companies have no GICS sector at all — floor-only screening for these (§A9). |

## Sprint 2 — quant screen

8 metrics, each scored `pass` / `fail` / `unavailable` (**not** a number — "couldn't
measure" ≠ "did badly", §A13). A metric passes only if it clears an absolute
floor **and** sits in the top tercile (≥66.7th percentile) of its own GICS
sector peer group; sector-relative is skipped (floor-only) with no sector or
fewer than 5 usable peers.

| Metric | What it measures | "higher/lower is better" |
|---|---|---|
| `roic` | Return on invested capital, latest fiscal year, as filed. | higher |
| `roe` | Return on equity, latest fiscal year, as filed. | higher |
| `free_cash_flow` | FCF **margin** = latest `free_cash_flow` / latest `revenue` (not a raw dollar figure). | higher |
| `revenue_eps_growth` | Revenue CAGR, first→last available fiscal year; also requires diluted EPS not to have fallen over the same window. | higher |
| `operating_margin` | Latest fiscal year, as filed. | higher |
| `debt` | `total_debt / free_cash_flow` (latest year); 0 if no debt; auto-fails if debt exists with no positive FCF to service it. | lower |
| `share_dilution` | CAGR of diluted share count, split/basis-adjusted (see below). | lower |
| `gross_margin` | Latest fiscal year; the floor check separately requires margin not to have eroded >0.5pp first→last year. | higher |

Per-metric detail columns (ticker drill-down table):

| Column | Meaning |
|---|---|
| `value` | The ratio above, as compared across sector peers. |
| `absolute_floor_pass` | 0/1 (or blank/None = not enough data to say). Floors: ROIC/ROE/FCF margin/op margin > 0%; revenue CAGR > 0% + EPS non-declining; debt/FCF ≤ 5.0x; dilution ≤ 1%/yr; gross-margin erosion ≤ 0.5pp. |
| `sector_percentile` | 0–100, this company's rank among sector peers with a usable value for this metric (ties favor the company); `null` if no sector or <5 peers. |
| `sector_relative_pass` | 1 if `sector_percentile` ≥ ~66.7. |
| `sector_peer_group` | The GICS sector used for the comparison. |
| `status` | `pass` / `fail` / `unavailable` / `not_applicable` (metric doesn't describe the sector's business model — e.g. gross margin/FCF/debt for Financials, §A14). |

Roll-up (`quality_scores`, one row per company per run):

| Value | Meaning |
|---|---|
| `composite_score` | % of *assessable* metrics passed = 100 × passed / assessed. `unavailable` metrics are excluded from both, so a company measured on fewer metrics isn't penalized — or flattered — by them. |
| `assessed` | How many of the 8 metrics had a pass/fail verdict (excludes `unavailable`/`not_applicable`). |
| `passed` | How many of `assessed` passed. |
| `passed_screen` | 1 if `composite_score` ≥ 50 **and** `assessed` ≥ 6/8 — both conditions required, so a company scored on only 1–2 metrics can't hit 100% and pass on thin coverage. |
| `notes` | Explains insufficient-coverage failures, incl. how many metrics were `not_applicable` to the sector. |

**Not shown, but feeds the above:** raw `fundamentals_annual` fields
(`revenue`, `net_income`, `operating_income`, `total_debt`, `shares_diluted`,
`eps_diluted`, `capex`, `depreciation_amortization`, `working_capital_change`,
`quality_flags`) and split/basis-change detection (`share_basis_changes`table)
— a share-count jump only adjusts history if a later 10-K actually restated
that period (§A10); rows flagged `implausible_ratio` are dropped from CAGR/trend
math and from everyone else's peer comparisons (quarantined).

## Sprint 4 — valuation

Primary method **Owner Earnings DCF** (bear/base/bull), plus 3 supporting
cross-checks. All per-share; `current_price` is the latest `price_history` close.
None of the three supporting methods carries a hardcoded pass/fail bar the way
the Sprint 2 screen does — read them comparatively (vs. `current_price`, vs.
the company's own history), not against an absolute cutoff.

| Value | Meaning | How to read it |
|---|---|---|
| `dcf_bear` / `dcf_base` / `dcf_bull` | Per-share intrinsic value from the two-stage DCF (10yr explicit + Gordon-growth terminal value), one run per scenario. | Compare each to `current_price`: `current_price` **below** a scenario's value = trading under that scenario's estimate of worth ("cheap" under that scenario); **above** = trading over it ("expensive" under that scenario). The bear→bull spread is the model's own error bar on one company, not three independent opinions — a wide spread means the scenarios disagree a lot, a narrow one means the value is fairly insensitive to growth assumptions. |
| `margin_of_safety` | `(bear intrinsic value − current_price) / bear intrinsic value`, **bear case only** (PRD §1's conservative low end, never the midpoint). Shows `"bear case: negative — not investable on this basis"` instead of a % when the bear intrinsic value is ≤ 0 (a sign-flip guard — the naive formula would make the least-safe case look safest). Shows `"no data"` if the DCF itself couldn't be computed. | **Positive %** = price sits that far *below* the bear-case value — a cushion of that size against the model being too optimistic (e.g. `+20%` = price is 20% below `dcf_bear`). **Negative %** = price sits that far *above* the bear-case value — you'd be paying more than even the conservative floor estimate, with no cushion (e.g. `−20%` = price is 20% above `dcf_bear`). There's no fixed "this % is enough" bar in the code; PRD §1 just says to prefer a larger positive number over a smaller one. |
| `fcf_yield` | Latest `free_cash_flow` / market cap (higher = cheaper — the inverse of a P/FCF multiple). `"unavailable"` if `free_cash_flow` is null (capex not ingested — never substituted with operating cash flow). | **Higher** = more free cash generated per dollar of market cap you'd be paying (cheaper). **Negative** = the company burned cash last fiscal year. No hardcoded bar, but the DCF's own hurdle rate is 9.5% (`discount_rate` below) — a yield **above ~9.5%** clears that same hurdle expressed as a cash yield; **below it** doesn't, for whatever that comparison is worth (FCF yield is a single-year snapshot, the DCF discount rate is applied to a 10yr+terminal projection — not a strict apples-to-apples check). |
| `ev_ebit` | `(market_cap + total_debt − cash_and_equiv) / operating_income`. `"unavailable"` if `total_debt` is null (a known extraction gap for ~18 companies — never assumed zero) or `operating_income` ≤ 0. | A multiple, like P/E but capital-structure-neutral. **Lower** = paying less enterprise value per dollar of annual operating earnings (cheaper); **higher** = paying more (pricier, or priced for growth the multiple alone doesn't show). No absolute threshold in this pipeline — read it against the same company's own multiple in a prior run, or against sector peers, not as a standalone "good/bad" number. |
| `pe_current` | `current_price / current eps_diluted`; `"n/a"` if EPS ≤ 0. | **Higher** = the market is paying more per dollar of this year's earnings (pricier, or growth is expected to justify it); **lower** = paying less per dollar of earnings (cheaper, or the market is skeptical of those earnings holding up). Don't read it alone — compare it to the same company's own `low`/`high` historical range in the `key_assumptions` JSON: at/below its own `low` reads cheap *vs. its own history*; at/above its own `high` reads expensive vs. its own history. |
| `pe_range_years` | How many of the company's own fiscal years have a usable (profitable) P/E point behind the historical range. | **More years** = the low/high comparison above spans more of the company's own cycle, so trust it more. **Fewer years** = the range is thin (e.g. a recently-listed company, or several loss years dropped) — the comparison is weaker evidence. |
| `pe_low_confidence` | True if `pe_range_years` < 5 — range is thin, e.g. recently-listed company. | `True` = treat the `pe_current` vs. own-range comparison with real skepticism; not enough history to call the range representative. `False` = the 5+-year bar the code uses as "enough" is met. |

**Per-company drill-down (`key_assumptions` JSON)** — the hidden inputs:

| Field | Meaning | How it moves the DCF output |
|---|---|---|
| `discount_rate` | Fixed **9.5%** for every company/scenario (not per-company CAPM/WACC) — §A16.3. | Higher discount rate → lower intrinsic value (future cash is worth less today). Fixed here, so it never explains *why* one company looks cheaper than another — only `growth_rate` and the trailing owner-earnings series vary by company. |
| `terminal_growth` | Gordon-growth rate applied after the 10yr projection window: bear 1.5%, base 2.5%, bull 3.0% (fixed, doesn't vary by company). | Higher terminal growth → higher intrinsic value, and increasingly sensitive to small changes in it as it approaches `discount_rate` (the formula divides by `discount_rate − terminal_growth`, so a narrower gap between the two amplifies the terminal value). Must stay below `discount_rate` or the calculation raises an error. |
| `growth_rate` | Company-specific: its own historical revenue CAGR, clamped to [−10%, +20%], then bear = −4pp / bull = +4pp from that base (additive, so bear ≤ base ≤ bull even for a shrinking company). 0% base if no usable revenue history. | Higher growth_rate → higher intrinsic value (bigger projected cash flows each of the 10 explicit years). This is the main per-company, per-scenario lever — it's what actually separates `dcf_bear` from `dcf_bull` for a given company, since `discount_rate` and `terminal_growth` are fixed/near-fixed. |
| `projection_years` | 10 (explicit DCF horizon, matches PRD §4's 5–10yr history preference). | Longer horizon = more years compounding at `growth_rate` before the terminal value takes over; fixed, doesn't vary here. |
| `trailing_years_used` / `trailing_owner_earnings_series` | How many, and which, of the company's trailing ≤10 fiscal years had a complete **owner earnings** figure (see below) — shown in full, not just the average, so one bad year is visible rather than hidden inside a summary number. | A short series (e.g. 2-3 years) means one unusually bad or good year has an outsized effect on `base_owner_earnings_avg` below — worth eyeballing the actual years before trusting the DCF output. |
| `base_owner_earnings_avg` | Average of that series — the year-0 figure the DCF projection compounds from. | This is the number everything else scales off. Near-zero or negative here (one real loss year dragging a short trailing average down — the SHOP finding, sprint-4.md) makes `dcf_bear`/`base`/`bull` low or negative regardless of a normal-looking `growth_rate` — check this figure and the series behind it before concluding the company is genuinely "worth less." |

**Owner earnings** (the DCF's core input, not shown as its own dashboard value):
`net_income + depreciation_amortization − capex − working_capital_change`, per
fiscal year. `None` for a year missing `net_income`/`D&A`/`capex` (no default —
a capital-intensive business with real spend must not look like it has none);
`working_capital_change` alone defaults to 0 if missing (an ingest-time
decision, flagged via `quality_flags`). Only the trailing 10 fiscal years are
eligible, to stop a stale, decades-old data point from dominating today's
per-share math (found on NVDA, see sprint-4.md).

**Other hidden inputs:** `market_cap` = `current_price × shares_diluted`;
`total_debt`, `cash_and_equiv`, `operating_income`, `eps_diluted` come straight
from the latest `fundamentals_annual` row; the P/E range pairs each fiscal
year's `eps_diluted` with the price on/before that year's `period_end_date`.
