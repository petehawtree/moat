"""Sanity checks on the known-bad-ticker exclusion constants (Sprint 5 C8,
moat/config.py) — catches a typo'd/duplicated ticker or a broken union
without needing the real database."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat import config


def test_debt_tag_gap_has_24_tickers():
    """§A17's confirmed count — a regression guard against an accidental
    add/remove going unnoticed."""
    assert len(config.DEBT_TAG_GAP_TICKERS) == 24  # 18 + 6 new passers after GitHub #9


def test_reit_and_debt_gap_dont_overlap():
    assert not (config.DEBT_TAG_GAP_TICKERS & config.REIT_INVALID_METRICS_TICKERS)


def test_ai_analysis_exclusion_is_20_plus_googl():
    assert config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS == (
        config.DEBT_TAG_GAP_TICKERS
        | config.REIT_INVALID_METRICS_TICKERS
        | config.DUAL_CLASS_FILING_GAP_TICKERS
    )
    assert len(config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS) == 28
    assert "GOOGL" in config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS
    assert "BKNG" not in config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS


def test_valuation_exclusion_is_20_plus_bkng():
    assert config.VALUATION_KNOWN_EXCLUDED_TICKERS == (
        config.DEBT_TAG_GAP_TICKERS
        | config.REIT_INVALID_METRICS_TICKERS
        | config.PRICE_SHARE_ANOMALY_TICKERS
    )
    assert len(config.VALUATION_KNOWN_EXCLUDED_TICKERS) == 28
    assert "BKNG" in config.VALUATION_KNOWN_EXCLUDED_TICKERS
    assert "GOOGL" not in config.VALUATION_KNOWN_EXCLUDED_TICKERS


def test_committee_exclusion_is_the_union_of_both_stages():
    assert config.COMMITTEE_KNOWN_EXCLUDED_TICKERS == (
        config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS | config.VALUATION_KNOWN_EXCLUDED_TICKERS
    )
    assert len(config.COMMITTEE_KNOWN_EXCLUDED_TICKERS) == 29  # 24 + AMT + SBAC + CCI + GOOGL + BKNG
    assert {"GOOGL", "BKNG", "AMT", "SBAC", "CCI"} <= config.COMMITTEE_KNOWN_EXCLUDED_TICKERS
