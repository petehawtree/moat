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
ABSOLUTE_FLOORS = {
    "roic": 0.0,             # must at least be capital-productive
    "roe": 0.0,
    "free_cash_flow": 0.0,   # must be non-negative
    "operating_margin": 0.0,
    "gross_margin_trend": "stable_or_improving",
}

# Data confidence tiers (A4)
CONFIDENCE_HIGH = "high"     # SEC EDGAR structured XBRL
CONFIDENCE_MEDIUM = "medium" # derived from EDGAR with assumptions
CONFIDENCE_LOW = "low"       # yfinance-only / unverified
