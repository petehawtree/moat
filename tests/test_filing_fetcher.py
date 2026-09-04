"""Tests for W1: select_latest_10k and supporting pure functions.

All tests here are offline (no network). Network-touching functions
(fetch_filing_document, run_for_ticker) require a live EDGAR endpoint
and are not tested here.
"""
import hashlib
from pathlib import Path

import pytest

from moat.ingest.filing_fetcher import (
    _accession_index_url,
    _primary_document_url,
    _store_document,
    select_latest_10k,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _submissions(filings: list[dict]) -> dict:
    """Build a minimal submissions payload from a list of filing dicts.

    Each dict needs: accessionNumber, form, reportDate, filingDate, primaryDocument.
    """
    keys = ("accessionNumber", "form", "reportDate", "filingDate", "primaryDocument")
    arrays = {k: [f[k] for f in filings] for k in keys}
    return {"filings": {"recent": arrays}}


# ---------------------------------------------------------------------------
# select_latest_10k
# ---------------------------------------------------------------------------

def test_select_latest_10k_empty_submissions():
    assert select_latest_10k({}) is None
    assert select_latest_10k({"filings": {}}) is None
    assert select_latest_10k({"filings": {"recent": {}}}) is None


def test_select_latest_10k_no_qualifying_forms():
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-Q",
         "reportDate": "2024-09-30", "filingDate": "2024-11-10", "primaryDocument": "form10q.htm"},
    ])
    assert select_latest_10k(subs) is None


def test_select_latest_10k_skips_no_period_filings():
    """Filings with empty reportDate must be excluded."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "", "filingDate": "2024-03-01", "primaryDocument": "form10k.htm"},
    ])
    assert select_latest_10k(subs) is None


def test_select_latest_10k_fallback_to_original():
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-15", "primaryDocument": "form10k.htm"},
    ])
    result = select_latest_10k(subs)
    assert result is not None
    assert result["form_type"] == "10-K"
    assert result["accession_number"] == "0001-00-000001"


def test_select_latest_10k_prefers_amendment():
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-15", "primaryDocument": "form10k.htm"},
        {"accessionNumber": "0001-00-000002", "form": "10-K/A",
         "reportDate": "2023-12-31", "filingDate": "2024-04-20", "primaryDocument": "form10ka.htm"},
    ])
    result = select_latest_10k(subs)
    assert result is not None
    assert result["form_type"] == "10-K/A"
    assert result["accession_number"] == "0001-00-000002"


def test_select_latest_10k_latest_period_wins():
    """When there are filings from multiple fiscal years, the latest year wins."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2022-12-31", "filingDate": "2023-02-10", "primaryDocument": "2022_10k.htm"},
        {"accessionNumber": "0001-00-000002", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-14", "primaryDocument": "2023_10k.htm"},
    ])
    result = select_latest_10k(subs)
    assert result is not None
    assert result["period_of_report"] == "2023-12-31"
    assert result["accession_number"] == "0001-00-000002"


def test_select_latest_10k_latest_amendment_wins():
    """When there are two 10-K/As for the same period, the most recently filed wins."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-15", "primaryDocument": "10k.htm"},
        {"accessionNumber": "0001-00-000002", "form": "10-K/A",
         "reportDate": "2023-12-31", "filingDate": "2024-04-01", "primaryDocument": "10ka1.htm"},
        {"accessionNumber": "0001-00-000003", "form": "10-K/A",
         "reportDate": "2023-12-31", "filingDate": "2024-05-20", "primaryDocument": "10ka2.htm"},
    ])
    result = select_latest_10k(subs)
    assert result is not None
    assert result["accession_number"] == "0001-00-000003"


def test_select_latest_10k_prefers_amendment_over_older_period():
    """Amendment for latest period beats 10-K for an earlier period."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2022-12-31", "filingDate": "2023-02-10", "primaryDocument": "old10k.htm"},
        {"accessionNumber": "0001-00-000002", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-14", "primaryDocument": "new10k.htm"},
        {"accessionNumber": "0001-00-000003", "form": "10-K/A",
         "reportDate": "2023-12-31", "filingDate": "2024-05-01", "primaryDocument": "new10ka.htm"},
    ])
    result = select_latest_10k(subs)
    assert result["accession_number"] == "0001-00-000003"


def test_select_latest_10k_returns_primary_document():
    subs = _submissions([
        {"accessionNumber": "0001570585-24-000050", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-15",
         "primaryDocument": "aapl-20231230.htm"},
    ])
    result = select_latest_10k(subs)
    assert result["primary_document"] == "aapl-20231230.htm"
    assert result["filing_date"] == "2024-02-15"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def test_primary_document_url():
    url = _primary_document_url("0000320193", "0001570585-24-000050", "aapl-20231230.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193"
        "/000157058524000050/aapl-20231230.htm"
    )


def test_accession_index_url():
    url = _accession_index_url("0000320193", "0001570585-24-000050")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193"
        "/000157058524000050/0001570585-24-000050-index.htm"
    )


# ---------------------------------------------------------------------------
# _store_document
# ---------------------------------------------------------------------------

def test_store_document_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr("moat.ingest.filing_fetcher.FILING_DOCS_DIR", tmp_path)
    content = b"<html><body>10-K content here</body></html>"
    dest = _store_document("0000320193", "0001570585-24-000050", "aapl.htm", content)
    assert dest.exists()
    assert dest.read_bytes() == content
    assert dest.parent.name == "000157058524000050"
    assert dest.parent.parent.name == "CIK0000320193"


def test_store_document_does_not_overwrite(tmp_path, monkeypatch):
    """Second call with different content must not change the file on disk."""
    monkeypatch.setattr("moat.ingest.filing_fetcher.FILING_DOCS_DIR", tmp_path)
    original_content = b"<html>original</html>"
    new_content = b"<html>new content</html>"

    dest1 = _store_document("0000320193", "0001570585-24-000050", "doc.htm", original_content)
    dest2 = _store_document("0000320193", "0001570585-24-000050", "doc.htm", new_content)

    assert dest1 == dest2
    assert dest1.read_bytes() == original_content   # unchanged


def test_cached_filing_fast_path(tmp_path, monkeypatch):
    """_cached_filing returns hit without any network calls when file + hash are valid."""
    from moat.ingest.filing_fetcher import _cached_filing
    import sqlite3, hashlib

    monkeypatch.setattr("moat.ingest.filing_fetcher.FILING_DOCS_DIR", tmp_path)
    content = b"<html><body>" + b"x" * 15_000 + b"</body></html>"
    path = tmp_path / "doc.htm"
    path.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE filings (accession_number TEXT, ticker TEXT, form_type TEXT, "
        "period_of_report TEXT, local_path TEXT, content_hash TEXT)"
    )
    conn.execute(
        "INSERT INTO filings VALUES (?,?,?,?,?,?)",
        ("0001-23-000001", "TST", "10-K", "2023-12-31", str(path), sha),
    )
    conn.commit()

    result = _cached_filing("TST", conn)
    assert result is not None
    assert result[0] == "0001-23-000001"


def test_store_document_sha256_matches(tmp_path, monkeypatch):
    """SHA-256 computed after write must match the original bytes."""
    monkeypatch.setattr("moat.ingest.filing_fetcher.FILING_DOCS_DIR", tmp_path)
    content = b"<html><body>Some 10-K text</body></html>"
    dest = _store_document("0000012345", "0000012345-23-000001", "doc.htm", content)
    stored = dest.read_bytes()
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(content).hexdigest()
