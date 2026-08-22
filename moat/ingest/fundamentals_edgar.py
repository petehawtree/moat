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

from moat.config import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    DATA_DIR,
    FILINGS_CACHE_DIR,
    FUNDAMENTALS_CACHE_MAX_AGE_DAYS,
    SEC_USER_AGENT,
)

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


def _facts_cache_path(cik: str) -> Path:
    return FILINGS_CACHE_DIR / f"CIK{cik}.json"


def fetch_company_facts(cik: str, use_cache: bool = True, max_age_days: int | None = None) -> dict:
    """Fetch the full XBRL company facts payload for one company. {} if none filed.

    Caches the raw payload under FILINGS_CACHE_DIR (docs/PRD_ADDENDUM.md §A11
    step 1). This is the project's "receipt": every derived number can be
    re-checked against the exact bytes it came from, offline and without
    SEC's live data having drifted underneath us (§A7 documents that drift
    happening mid-project). It also makes scripts/verify.py instant.

    The cache **expires** after `max_age_days` (default
    FUNDAMENTALS_CACHE_MAX_AGE_DAYS). Sprint 2.1 shipped this cache with no
    expiry at all, which silently broke §A6's quarterly refresh — prices kept
    updating while fundamentals were frozen forever (§A13). Verification reads
    should pass `max_age_days=None` to force the cached copy, so re-checking a
    past claim isn't affected by SEC having since revised the data.
    """
    cache_path = _facts_cache_path(cik)
    if use_cache and cache_path.exists():
        if max_age_days is None:
            return json.loads(cache_path.read_text())
        age_days = (time.time() - cache_path.stat().st_mtime) / 86400
        if age_days <= max_age_days:
            return json.loads(cache_path.read_text())

    facts = _get_json(COMPANY_FACTS_URL.format(cik=cik))
    if facts:
        FILINGS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(facts))
    return facts


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
    # ASC 842 lease income. Kept separate from `revenue` (ASC 606) rather than
    # added to the candidate list, because the two standards cover mutually
    # exclusive revenue streams — a filer reporting both is reporting two
    # disjoint components of one top line, so they must be SUMMED, not ranked.
    # Camden Property Trust tags $1.573bn of rental income here while its ASC
    # 606 tag carries only $12.967m of non-lease fee income; taking either
    # alone misstates the company (§A13).
    "lease_income": (
        ["OperatingLeaseLeaseIncome", "OperatingLeasesIncomeStatementLeaseRevenue"],
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
            # Record which tag supplied the value: callers need to know
            # whether a figure is a consolidated total or one component of
            # one (see the ASC 606 + ASC 842 revenue combination in
            # extract_annual_fundamentals).
            merged.setdefault(end, {**row, "_tag": name})
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


SHARES_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"

# A restatement this large is a share-basis change (split / reverse split),
# not a revision. Ordinary restatements move a figure by a few percent.
_BASIS_CHANGE_MIN_RATIO = 1.4

# eps_diluted * shares_diluted should reconstruct net income. Wider than it
# looks deliberately: the numerator of EPS is net income *available to common*,
# so preferred dividends, noncontrolling interests and participating
# securities legitimately move this ratio (banks especially). Outside this
# band the row is not "slightly off" — it is a different unit or a corrupt
# value. See docs/PRD_ADDENDUM.md §A10.
_EPS_CONSISTENCY_LOW, _EPS_CONSISTENCY_HIGH = 0.5, 2.0

FLAG_EPS_SHARES_MISMATCH = "eps_shares_ni_mismatch"

# Powers of 1000 are units (thousands/millions/billions), not splits — no
# company runs a 1,000,000-for-1 split. Southwest's FY2009 diluted shares
# were filed as `741` and later restated to `741,000,000`: the filer
# correcting a unit, which happens to look identical to a split in the
# merged series. Same numeric treatment, different label, because the label
# is what tells a reader whether to trust the underlying row (§A10).
_UNIT_SCALES = (1e3, 1e6, 1e9)
_UNIT_TOLERANCE = 0.02

# Deliberately looser than _UNIT_TOLERANCE. The eps*shares/net_income ratio
# carries the same structural noise that separates net income from the EPS
# numerator, so a genuine thousands-unit row can land a few percent off a
# clean 1000x — Northern Trust's corrupt FY2008 value comes out at 9.78e5,
# 2.2% shy of 1e6. Nothing structural is ever within 10% of a power of 1000,
# so widening this costs no precision.
_UNIT_RATIO_TOLERANCE = 0.10


def classify_basis_change(ratio: float) -> str:
    """'unit_correction' if the restatement is a power-of-1000 rescale, else 'split'."""
    magnitude = ratio if ratio >= 1 else 1 / ratio
    for scale in _UNIT_SCALES:
        if abs(magnitude - scale) / scale <= _UNIT_TOLERANCE:
            return "unit_correction"
    return "split"


def detect_share_basis_changes(facts: dict) -> list[dict]:
    """Find periods whose diluted share count was RESTATED by a later filing.

    This is the evidence that separates a genuine stock split from real
    dilution (docs/PRD_ADDENDUM.md §A10). A split rebases prior-period
    comparatives, so the same period-end carries two different values across
    two filings — e.g. Walmart's FY2022 was filed as 2.805B, then restated to
    8.415B by the post-split FY2024 10-K. A real share issuance (IPO, merger,
    recapitalisation) restates nothing: the count genuinely grew and every
    filing agrees on every period.

    Sprint 2 inferred splits from a >=40% jump in the *merged* series, which
    cannot tell those apart and fired on 37.4% of the universe. This looks at
    the disagreement between filings instead, which is only visible here at
    ingest — the merge in _annual_entries discards the losing value.
    """
    rows = facts.get("facts", {}).get("us-gaap", {}).get(SHARES_TAG, {}).get("units", {}).get("shares", [])

    by_period: dict[str, dict[str, dict]] = {}
    for row in rows:
        if row.get("form") != "10-K" or "start" not in row or "end" not in row:
            continue
        filed = row.get("filed")
        if filed is None:
            continue
        # Annual durations only. A 10-K's XBRL also carries quarterly figures
        # tagged identically (the Sprint 1 lesson, see _annual_entries) — and
        # those restate around a split too, which would otherwise register
        # four bogus "basis changes" per split event.
        try:
            days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
        except (ValueError, TypeError):
            continue
        if not (350 <= days <= 380):
            continue
        by_period.setdefault(row["end"], {})[filed] = row

    changes = []
    for period_end, filings in sorted(by_period.items()):
        ordered = [filings[f] for f in sorted(filings)]
        first, last = ordered[0], ordered[-1]
        original, restated = first.get("val"), last.get("val")
        if not original or not restated or original <= 0:
            continue
        ratio = restated / original
        if ratio >= _BASIS_CHANGE_MIN_RATIO or ratio <= 1 / _BASIS_CHANGE_MIN_RATIO:
            changes.append(
                {
                    "period_end_date": period_end,
                    "original_value": original,
                    "restated_value": restated,
                    "ratio": ratio,
                    "change_type": classify_basis_change(ratio),
                    "original_accession": first.get("accn"),
                    "original_filed": first.get("filed"),
                    "restated_accession": last.get("accn"),
                    "restated_filed": last.get("filed"),
                }
            )
    return changes


def check_row_quality(row: dict) -> list[str]:
    """Internal-consistency checks on one extracted fundamentals row.

    Returns flag names, empty when clean. Flags rather than rejects: some
    failures are legitimate, and silently dropping a row would hide a data
    problem instead of surfacing it (§A4).

    `eps_diluted * shares_diluted` should reconstruct `net_income`. When it
    misses, the *size* of the miss says which figure to distrust:

    - **a power of 1000** -> the share count is in the wrong unit. Southwest
      filed FY2007 diluted shares as `768`, ConocoPhillips filed FY2010-2019
      in thousands. The share count is unusable; EPS and net income are fine.
    - **anything else** -> a structural difference between net income and the
      EPS numerator: preferred dividends, noncontrolling interests,
      participating securities. TKO (UFC/WWE, large NCI) lands here. EPS is
      not comparable across years, but the *share count is perfectly good* —
      which matters, because TKO's real merger dilution is exactly what the
      Sprint 2 defect erased (§A10).

    Distinguishing them keeps a structural quirk from discarding a sound
    share count, and vice versa.
    """
    flags = []
    eps, shares, net_income = row.get("eps_diluted"), row.get("shares_diluted"), row.get("net_income")
    if eps and shares and net_income:
        ratio = (eps * shares) / net_income
        if _is_power_of_1000(ratio):
            flags.append(FLAG_SHARE_UNIT_OUTLIER)
        elif not (_EPS_CONSISTENCY_LOW < ratio < _EPS_CONSISTENCY_HIGH):
            flags.append(FLAG_EPS_SHARES_MISMATCH)
    return flags


def _is_power_of_1000(ratio: float) -> bool:
    """True when `ratio` is ~1000^n for some non-zero integer n (either
    direction) — the signature of a unit-of-measure error rather than an
    accounting difference.
    """
    if ratio <= 0:
        return False
    magnitude = ratio if ratio >= 1 else 1 / ratio
    for scale in _UNIT_SCALES:
        if abs(magnitude - scale) / scale <= _UNIT_RATIO_TOLERANCE:
            return True
    return False


FLAG_SHARE_UNIT_OUTLIER = "share_count_unit_outlier"
FLAG_IMPLAUSIBLE_RATIO = "implausible_ratio"

# Net income exceeding revenue means the "revenue" figure is a fragment, not
# a consolidated top line — the tag we picked measures something narrower
# than the business. A small tolerance allows genuine one-off gains (asset
# sales, tax benefits) to exceed revenue without tripping the flag.
_NET_INCOME_OVER_REVENUE_LIMIT = 1.0

# Deliberately NOT a margin-magnitude check. A margin worse than -100% is
# perfectly real for a loss-making company: Moderna posted a -158% operating
# margin on collapsing revenue, MicroStrategy -1141% on bitcoin impairments.
# Flagging those would quarantine genuine distress as a data error and
# exclude it from peer comparison — the opposite of what a quality screen
# wants. The reliable signal for a *wrong revenue tag* is an income figure
# exceeding the revenue it was supposedly earned on.


def check_ratio_plausibility(row: dict) -> list[str]:
    """Cross-field sanity checks that catch a wrong *revenue* tag (§A13).

    The unit checks in check_row_quality validate one figure against another
    of the same kind. This catches a different failure: a revenue tag that
    parses cleanly and is internally consistent but measures the wrong scope.
    Camden Property Trust ingested $12.967m of revenue against a real ~$1.6bn,
    producing a 6,375% "FCF margin" that took the 100th percentile in Real
    Estate — and, because percentiles are relative, shifted all 30 of its
    sector peers and pushed one of them across the top-tercile bar. A single
    bad row is not contained to its own company, which is why these are
    quarantined from peer groups rather than merely flagged.
    """
    revenue = row.get("revenue")
    if not revenue or revenue <= 0:
        return []
    # Income exceeding the revenue that produced it means the revenue figure
    # is a fragment of the top line, not the top line. DTE Energy ingested
    # $61m of revenue against $2.37bn of operating income (real revenue
    # ~$13bn); Fifth Third $80m against $2.52bn of net income (real ~$8bn).
    # Only positive income counts — a large *loss* says nothing about
    # whether revenue was captured correctly.
    for field in ("net_income", "operating_income"):
        value = row.get(field)
        if value and value > 0 and value / revenue > _NET_INCOME_OVER_REVENUE_LIMIT:
            return [FLAG_IMPLAUSIBLE_RATIO]
    return []


def persist_share_basis_changes(ticker: str, changes: list[dict], conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO share_basis_changes (
            ticker, period_end_date, original_value, restated_value, ratio, change_type,
            original_accession, original_filed, restated_accession, restated_filed, detected_at
        ) VALUES (
            :ticker, :period_end_date, :original_value, :restated_value, :ratio, :change_type,
            :original_accession, :original_filed, :restated_accession, :restated_filed, :detected_at
        )
        ON CONFLICT(ticker, period_end_date) DO UPDATE SET
            original_value=excluded.original_value, restated_value=excluded.restated_value,
            ratio=excluded.ratio, change_type=excluded.change_type,
            original_accession=excluded.original_accession,
            original_filed=excluded.original_filed, restated_accession=excluded.restated_accession,
            restated_filed=excluded.restated_filed, detected_at=excluded.detected_at
        """,
        [{**c, "ticker": ticker, "detected_at": now} for c in changes],
    )
    conn.commit()


def _accession_url(cik: str, accession: str) -> str:
    """EDGAR filing-index URL for an accession number (A11 step 4)."""
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{accession}-index.htm"
    )


def extract_filings(facts: dict, cik: str) -> list[dict]:
    """Distinct 10-K filings referenced anywhere in the XBRL payload.

    Populates the `filings` table, which has existed since Sprint 0 and sat
    empty — it's what §A3's citation enforcement resolves against in Sprint 3,
    and what makes any number in fundamentals_annual traceable to a real
    EDGAR document today.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    seen: dict[str, dict] = {}
    for tag_data in gaap.values():
        for unit_rows in tag_data.get("units", {}).values():
            for row in unit_rows:
                accn, filed = row.get("accn"), row.get("filed")
                if row.get("form") != "10-K" or not accn or not filed:
                    continue
                if accn not in seen or row.get("end", "") > seen[accn]["period_of_report"]:
                    seen[accn] = {
                        "accession_number": accn,
                        "form_type": "10-K",
                        "filing_date": filed,
                        "period_of_report": row.get("end", ""),
                        "document_url": _accession_url(cik, accn),
                    }
    return list(seen.values())


def persist_filings(ticker: str, filings: list[dict], conn) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """
        INSERT INTO filings (
            accession_number, ticker, form_type, filing_date, period_of_report,
            document_url, retrieved_at
        ) VALUES (
            :accession_number, :ticker, :form_type, :filing_date, :period_of_report,
            :document_url, :retrieved_at
        )
        ON CONFLICT(accession_number) DO UPDATE SET
            form_type=excluded.form_type, filing_date=excluded.filing_date,
            period_of_report=excluded.period_of_report, document_url=excluded.document_url,
            retrieved_at=excluded.retrieved_at
        """,
        [{**f, "ticker": ticker, "retrieved_at": now} for f in filings],
    )
    conn.commit()


# Tags that are already a consolidated top line — lease income is inside them
# and must not be added again.
_CONSOLIDATED_REVENUE_TAGS = {"Revenues", "RevenuesNetOfInterestExpense", "SalesRevenueNet"}


def _total_revenue(contract_revenue: float | None, lease_income: float | None, revenue_tag: str | None) -> float | None:
    """Combine ASC 606 contract revenue with ASC 842 lease income (§A13).

    The two standards cover mutually exclusive revenue streams, so a filer
    reporting both is reporting two disjoint components of one top line and
    they must be summed. A REIT is the common case: Camden Property Trust
    tags $1.573bn of rental income as lease income and only $12.967m of
    non-lease fee income as contract revenue — taking either alone misstates
    the business by two orders of magnitude.

    When the figure already came from a consolidated tag (`Revenues` and
    friends), it includes lease income by construction and is returned
    untouched — double-counting would be just as wrong as under-counting.
    """
    if revenue_tag in _CONSOLIDATED_REVENUE_TAGS:
        return contract_revenue
    if lease_income is None:
        return contract_revenue
    if contract_revenue is None:
        return lease_income
    return contract_revenue + lease_income


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

        revenue = _total_revenue(
            contract_revenue=val["revenue"],
            lease_income=val["lease_income"],
            revenue_tag=series["revenue"].get(end, {}).get("_tag"),
        )
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

        # Free cash flow is operating cash flow MINUS capex. Never substitute
        # OCF when capex is missing (docs/PRD_ADDENDUM.md §A13): that inflates
        # both FCF margin and debt/FCF, and it inverts the meaning of the
        # metric for exactly the capital-intensive businesses where capex
        # matters most. Sprint 2 substituted it on 155 of 505 companies, 38 of
        # which passed the screen on the inflated figure. An unknown FCF is
        # now reported as unknown; `operating_cash_flow` is stored separately
        # so the number we *do* have isn't lost.
        confidence = CONFIDENCE_HIGH
        operating_cash_flow = val["operating_cash_flow"]
        free_cash_flow = None
        if operating_cash_flow is not None and val["capex"] is not None:
            free_cash_flow = operating_cash_flow - val["capex"]

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

        # Provenance (A11): which filing these figures came from. Taken from
        # the revenue entry, falling back to net income — whichever anchored
        # this fiscal year in the first place (see period_ends above).
        anchor = series["revenue"].get(end) or series["net_income"].get(end) or {}

        row = {
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
            "operating_cash_flow": operating_cash_flow,
            "capex": val["capex"],
            "total_debt": total_debt,
            "cash_and_equiv": val["cash_and_equiv"],
            "shares_diluted": val["shares_diluted"],
            "source": "sec_edgar",
            "confidence": confidence,
            "accession_number": anchor.get("accn"),
            "filed": anchor.get("filed"),
            "retrieved_at": now_iso,
        }
        row["quality_flags"] = ",".join(check_row_quality(row) + check_ratio_plausibility(row)) or None
        rows.append(row)

    return rows


def persist_annual_fundamentals(ticker: str, rows: list[dict], conn) -> None:
    conn.executemany(
        """
        INSERT INTO fundamentals_annual (
            ticker, fiscal_year, period_end_date, revenue, eps_diluted, net_income,
            operating_income, operating_margin, gross_margin, roic, roe, free_cash_flow,
            operating_cash_flow, capex, total_debt, cash_and_equiv, shares_diluted, source, confidence,
            accession_number, filed, quality_flags, retrieved_at
        ) VALUES (
            :ticker, :fiscal_year, :period_end_date, :revenue, :eps_diluted, :net_income,
            :operating_income, :operating_margin, :gross_margin, :roic, :roe, :free_cash_flow,
            :operating_cash_flow, :capex, :total_debt, :cash_and_equiv, :shares_diluted, :source, :confidence,
            :accession_number, :filed, :quality_flags, :retrieved_at
        )
        ON CONFLICT(ticker, fiscal_year) DO UPDATE SET
            period_end_date=excluded.period_end_date, revenue=excluded.revenue,
            eps_diluted=excluded.eps_diluted, net_income=excluded.net_income,
            operating_income=excluded.operating_income, operating_margin=excluded.operating_margin,
            gross_margin=excluded.gross_margin, roic=excluded.roic, roe=excluded.roe,
            free_cash_flow=excluded.free_cash_flow, operating_cash_flow=excluded.operating_cash_flow,
            capex=excluded.capex, total_debt=excluded.total_debt,
            cash_and_equiv=excluded.cash_and_equiv, shares_diluted=excluded.shares_diluted,
            source=excluded.source, confidence=excluded.confidence,
            accession_number=excluded.accession_number, filed=excluded.filed,
            quality_flags=excluded.quality_flags, retrieved_at=excluded.retrieved_at
        """,
        [{**r, "ticker": ticker} for r in rows],
    )
    conn.commit()


def run_for_ticker(ticker: str, conn, max_age_days: int | None = FUNDAMENTALS_CACHE_MAX_AGE_DAYS) -> tuple[int, str | None]:
    """Fetch + persist one company's fundamentals. Returns (years_written, error).

    Re-fetches from SEC when the cached payload is older than `max_age_days`
    (§A6 refresh cadence). Pass 0 to force a refresh, None to work purely
    offline from cache.
    """
    cik = lookup_cik(ticker)
    if cik is None:
        return 0, "no CIK found (not SEC-registered under this ticker)"

    facts = fetch_company_facts(cik, max_age_days=max_age_days)
    if not facts:
        return 0, "no filings found at SEC EDGAR for this CIK"

    rows = extract_annual_fundamentals(facts)
    if not rows:
        return 0, "filings found but no usable annual revenue/income tags"

    persist_annual_fundamentals(ticker, rows, conn)
    persist_share_basis_changes(ticker, detect_share_basis_changes(facts), conn)
    persist_filings(ticker, extract_filings(facts, cik), conn)
    return len(rows), None
