"""Deterministic quantitative screen (PRD §4) with sector-relative bars (§A2).

Writes one `quant_scores` row per (ticker, metric): the raw value, whether it
clears the absolute floor, its percentile within its own GICS sector (when
computable), whether that clears the sector-relative bar, and the combined
`overall_pass`. Rolling that up into `quality_scores` is a separate stage —
see `moat/quality/quality_score.py`.

Design notes (full reasoning in docs/PRD_ADDENDUM.md §A9):
- Each metric's `value` is the number actually compared across sector peers
  — a *ratio*, not a raw dollar amount, so company size doesn't dominate the
  comparison (e.g. free cash flow is stored as FCF margin, not raw FCF).
- `debt` and `share_dilution` are "lower is better" metrics; everything else
  is "higher is better" — see METRIC_DIRECTION.
- A company with no GICS sector (15 NASDAQ-100-only names — see
  docs/PRD_ADDENDUM.md ingest note) or too few sector peers
  (MIN_SECTOR_PEER_GROUP) falls back to absolute-floor-only rather than
  being penalized for a comparison we can't compute.
"""
from __future__ import annotations

from moat.config import ABSOLUTE_FLOORS, MIN_SECTOR_PEER_GROUP, SECTOR_RELATIVE_TOP_TERCILE_PCT

METRICS = [
    "roic",
    "roe",
    "free_cash_flow",
    "revenue_eps_growth",
    "operating_margin",
    "debt",
    "share_dilution",
    "gross_margin",
]

METRIC_DIRECTION = {
    "roic": "higher_better",
    "roe": "higher_better",
    "free_cash_flow": "higher_better",       # FCF margin
    "revenue_eps_growth": "higher_better",   # revenue CAGR
    "operating_margin": "higher_better",
    "debt": "lower_better",                  # total_debt / FCF
    "share_dilution": "lower_better",        # diluted-share-count CAGR
    "gross_margin": "higher_better",
}


def compute_sector_percentile(ticker: str, metric: str, sector: str | None, all_values_by_sector: dict) -> float | None:
    """Percentile rank of `ticker`'s metric value within its own sector.

    `all_values_by_sector` is `{sector: {ticker: value}}` for this metric,
    pre-filtered to companies with a usable (non-null) value. Returns None
    when there's no sector to compare against, the ticker itself has no
    value, or the peer group is too small to be meaningful
    (MIN_SECTOR_PEER_GROUP) — see the module docstring on the floor-only
    fallback this triggers.

    Percentile = % of sector peers this company is at least as good as
    (ties count in the company's favour, i.e. the "weak" percentile-rank
    convention) — direction-aware via METRIC_DIRECTION so "lower is better"
    metrics like debt rank a low value highly.
    """
    if sector is None:
        return None
    peers = all_values_by_sector.get(sector, {})
    if ticker not in peers or len(peers) < MIN_SECTOR_PEER_GROUP:
        return None

    value = peers[ticker]
    if METRIC_DIRECTION[metric] == "higher_better":
        at_least_as_good = sum(1 for v in peers.values() if v <= value)
    else:
        at_least_as_good = sum(1 for v in peers.values() if v >= value)
    return 100.0 * at_least_as_good / len(peers)


_SPLIT_JUMP_RATIO = 1.4  # a single-year share-count move this large is a split, not real issuance/buybacks


