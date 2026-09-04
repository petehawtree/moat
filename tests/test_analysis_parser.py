"""Tests for W4: parse, resolve and validate.

W4 acceptance (sprint-3-plan.md):
  - A multi-claim block, an uncited connective assertion, and an
    insufficient-evidence outcome each parse correctly.
  - A one-character mismatch and a mis-mapped document index each fail
    with no database writes.
  - Coverage = asserted claims cited ÷ asserted claims.
"""
import dataclasses
import hashlib
import sqlite3
import tempfile
from pathlib import Path

import pytest

from moat.analysis.parser import (
    ParsedClaim,
    RawCitation,
    _compute_coverage,
    _parse_claims,
    _reconstruct_stream,
    parse_and_validate,
    resolve_citations,
)
from moat.analysis.caller import CallResult
from moat.analysis.prompt import PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _block(text: str, citations=None) -> dict:
    return {"type": "text", "text": text, "citations": citations}


def _cite(doc_idx: int, start: int, end: int, cited_text: str) -> dict:
    return {
        "type": "char_location",
        "document_index": doc_idx,
        "start_char_index": start,
        "end_char_index": end,
        "cited_text": cited_text,
    }


def _make_result(content_blocks: list[dict], document_map=None) -> CallResult:
    return CallResult(
        ticker="TST",
        accession="0001-23-000001",
        model_id="claude-sonnet-4-6",
        stop_reason="end_turn",
        content_blocks=content_blocks,
        document_map=document_map or {0: 1},
        usage={"input_tokens": 1000, "output_tokens": 200},
        prompt_sha256="abc",
        protocol_version=PROTOCOL_VERSION,
        cost_estimate=0.01,
    )


def _in_memory_conn_with_section(text: str, filing_document_id: int = 1) -> tuple:
    """Return (conn, tmp_path) with one filing_documents row pointing to a temp file."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(text)
    tmp.close()

    sha = hashlib.sha256(text.encode()).hexdigest()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE filing_documents (
            filing_document_id INTEGER PRIMARY KEY,
            accession_number TEXT,
            section_id TEXT,
            norm_version TEXT,
            doc_sha256 TEXT,
            local_path TEXT
        )
    """)
    conn.execute(
        "INSERT INTO filing_documents VALUES (?,?,?,?,?,?)",
        (filing_document_id, "0001-23-000001", "full", "v1", sha, tmp.name),
    )
    conn.commit()
    return conn, Path(tmp.name)


# ---------------------------------------------------------------------------
# _reconstruct_stream
# ---------------------------------------------------------------------------

def test_reconstruct_stream_merges_citationless():
    blocks = [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("some claim text", [_cite(0, 0, 4, "some")]),
        _block("\n\nCLAIM: "),
        _block("another claim", [_cite(0, 5, 12, "another")]),
    ]
    stream = _reconstruct_stream(blocks)
    assert len(stream) == 4


def test_reconstruct_stream_skips_non_text_blocks():
    blocks = [
        {"type": "thinking", "thinking": "internal"},
        _block("hello"),
    ]
    stream = _reconstruct_stream(blocks)
    assert len(stream) == 1
    assert stream[0][0] == "hello"


# ---------------------------------------------------------------------------
# _parse_claims — fixture cases from the W4 spec
# ---------------------------------------------------------------------------

def _standard_blocks() -> list[dict]:
    """Minimal well-formed four-section response."""
    return [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("Revenue grew 10% to $100B.", [_cite(0, 0, 10, "Revenue gr")]),
        _block("\n\nCLAIM: "),
        _block("Gross margin expanded to 45%.", [_cite(0, 20, 30, "Gross marg")]),
        _block("\n\nINSUFFICIENT EVIDENCE: Segment-level breakdown not disclosed.\n\n"),
        _block("## MOAT\n\nCLAIM: "),
        _block("Ecosystem switching costs are high.", [_cite(0, 50, 60, "Ecosystem ")]),
        _block("\n\n## MANAGEMENT\nNOTE: 10-K only.\n\nCLAIM: "),
        _block("R&D spend rose 8% to $34B.", [_cite(0, 70, 80, "R&D spend ")]),
        _block("\n\n## RISK\n\nCLAIM: "),
        _block("Tariff exposure in China is material.", [_cite(0, 90, 100, "Tariff exp")]),
    ]


