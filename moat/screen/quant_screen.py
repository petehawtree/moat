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


_SPLIT_JUMP_RATIO = 1.4  # a single-year share-count move this large is a basis change, not real issuance


def _detect_split_factors(
    shares_series: list[tuple[int, float | None]],
    corroborated_years: set[int] | None = None,
) -> dict[int, float]:
    """Cumulative adjustment factor per fiscal year to express that year's
    diluted share count on the *latest* fiscal year's basis (>1.0 for years
    before a forward split, <1.0 before a reverse split, 1.0 with no basis
    change in between).

    A jump is only treated as a basis change when it is **corroborated by an
    actual restatement in the filings** — `corroborated_years` holds the
    fiscal years for which `share_basis_changes` records that a later 10-K
    restated the same period's share count (see
    `moat.ingest.fundamentals_edgar.detect_share_basis_changes`).

    Sprint 2 inferred a split from jump size alone and fired on 37.4% of the
    universe, silently erasing real dilution at TKO (WWE/UFC merger), CRWV
    and ALAB (IPOs), CHTR and KHC (mergers) — none of which restate anything,
    because their share counts genuinely grew. Requiring the restatement is
    what separates "the filer rebased this period" from "this company issued
    a lot of stock" (docs/PRD_ADDENDUM.md §A10).

    Passing `corroborated_years=None` disables the gate and restores the old
    jump-only behaviour — used only to reproduce the Sprint 2 numbers for
    regression comparison, never in the pipeline.
    """
    points = sorted((y, v) for y, v in shares_series if v is not None and v > 0)
    if len(points) < 2:
        return {y: 1.0 for y, _ in points}

    factors = {points[-1][0]: 1.0}
    cume = 1.0
    for i in range(len(points) - 1, 0, -1):
        year_next, v_next = points[i]
        y_prev, v_prev = points[i - 1]
        ratio = v_next / v_prev
        jumped = ratio >= _SPLIT_JUMP_RATIO or ratio <= 1 / _SPLIT_JUMP_RATIO
        corroborated = corroborated_years is None or year_next in corroborated_years
        if jumped and corroborated:
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


def _usable_share_row(row) -> bool:
    """Exclude share counts the ingest validation found to be in the wrong unit.

    Only `share_count_unit_outlier` disqualifies a share count (Southwest
    filing `768` diluted shares, ConocoPhillips filing a decade in
    thousands). `eps_shares_ni_mismatch` is deliberately NOT excluded here:
    it flags a structural gap between net income and the EPS numerator
    (noncontrolling interests, preferred dividends), which says nothing about
    the share count — and dropping those rows would erase exactly the real
    merger dilution at TKO that this sprint exists to restore (§A10).

    Sprint 2 rescaled bad units into plausible-looking numbers; dropping them
    means the metric is computed from data we can stand behind, or reported
    as unavailable (docs/PRD_ADDENDUM.md §A4).
    """
    flags = row["quality_flags"] if "quality_flags" in row.keys() else None
    return not (flags and "share_count_unit_outlier" in flags)


def _compute_raw_metrics(history: list, corroborated_years: set[int] | None = None) -> tuple[dict[str, float | None], dict]:
    """From one ticker's `fundamentals_annual` rows (ascending fiscal_year),
    compute the 8 METRICS values plus a little extra context the absolute
    floor checks need but that isn't itself sector-comparable (e.g. the
    first/last gross margin points behind the trend check).

    `corroborated_years` gates the split adjustment on filing evidence — see
    _detect_split_factors.
    """
    latest = history[-1]

    revenue_series = [(r["fiscal_year"], r["revenue"]) for r in history]
    gross_margin_series = [(r["fiscal_year"], r["gross_margin"]) for r in history]

    # Share count and EPS need basis-adjusting before any cross-year
    # comparison — see _detect_split_factors. Rows whose share/EPS figures
    # failed ingest validation are dropped rather than adjusted.
    share_history = [r for r in history if _usable_share_row(r)]
    raw_shares_series = [(r["fiscal_year"], r["shares_diluted"]) for r in share_history]
    raw_eps_series = [(r["fiscal_year"], r["eps_diluted"]) for r in share_history]
    split_factors = _detect_split_factors(raw_shares_series, corroborated_years)
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


