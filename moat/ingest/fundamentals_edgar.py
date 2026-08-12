"""US fundamentals via SEC EDGAR's structured XBRL companyfacts API.

Highest-confidence free data source in the project (docs/PRD_ADDENDUM.md §A4:
confidence='high' when the primary XBRL tag is present directly). Requires a
descriptive User-Agent per SEC's fair-access policy — see moat/config.py.

Docs: https://www.sec.gov/edgar/sec-api-documentation
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from moat.config import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, DATA_DIR, SEC_USER_AGENT

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKER_CIK_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"
TICKER_CIK_CACHE_PATH = DATA_DIR / "sec_company_tickers.json"

# SEC asks for a fair-use rate; this keeps us comfortably under 10 req/s
# including the request itself.
_REQUEST_DELAY_SECONDS = 0.15

# Owner-earnings/ROIC is only ever an estimate — this assumption is the one
# place that's baked in rather than pulled from filings, which is why ROIC
# is always flagged confidence='medium' (see extract_annual_fundamentals).
ASSUMED_TAX_RATE = 0.21


class TransientSecError(Exception):
    """Raised on retryable SEC API failures (429/5xx/network)."""


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception_type(TransientSecError),
)
def _get_json(url: str) -> dict:
    try:
        resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    except requests.RequestException as exc:
        raise TransientSecError(str(exc)) from exc
    finally:
        time.sleep(_REQUEST_DELAY_SECONDS)

    if resp.status_code == 404:
        return {}
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientSecError(f"HTTP {resp.status_code} from {url}")
    resp.raise_for_status()
    return resp.json()


def _load_ticker_cik_map(force_refresh: bool = False) -> dict[str, str]:
    if not force_refresh and TICKER_CIK_CACHE_PATH.exists():
        raw = json.loads(TICKER_CIK_CACHE_PATH.read_text())
    else:
        raw = _get_json(TICKER_CIK_LOOKUP_URL)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TICKER_CIK_CACHE_PATH.write_text(json.dumps(raw))

    return {entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in raw.values()}


_TICKER_CIK_CACHE: dict[str, str] | None = None

_BROWSE_EDGAR_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
_CIK_FROM_ATOM_RE = re.compile(r"<cik>(\d+)</cik>")


def _lookup_cik_via_browse_edgar(ticker: str) -> str | None:
    """Fallback for tickers absent from the company_tickers.json bulk file.

    That file is SEC's documented canonical ticker->CIK mapping, but in
    practice it's occasionally missing large, unambiguously-registered
    companies (found during Sprint 1 validation: AEP/American Electric
    Power is absent from it entirely, despite having filed 10-Ks for
    decades). EDGAR's browse-edgar company search accepts a ticker directly
    in the CIK param and resolves it, so it's used as a second attempt
    rather than treating a bulk-file miss as "not SEC-registered."
    """
    try:
        resp = requests.get(
            _BROWSE_EDGAR_URL,
            params={"action": "getcompany", "CIK": ticker, "type": "10-K", "owner": "include", "count": "1", "output": "atom"},
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=30,
        )
    except requests.RequestException:
        return None
    finally:
        time.sleep(_REQUEST_DELAY_SECONDS)

    if resp.status_code != 200:
        return None
    match = _CIK_FROM_ATOM_RE.search(resp.text)
    return match.group(1).zfill(10) if match else None


def lookup_cik(ticker: str) -> str | None:
    """Resolve a ticker to its 10-digit zero-padded CIK, via a cached lookup
    with a live fallback (see _lookup_cik_via_browse_edgar) for names the
    bulk file misses.
    """
    global _TICKER_CIK_CACHE
    if _TICKER_CIK_CACHE is None:
        _TICKER_CIK_CACHE = _load_ticker_cik_map()

    ticker = ticker.upper()
    cik = _TICKER_CIK_CACHE.get(ticker)
    if cik is None:
        cik = _lookup_cik_via_browse_edgar(ticker)
        if cik:
            _TICKER_CIK_CACHE[ticker] = cik  # cache for the rest of this run
    return cik


def fetch_company_facts(cik: str) -> dict:
    """Fetch the full XBRL company facts payload for one company. {} if none filed."""
    return _get_json(COMPANY_FACTS_URL.format(cik=cik))


# Metric -> (candidate XBRL tags in priority order, unit key, 'duration'|'instant')
TAG_CANDIDATES: dict[str, tuple[list[str], str, str]] = {
    "revenue": (
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",  # e.g. CrowdStrike, Kraft Heinz, APA, Alexandria RE
            "RevenuesNetOfInterestExpense",  # broker-dealers, e.g. Goldman Sachs
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "SalesRevenueServicesNet",  # e.g. Old Dominion Freight Line
        ],
        "USD",
        "duration",
    ),
    "cost_of_revenue": (["CostOfRevenue", "CostOfGoodsAndServicesSold"], "USD", "duration"),
    "gross_profit": (["GrossProfit"], "USD", "duration"),
    "operating_income": (["OperatingIncomeLoss"], "USD", "duration"),
    "net_income": (
        ["NetIncomeLoss", "ProfitLoss"],  # some filers (e.g. PNC, Fox Corp) only tag ProfitLoss on their 10-K
        "USD",
        "duration",
    ),
    "eps_diluted": (["EarningsPerShareDiluted"], "USD/shares", "duration"),
    "shares_diluted": (["WeightedAverageNumberOfDilutedSharesOutstanding"], "shares", "duration"),
    "operating_cash_flow": (
        ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
        "USD",
        "duration",
    ),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForCapitalImprovements"], "USD", "duration"),
    "cash_and_equiv": (
        ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
        "USD",
        "instant",
    ),
    "stockholders_equity": (["StockholdersEquity"], "USD", "instant"),
    "long_term_debt_noncurrent": (["LongTermDebtNoncurrent"], "USD", "instant"),
    "long_term_debt_current": (["LongTermDebtCurrent", "DebtCurrent"], "USD", "instant"),
}


def _merged_annual_entries(gaap: dict, names: list[str], unit_key: str, kind: str) -> dict[str, dict]:
    """Union period-end entries across every candidate tag for a metric.

    Companies switch XBRL tags over time (e.g. Apple reported revenue under
    'Revenues' through fiscal 2017, then 'RevenueFromContractWithCustomer...'
    from fiscal 2018 on, after ASC 606 adoption). Using only the first tag
    that exists truncates history to whichever tag happened to match first —
    this merges all of them, with earlier-priority tags winning where two
    tags both cover the same period end.
    """
    merged: dict[str, dict] = {}
    for name in names:
        entries = _annual_entries(gaap.get(name), unit_key, kind)
        for end, row in entries.items():
            merged.setdefault(end, row)
    return merged


def _annual_entries(tag_data: dict | None, unit_key: str, kind: str) -> dict[str, dict]:
    """Return {period_end_date: xbrl_row} for the as-filed annual (10-K) value.

    XBRL companyfacts mixes annual, quarterly, and multi-year-restated
    comparative values under one tag. We only trust rows filed on a 10-K,
    and for duration (flow) metrics only those covering ~a full fiscal year
    (350-380 days) — a 10-K's XBRL also carries quarterly footnote figures
    tagged the same way, which this excludes. When a period end appears
    more than once (restatements), the most recently filed version wins.
    """
    if tag_data is None:
        return {}
    rows = tag_data.get("units", {}).get(unit_key, [])

    filtered = []
    for row in rows:
        if row.get("form") != "10-K":
            continue
        if kind == "duration":
            if "start" not in row or "end" not in row:
                continue
            try:
                days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
            except ValueError:
                continue
            if not (350 <= days <= 380):
                continue
        else:  # instant
            if "start" in row or "end" not in row:
                continue
        filtered.append(row)

    by_end: dict[str, dict] = {}
    for row in filtered:
        key = row["end"]
        if key not in by_end or row.get("filed", "") > by_end[key].get("filed", ""):
            by_end[key] = row
    return by_end


def extract_annual_fundamentals(facts: dict) -> list[dict]:
    """Pull the metrics in PRD §4 out of the raw XBRL facts payload.

    One row per fiscal year, keyed by the period end date found on the
    revenue/net-income tags. A fiscal year with neither revenue nor net
    income resolvable is dropped rather than persisted half-filled — see
    docs/PRD_ADDENDUM.md §A4 on not silently zero-filling missing data.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    if not gaap:
        return []

    series = {
        metric: _merged_annual_entries(gaap, names, unit_key, kind)
        for metric, (names, unit_key, kind) in TAG_CANDIDATES.items()
    }

    period_ends = set(series["revenue"]) | set(series["net_income"])
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []

    for end in sorted(period_ends):
        val = {metric: series[metric].get(end, {}).get("val") for metric in series}

        revenue = val["revenue"]
        net_income = val["net_income"]
        operating_income = val["operating_income"]
        if revenue is None or (net_income is None and operating_income is None):
            continue

        gross_margin = None
        if val["gross_profit"] is not None and revenue:
            gross_margin = val["gross_profit"] / revenue
        elif val["cost_of_revenue"] is not None and revenue:
            gross_margin = (revenue - val["cost_of_revenue"]) / revenue

        operating_margin = operating_income / revenue if operating_income is not None and revenue else None

        confidence = CONFIDENCE_HIGH
        free_cash_flow = None
        if val["operating_cash_flow"] is not None and val["capex"] is not None:
            free_cash_flow = val["operating_cash_flow"] - val["capex"]
        elif val["operating_cash_flow"] is not None:
            free_cash_flow = val["operating_cash_flow"]  # capex unavailable; less conservative
            confidence = CONFIDENCE_MEDIUM

        equity = val["stockholders_equity"]
        roe = net_income / equity if net_income is not None and equity else None

        total_debt = None
        if val["long_term_debt_noncurrent"] is not None or val["long_term_debt_current"] is not None:
            total_debt = (val["long_term_debt_noncurrent"] or 0) + (val["long_term_debt_current"] or 0)

        roic = None
        if operating_income is not None and equity is not None and total_debt is not None:
            invested_capital = total_debt + equity - (val["cash_and_equiv"] or 0)
            if invested_capital > 0:
                nopat = operating_income * (1 - ASSUMED_TAX_RATE)
                roic = nopat / invested_capital
                confidence = CONFIDENCE_MEDIUM  # always an estimate — bakes in ASSUMED_TAX_RATE

        rows.append(
            {
                "fiscal_year": date.fromisoformat(end).year,
                "period_end_date": end,
                "revenue": revenue,
                "eps_diluted": val["eps_diluted"],
                "net_income": net_income,
                "operating_income": operating_income,
                "operating_margin": operating_margin,
                "gross_margin": gross_margin,
                "roic": roic,
                "roe": roe,
                "free_cash_flow": free_cash_flow,
                "capex": val["capex"],
                "total_debt": total_debt,
                "cash_and_equiv": val["cash_and_equiv"],
                "shares_diluted": val["shares_diluted"],
                "source": "sec_edgar",
                "confidence": confidence,
                "retrieved_at": now_iso,
            }
        )

    return rows


