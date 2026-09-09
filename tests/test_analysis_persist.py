"""Tests for W5: persist and cache (moat/analysis/persist.py)."""
import dataclasses
import hashlib
import json
import sqlite3
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from moat.analysis.caller import CallResult
from moat.analysis.parser import ParsedClaim, ParsedResponse, RawCitation
from moat.analysis.persist import (
    _doc_sha256s_for_result,
    compute_bundle_key,
    find_cached_run,
    persist_result,
    run_analysis,
)
from moat.analysis.prompt import ANALYSIS_TYPES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """Return an in-memory-style connection with the minimal schema for W5."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE pipeline_runs (
            run_id TEXT, started_at TEXT, completed_at TEXT,
            stage_reached TEXT, status TEXT, notes TEXT
        );
        CREATE TABLE filing_documents (
            filing_document_id INTEGER PRIMARY KEY,
            accession_number TEXT,
            section_id TEXT,
            norm_version TEXT,
            doc_sha256 TEXT,
            local_path TEXT,
            extraction_method TEXT
        );
        CREATE TABLE filings (
            accession_number TEXT PRIMARY KEY,
            ticker TEXT,
            form_type TEXT,
            filing_date TEXT,
            period_of_report TEXT,
            local_path TEXT
        );
        CREATE TABLE ai_analysis (
            run_id TEXT, ticker TEXT, analysis_type TEXT,
            content TEXT, model TEXT, prompt_version TEXT,
            cache_key TEXT, created_at TEXT,
            is_current INTEGER DEFAULT 1,
            superseded_by_run_id TEXT,
            reused_from_run_id TEXT,
            claim_coverage REAL,
            PRIMARY KEY (run_id, ticker, analysis_type)
        );
        CREATE TABLE analysis_claims (
            claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, ticker TEXT, analysis_type TEXT,
            claim_order INTEGER, claim_text TEXT, assertion_status TEXT
        );
        CREATE TABLE citations (
            citation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER,
            accession_number TEXT, section_id TEXT,
            doc_sha256 TEXT, norm_version TEXT,
            start_char INTEGER, end_char INTEGER,
            quote TEXT, quote_sha256 TEXT,
            prefix TEXT, suffix TEXT,
            created_at TEXT
        );
        CREATE TABLE analysis_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, ticker TEXT,
            batch_id TEXT, custom_id TEXT UNIQUE,
            accession_number TEXT,
            model_id TEXT, prompt_sha256 TEXT,
            protocol_version TEXT, document_map TEXT,
            usage_json TEXT, cost_estimate REAL,
            outcome TEXT, failure_reason TEXT,
            raw_response TEXT, created_at TEXT
        );
    """)
    return conn


def _insert_doc(conn, fdi, doc_sha256, local_path, section_id="full_fallback", accession="0000-00"):
    conn.execute(
        "INSERT INTO filing_documents "
        "(filing_document_id, accession_number, section_id, norm_version, doc_sha256, local_path) "
        "VALUES (?,?,?,?,?,?)",
        (fdi, accession, section_id, "v1", doc_sha256, str(local_path)),
    )


def _make_result(doc_map=None, ticker="TST", accession="0000-00"):
    return CallResult(
        ticker=ticker,
        accession=accession,
        model_id="claude-sonnet-4-6",
        stop_reason="end_turn",
        content_blocks=[{"type": "text", "text": "ok", "citations": None}],
        document_map=doc_map or {0: 1},
        usage={"input_tokens": 1000, "output_tokens": 200},
        prompt_sha256="abc123",
        protocol_version="v1",
        cost_estimate=0.05,
        is_batch=False,
        batch_id=None,
        custom_id=None,
    )


def _make_parsed(ticker="TST", accession="0000-00", claim_coverage=1.0, errors=None):
    cite = RawCitation(
        document_index=0,
        start_char=0,
        end_char=5,
        cited_text="hello",
        accession_number=accession,
        section_id="full_fallback",
        doc_sha256="d_sha",
        norm_version="v1",
        quote_sha256=hashlib.sha256(b"hello").hexdigest(),
        prefix="",
        suffix=" world",
    )
    claims = []
    for i, at in enumerate(ANALYSIS_TYPES):
        claims.append(ParsedClaim(
            analysis_type=at,
            claim_order=1,
            claim_text=f"Claim for {at}",
            assertion_status="asserted",
            citations=[cite],
        ))
    return ParsedResponse(
        ticker=ticker,
        accession=accession,
        claims=claims,
        claim_coverage=claim_coverage,
        validation_errors=errors or [],
    )


