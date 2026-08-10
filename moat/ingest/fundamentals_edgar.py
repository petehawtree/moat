"""US fundamentals via SEC EDGAR's structured XBRL companyfacts API.

Highest-confidence free data source in the project (docs/PRD_ADDENDUM.md §A4:
confidence='high'). Requires a descriptive User-Agent per SEC's fair-access
policy — see moat/config.py:SEC_USER_AGENT.

Docs: https://www.sec.gov/edgar/sec-api-documentation
"""
from __future__ import annotations

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:0>10}.json"
TICKER_CIK_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"


def lookup_cik(ticker: str) -> str | None:
    """Resolve a ticker to its 10-digit zero-padded CIK.

    TODO (Sprint 1): fetch TICKER_CIK_LOOKUP_URL once, cache locally in
    data/, build a ticker->cik dict. Refresh weekly.
    """
    raise NotImplementedError


def fetch_company_facts(cik: str) -> dict:
    """Fetch the full XBRL company facts payload for one company.

    TODO (Sprint 1): GET COMPANY_FACTS_URL with SEC_USER_AGENT header,
    handle 404 (no filings) and rate limiting (SEC asks for <=10 req/s).
    """
    raise NotImplementedError


def extract_annual_fundamentals(facts: dict) -> list[dict]:
    """Pull the metrics in PRD §4 out of the raw XBRL facts payload.

    Maps XBRL us-gaap tags (e.g. 'Revenues', 'NetCashProvidedByUsedInOperatingActivities',
    'PaymentsForCapitalImprovements') to the fundamentals_annual schema columns.
    Computes derived metrics (ROIC, ROE, FCF = operating cash flow - capex,
    operating margin, gross margin) rather than trusting a single tag, and
    marks confidence='high' since the inputs are as-filed structured data.

    TODO (Sprint 1): implement tag mapping + derivation. Flag fiscal years
    with missing required tags rather than silently zero-filling.
    """
    raise NotImplementedError