def _detect_split_factors(shares_series: list[tuple[int, float | None]]) -> dict[int, float]:
    """Cumulative adjustment factor per fiscal year to express that year's
    diluted share count on the *latest* fiscal year's basis (>1.0 for years
    before a forward split, <1.0 before a reverse split, 1.0 with no split
    in between). Detected from >=40%-in-a-single-year jumps in diluted
    share count — real buybacks/issuance essentially never move that fast
    in one year, but splits do by definition.

    Not theoretical: Walmart's shares_diluted jumps 2.85B -> 8.42B between
    our stored FY2021 and FY2022 rows. That is NOT when Walmart's real
    3-for-1 split happened (Feb 2024) — verified directly against SEC's
    raw XBRL "filed" timestamps, the jump is a side effect of how 10-Ks
    restate history: a 10-K filed after a split restates its comparative
    income statement (current year + ~2 priors) to the post-split share
    basis, and our merge logic (correctly, for ordinary restatements)
    always prefers the most-recently-filed value for a given period end.
    So the jump lands wherever a later filing's comparative window first
    reaches back far enough to carry a restated figure — for Walmart,
    that's the FY2024 10-K (filed 2024-03-15, after the split) restating
    FY2022 as its oldest comparative; FY2021 is never restated by any
    later filing, so it's stuck on the pre-split basis. Confirmed the same
    ~2-year offset between the visible jump and the real split date on
    Apple (real split Aug 2020; jump lands at FY2018, restated in the
    FY2020 10-K filed 2020-10-30 — the FY2018 comparative moves from
    5,000,109,000 to exactly 20,000,435,000, a clean 4.0x) and Nvidia
    (real splits 2021 and 2024; jumps land at FY2020 and FY2023
    respectively). The detector doesn't need to know the real split date
    to work — it just needs to find wherever the basis actually changes in
    our merged series, which this does regardless of why. Left unadjusted,
    a naive CAGR across a jump like this reads as heavy dilution — inverted
    from Walmart's real story (steady buybacks for over a decade) — and
    the matching EPS collapse (eps_diluted / shares_diluted) reads as an
    earnings crash that never happened. Apply the same factor inversely to
    eps_diluted (divide rather than multiply) since EPS moves opposite to
    share count.

    Trade-off: a real, non-split issuance that happens to jump >=40% in one
    year (e.g. a large stock-funded acquisition) would be misread as a
    split too — treated as cosmetic rather than real dilution, which
    understates the effect. Rare in practice and a false-negative (missed
    dilution) rather than a false-positive, so left as-is for Sprint 2.
    """
    points = sorted((y, v) for y, v in shares_series if v is not None and v > 0)
    if len(points) < 2:
        return {y: 1.0 for y, _ in points}

    factors = {points[-1][0]: 1.0}
    cume = 1.0
    for i in range(len(points) - 1, 0, -1):
        _, v_next = points[i]
        y_prev, v_prev = points[i - 1]
        ratio = v_next / v_prev
        if ratio >= _SPLIT_JUMP_RATIO or ratio <= 1 / _SPLIT_JUMP_RATIO:
            cume *= ratio
        factors[y_prev] = cume
    return factors


def _split_adjust(series: list[tuple[int, float | None]], factors: dict[int, float], *, invert: bool) -> list[tuple[int, float | None]]:
    """Apply _detect_split_factors' per-year factors to `series` — multiply
    for a share-count series, divide (`invert=True`) for an EPS series.
    """
    out = []
    for year, value in series:
        if value is None:
            out.append((year, None))
            continue
        factor = factors.get(year, 1.0)
        out.append((year, value / factor if invert else value * factor))
    return out


def _cagr(series: list[tuple[int, float | None]]) -> float | None:
    """Compound annual growth rate from the earliest to the latest available
    non-null value in `series` (a list of (fiscal_year, value), any order).

    None when fewer than two distinct fiscal years have a value, or the
    earliest value isn't positive — CAGR off a zero/negative base is
    undefined (and misleading if forced), so we decline to compute it rather
    than return a number that looks precise but isn't (docs/PRD_ADDENDUM.md
    §A4's "don't silently guess" rule applies here too).
    """
    points = sorted((year, val) for year, val in series if val is not None)
    if len(points) < 2:
        return None
    first_year, first_val = points[0]
    last_year, last_val = points[-1]
    if last_year == first_year or first_val <= 0:
        return None
    return (last_val / first_val) ** (1 / (last_year - first_year)) - 1


