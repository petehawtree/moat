# Sprint 6.0 re-ingest: row-level diff against the baseline

**Ingest run:** `20260929T134512Z`. It ran the ingest stage only, from the `sprint-6.0` code, against `data/moat.db`.

**Baseline:** a backup taken immediately before, `data/moat-baseline-pre-sprint6.0.db`. It holds the data behind screen run `20260923T122501Z`. The file is gitignored and local only.

**Fundamentals were re-extracted from the cached SEC companyfacts.** None of the 515 payloads was past the 90-day refresh threshold, so no new filings entered. Every difference below comes from the #1/#10/#16 code changes.

## What the ingest reported
- **Prices: 518 ok, 0 failed.** No empty yfinance responses came back from a full sequential run over the universe, so throttling did not occur (#15).
- **The new staleness check flagged two tickers:** AVB (last close 2026-08-14) and EQR (2026-08-17). yfinance reports both as "possibly delisted" with no data for a month. Neither passed screen `20260923T122501Z`. The universe list has not been refreshed, which is outside Sprint 6.0's scope. If either passes the re-screen, the valuation stage refuses to run until that is resolved.
- **Fundamentals: 506 ok, 12 failed**, the same foreign and no-annual-tag filers as before.

## Sanity check
Every field the fixes don't touch is unchanged on all 8,279 rows. That covers revenue, net income, operating income, both margins, OCF, capex, FCF, cash, shares, working-capital change, and the accession and filing dates.

The fields that do change:
- `roic` changes as a consequence of `total_debt`: it needs debt, so 1,194 rows gain a figure.
- `confidence` changes with it: a row with ROIC is marked medium confidence.

| field | rows changed | companies | passer rows | NULL→value | value→NULL | value→value |
|---|---:|---:|---:|---:|---:|---:|
| roic | 2301 | 274 | 522 | 1194 | 0 | 1107 |
| roe | 7437 | 492 | 1529 | 0 | 312 | 7125 |
| total_debt | 3382 | 357 | 614 | 1926 | 0 | 1456 |
| confidence | 1194 | 160 | 220 | 0 | 0 | 1194 |
| quality_flags | 1485 | 222 | 301 | 39 | 0 | 1446 |
| depreciation_amortization | 564 | 96 | 101 | 0 | 143 | 421 |


## Passers of `20260923T122501Z`: material moves in the latest fiscal year

Moves shown are over 10% or to/from NULL.

**Debt: 35 passers.** 10 go from NULL to a figure; 25 were understated and are corrected.

This is more than the 11 estimated before implementation. The implemented rule treats a current-portion-only figure as a fragment (the AMT refinement). As a result, CMCSA, NSC, YUM, MPC, IR, ECL and others now take their full lease-inclusive totals, flagged, instead of their current debt alone. ADBE had stored exactly 0.

- A FY2025: debt NULL→3,050M
- AAPL FY2025: roe 151.9%→171.4%
- ADBE FY2025: debt 0M→6,210M
- ADSK FY2026: debt NULL→2,500M
- ALAB FY2025: roe 16.1%→18.8%
- ALNY FY2025: roe 39.8%→73.3%
- AMGN FY2025: roe 89.1%→106.1%
- AMT FY2025: debt 3,388M→37,220M
- ANET FY2025: roe 28.4%→31.4%
- APP FY2025: roe 156.2%→206.8%
- AVGO FY2025: debt 3,152M→67,120M
- AVY FY2025: debt 523M→3,199M
- AZO FY2025: roe -73.2%→-61.2%
- CBRE FY2025: D&A 393M→729M
- CCI FY2025: debt 2,783M→24,337M
- CL FY2025: roe 3948.1%→1603.0%
- CMCSA FY2025: debt 5,958M→92,979M
- COST FY2025: roe 27.8%→30.7%
- CTVA FY2025: debt 782M→1,686M
- DDOG FY2025: debt NULL→983M; roe 2.9%→3.3%
- DPZ FY2025: debt 0M→15M
- DXCM FY2025: debt NULL→1,241M; roe 30.5%→34.5%
- ECL FY2025: debt 870M→8,125M
- FICO FY2025: roe -37.3%→-48.1%
- FIX FY2025: roe 41.8%→49.2%
- GE FY2025: debt 1,686M→20,494M
- GOOG FY2025: roe 31.8%→35.7%
- GOOGL FY2025: roe 31.8%→35.7%
- IR FY2025: debt 1M→4,785M
- IT FY2025: roe 228.0%→86.9%
- ITW FY2025: debt 7,682M→8,969M
- KMB FY2025: debt 694M→6,886M; roe 134.6%→172.6%
- KVUE FY2025: debt 750M→8,524M
- LII FY2025: debt 18M→1,162M
- LIN FY2025: debt 22,479M→26,989M
- LRCX FY2026: roe 58.3%→65.1%
- MELI FY2025: debt NULL→9,193M; roe 29.6%→36.0%; D&A 9M→818M
- MNST FY2025: roe 23.1%→26.8%
- MO FY2025: roe -198.4%→-242.1%
- MPC FY2025: debt 2,371M→32,876M
- NEE FY2025: D&A 65M→6,580M
- NOW FY2025: debt NULL→1,491M; roe 13.5%→15.5%
- NSC FY2025: debt 607M→17,087M
- NTAP FY2026: roe 94.4%→106.7%
- NVDA FY2026: roe 76.3%→101.5%
- ORCL FY2026: debt 7,199M→129,541M; roe 40.2%→54.3%
- ORLY FY2025: roe -332.5%→-237.8%
- PANW FY2025: roe 14.5%→17.5%
- PLTR FY2025: roe 22.0%→26.2%
- PM FY2025: debt NULL→48,667M
- PODD FY2025: debt 18M→949M; roe 16.3%→18.1%
- PTC FY2025: debt 25M→1,197M
- RMD FY2026: debt NULL→659M
- ROL FY2025: debt NULL→486M
- SNDK FY2026: roe 72.7%→91.6%
- STX FY2026: roe 146.9%→371.5%
- SYY FY2026: debt NULL→13,516M; roe 65.9%→78.2%
- TDG FY2025: debt 124M→29,291M; roe -21.4%→-26.0%
- TPR FY2026: roe 220.7%→197.1%
- UBER FY2025: roe 37.2%→41.4%
- VRSK FY2025: debt 1,509M→4,774M; roe 293.9%→444.0%
- WAT FY2025: debt 947M→1,410M; roe 25.1%→29.3%
- WDC FY2026: roe 106.3%→133.0%
- YUM FY2025: debt 38M→11,976M
- ZTS FY2025: roe 80.2%→66.0%


## price_history
- baseline: 259999 rows; latest-date distribution [('2026-09-21', 91), ('2026-08-11', 366), ('2026-08-10', 61)]
- now: 275058 rows; latest-date distribution [('2026-09-29', 516), ('2026-08-17', 1), ('2026-08-14', 1)]
- passers with latest close before 2026-09-24: 0
