"""Tests for context-block assembly and persona request construction
(Sprint 5)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.committee.committee import _COLUMN_TO_WEIGHT_KEY
from moat.committee.prompt import (
    PERSONA_SCORE_COMPONENTS,
    PERSONAS,
    SCORE_LABELS,
    build_context_block,
    build_request,
)

COMPANY_ROW = {"name": "Test Co", "sector": "Technology"}

CLAIMS_BY_TYPE = {
    "business_quality": [
        {"claim_id": 1, "claim_text": "Revenue grew every year for a decade.", "assertion_status": "asserted"},
        {"claim_id": 2, "claim_text": "No breakdown of segment margins is disclosed.", "assertion_status": "insufficient_evidence"},
    ],
    "moat": [
        {"claim_id": 3, "claim_text": "Switching costs are high due to data lock-in.", "assertion_status": "asserted"},
    ],
    "management": [],
    "risk": [
        {"claim_id": 4, "claim_text": "Top customer is 30% of revenue.", "assertion_status": "asserted"},
    ],
}


def _valuation_rows():
    return [
        {
            "method": "owner_earnings_dcf", "scenario": "bear",
            "intrinsic_value_low": 80.0, "intrinsic_value_high": 80.0,
            "current_price": 100.0, "margin_of_safety_pct": -0.25,
            "key_assumptions": "{}",
        },
        {
            "method": "owner_earnings_dcf", "scenario": "base",
            "intrinsic_value_low": 120.0, "intrinsic_value_high": 120.0,
            "current_price": 100.0, "margin_of_safety_pct": 0.1667,
            "key_assumptions": "{}",
        },
        {
            "method": "owner_earnings_dcf", "scenario": "bull",
            "intrinsic_value_low": 150.0, "intrinsic_value_high": 150.0,
            "current_price": 100.0, "margin_of_safety_pct": 0.333,
            "key_assumptions": "{}",
        },
        {
            "method": "fcf_yield", "scenario": None,
            "intrinsic_value_low": None, "intrinsic_value_high": None,
            "current_price": 100.0, "margin_of_safety_pct": None,
            "key_assumptions": json.dumps({"fcf_yield": 0.06, "market_cap": 1000.0}),
        },
        {
            "method": "ev_ebit", "scenario": None,
            "intrinsic_value_low": None, "intrinsic_value_high": None,
            "current_price": 100.0, "margin_of_safety_pct": None,
            "key_assumptions": json.dumps({"status": "unavailable", "reason": "total_debt unavailable"}),
        },
        {
            "method": "pe_historical", "scenario": None,
            "intrinsic_value_low": None, "intrinsic_value_high": None,
            "current_price": 100.0, "margin_of_safety_pct": None,
            "key_assumptions": json.dumps({
                "current": 22.0, "low": 15.0, "high": 30.0,
                "years_covered": 2, "low_confidence": True, "status": "ok",
            }),
        },
    ]


QUANT_ROWS = [
    {"metric": "roic", "value": 0.34, "status": "pass", "sector_percentile": 91.0, "sector_peer_group": "Technology"},
    {"metric": "debt", "value": None, "status": "unavailable", "sector_percentile": None, "sector_peer_group": "Technology"},
]

QUALITY_ROW = {"composite_score": 87.5, "metrics_assessed": 7, "metrics_passed": 7, "notes": None}


def test_build_context_block_includes_company_header_and_confidence():
    block = build_context_block(
        "TEST", COMPANY_ROW, CLAIMS_BY_TYPE, _valuation_rows(), QUANT_ROWS, QUALITY_ROW, "medium",
    )
    assert "COMPANY: TEST — Test Co (Technology)" in block
    assert "DATA CONFIDENCE: medium" in block


def test_build_context_block_renders_asserted_claims_with_bracketed_ids():
    block = build_context_block(
        "TEST", COMPANY_ROW, CLAIMS_BY_TYPE, _valuation_rows(), QUANT_ROWS, QUALITY_ROW, "high",
    )
    assert "[1] Revenue grew every year for a decade." in block
    assert "[3] Switching costs are high due to data lock-in." in block
    assert "[4] Top customer is 30% of revenue." in block


def test_build_context_block_renders_insufficient_evidence_without_a_bracket_id():
    block = build_context_block(
        "TEST", COMPANY_ROW, CLAIMS_BY_TYPE, _valuation_rows(), QUANT_ROWS, QUALITY_ROW, "high",
    )
    assert "(insufficient evidence: No breakdown of segment margins is disclosed.)" in block
    assert "[2]" not in block


def test_build_context_block_dcf_bear_negative_is_labelled_not_a_number():
    rows = _valuation_rows()
    rows[0]["intrinsic_value_low"] = -10.0
    rows[0]["intrinsic_value_high"] = -10.0
    block = build_context_block(
        "TEST", COMPANY_ROW, CLAIMS_BY_TYPE, rows, QUANT_ROWS, QUALITY_ROW, "high",
    )
    assert "bear case negative, not investable on this basis" in block


def test_build_context_block_unavailable_methods_state_the_reason():
    block = build_context_block(
        "TEST", COMPANY_ROW, CLAIMS_BY_TYPE, _valuation_rows(), QUANT_ROWS, QUALITY_ROW, "high",
    )
    assert "EV/EBIT: unavailable — total_debt unavailable" in block


def test_format_quant_block_handles_a_fail_status_with_no_computable_value():
    """Found running the first real pilot against live data: debt outstanding
    with no positive FCF to service it fails the debt metric outright while
    debt/FCF itself stays None (moat/screen/quant_screen.py's
    _absolute_floor_pass docstring) — 141 rows in one real quality run alone.
    Must not crash trying to format None as a float."""
    rows = [
        {"metric": "debt", "value": None, "status": "fail", "sector_percentile": None, "sector_peer_group": "Technology"},
    ]
    block = build_context_block(
        "TEST", COMPANY_ROW, CLAIMS_BY_TYPE, _valuation_rows(), rows, QUALITY_ROW, "high",
    )
    assert "debt: fail (no comparable ratio computed)" in block


def test_build_context_block_flags_low_confidence_pe_range():
    block = build_context_block(
        "TEST", COMPANY_ROW, CLAIMS_BY_TYPE, _valuation_rows(), QUANT_ROWS, QUALITY_ROW, "high",
    )
    assert "LOW CONFIDENCE — thin price history" in block


def test_build_request_returns_persona_specific_system_prompt_and_embeds_context():
    context_block = "COMPANY: TEST — Test Co (Technology)"
    for persona in PERSONAS:
        system, user = build_request(persona, "TEST", context_block)
        assert "TEST" in user
        assert context_block in user
        assert system  # non-empty, persona-specific
    quality_system, _ = build_request("quality", "TEST", context_block)
    bear_system, _ = build_request("bear", "TEST", context_block)
    assert quality_system != bear_system


def test_build_request_unknown_persona_raises():
    import pytest
    with pytest.raises(ValueError, match="unknown persona"):
        build_request("bull", "TEST", "context")


def test_persona_score_components_cover_every_prd_8_column_exactly_once():
    """Cross-module regression guard: every committee_verdicts weighted
    score column (committee.py's _COLUMN_TO_WEIGHT_KEY) must be produced by
    exactly one persona — no gap, no double-assignment."""
    all_columns = [col for cols in PERSONA_SCORE_COMPONENTS.values() for col in cols]
    assert set(all_columns) == set(_COLUMN_TO_WEIGHT_KEY)
    assert len(all_columns) == len(set(all_columns))  # no column claimed twice


def test_score_labels_cover_every_persona_score_component():
    for cols in PERSONA_SCORE_COMPONENTS.values():
        for col in cols:
            assert col in SCORE_LABELS