def _compute_raw_metrics(history: list) -> tuple[dict[str, float | None], dict]:
    """From one ticker's `fundamentals_annual` rows (ascending fiscal_year),
    compute the 8 METRICS values plus a little extra context the absolute
    floor checks need but that isn't itself sector-comparable (e.g. the
    first/last gross margin points behind the trend check).
    """
    latest = history[-1]

    revenue_series = [(r["fiscal_year"], r["revenue"]) for r in history]
    gross_margin_series = [(r["fiscal_year"], r["gross_margin"]) for r in history]

    # Share count and EPS need split-adjusting before any cross-year
    # comparison — see _detect_split_factors.
    raw_shares_series = [(r["fiscal_year"], r["shares_diluted"]) for r in history]
    raw_eps_series = [(r["fiscal_year"], r["eps_diluted"]) for r in history]
    split_factors = _detect_split_factors(raw_shares_series)
    shares_series = _split_adjust(raw_shares_series, split_factors, invert=False)
    eps_series = _split_adjust(raw_eps_series, split_factors, invert=True)

    fcf_margin = None
    if latest["free_cash_flow"] is not None and latest["revenue"]:
        fcf_margin = latest["free_cash_flow"] / latest["revenue"]

    debt_to_fcf = None
    total_debt = latest["total_debt"]
    fcf_latest = latest["free_cash_flow"]
    if total_debt is not None:
        if total_debt <= 0:
            debt_to_fcf = 0.0  # no debt is trivially "sensible relative to cash flow"
        elif fcf_latest is not None and fcf_latest > 0:
            debt_to_fcf = total_debt / fcf_latest
        # else: debt outstanding with no positive FCF to service it — left
        # as None here; _absolute_floor_pass disqualifies this explicitly
        # rather than leaving it looking like "no data".

    def _first_last(series):
        non_null = [v for _, v in sorted(series) if v is not None]
        return (non_null[0], non_null[-1]) if non_null else (None, None)

    eps_first, eps_last = _first_last(eps_series)
    gm_first, gm_last = _first_last(gross_margin_series)

    values = {
        "roic": latest["roic"],
        "roe": latest["roe"],
        "free_cash_flow": fcf_margin,
        "revenue_eps_growth": _cagr(revenue_series),
        "operating_margin": latest["operating_margin"],
        "debt": debt_to_fcf,
        "share_dilution": _cagr(shares_series),
        "gross_margin": latest["gross_margin"],
    }
    extra = {
        "eps_first": eps_first,
        "eps_last": eps_last,
        "gross_margin_first": gm_first,
        "gross_margin_last": gm_last,
        "total_debt": total_debt,
        "free_cash_flow": fcf_latest,
    }
    return values, extra


def _absolute_floor_pass(metric: str, value: float | None, extra: dict) -> int | None:
    """0/1, or None when there isn't enough data to say either way (see A4 —
    this is surfaced as-is, not defaulted to pass or fail).
    """
    if metric in ("roic", "roe", "free_cash_flow", "operating_margin"):
        return None if value is None else int(value > ABSOLUTE_FLOORS[metric])

    if metric == "revenue_eps_growth":
        if value is None:
            return None
        eps_ok = True  # don't penalize when EPS history isn't available at all
        if extra["eps_first"] is not None and extra["eps_last"] is not None:
            eps_ok = extra["eps_last"] >= extra["eps_first"]
        return int(value > ABSOLUTE_FLOORS["revenue_eps_growth"] and eps_ok)

    if metric == "debt":
        total_debt, fcf = extra["total_debt"], extra["free_cash_flow"]
        if total_debt is not None and total_debt > 0 and not (fcf is not None and fcf > 0):
            return 0  # debt with no cash flow to service it — disqualifying regardless of sector
        return None if value is None else int(value <= ABSOLUTE_FLOORS["debt_to_fcf_max"])

    if metric == "share_dilution":
        return None if value is None else int(value <= ABSOLUTE_FLOORS["share_dilution_max_annual"])

    if metric == "gross_margin":
        gm_first, gm_last = extra["gross_margin_first"], extra["gross_margin_last"]
        if gm_first is None or gm_last is None:
            return None
        return int((gm_last - gm_first) >= -ABSOLUTE_FLOORS["gross_margin_erosion_tolerance"])

    raise ValueError(f"unknown metric: {metric}")