# ---------------------------------------------------------------------------
# compute_bundle_key
# ---------------------------------------------------------------------------

class TestComputeBundleKey:
    def test_deterministic(self):
        k1 = compute_bundle_key(["sha_a", "sha_b"], "p_sha", "model", "v1", "v1")
        k2 = compute_bundle_key(["sha_a", "sha_b"], "p_sha", "model", "v1", "v1")
        assert k1 == k2

    def test_doc_order_invariant(self):
        k1 = compute_bundle_key(["sha_a", "sha_b"], "p", "m", "v1", "v1")
        k2 = compute_bundle_key(["sha_b", "sha_a"], "p", "m", "v1", "v1")
        assert k1 == k2

    def test_different_prompt_different_key(self):
        k1 = compute_bundle_key(["sha"], "p1", "m", "v1", "v1")
        k2 = compute_bundle_key(["sha"], "p2", "m", "v1", "v1")
        assert k1 != k2

    def test_different_model_different_key(self):
        k1 = compute_bundle_key(["sha"], "p", "m1", "v1", "v1")
        k2 = compute_bundle_key(["sha"], "p", "m2", "v1", "v1")
        assert k1 != k2

    def test_different_norm_version_different_key(self):
        k1 = compute_bundle_key(["sha"], "p", "m", "v1", "v1")
        k2 = compute_bundle_key(["sha"], "p", "m", "v2", "v1")
        assert k1 != k2

    def test_returns_hex_sha256(self):
        k = compute_bundle_key(["sha"], "p", "m", "v1", "v1")
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)


# ---------------------------------------------------------------------------
# find_cached_run
# ---------------------------------------------------------------------------

class TestFindCachedRun:
    def test_no_rows_returns_none(self):
        conn = _make_db(None)
        assert find_cached_run("TST", "bundle_key", conn) is None

    def test_finds_existing_current_row(self):
        conn = _make_db(None)
        conn.execute(
            "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
            "prompt_version, cache_key, is_current, claim_coverage, created_at) "
            "VALUES ('run1','TST','business_quality','x','m','v1','bk',1,1.0,'2026-01-01')"
        )
        assert find_cached_run("TST", "bk", conn) == "run1"

    def test_superseded_source_is_still_returned(self):
        # After a cache-forward write the original source has is_current=0 but
        # reused_from_run_id IS NULL — it's still the authority for claims.
        conn = _make_db(None)
        conn.execute(
            "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
            "prompt_version, cache_key, is_current, claim_coverage, created_at) "
            "VALUES ('run1','TST','business_quality','x','m','v1','bk',0,1.0,'2026-01-01')"
        )
        assert find_cached_run("TST", "bk", conn) == "run1"

    def test_copy_forward_row_not_returned_as_source(self):
        # A cache-forward copy has reused_from_run_id set — should not be returned
        # as the cache source (the caller would then try to copy it again).
        conn = _make_db(None)
        conn.execute(
            "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
            "prompt_version, cache_key, is_current, reused_from_run_id, claim_coverage, created_at) "
            "VALUES ('run2','TST','business_quality','x','m','v1','bk',1,'run1',1.0,'2026-01-02')"
        )
        assert find_cached_run("TST", "bk", conn) is None

    def test_different_ticker_not_returned(self):
        conn = _make_db(None)
        conn.execute(
            "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
            "prompt_version, cache_key, is_current, claim_coverage, created_at) "
            "VALUES ('run1','OTHER','business_quality','x','m','v1','bk',1,1.0,'2026-01-01')"
        )
        assert find_cached_run("TST", "bk", conn) is None


# ---------------------------------------------------------------------------
# persist_result — valid path
# ---------------------------------------------------------------------------