STATUS_PASS, STATUS_FAIL, STATUS_UNAVAILABLE = "pass", "fail", "unavailable"


def _metric_status(value: float | None, absolute_floor_pass: int | None, overall_pass: int) -> str:
    """'pass' | 'fail' | 'unavailable' (docs/PRD_ADDENDUM.md §A13).

    "We couldn't measure this" is not "this company did badly." Sprint 2
    collapsed both into overall_pass = 0 and then divided by all eight
    metrics, so 257 companies were scored as failing ROIC when the truth was
    that ROIC wasn't computable from their filings. That produces false
    rejects and, worse, makes them indistinguishable from real ones.
    """
    if value is None or absolute_floor_pass is None:
        return STATUS_UNAVAILABLE
    return STATUS_PASS if overall_pass else STATUS_FAIL


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

    # Fiscal years whose share count a later filing actually restated — the
    # evidence that a jump is a basis change rather than real issuance (§A10).
    # Only genuine splits corroborate an adjustment. A 'unit_correction' is a
    # data error, not a share-basis event: rescaling on it assumes one clean
    # switchover, but the bad unit can occupy a *middle* segment of the
    # history (ConocoPhillips filed FY2010-2019 in thousands, actual units
    # either side), so rescaling everything before the boundary corrupts the
    # years that were already right. Those rows are excluded instead — §A10's
    # "reject at ingest", not "silently normalise".
    corroborated: dict[str, set[int]] = {}
    for row in conn.execute(
        "SELECT ticker, period_end_date FROM share_basis_changes WHERE change_type = 'split'"
    ):
        try:
            year = int(row["period_end_date"][:4])
        except (TypeError, ValueError):
            continue
        corroborated.setdefault(row["ticker"], set()).add(year)

    values_by_ticker: dict[str, dict] = {}
    extra_by_ticker: dict[str, dict] = {}
    for ticker in sector_by_ticker:
        history = history_by_ticker.get(ticker)
        if not history:
            continue  # no fundamentals at all — same 13-company gap as Sprint 1 (§A7)
        values_by_ticker[ticker], extra_by_ticker[ticker] = _compute_raw_metrics(
            history, corroborated.get(ticker, set())
        )

    # Sector peer groups, built once per metric so compute_sector_percentile
    # doesn't re-scan every company per call.
    # Companies whose latest row failed plausibility validation are excluded
    # from *everyone's* peer group (§A13). A percentile is relative, so one
    # nonsense value doesn't just mis-score its own company — Camden Property
    # Trust's 6,375% "FCF margin" shifted all 30 Real Estate peers and pushed
    # one of them across the top-tercile bar. Quarantining is what keeps a bad
    # row's blast radius to itself.
    quarantined = {
        ticker for ticker, history in history_by_ticker.items()
        if history and (history[-1]["quality_flags"] or "") and "implausible_ratio" in (history[-1]["quality_flags"] or "")
    }

    peer_values: dict[str, dict[str, dict[str, float]]] = {m: {} for m in METRICS}
    for ticker, values in values_by_ticker.items():
        sector = sector_by_ticker[ticker]
        if sector is None or ticker in quarantined:
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
            overall = _combine_pass(afp, srp)
            status = _metric_status(value, afp, overall)
            if ticker in quarantined:
                # Its own values are not trustworthy either — report them as
                # unmeasurable rather than scoring a number we don't believe.
                status, overall = STATUS_UNAVAILABLE, 0
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
                    "overall_pass": overall,
                    "status": status,
                }
            )

    conn.executemany(
        """
        INSERT INTO quant_scores (
            run_id, ticker, sector_peer_group, metric, value,
            absolute_floor_pass, sector_percentile, sector_relative_pass, overall_pass, status
        ) VALUES (
            :run_id, :ticker, :sector_peer_group, :metric, :value,
            :absolute_floor_pass, :sector_percentile, :sector_relative_pass, :overall_pass, :status
        )
        """,
        rows,
    )
    conn.commit()