def _combine_pass(absolute_floor_pass: int | None, sector_relative_pass: int | None) -> int:
    """Per A2: a company passes a metric if it clears the absolute floor,
    with the sector-relative bar as an additional, stricter gate on top of
    that when a comparison is available. Falls back to floor-only when it
    isn't (missing sector / too-small peer group) rather than failing a
    company purely because we couldn't compute a comparison.

    `overall_pass` is NOT NULL in the schema, so this always returns 0/1 —
    "can't confirm" (a None floor result) counts as not passing, same as an
    explicit fail.
    """
    if not absolute_floor_pass:
        return 0
    if sector_relative_pass is None:
        return int(absolute_floor_pass)
    return int(bool(sector_relative_pass))


def run_screen(run_id: str, conn) -> None:
    """Score every active company against METRICS and write `quant_scores`.

    Rolling this up into `quality_scores.passed_screen` is a separate stage
    — see `moat.quality.quality_score.run_quality`, called next in the
    pipeline (scripts/run_pipeline.py).
    """
    companies = conn.execute("SELECT ticker, sector FROM companies WHERE is_active = 1").fetchall()
    sector_by_ticker = {r["ticker"]: r["sector"] for r in companies}

    history_by_ticker: dict[str, list] = {}
    for row in conn.execute("SELECT * FROM fundamentals_annual ORDER BY ticker, fiscal_year"):
        history_by_ticker.setdefault(row["ticker"], []).append(row)

    values_by_ticker: dict[str, dict] = {}
    extra_by_ticker: dict[str, dict] = {}
    for ticker in sector_by_ticker:
        history = history_by_ticker.get(ticker)
        if not history:
            continue  # no fundamentals at all — same 13-company gap as Sprint 1 (§A7)
        values_by_ticker[ticker], extra_by_ticker[ticker] = _compute_raw_metrics(history)

    # Sector peer groups, built once per metric so compute_sector_percentile
    # doesn't re-scan every company per call.
    peer_values: dict[str, dict[str, dict[str, float]]] = {m: {} for m in METRICS}
    for ticker, values in values_by_ticker.items():
        sector = sector_by_ticker[ticker]
        if sector is None:
            continue
        for metric in METRICS:
            v = values[metric]
            if v is not None:
                peer_values[metric].setdefault(sector, {})[ticker] = v

    rows = []
    for ticker, values in values_by_ticker.items():
        sector = sector_by_ticker[ticker]
        extra = extra_by_ticker[ticker]
        for metric in METRICS:
            value = values[metric]
            afp = _absolute_floor_pass(metric, value, extra)
            pct = compute_sector_percentile(ticker, metric, sector, peer_values[metric]) if value is not None else None
            srp = None if pct is None else int(pct >= SECTOR_RELATIVE_TOP_TERCILE_PCT)
            rows.append(
                {
                    "run_id": run_id,
                    "ticker": ticker,
                    "sector_peer_group": sector,
                    "metric": metric,
                    "value": value,
                    "absolute_floor_pass": afp,
                    "sector_percentile": pct,
                    "sector_relative_pass": srp,
                    "overall_pass": _combine_pass(afp, srp),
                }
            )

    conn.executemany(
        """
        INSERT INTO quant_scores (
            run_id, ticker, sector_peer_group, metric, value,
            absolute_floor_pass, sector_percentile, sector_relative_pass, overall_pass
        ) VALUES (
            :run_id, :ticker, :sector_peer_group, :metric, :value,
            :absolute_floor_pass, :sector_percentile, :sector_relative_pass, :overall_pass
        )
        """,
        rows,
    )
    conn.commit()