def test_parse_claims_finds_all_four_sections():
    claims = _parse_claims(_reconstruct_stream(_standard_blocks()))
    found = {c.analysis_type for c in claims}
    assert found == {"business_quality", "moat", "management", "risk"}


def test_parse_claims_insufficient_evidence_no_citations():
    """INSUFFICIENT EVIDENCE entries must parse as insufficient_evidence with no citations."""
    claims = _parse_claims(_reconstruct_stream(_standard_blocks()))
    ie = [c for c in claims if c.assertion_status == "insufficient_evidence"]
    assert ie, "expected at least one insufficient_evidence claim"
    for c in ie:
        assert c.citations == []


def test_parse_claims_asserted_claims_have_citations():
    claims = _parse_claims(_reconstruct_stream(_standard_blocks()))
    asserted = [c for c in claims if c.assertion_status == "asserted"]
    assert asserted
    for c in asserted:
        assert c.citations, f"asserted claim has no citations: {c.claim_text!r}"


def test_parse_claims_claim_order_per_section():
    claims = _parse_claims(_reconstruct_stream(_standard_blocks()))
    bq = sorted(
        [c for c in claims if c.analysis_type == "business_quality"],
        key=lambda c: c.claim_order,
    )
    assert [c.claim_order for c in bq] == list(range(1, len(bq) + 1))


def test_parse_multi_claim_block():
    """Multiple CLAIM: items in one analysis section."""
    blocks = [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("Claim A.", [_cite(0, 0, 5, "Claim")]),
        _block("\n\nCLAIM: "),
        _block("Claim B.", [_cite(0, 10, 15, "Claim")]),
        _block("\n\nCLAIM: "),
        _block("Claim C.", [_cite(0, 20, 25, "Claim")]),
        _block("\n\n## MOAT\n\nCLAIM: "),
        _block("Moat claim.", [_cite(0, 30, 35, "Moat c")]),
        _block("\n\n## MANAGEMENT\n\nCLAIM: "),
        _block("Mgmt claim.", [_cite(0, 40, 45, "Mgmt c")]),
        _block("\n\n## RISK\n\nCLAIM: "),
        _block("Risk claim.", [_cite(0, 50, 55, "Risk c")]),
    ]
    claims = _parse_claims(_reconstruct_stream(blocks))
    bq = [c for c in claims if c.analysis_type == "business_quality"]
    assert len(bq) == 3
    assert [c.claim_text for c in sorted(bq, key=lambda c: c.claim_order)] == [
        "Claim A.", "Claim B.", "Claim C."
    ]


def test_parse_uncited_connective_ignored():
    """A connector block with text but no citations does not become a claim."""
    blocks = [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("Real claim.", [_cite(0, 0, 4, "Real")]),
        _block("\n\nSome narrative prose without a citation or CLAIM prefix.\n\n"),
        _block("## MOAT\n\nCLAIM: "),
        _block("Moat claim.", [_cite(0, 0, 4, "Moat")]),
        _block("\n\n## MANAGEMENT\n\nCLAIM: "),
        _block("Mgmt claim.", [_cite(0, 0, 4, "Mgmt")]),
        _block("\n\n## RISK\n\nCLAIM: "),
        _block("Risk claim.", [_cite(0, 0, 4, "Risk")]),
    ]
    claims = _parse_claims(_reconstruct_stream(blocks))
    bq = [c for c in claims if c.analysis_type == "business_quality"]
    assert len(bq) == 1
    assert bq[0].claim_text == "Real claim."


# ---------------------------------------------------------------------------
# resolve_citations
# ---------------------------------------------------------------------------

def test_resolve_citations_byte_equality_pass():
    doc_text = "Hello world, this is the filing text for testing purposes."
    conn, tmp = _in_memory_conn_with_section(doc_text)

    claim = ParsedClaim(
        analysis_type="business_quality",
        claim_order=1,
        claim_text="Hello world.",
        assertion_status="asserted",
        citations=[RawCitation(
            document_index=0,
            start_char=0,
            end_char=11,
            cited_text="Hello world",
        )],
    )
    errors = resolve_citations([claim], {0: 1}, conn)
    assert errors == []
    assert claim.citations[0].accession_number == "0001-23-000001"
    assert claim.citations[0].quote_sha256 == hashlib.sha256(b"Hello world").hexdigest()
    tmp.unlink()


