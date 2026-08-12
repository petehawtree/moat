"""Offline tests for universe merging/normalization — no network calls."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.ingest.universe import CompanyRecord, _normalize_ticker, build_universe
from moat.ingest import universe as universe_module


def test_ticker_normalization_converts_dot_to_hyphen():
    assert _normalize_ticker("brk.b") == "BRK-B"
    assert _normalize_ticker("AAPL") == "AAPL"


def test_build_universe_dedupes_and_prefers_richer_record(monkeypatch):
    sp500 = [CompanyRecord("AAPL", "Apple Inc.", "Technology", "Hardware", None, "USD", "sp500", cik="0000320193")]
    nasdaq100 = [
        CompanyRecord("AAPL", "Apple Inc.", None, None, "NASDAQ", "USD", "nasdaq100", cik=None),
        CompanyRecord("PDD", "PDD Holdings", None, None, "NASDAQ", "USD", "nasdaq100", cik=None),
    ]
    monkeypatch.setattr(universe_module, "fetch_sp500_constituents", lambda: sp500)
    monkeypatch.setattr(universe_module, "fetch_nasdaq100_constituents", lambda: nasdaq100)

    records = build_universe()
    by_ticker = {r.ticker: r for r in records}

    assert len(records) == 2
    assert by_ticker["AAPL"].universe == "nasdaq100,sp500"
    assert by_ticker["AAPL"].sector == "Technology"  # sp500's GICS sector wins, not overwritten by nasdaq100's None
    assert by_ticker["AAPL"].cik == "0000320193"
    assert by_ticker["PDD"].sector is None  # no fabricated sector for NASDAQ-100-only names