class TestPersistResultValid:
    def test_writes_four_ai_analysis_rows(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_doc(conn, 1, "doc_sha", tmp_path / "doc.txt")
        (tmp_path / "doc.txt").write_text("hello world extra text for context")

        result = _make_result()
        parsed = _make_parsed()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "run1", conn)

        rows = conn.execute(
            "SELECT analysis_type, content, claim_coverage, is_current "
            "FROM ai_analysis WHERE ticker = 'TST' AND run_id = 'run1'"
        ).fetchall()
        assert len(rows) == 4
        types_written = {r["analysis_type"] for r in rows}
        assert types_written == set(ANALYSIS_TYPES)
        for r in rows:
            assert r["is_current"] == 1
            assert r["claim_coverage"] == 1.0

    def test_writes_analysis_claims(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_doc(conn, 1, "doc_sha", tmp_path / "doc.txt")
        (tmp_path / "doc.txt").write_text("hello world extra text")

        result = _make_result()
        parsed = _make_parsed()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "run1", conn)

        claim_rows = conn.execute("SELECT * FROM analysis_claims").fetchall()
        assert len(claim_rows) == 4  # one per analysis_type

    def test_writes_citations(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_doc(conn, 1, "doc_sha", tmp_path / "doc.txt")
        (tmp_path / "doc.txt").write_text("hello world extra text")

        result = _make_result()
        parsed = _make_parsed()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "run1", conn)

        cite_rows = conn.execute("SELECT * FROM citations").fetchall()
        assert len(cite_rows) == 4  # one citation per claim

    def test_writes_analysis_attempts(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_doc(conn, 1, "doc_sha", tmp_path / "doc.txt")
        (tmp_path / "doc.txt").write_text("hello world extra text")

        result = _make_result()
        parsed = _make_parsed()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "run1", conn)

        attempt = conn.execute("SELECT * FROM analysis_attempts").fetchone()
        assert attempt is not None
        assert attempt["outcome"] == "persisted"
        assert attempt["ticker"] == "TST"

    def test_returns_attempt_id(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_doc(conn, 1, "doc_sha", tmp_path / "doc.txt")
        (tmp_path / "doc.txt").write_text("hello world extra text")

        result = _make_result()
        parsed = _make_parsed()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            attempt_id = persist_result(result, parsed, "run1", conn)

        assert isinstance(attempt_id, int)
        assert attempt_id >= 1


# ---------------------------------------------------------------------------
# persist_result — validation failures
# ---------------------------------------------------------------------------

class TestPersistResultFailure:
    def test_validation_failure_writes_only_attempt(self, tmp_path):
        conn = _make_db(tmp_path)
        result = _make_result()
        parsed = _make_parsed(errors=["missing analysis section: moat"])

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "run1", conn)

        assert conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM analysis_claims").fetchone()[0] == 0
        attempt = conn.execute("SELECT * FROM analysis_attempts").fetchone()
        assert attempt["outcome"] == "validation_failed"
        assert "moat" in attempt["failure_reason"]

    def test_coverage_below_1_writes_only_attempt(self, tmp_path):
        conn = _make_db(tmp_path)
        result = _make_result()
        parsed = _make_parsed(claim_coverage=0.75)

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "run1", conn)

        assert conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0] == 0
        attempt = conn.execute("SELECT * FROM analysis_attempts").fetchone()
        assert attempt["outcome"] == "validation_failed"
        assert "0.750" in attempt["failure_reason"]

    def test_none_parsed_writes_only_attempt(self, tmp_path):
        conn = _make_db(tmp_path)
        result = _make_result()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, None, "run1", conn)

        assert conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0] == 0
        attempt = conn.execute("SELECT * FROM analysis_attempts").fetchone()
        assert attempt["outcome"] == "validation_failed"

    def test_exception_rolls_back(self, tmp_path):
        conn = _make_db(tmp_path)
        result = _make_result()
        parsed = _make_parsed()

        # Force an exception by making _ensure_pipeline_run fail via monkeypatching
        import moat.analysis.persist as mod
        original = mod._ensure_pipeline_run
        def boom(*a, **kw):
            raise RuntimeError("simulated failure")
        mod._ensure_pipeline_run = boom

        try:
            with pytest.raises(RuntimeError):
                with patch("moat.analysis.persist.NORM_VERSION", "v1"):
                    persist_result(result, parsed, "run1", conn)
        finally:
            mod._ensure_pipeline_run = original

        assert conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM analysis_attempts").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# persist_result — cache hit (reused_from_run_id)
# ---------------------------------------------------------------------------

