"""Project-wide configuration and constants.

Sprint 1 scope: US only (S&P 500 + NASDAQ 100). See docs/PRD_ADDENDUM.md §A1.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
DATA_DIR = PROJECT_ROOT / "data"
FILINGS_CACHE_DIR = DATA_DIR / "filings"

# Universe (A1: US-only for Sprint 1)
UNIVERSES_IN_SCOPE = ["sp500", "nasdaq100"]

# SEC EDGAR requires a descriptive User-Agent identifying the requester.
# Set MOAT_CONTACT_EMAIL in your environment (see .env.example).
SEC_USER_AGENT = f"Project Moat (personal research tool; {os.environ.get('MOAT_CONTACT_EMAIL', 'set MOAT_CONTACT_EMAIL')})"

# Anthropic API key for the AI analysis stages (Sprint 3+)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Quant screen thresholds (PRD §4) — absolute floors.
# Sector-relative bars (A2) are computed at runtime in moat/screen/, not
# hardcoded here, since they depend on the peer group's distribution.
# Sprint 2 (docs/PRD_ADDENDUM.md §A9): these are deliberately much looser
# than PRD §4's flat ">15%"-style criteria — they exist only to disqualify
# outright, regardless of sector (e.g. persistently negative FCF). The old
# flat bars now live in the sector-relative comparison instead, so a
# capital-intensive sector isn't screened out by a threshold tuned for
# asset-light businesses.
ABSOLUTE_FLOORS = {
    "roic": 0.0,             # must at least be capital-productive
    "roe": 0.0,
    "free_cash_flow": 0.0,   # FCF margin must be non-negative
    "operating_margin": 0.0,
    "revenue_eps_growth": 0.0,          # revenue CAGR must be non-negative
    "debt_to_fcf_max": 5.0,             # total debt shouldn't exceed 5x annual FCF
    "share_dilution_max_annual": 0.01,  # diluted share count growing <=1%/yr on average
    "gross_margin_trend": "stable_or_improving",  # descriptive; see gross_margin_erosion_tolerance for the check
    "gross_margin_erosion_tolerance": 0.005,  # allow up to 0.5pp of margin erosion as "roughly stable"
}

# Sector-relative screen (A2/A9): a metric only clears the sector-relative
# bar if the company sits at or above this percentile within its own GICS
# sector — "top tercile" in A2's own wording.
SECTOR_RELATIVE_TOP_TERCILE_PCT = 200 / 3  # ~66.7th percentile

# Below this many sector peers with a usable value, a percentile is too
# noisy to be a meaningful bar — falls back to absolute-floor-only for that
# company/metric rather than ranking against a tiny, unstable peer group.
MIN_SECTOR_PEER_GROUP = 5

# Pre-AI quality score (moat/quality/quality_score.py) — composite_score is
# 0-100, the % of the 8 PRD §4 metrics a company passed (floor + sector-
# relative). >=50 means "passed at least half" — see docs/PRD_ADDENDUM.md
# §A9 for the empirical distribution this was chosen against.
QUALITY_SCORE_PASS_THRESHOLD = 50.0

# Data confidence tiers (A4)
CONFIDENCE_HIGH = "high"     # SEC EDGAR structured XBRL
CONFIDENCE_MEDIUM = "medium" # derived from EDGAR with assumptions
CONFIDENCE_LOW = "low"       # yfinance-only / unverified
