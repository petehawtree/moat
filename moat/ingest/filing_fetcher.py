"""Fetch 10-K / 10-K/A primary documents from SEC EDGAR (W1, Sprint 3).

_get_json() (fundamentals_edgar) returns resp.json() and cannot fetch HTML.
This module adds _get_bytes() with the same retry / User-Agent / rate-limit
contract, then builds the selection and storage logic on top.

W1 acceptance (sprint-3-plan.md):
- Retrieval validated on HTTP status, minimum length, and parseability.
- Source URL, CIK and retrieval time recorded on filings rows.
- filings.local_path / content_hash populated as the raw receipt.
- A second run re-reads bytes from disk without a network request.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from moat.config import DATA_DIR, SEC_USER_AGENT
from moat.ingest.fundamentals_edgar import TransientSecError, _get_json, lookup_cik

# Directory for immutable raw HTML receipts — separate from the XBRL companyfacts
# namespace (data/filings/CIK*.json).
FILING_DOCS_DIR = DATA_DIR / "filing_docs"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_REQUEST_DELAY_SECONDS = 0.15   # mirrors fundamentals_edgar; SEC fair-use rate
_DOWNLOAD_TIMEOUT_SECS = 90     # large 10-Ks can be several MB
_MIN_BYTES = 10_000             # below this the response is almost certainly an error page


# ---------------------------------------------------------------------------
# Low-level fetch (bytes)
# ---------------------------------------------------------------------------

@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception_type(TransientSecError),
)
def _get_bytes(url: str) -> bytes:
    """Fetch raw bytes from a URL. Retries on 429 / 5xx / network error."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=_DOWNLOAD_TIMEOUT_SECS,
        )
    except requests.RequestException as exc:
        raise TransientSecError(str(exc)) from exc
    finally:
        time.sleep(_REQUEST_DELAY_SECONDS)

    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientSecError(f"HTTP {resp.status_code} from {url}")
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# Filing selection
# ---------------------------------------------------------------------------

def fetch_submissions(cik: str) -> dict:
    """Fetch the SEC submissions JSON for a CIK.

    Unlike companyfacts, this payload covers all form types and includes the
    primaryDocument filename for each filing — needed to locate the HTML file
    and to surface 10-K/A amendments.
    """
    return _get_json(SUBMISSIONS_URL.format(cik=cik))