def test_resolve_citations_byte_equality_fail():
    """A one-character mismatch must produce a validation error."""
    doc_text = "Hello world, this is the filing text."
    conn, tmp = _in_memory_conn_with_section(doc_text)

    claim = ParsedClaim(
        analysis_type="business_quality",
        claim_order=1,
        claim_text="Hello world.",
        assertion_status="asserted",
        citations=[RawCitation(
            document_index=0,
            start_char=0,
            end_char=11,
            cited_text="Hello WORLD",   # mismatch: capital WORLD
        )],
    )
    errors = resolve_citations([claim], {0: 1}, conn)
    assert any("byte-equality" in e for e in errors)
    tmp.unlink()


def test_resolve_citations_wrong_document_index():
    """A document_index absent from document_map must produce a validation error."""
    doc_text = "Some filing text here."
    conn, tmp = _in_memory_conn_with_section(doc_text)

    claim = ParsedClaim(
        analysis_type="moat",
        claim_order=1,
        claim_text="Claim text.",
        assertion_status="asserted",
        citations=[RawCitation(
            document_index=99,   # not in document_map
            start_char=0,
            end_char=4,
            cited_text="Some",
        )],
    )
    errors = resolve_citations([claim], {0: 1}, conn)
    assert any("document_index 99" in e for e in errors)
    tmp.unlink()


def test_resolve_citations_prefix_suffix_populated():
    doc_text = "The quick brown fox jumps over the lazy dog."
    conn, tmp = _in_memory_conn_with_section(doc_text)

    claim = ParsedClaim(
        analysis_type="risk",
        claim_order=1,
        claim_text="brown fox",
        assertion_status="asserted",
        citations=[RawCitation(
            document_index=0,
            start_char=10,
            end_char=19,
            cited_text="brown fox",
        )],
    )
    resolve_citations([claim], {0: 1}, conn)
    c = claim.citations[0]
    assert c.prefix == "The quick "
    assert c.suffix == " jumps over the lazy dog."
    tmp.unlink()


# ---------------------------------------------------------------------------
# _compute_coverage
# ---------------------------------------------------------------------------

def test_coverage_all_cited():
    claims = [
        ParsedClaim("business_quality", 1, "A", "asserted",
                    [RawCitation(0, 0, 1, "x")]),
        ParsedClaim("moat", 1, "B", "asserted",
                    [RawCitation(0, 1, 2, "y")]),
    ]
    assert _compute_coverage(claims) == 1.0


def test_coverage_partial():
    claims = [
        ParsedClaim("business_quality", 1, "A", "asserted",
                    [RawCitation(0, 0, 1, "x")]),
        ParsedClaim("moat", 1, "B", "asserted", []),  # uncited
    ]
    assert _compute_coverage(claims) == 0.5


def test_coverage_ie_claims_excluded():
    """INSUFFICIENT EVIDENCE claims do not count toward the denominator."""
    claims = [
        ParsedClaim("business_quality", 1, "A", "asserted",
                    [RawCitation(0, 0, 1, "x")]),
        ParsedClaim("business_quality", 2, "B", "insufficient_evidence", []),
    ]
    assert _compute_coverage(claims) == 1.0


def test_coverage_no_asserted_claims():
    claims = [
        ParsedClaim("business_quality", 1, "A", "insufficient_evidence", []),
    ]
    assert _compute_coverage(claims) == 1.0


# ---------------------------------------------------------------------------
# parse_and_validate — integration
# ---------------------------------------------------------------------------