def persist_annual_fundamentals(ticker: str, rows: list[dict], conn) -> None:
    conn.executemany(
        """
        INSERT INTO fundamentals_annual (
            ticker, fiscal_year, period_end_date, revenue, eps_diluted, net_income,
            operating_income, operating_margin, gross_margin, roic, roe, free_cash_flow,
            capex, total_debt, cash_and_equiv, shares_diluted, source, confidence, retrieved_at
        ) VALUES (
            :ticker, :fiscal_year, :period_end_date, :revenue, :eps_diluted, :net_income,
            :operating_income, :operating_margin, :gross_margin, :roic, :roe, :free_cash_flow,
            :capex, :total_debt, :cash_and_equiv, :shares_diluted, :source, :confidence, :retrieved_at
        )
        ON CONFLICT(ticker, fiscal_year) DO UPDATE SET
            period_end_date=excluded.period_end_date, revenue=excluded.revenue,
            eps_diluted=excluded.eps_diluted, net_income=excluded.net_income,
            operating_income=excluded.operating_income, operating_margin=excluded.operating_margin,
            gross_margin=excluded.gross_margin, roic=excluded.roic, roe=excluded.roe,
            free_cash_flow=excluded.free_cash_flow, capex=excluded.capex, total_debt=excluded.total_debt,
            cash_and_equiv=excluded.cash_and_equiv, shares_diluted=excluded.shares_diluted,
            source=excluded.source, confidence=excluded.confidence, retrieved_at=excluded.retrieved_at
        """,
        [{**r, "ticker": ticker} for r in rows],
    )
    conn.commit()


def run_for_ticker(ticker: str, conn) -> tuple[int, str | None]:
    """Fetch + persist one company's fundamentals. Returns (years_written, error)."""
    cik = lookup_cik(ticker)
    if cik is None:
        return 0, "no CIK found (not SEC-registered under this ticker)"

    facts = fetch_company_facts(cik)
    if not facts:
        return 0, "no filings found at SEC EDGAR for this CIK"

    rows = extract_annual_fundamentals(facts)
    if not rows:
        return 0, "filings found but no usable annual revenue/income tags"

    persist_annual_fundamentals(ticker, rows, conn)
    return len(rows), None
