"""Sanity checks on the known-bad-ticker exclusion constants (Sprint 5 C8,
moat/config.py) — catches a typo'd/duplicated ticker or a broken union
without needing the real database."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat import config


def test_debt_tag_gap_exclusion_is_gone():
    """GitHub #1 is fixed at the source in Sprint 6.0; the 24-ticker
    exclusion must not come back as a stand-in for the fix."""
    assert not hasattr(config, "DEBT_TAG_GAP_TICKERS")
    assert "A" not in config.COMMITTEE_KNOWN_EXCLUDED_TICKERS


def test_ai_analysis_exclusion_is_reits_plus_googl():
    assert config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS == (
        config.REIT_INVALID_METRICS_TICKERS | config.DUAL_CLASS_FILING_GAP_TICKERS
    )
    assert len(config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS) == 4
    assert "GOOGL" in config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS
    assert "BKNG" not in config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS


def test_valuation_exclusion_is_reits_plus_bkng():
    assert config.VALUATION_KNOWN_EXCLUDED_TICKERS == (
        config.REIT_INVALID_METRICS_TICKERS | config.PRICE_SHARE_ANOMALY_TICKERS
    )
    assert len(config.VALUATION_KNOWN_EXCLUDED_TICKERS) == 4
    assert "BKNG" in config.VALUATION_KNOWN_EXCLUDED_TICKERS
    assert "GOOGL" not in config.VALUATION_KNOWN_EXCLUDED_TICKERS


def test_committee_exclusion_is_the_union_of_both_stages():
    assert config.COMMITTEE_KNOWN_EXCLUDED_TICKERS == (
        config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS | config.VALUATION_KNOWN_EXCLUDED_TICKERS
    )
    assert len(config.COMMITTEE_KNOWN_EXCLUDED_TICKERS) == 5  # AMT + SBAC + CCI + GOOGL + BKNG
    assert {"GOOGL", "BKNG", "AMT", "SBAC", "CCI"} == config.COMMITTEE_KNOWN_EXCLUDED_TICKERS