class TestPersistResultCacheHit:
    def test_cache_hit_copies_forward(self, tmp_path):
        conn = _make_db(tmp_path)
        # Write original run rows
        for at in ANALYSIS_TYPES:
            conn.execute(
                "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
                "prompt_version, cache_key, is_current, claim_coverage, created_at) "
                f"VALUES ('orig_run','TST','{at}','content for {at}','m','v1','bk',1,1.0,'2026-01-01')"
            )
        conn.commit()

        result = _make_result()
        parsed = _make_parsed()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "new_run", conn, reused_from_run_id="orig_run")

        new_rows = conn.execute(
            "SELECT * FROM ai_analysis WHERE run_id = 'new_run'"
        ).fetchall()
        assert len(new_rows) == 4
        for r in new_rows:
            assert r["reused_from_run_id"] == "orig_run"
            assert r["is_current"] == 1

    def test_cache_hit_writes_attempt(self, tmp_path):
        conn = _make_db(tmp_path)
        for at in ANALYSIS_TYPES:
            conn.execute(
                "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
                "prompt_version, cache_key, is_current, claim_coverage, created_at) "
                f"VALUES ('orig_run','TST','{at}','x','m','v1','bk',1,1.0,'2026-01-01')"
            )
        conn.commit()

        result = _make_result()
        parsed = _make_parsed()

        with patch("moat.analysis.persist.NORM_VERSION", "v1"):
            persist_result(result, parsed, "new_run", conn, reused_from_run_id="orig_run")

        attempt = conn.execute(
            "SELECT * FROM analysis_attempts WHERE run_id = 'new_run'"
        ).fetchone()
        assert attempt is not None
        assert attempt["outcome"] == "persisted"
        assert "cache_hit" in attempt["failure_reason"]


# ---------------------------------------------------------------------------
# run_analysis — amendment fallback (Sprint 3.1, item 4 test-coverage gap)
#
# Decision 3 (sprint-3-plan.md): prefer the most recent 10-K/A; if its
# section extraction fails, retry with the original 10-K for the same
# period. No test exercised this before Sprint 3.1.
# ---------------------------------------------------------------------------

class TestRunAnalysisAmendmentFallback:
    def _seed_filings(self, conn):
        conn.execute(
            "INSERT INTO filings (accession_number, ticker, form_type, filing_date, "
            "period_of_report, local_path) VALUES (?,?,?,?,?,?)",
            ("0000-02-000002", "TST", "10-K/A", "2024-04-20", "2023-12-31", "amendment.htm"),
        )
        conn.execute(
            "INSERT INTO filings (accession_number, ticker, form_type, filing_date, "
            "period_of_report, local_path) VALUES (?,?,?,?,?,?)",
            ("0000-01-000001", "TST", "10-K", "2024-02-15", "2023-12-31", "original.htm"),
        )
        conn.commit()

    def test_falls_back_to_original_10k_when_amendment_extraction_fails(self, tmp_path):
        conn = _make_db(tmp_path)
        self._seed_filings(conn)

        # Amendment's own filing_documents row is absent and its on-disk file
        # (patched in via filings.local_path -> a real short file) is too
        # short to be a usable full_fallback -> prepare_sections() raises for
        # accession 0000-02-000002. The original 10-K already has usable
        # sections cached, so run_analysis() must retry with it instead of
        # failing the ticker outright.
        amendment_path = tmp_path / "amendment.htm"
        amendment_path.write_text("<html><body>Part III only, nothing else here.</body></html>")
        conn.execute(
            "UPDATE filings SET local_path = ? WHERE accession_number = '0000-02-000002'",
            (str(amendment_path),),
        )
        conn.commit()

        for fdi, section_id in enumerate(("item_1", "item_1a", "item_7"), start=1):
            path = tmp_path / f"{section_id}.txt"
            path.write_text(f"{section_id} content, plenty of words to be non-trivial.")
            _insert_doc(
                conn, fdi, hashlib.sha256(path.read_bytes()).hexdigest(), path,
                section_id=section_id, accession="0000-01-000001",
            )
        conn.commit()

        client = MagicMock()
        client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=123)

        with patch("moat.analysis.persist.NORM_VERSION", "v1"), \
             patch("moat.analysis.caller.NORM_VERSION", "v1"):
            result = run_analysis("TST", "run1", client, conn, dry_run=True)

        assert result["outcome"] == "dry_run"
        # Confirms extraction actually happened against the fallback filing.
        client.messages.count_tokens.assert_called_once()