def select_latest_10k(submissions: dict) -> dict | None:
    """Find the best 10-K or 10-K/A to analyse.

    Decision 3 (sprint-3-plan.md): for the latest period, prefer the most
    recent 10-K/A amendment; fall back to the original 10-K if none exists.

    Returns a dict with keys: accession_number, form_type, filing_date,
    period_of_report, primary_document.
    Returns None when no qualifying filing is found.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    keys = ("accessionNumber", "form", "reportDate", "filingDate", "primaryDocument")
    arrays = [recent.get(k, []) for k in keys]

    candidates = [
        {
            "accession_number": a,
            "form_type": f,
            "period_of_report": r,
            "filing_date": d,
            "primary_document": p,
        }
        for a, f, r, d, p in zip(*arrays)
        if f in ("10-K", "10-K/A") and r  # exclude filings with no period date
    ]

    if not candidates:
        return None

    by_period: dict[str, list[dict]] = {}
    for c in candidates:
        by_period.setdefault(c["period_of_report"], []).append(c)

    latest_period = max(by_period)
    group = sorted(by_period[latest_period], key=lambda f: f["filing_date"], reverse=True)

    amendments = [f for f in group if f["form_type"] == "10-K/A"]
    originals  = [f for f in group if f["form_type"] == "10-K"]
    return amendments[0] if amendments else (originals[0] if originals else None)


# ---------------------------------------------------------------------------
# Fetch, validate, store
# ---------------------------------------------------------------------------

def _primary_document_url(cik: str, accession: str, primary_doc: str) -> str:
    accn_clean = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
        f"/{accn_clean}/{primary_doc}"
    )


def _accession_index_url(cik: str, accession: str) -> str:
    accn_clean = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
        f"/{accn_clean}/{accession}-index.htm"
    )


def fetch_filing_document(cik: str, accession: str, primary_doc: str) -> tuple[bytes, str]:
    """Fetch and validate the primary HTML document.

    Validates HTTP status (raised by _get_bytes), minimum byte length, and
    parseability (BeautifulSoup produces non-empty text).

    Returns (raw_bytes, source_url).
    Raises ValueError on validation failure.
    """
    url = _primary_document_url(cik, accession, primary_doc)
    content = _get_bytes(url)

    if len(content) < _MIN_BYTES:
        raise ValueError(
            f"content too short ({len(content)} bytes, minimum {_MIN_BYTES}): {url}"
        )

    try:
        text_sample = BeautifulSoup(content, "html.parser").get_text(strip=True)[:200]
    except Exception as exc:
        raise ValueError(f"unparseable content from {url}: {exc}") from exc

    if not text_sample.strip():
        raise ValueError(f"no text extracted from {url}")

    return content, url


def _store_document(cik: str, accession: str, primary_doc: str, content: bytes) -> Path:
    """Write raw bytes to an immutable on-disk receipt. Never overwrites an existing file."""
    accn_clean = accession.replace("-", "")
    dest = FILING_DOCS_DIR / f"CIK{cik}" / accn_clean / primary_doc
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(content)
    return dest


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _cached_filing(ticker: str, conn) -> tuple[str, str] | None:
    """Return (accession_number, local_path) if a verified cache entry exists.

    Checks: row has local_path + content_hash, file is present, SHA-256 matches.
    Returns None on any miss so the caller falls through to a live fetch.
    """
    row = conn.execute(
        "SELECT accession_number, local_path, content_hash FROM filings "
        "WHERE ticker = ? AND form_type IN ('10-K', '10-K/A') "
        "  AND local_path IS NOT NULL AND content_hash IS NOT NULL "
        "ORDER BY period_of_report DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if not row:
        return None
    path = Path(row["local_path"])
    if not path.exists():
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["content_hash"]:
        return None
    return row["accession_number"], row["local_path"]


def run_for_ticker(
    ticker: str,
    conn,
    offline: bool = False,
) -> tuple[str | None, str | None]:
    """Fetch and cache the primary 10-K document for a ticker.

    Returns (accession_number, error_or_None).

    On success: filings.local_path, content_hash, and primary_document_url
    are populated. A second call with an unchanged filing returns immediately
    from the on-disk cache with no network request.

    offline=True: reuse whatever is already on disk; do not contact SEC.
    """
    # Fast path: verified cache entry → no network calls at all.
    cached = _cached_filing(ticker, conn)
    if cached and not offline:
        return cached[0], None

    if offline:
        if cached:
            return cached[0], None
        return None, f"offline: no cached filing for {ticker}"

    cik = lookup_cik(ticker)
    if cik is None:
        return None, f"no CIK found for {ticker}"

    submissions = fetch_submissions(cik)
    if not submissions:
        return None, f"no submissions at SEC EDGAR for CIK {cik}"

    filing = select_latest_10k(submissions)
    if filing is None:
        return None, "no 10-K or 10-K/A in submissions"

    accession   = filing["accession_number"]
    primary_doc = filing["primary_document"]

    if not primary_doc:
        return None, f"primaryDocument empty for {accession}"

    # Fetch, validate, store
    try:
        content, source_url = fetch_filing_document(cik, accession, primary_doc)
    except (ValueError, TransientSecError) as exc:
        return None, str(exc)

    local_path   = _store_document(cik, accession, primary_doc, content)
    content_hash = hashlib.sha256(content).hexdigest()
    now          = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO filings (
            accession_number, ticker, form_type, filing_date, period_of_report,
            document_url, primary_document_url, local_path, content_hash, retrieved_at
        ) VALUES (
            :accession_number, :ticker, :form_type, :filing_date, :period_of_report,
            :document_url, :primary_document_url, :local_path, :content_hash, :retrieved_at
        )
        ON CONFLICT(accession_number) DO UPDATE SET
            primary_document_url = excluded.primary_document_url,
            local_path           = excluded.local_path,
            content_hash         = excluded.content_hash,
            retrieved_at         = excluded.retrieved_at
        """,
        {
            "accession_number":    accession,
            "ticker":              ticker,
            "form_type":           filing["form_type"],
            "filing_date":         filing["filing_date"],
            "period_of_report":    filing["period_of_report"],
            "document_url":        _accession_index_url(cik, accession),
            "primary_document_url": source_url,
            "local_path":          str(local_path),
            "content_hash":        content_hash,
            "retrieved_at":        now,
        },
    )
    conn.commit()
    return accession, None