def test_parse_and_validate_valid_response():
    # Offsets must match exactly (byte-equality check).
    # "Revenue grew.  " = 15 chars [0:15]
    # "Ecosystem high." = 15 chars [15:30]
    # "R&D spend up.  " = 15 chars [30:45]
    # "Tariff risk.   " = 15 chars [45:60]
    doc_text = (
        "Revenue grew.  "
        "Ecosystem high."
        "R&D spend up.  "
        "Tariff risk.   "
    )
    conn, tmp = _in_memory_conn_with_section(doc_text)

    blocks = [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("Revenue grew.", [_cite(0, 0, 13, "Revenue grew.")]),
        _block("\n\n## MOAT\n\nCLAIM: "),
        _block("Ecosystem high.", [_cite(0, 15, 30, "Ecosystem high.")]),
        _block("\n\n## MANAGEMENT\nNOTE: 10-K only.\n\nCLAIM: "),
        _block("R&D spend up.", [_cite(0, 30, 43, "R&D spend up.")]),
        _block("\n\n## RISK\n\nCLAIM: "),
        _block("Tariff risk.", [_cite(0, 45, 57, "Tariff risk.")]),
    ]
    result = _make_result(blocks)
    parsed = parse_and_validate(result, conn)
    assert parsed.is_valid, parsed.validation_errors
    assert parsed.claim_coverage == 1.0
    tmp.unlink()


def test_parse_and_validate_missing_section_is_invalid():
    doc_text = "Revenue grew.  Ecosystem high."
    conn, tmp = _in_memory_conn_with_section(doc_text)

    blocks = [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("Revenue grew.", [_cite(0, 0, 13, "Revenue grew.")]),
        # MOAT, MANAGEMENT, RISK missing
    ]
    result = _make_result(blocks)
    parsed = parse_and_validate(result, conn)
    assert not parsed.is_valid
    missing = [e for e in parsed.validation_errors if "missing analysis section" in e]
    assert len(missing) == 3
    tmp.unlink()


def test_parse_and_validate_uncited_asserted_claim_is_invalid():
    # "Revenue grew.  " [0:15], "Ecosystem high." [15:30],
    # "R&D spend up. " [30:44], "Tariff risk.  " [44:56]
    doc_text = "Revenue grew.  Ecosystem high.R&D spend up. Tariff risk.  "
    conn, tmp = _in_memory_conn_with_section(doc_text)

    blocks = [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("Revenue grew.", [_cite(0, 0, 13, "Revenue grew.")]),
        _block("\n\nCLAIM: "),
        _block("Uncited assertion with no citation."),  # no citations key at all
        _block("\n\n## MOAT\n\nCLAIM: "),
        _block("Ecosystem high.", [_cite(0, 15, 30, "Ecosystem high.")]),
        _block("\n\n## MANAGEMENT\n\nCLAIM: "),
        _block("R&D spend up.", [_cite(0, 30, 43, "R&D spend up.")]),
        _block("\n\n## RISK\n\nCLAIM: "),
        _block("Tariff risk.", [_cite(0, 44, 56, "Tariff risk.")]),
    ]
    result = _make_result(blocks)
    parsed = parse_and_validate(result, conn)
    assert not parsed.is_valid
    assert any("without citation" in e for e in parsed.validation_errors)
    assert parsed.claim_coverage < 1.0
    tmp.unlink()


def test_parse_and_validate_byte_mismatch_no_db_writes(tmp_path):
    """A byte-equality failure must surface as a validation error before any DB write."""
    doc_text = "Hello world filing text here for testing."
    conn, tmp = _in_memory_conn_with_section(doc_text)

    blocks = [
        _block("## BUSINESS QUALITY\n\nCLAIM: "),
        _block("Hello world.", [_cite(0, 0, 11, "Hello WORLD")]),  # mismatch
        _block("\n\n## MOAT\n\nCLAIM: "),
        _block("Ecosystem.", [_cite(0, 12, 19, "world f")]),
        _block("\n\n## MANAGEMENT\n\nCLAIM: "),
        _block("R&D.", [_cite(0, 20, 27, "filing ")]),
        _block("\n\n## RISK\n\nCLAIM: "),
        _block("Risk.", [_cite(0, 28, 32, "text")]),
    ]
    result = _make_result(blocks)
    parsed = parse_and_validate(result, conn)
    assert not parsed.is_valid
    assert any("byte-equality" in e for e in parsed.validation_errors)
    tmp.unlink()
