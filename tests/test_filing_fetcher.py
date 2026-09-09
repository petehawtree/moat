"""Tests for W1: select_latest_10k and supporting pure functions.

All tests here are offline (no network). Network-touching functions
(fetch_filing_document, run_for_ticker) require a live EDGAR endpoint
and are not tested here.
"""
import hashlib
import sqlite3
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
    assert select_latest_10k({}) == (None, None)
    assert select_latest_10k({"filings": {}}) == (None, None)
    assert select_latest_10k({"filings": {"recent": {}}}) == (None, None)


def test_select_latest_10k_no_qualifying_forms():
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-Q",
         "reportDate": "2024-09-30", "filingDate": "2024-11-10", "primaryDocument": "form10q.htm"},
    ])
    primary, fallback = select_latest_10k(subs)
    assert primary is None
    assert fallback is None


def test_select_latest_10k_skips_no_period_filings():
    """Filings with empty reportDate must be excluded."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "", "filingDate": "2024-03-01", "primaryDocument": "form10k.htm"},
    ])
    primary, fallback = select_latest_10k(subs)
    assert primary is None
    assert fallback is None


def test_select_latest_10k_original_only():
    """When only an original 10-K exists, fallback is None."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-15", "primaryDocument": "form10k.htm"},
    ])
    primary, fallback = select_latest_10k(subs)
    assert primary is not None
    assert primary["form_type"] == "10-K"
    assert primary["accession_number"] == "0001-00-000001"
    assert fallback is None


def test_select_latest_10k_prefers_amendment():
    """Primary is the 10-K/A; fallback is the original 10-K for the same period."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-15", "primaryDocument": "form10k.htm"},
        {"accessionNumber": "0001-00-000002", "form": "10-K/A",
         "reportDate": "2023-12-31", "filingDate": "2024-04-20", "primaryDocument": "form10ka.htm"},
    ])
    primary, fallback = select_latest_10k(subs)
    assert primary is not None
    assert primary["form_type"] == "10-K/A"
    assert primary["accession_number"] == "0001-00-000002"
    assert fallback is not None
    assert fallback["form_type"] == "10-K"
    assert fallback["accession_number"] == "0001-00-000001"


def test_select_latest_10k_latest_period_wins():
    """When there are filings from multiple fiscal years, the latest year wins."""
    subs = _submissions([
        {"accessionNumber": "0001-00-000001", "form": "10-K",
         "reportDate": "2022-12-31", "filingDate": "2023-02-10", "primaryDocument": "2022_10k.htm"},
        {"accessionNumber": "0001-00-000002", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-14", "primaryDocument": "2023_10k.htm"},
    ])
    primary, fallback = select_latest_10k(subs)
    assert primary is not None
    assert primary["period_of_report"] == "2023-12-31"
    assert primary["accession_number"] == "0001-00-000002"
    assert fallback is None  # no amendment, so no fallback


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
    primary, fallback = select_latest_10k(subs)
    assert primary is not None
    assert primary["accession_number"] == "0001-00-000003"
    assert fallback is not None
    assert fallback["form_type"] == "10-K"


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
    primary, fallback = select_latest_10k(subs)
    assert primary["accession_number"] == "0001-00-000003"
    assert fallback["accession_number"] == "0001-00-000002"


def test_select_latest_10k_returns_primary_document():
    subs = _submissions([
        {"accessionNumber": "0001570585-24-000050", "form": "10-K",
         "reportDate": "2023-12-31", "filingDate": "2024-02-15",
         "primaryDocument": "aapl-20231230.htm"},
    ])
    primary, fallback = select_latest_10k(subs)
    assert primary["primary_document"] == "aapl-20231230.htm"
    assert primary["filing_date"] == "2024-02-15"
    assert fallback is None


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


# ---------------------------------------------------------------------------
# run_for_ticker — Sprint 3.1 freshness fix
#
# Non-offline runs must always check SEC's submissions before trusting a
# cached filing, and skip the document download only when the cached
# accession matches what SEC currently reports as latest.
# ---------------------------------------------------------------------------

def _filings_conn():
    """In-memory conn with the columns run_for_ticker's SQL actually touches."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE filings (
            accession_number    TEXT PRIMARY KEY,
            ticker              TEXT NOT NULL,
            form_type           TEXT NOT NULL,
            filing_date         TEXT NOT NULL,
            period_of_report    TEXT,
            document_url        TEXT NOT NULL,
            primary_document_url TEXT,
            local_path          TEXT,
            content_hash        TEXT,
            retrieved_at        TEXT NOT NULL
        );
        """
    )
    return conn


def _insert_cached_filing(conn, tmp_path, accession, ticker="TST", content=None):
    """Write a verified on-disk cache entry for `accession` and its filings row."""
    content = content or (b"<html><body>" + b"x" * 15_000 + b"</body></html>")
    path = tmp_path / f"{accession}.htm"
    path.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    conn.execute(
        "INSERT INTO filings (accession_number, ticker, form_type, filing_date, "
        "period_of_report, document_url, local_path, content_hash, retrieved_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (accession, ticker, "10-K", "2024-02-15", "2023-12-31",
         "https://example.com/index.htm", str(path), sha, "2024-02-16T00:00:00Z"),
    )
    conn.commit()
    return path, sha


def test_run_for_ticker_offline_no_cache_is_an_error():
    from moat.ingest.filing_fetcher import run_for_ticker

    conn = _filings_conn()
    accession, err = run_for_ticker("TST", conn, offline=True)
    assert accession is None
    assert "offline" in err


def test_run_for_ticker_offline_uses_cache_with_zero_network(tmp_path, monkeypatch):
    """offline=True must never call fetch_submissions or the document fetcher."""
    from moat.ingest.filing_fetcher import run_for_ticker

    def _boom(*a, **kw):
        raise AssertionError("offline run must not touch the network")

    monkeypatch.setattr("moat.ingest.filing_fetcher.fetch_submissions", _boom)
    monkeypatch.setattr("moat.ingest.filing_fetcher.fetch_filing_document", _boom)
    monkeypatch.setattr("moat.ingest.filing_fetcher.lookup_cik", _boom)

    conn = _filings_conn()
    _insert_cached_filing(conn, tmp_path, "0001-24-000001")

    accession, err = run_for_ticker("TST", conn, offline=True)
    assert accession == "0001-24-000001"
    assert err is None


def test_run_for_ticker_non_offline_skips_download_when_accession_matches(tmp_path, monkeypatch):
    """Cached accession == SEC's latest -> submissions is checked, download is not."""
    from moat.ingest.filing_fetcher import run_for_ticker

    conn = _filings_conn()
    _insert_cached_filing(conn, tmp_path, "0001-24-000001")

    monkeypatch.setattr("moat.ingest.filing_fetcher.lookup_cik", lambda ticker: "0000320193")
    monkeypatch.setattr(
        "moat.ingest.filing_fetcher.fetch_submissions",
        lambda cik: {"filings": {"recent": {}}},  # select_latest_10k is patched below, so content doesn't matter
    )
    monkeypatch.setattr(
        "moat.ingest.filing_fetcher.select_latest_10k",
        lambda submissions: (
            {"accession_number": "0001-24-000001", "form_type": "10-K",
             "period_of_report": "2023-12-31", "filing_date": "2024-02-15",
             "primary_document": "tst.htm"},
            None,
        ),
    )

    def _boom(*a, **kw):
        raise AssertionError("accession matches cache — must not re-download")

    monkeypatch.setattr("moat.ingest.filing_fetcher.fetch_filing_document", _boom)

    accession, err = run_for_ticker("TST", conn, offline=False)
    assert accession == "0001-24-000001"
    assert err is None


