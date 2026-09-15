"""Tests for parsing one persona's raw response text (Sprint 5)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.committee.parser import parse_persona_response

QUALITY_RESPONSE = """\
## VERDICT
This is a strong, durable business with real pricing power.

## SCORES
BUSINESS_QUALITY: 85
COMPETITIVE_MOAT: 78
MANAGEMENT: 60

## STATEMENTS
STATEMENT: The company has grown revenue every year for a decade. [refs: 1, 2]
STATEMENT: Management has a long track record of disciplined capital allocation. [refs: 3]
"""

BEAR_RESPONSE = """\
## VERDICT
Customer concentration is a real, material risk to this thesis.

## SCORES
RISK: 55
SEVERITY: medium

## STATEMENTS
STATEMENT: The top customer accounts for 30% of revenue. [refs: 5]
STATEMENT: Regulatory scrutiny in this sector is increasing. [refs: 6]
"""

VALUATION_RESPONSE = """\
## VERDICT
The stock trades below the conservative bear-case DCF estimate.

## SCORES
VALUATION: 72
FINANCIAL_STRENGTH: 65

## STATEMENTS
STATEMENT: The bear-case DCF shows a positive margin of safety at the current price.
STATEMENT: FCF yield comfortably clears the model's own discount rate.
"""


def test_parse_quality_response_extracts_verdict_scores_and_statements():
    parsed = parse_persona_response("quality", QUALITY_RESPONSE, known_claim_ids={1, 2, 3})
    assert parsed.is_valid, parsed.validation_errors
    assert "durable business" in parsed.verdict
    assert parsed.scores == {
        "business_quality_score": 85.0,
        "competitive_moat_score": 78.0,
        "management_score": 60.0,
    }
    assert parsed.severity is None
    assert len(parsed.statements) == 2
    assert parsed.statements[0].refs == [1, 2]
    assert parsed.statements[1].refs == [3]


def test_parse_bear_response_extracts_severity():
    parsed = parse_persona_response("bear", BEAR_RESPONSE, known_claim_ids={5, 6})
    assert parsed.is_valid, parsed.validation_errors
    assert parsed.scores == {"risk_score": 55.0}
    assert parsed.severity == "medium"


def test_parse_valuation_response_allows_statements_with_no_refs():
    """Valuation STATEMENTs may cite quant/valuation figures by name in the
    text itself rather than a claim id (prompt.py's own rule 1)."""
    parsed = parse_persona_response("valuation", VALUATION_RESPONSE, known_claim_ids=set())
    assert parsed.is_valid, parsed.validation_errors
    assert parsed.scores == {"valuation_score": 72.0, "financial_strength_score": 65.0}
    assert all(s.refs == [] for s in parsed.statements)


def test_parse_missing_verdict_section_is_invalid():
    text = QUALITY_RESPONSE.replace("## VERDICT\nThis is a strong, durable business with real pricing power.\n\n", "")
    parsed = parse_persona_response("quality", text, known_claim_ids={1, 2, 3})
    assert not parsed.is_valid
    assert any("VERDICT" in e for e in parsed.validation_errors)


def test_parse_missing_score_is_invalid():
    text = QUALITY_RESPONSE.replace("MANAGEMENT: 60\n", "")
    parsed = parse_persona_response("quality", text, known_claim_ids={1, 2, 3})
    assert not parsed.is_valid
    assert any("MANAGEMENT" in e for e in parsed.validation_errors)


def test_parse_bear_missing_severity_is_invalid():
    text = BEAR_RESPONSE.replace("SEVERITY: medium\n", "")
    parsed = parse_persona_response("bear", text, known_claim_ids={5, 6})
    assert not parsed.is_valid
    assert any("SEVERITY" in e for e in parsed.validation_errors)


def test_parse_unknown_claim_ref_is_flagged_but_statement_still_captured():
    """A hallucinated claim id is a validation error (caught before
    persistence), not silently dropped — the statement itself is still
    parsed so the error message can show what was actually claimed."""
    parsed = parse_persona_response("quality", QUALITY_RESPONSE, known_claim_ids={1, 2})  # 3 missing
    assert not parsed.is_valid
    assert any("unknown claim id 3" in e for e in parsed.validation_errors)
    assert len(parsed.statements) == 2


def test_parse_no_statements_is_invalid():
    text = "## VERDICT\nFine.\n\n## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n## STATEMENTS\n"
    parsed = parse_persona_response("quality", text, known_claim_ids=set())
    assert not parsed.is_valid
    assert any("no STATEMENT" in e for e in parsed.validation_errors)


def test_parse_score_out_of_range_is_invalid_but_clamped():
    text = QUALITY_RESPONSE.replace("BUSINESS_QUALITY: 85", "BUSINESS_QUALITY: 140")
    parsed = parse_persona_response("quality", text, known_claim_ids={1, 2, 3})
    assert not parsed.is_valid
    assert any("out of range" in e for e in parsed.validation_errors)
    assert parsed.scores["business_quality_score"] == 100.0
