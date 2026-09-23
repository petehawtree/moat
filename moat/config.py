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

# A company can't pass a screen we couldn't actually run on it (A13).
# composite_score is now the % of *assessable* metrics passed, so a company
# with one measurable metric could otherwise score 100. Requiring most of the
# metrics to be measurable keeps the score comparable across companies.
MIN_METRICS_ASSESSED = 6

# How stale a cached SEC companyfacts payload may be before ingest re-fetches
# it (A6: fundamentals refresh quarterly). Sprint 2.1's cache had no expiry,
# which froze fundamentals indefinitely while prices kept refreshing — see A13.
FUNDAMENTALS_CACHE_MAX_AGE_DAYS = 90

# Data confidence tiers (A4)
CONFIDENCE_HIGH = "high"     # SEC EDGAR structured XBRL
CONFIDENCE_MEDIUM = "medium" # derived from EDGAR with assumptions
CONFIDENCE_LOW = "low"       # yfinance-only / unverified

# ---------------------------------------------------------------------
# Known-bad-ticker operational exclusions (Sprint 5 C8).
#
# Every one of these was found, filed and deliberately deferred rather than
# fixed at the source — see the linked addendum section for why. Before this,
# each stage's own `--exclude` CLI flag was the only place this list existed,
# opt-in and retyped from memory every run; the judge's review after Sprint 4
# flagged that as its top finding (a stage silently including a known-bad
# ticker the moment someone forgets the flag). This is the single source of
# truth instead — a stage composes the subset it actually needs, and can
# still be overridden via --exclude/--include-excluded for a specific run.
# ---------------------------------------------------------------------

# §A17 (GitHub issue #1): confirmed `total_debt IS NULL` extraction gap —
# not a reliable "no debt" signal for these tickers. Feeds the quant
# screen's debt metric, ev_ebit()'s enterprise-value debt add-back, and
# (Sprint 5) financial_strength/valuation persona input alike. The second
# row newly passed the 20260923T122501Z screen once GitHub #9 recovered
# their FCF metrics — same NULL-debt condition, so same exclusion (SYY and
# MELI clearly carry debt; the rest may be near debt-free, but the screen
# can't tell "no debt" from "debt not extracted").
DEBT_TAG_GAP_TICKERS = frozenset({
    "A", "ADSK", "ALAB", "ALNY", "DDOG", "DECK", "DXCM", "GRMN", "LULU",
    "MNST", "NOW", "PLTR", "PM", "RMD", "ROL", "SHOP", "VRTX", "WSM",
    "ANET", "EXPD", "ISRG", "MELI", "PANW", "SYY",
})

# §A17 (GitHub issue #2): REITs scored on gross_margin/free_cash_flow/debt —
# metrics the addendum already documented as invalid for this business
# model (FFO/AFFO not yet implemented).
# CCI (tower REIT, same model as AMT/SBAC) newly passed the
# 20260923T122501Z screen once GitHub #9 recovered its FCF.
REIT_INVALID_METRICS_TICKERS = frozenset({"AMT", "SBAC", "CCI"})

# §A18 (GitHub issue #3): GOOG/GOOGL share one CIK; GOOGL's own
# `filings.ticker` lookups return nothing even though the filing is cached
# under GOOG. Affects filing/citation-dependent stages only — valuation
# doesn't read filing text, so GOOGL's valuation is unaffected.
DUAL_CLASS_FILING_GAP_TICKERS = frozenset({"GOOGL"})

# §A20: BKNG's ingested price_history (2-year window, $135-215) is
# inconsistent with its real ~32.6M-share, multi-thousand-dollar-per-share
# structure — a data anomaly, not a code defect. Valuation only.
PRICE_SHARE_ANOMALY_TICKERS = frozenset({"BKNG"})

# Per-stage composition. ai_analysis/valuation's own defaults are
# deliberately unchanged by this (Sprint 5's "Open for discussion" #1:
# continue deferring §A17/§A18/§A20 fixes, `--exclude` stays opt-in there) —
# these constants exist so a caller who *does* want the known-current list
# doesn't have to retype it, not to silently change prior sprints' behavior.
AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS = (
    DEBT_TAG_GAP_TICKERS | REIT_INVALID_METRICS_TICKERS | DUAL_CLASS_FILING_GAP_TICKERS
)
VALUATION_KNOWN_EXCLUDED_TICKERS = (
    DEBT_TAG_GAP_TICKERS | REIT_INVALID_METRICS_TICKERS | PRICE_SHARE_ANOMALY_TICKERS
)
# Sprint 5 C8: the committee stage defaults to excluding this union, since a
# committee verdict needs *both* upstream stages clean — the highest-stakes
# place for a known-invalid input to leak into a ranked recommendation
# unflagged, per the sprint-5 plan's own reasoning.
COMMITTEE_KNOWN_EXCLUDED_TICKERS = (
    AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS | VALUATION_KNOWN_EXCLUDED_TICKERS
)
