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
STATEMENT: Gross margins have stayed stable across the cycle.
STATEMENT: The brand commands real pricing power versus peers.
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
STATEMENT: A key patent expires within the projection window.
STATEMENT: Working capital needs have grown faster than revenue.
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
STATEMENT: EV/EBIT is reasonable relative to sector peers.
STATEMENT: The balance sheet carries manageable leverage.
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
    assert len(parsed.statements) == 4
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
    assert len(parsed.statements) == 4


def test_parse_no_statements_is_invalid():
    text = "## VERDICT\nFine.\n\n## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n## STATEMENTS\n"
    parsed = parse_persona_response("quality", text, known_claim_ids=set())
    assert not parsed.is_valid
    assert any("expected at least" in e for e in parsed.validation_errors)


def test_parse_too_few_statements_is_invalid():
    """prompt.py's own rule: 4-8 STATEMENT lines per persona. Found by
    judge review: only zero statements was ever rejected — a thin,
    1-statement response passed and could persist as a complete-looking
    verdict despite supplying a fraction of the requested analysis."""
    text = (
        "## VERDICT\nFine.\n\n## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n"
        "## STATEMENTS\nSTATEMENT: Just one thing.\nSTATEMENT: And another.\nSTATEMENT: A third.\n"
    )
    parsed = parse_persona_response("quality", text, known_claim_ids=set())
    assert not parsed.is_valid
    assert any("only 3 STATEMENT" in e and "at least 4" in e for e in parsed.validation_errors)


def test_parse_too_many_statements_is_invalid():
    text = (
        "## VERDICT\nFine.\n\n## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n"
        "## STATEMENTS\n" + "".join(f"STATEMENT: Point number {i}.\n" for i in range(9))
    )
    parsed = parse_persona_response("quality", text, known_claim_ids=set())
    assert not parsed.is_valid
    assert any("9 STATEMENT" in e and "at most 8" in e for e in parsed.validation_errors)


def test_parse_exactly_min_and_max_statements_are_both_valid():
    base = "## VERDICT\nFine.\n\n## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n## STATEMENTS\n"
    four = base + "".join(f"STATEMENT: Point {i}.\n" for i in range(4))
    eight = base + "".join(f"STATEMENT: Point {i}.\n" for i in range(8))
    assert parse_persona_response("quality", four, known_claim_ids=set()).is_valid
    assert parse_persona_response("quality", eight, known_claim_ids=set()).is_valid


def test_parse_statement_with_empty_refs_bracket_does_not_crash():
    """Found running the first real pilot: a persona emitted
    'STATEMENT: text [refs: ]' (whitespace only inside the brackets) —
    must parse as zero refs, not crash on int('')."""
    text = (
        "## VERDICT\nFine.\n\n"
        "## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n"
        "## STATEMENTS\nSTATEMENT: Something true but uncited. [refs: ]\n"
    )
    parsed = parse_persona_response("quality", text, known_claim_ids=set())
    assert parsed.statements[0].refs == []


def test_parse_statement_with_trailing_comma_in_refs_does_not_crash():
    text = (
        "## VERDICT\nFine.\n\n"
        "## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n"
        "## STATEMENTS\nSTATEMENT: Something. [refs: 12,]\n"
    )
    parsed = parse_persona_response("quality", text, known_claim_ids={12})
    assert parsed.statements[0].refs == [12]


def test_parse_non_numeric_ref_is_invalid_not_silently_swallowed():
    """Found running the first real pilot against live data: a persona
    emitted '[refs: roic]'/'[refs: DCF bear]' — a quant concept referenced
    by name instead of a claim id. The old digits-only regex simply failed
    to match the bracket at all, silently folding the literal
    '[refs: roic]' text into the STATEMENT itself — invisible to
    validation. Must now be flagged, not swallowed."""
    text = (
        "## VERDICT\nFine.\n\n"
        "## SCORES\nBUSINESS_QUALITY: 50\nCOMPETITIVE_MOAT: 50\nMANAGEMENT: 50\n\n"
        "## STATEMENTS\nSTATEMENT: ROIC is strong. [refs: roic]\n"
    )
    parsed = parse_persona_response("quality", text, known_claim_ids=set())
    assert not parsed.is_valid
    assert any("non-numeric ref" in e and "roic" in e for e in parsed.validation_errors)
    # The statement text itself is clean — the bracket doesn't leak into it.
    assert parsed.statements[0].text == "ROIC is strong."
    assert parsed.statements[0].refs == []


def test_extract_statements_silently_drops_non_numeric_refs():
    """extract_statements() (the dashboard's best-effort path, no validation
    contract) drops a non-numeric ref rather than raising — the strict
    parse_persona_response() above is where that gets flagged as an error."""
    from moat.committee.parser import extract_statements

    text = "## STATEMENTS\nSTATEMENT: ROIC is strong. [refs: roic]\nSTATEMENT: Real one. [refs: 5]\n"
    statements = extract_statements(text)
    assert statements[0].text == "ROIC is strong."
    assert statements[0].refs == []
    assert statements[1].refs == [5]


def test_parse_valuation_non_numeric_ref_is_silently_stripped_not_an_error():
    """Found running the real pilot: the Valuation Analyst — whose
    statements are grounded in quant/DCF figures already visible in the
    CONTEXT block, never in a claim_id — still sometimes tags a figure with
    a descriptive bracket like '[refs: DCF bear]'. Unlike Quality/Bear
    (where a non-numeric ref means a broken citation trail), that's a
    formatting slip on already-grounded content and must not fail
    validation — 3/16 real companies were discarded and re-billed for
    exactly this before the carve-out."""
    text = (
        "## VERDICT\nFine.\n\n"
        "## SCORES\nVALUATION: 60\nFINANCIAL_STRENGTH: 60\n\n"
        "## STATEMENTS\n"
        "STATEMENT: The bear-case DCF is $107.74. [refs: DCF bear]\n"
        "STATEMENT: FCF yield is 8%.\n"
        "STATEMENT: EV/EBIT is 12x.\n"
        "STATEMENT: The balance sheet is conservative.\n"
    )
    parsed = parse_persona_response("valuation", text, known_claim_ids=set())
    assert parsed.is_valid, parsed.validation_errors
    assert parsed.statements[0].text == "The bear-case DCF is $107.74."
    assert parsed.statements[0].refs == []


def test_parse_score_out_of_range_is_invalid_but_clamped():
    text = QUALITY_RESPONSE.replace("BUSINESS_QUALITY: 85", "BUSINESS_QUALITY: 140")
    parsed = parse_persona_response("quality", text, known_claim_ids={1, 2, 3})
    assert not parsed.is_valid
    assert any("out of range" in e for e in parsed.validation_errors)
    assert parsed.scores["business_quality_score"] == 100.0