def test_run_for_ticker_non_offline_refetches_on_new_accession(tmp_path, monkeypatch):
    """This is the freshness bug itself: SEC reports a newer accession than the
    cache holds (a new 10-K/A filed since the initial fetch). The stale cache
    must not be trusted — a fresh download must happen and overwrite it."""
    from moat.ingest.filing_fetcher import run_for_ticker

    conn = _filings_conn()
    _insert_cached_filing(conn, tmp_path, "0001-24-000001")  # stale — an amendment supersedes it
    monkeypatch.setattr("moat.ingest.filing_fetcher.FILING_DOCS_DIR", tmp_path)

    monkeypatch.setattr("moat.ingest.filing_fetcher.lookup_cik", lambda ticker: "0000320193")
    monkeypatch.setattr(
        "moat.ingest.filing_fetcher.fetch_submissions",
        lambda cik: {"filings": {"recent": {}}},
    )
    monkeypatch.setattr(
        "moat.ingest.filing_fetcher.select_latest_10k",
        lambda submissions: (
            {"accession_number": "0002-24-000009", "form_type": "10-K/A",
             "period_of_report": "2023-12-31", "filing_date": "2024-04-20",
             "primary_document": "tst_a.htm"},
            None,
        ),
    )

    new_content = b"<html><body>" + b"y" * 15_000 + b"</body></html>"
    fetch_calls = []

    def _fake_fetch(cik, accession, primary_doc):
        fetch_calls.append(accession)
        return new_content, f"https://example.com/{accession}/{primary_doc}"

    monkeypatch.setattr("moat.ingest.filing_fetcher.fetch_filing_document", _fake_fetch)

    accession, err = run_for_ticker("TST", conn, offline=False)
    assert err is None
    assert accession == "0002-24-000009"
    assert fetch_calls == ["0002-24-000009"]

    row = conn.execute(
        "SELECT local_path, content_hash FROM filings WHERE accession_number = ?",
        (accession,),
    ).fetchone()
    assert row is not None
    assert Path(row["local_path"]).read_bytes() == new_content
    assert row["content_hash"] == hashlib.sha256(new_content).hexdigest()


def test_run_for_ticker_non_offline_fetches_when_cache_row_absent(tmp_path, monkeypatch):
    """No cache at all -> fetches and stores, same as a first run."""
    from moat.ingest.filing_fetcher import run_for_ticker

    conn = _filings_conn()
    monkeypatch.setattr("moat.ingest.filing_fetcher.FILING_DOCS_DIR", tmp_path)
    monkeypatch.setattr("moat.ingest.filing_fetcher.lookup_cik", lambda ticker: "0000320193")
    monkeypatch.setattr(
        "moat.ingest.filing_fetcher.fetch_submissions",
        lambda cik: {"filings": {"recent": {}}},
    )
    monkeypatch.setattr(
        "moat.ingest.filing_fetcher.select_latest_10k",
        lambda submissions: (
            {"accession_number": "0001-24-000001", "form_type": "10-K",
             "period_of_report": "2023-12-31", "filing_date": "2024-02-15",
             "primary_document": "tst.htm"},
            None,
        ),
    )
    content = b"<html><body>" + b"z" * 15_000 + b"</body></html>"
    monkeypatch.setattr(
        "moat.ingest.filing_fetcher.fetch_filing_document",
        lambda cik, accession, primary_doc: (content, "https://example.com/tst.htm"),
    )

    accession, err = run_for_ticker("TST", conn, offline=False)
    assert err is None
    assert accession == "0001-24-000001"
    row = conn.execute(
        "SELECT local_path FROM filings WHERE accession_number = ?", (accession,),
    ).fetchone()
    assert Path(row["local_path"]).exists()
