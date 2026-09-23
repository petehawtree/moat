"""Integration tests for run_committee()/run_committee_stage(): real schema,
temp DB, mocked Anthropic client — same convention as
test_valuation_engine.py's `valuation_db` fixture and
test_analysis_persist.py's MagicMock client pattern.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.committee.committee import run_committee

NOW = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def committee_db(tmp_path):
    from moat.db.connection import get_connection, init_db

    db_path = tmp_path / "committee_test.db"
    init_db(db_path=db_path)
    conn = get_connection(db_path=db_path)

    conn.execute(
        "INSERT INTO companies (ticker, name, sector, universe, is_active, added_date) "
        "VALUES ('TEST', 'Test Co', 'Technology', 'sp500', 1, ?)", (NOW,),
    )
    for rid in ("ai_run", "valuation_run", "quality_run", "committee_run"):
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, status) VALUES (?, ?, 'complete')",
            (rid, NOW),
        )

    # --- ai_analysis + analysis_claims (current) ---
    claim_ids = {}
    for at, texts in (
        ("business_quality", ["Revenue grew every year for a decade."]),
        ("moat", ["Switching costs are high due to data lock-in."]),
        ("management", ["Buybacks have been disciplined."]),
        ("risk", ["Top customer is 30% of revenue."]),
    ):
        conn.execute(
            "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
            "prompt_version, cache_key, is_current, created_at) VALUES "
            "('ai_run', 'TEST', ?, 'content', 'test-model', 'v1', 'key', 1, ?)",
            (at, NOW),
        )
        cur = conn.execute(
            "INSERT INTO analysis_claims (run_id, ticker, analysis_type, claim_order, claim_text, assertion_status) "
            "VALUES ('ai_run', 'TEST', ?, 1, ?, 'asserted')",
            (at, texts[0]),
        )
        claim_ids[at] = cur.lastrowid

    # --- valuations ---
    for scenario, low in (("bear", 90.0), ("base", 120.0), ("bull", 150.0)):
        conn.execute(
            "INSERT INTO valuations (run_id, ticker, method, scenario, intrinsic_value_low, "
            "intrinsic_value_high, current_price, margin_of_safety_pct, key_assumptions, created_at) "
            "VALUES ('valuation_run', 'TEST', 'owner_earnings_dcf', ?, ?, ?, 100.0, ?, '{}', ?)",
            (scenario, low, low, (low - 100.0) / low, NOW),
        )
    conn.execute(
        "INSERT INTO valuations (run_id, ticker, method, scenario, current_price, key_assumptions, created_at) "
        "VALUES ('valuation_run', 'TEST', 'fcf_yield', NULL, 100.0, ?, ?)",
        (json.dumps({"fcf_yield": 0.08, "market_cap": 1000.0}), NOW),
    )
    conn.execute(
        "INSERT INTO valuations (run_id, ticker, method, scenario, current_price, key_assumptions, created_at) "
        "VALUES ('valuation_run', 'TEST', 'ev_ebit', NULL, 100.0, ?, ?)",
        (json.dumps({"ev_ebit_multiple": 12.0, "market_cap": 1000.0}), NOW),
    )
    conn.execute(
        "INSERT INTO valuations (run_id, ticker, method, scenario, current_price, key_assumptions, created_at) "
        "VALUES ('valuation_run', 'TEST', 'pe_historical', NULL, 100.0, ?, ?)",
        (json.dumps({"current": 18.0, "low": 12.0, "high": 25.0, "years_covered": 8, "low_confidence": False, "status": "ok"}), NOW),
    )

    # --- quant_scores + quality_scores ---
    conn.execute(
        "INSERT INTO quant_scores (run_id, ticker, sector_peer_group, metric, value, "
        "absolute_floor_pass, sector_percentile, sector_relative_pass, overall_pass, status) "
        "VALUES ('quality_run', 'TEST', 'Technology', 'roic', 0.34, 1, 91.0, 1, 1, 'pass')"
    )
    conn.execute(
        "INSERT INTO quality_scores (run_id, ticker, passed_screen, composite_score, "
        "metrics_assessed, metrics_passed) VALUES ('quality_run', 'TEST', 1, 87.5, 7, 7)"
    )

    # --- fundamentals (for data_confidence rollup) ---
    conn.execute(
        "INSERT INTO fundamentals_annual (ticker, fiscal_year, source, confidence, retrieved_at) "
        "VALUES ('TEST', 2024, 'sec_edgar', 'high', ?)", (NOW,),
    )

    conn.commit()
    yield conn, claim_ids
    conn.close()


def _stream_cm(message):
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.return_value = message
    cm.__exit__.return_value = False
    return cm


def _fake_message(text, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=500, output_tokens=150,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


def _canned_responses(claim_ids):
    # 4 STATEMENT lines each — the minimum prompt.py's own "Make 4-8
    # STATEMENT lines total" rule requires (MIN_STATEMENTS, parser.py).
    quality = (
        "## VERDICT\nA durable, well-run business.\n\n"
        "## SCORES\nBUSINESS_QUALITY: 85\nCOMPETITIVE_MOAT: 78\nMANAGEMENT: 70\n\n"
        "## STATEMENTS\n"
        f"STATEMENT: Revenue has grown every year for a decade. [refs: {claim_ids['business_quality']}]\n"
        f"STATEMENT: Switching costs are high. [refs: {claim_ids['moat']}]\n"
        "STATEMENT: Margins have been stable across the cycle.\n"
        "STATEMENT: Management has a long tenure and a disciplined track record.\n"
    )
    bear = (
        "## VERDICT\nCustomer concentration is a real risk.\n\n"
        "## SCORES\nRISK: 45\nSEVERITY: medium\n\n"
        "## STATEMENTS\n"
        f"STATEMENT: The top customer is 30% of revenue. [refs: {claim_ids['risk']}]\n"
        "STATEMENT: A downturn in that customer's business would hit revenue hard.\n"
        "STATEMENT: Regulatory scrutiny in this sector is increasing.\n"
        "STATEMENT: A key patent expires within the projection window.\n"
    )
    valuation = (
        "## VERDICT\nTrading below the conservative bear-case estimate.\n\n"
        "## SCORES\nVALUATION: 80\nFINANCIAL_STRENGTH: 72\n\n"
        "## STATEMENTS\n"
        "STATEMENT: The bear-case DCF shows a positive margin of safety.\n"
        "STATEMENT: FCF yield of 8% comfortably clears the model's discount rate.\n"
        "STATEMENT: EV/EBIT is reasonable relative to sector peers.\n"
        "STATEMENT: The balance sheet carries manageable leverage.\n"
    )
    return [quality, bear, valuation]


def test_run_committee_persists_one_row_with_weighted_score_and_status(committee_db):
    conn, claim_ids = committee_db
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]

    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)

    assert result["outcome"] == "persisted"
    row = conn.execute(
        "SELECT * FROM committee_verdicts WHERE run_id = 'committee_run' AND ticker = 'TEST'"
    ).fetchone()
    assert row is not None
    assert row["business_quality_score"] == 85.0
    assert row["competitive_moat_score"] == 78.0
    assert row["management_score"] == 70.0
    assert row["valuation_score"] == 80.0
    assert row["financial_strength_score"] == 72.0
    assert row["risk_score"] == 45.0
    # risk_score is inverted before weighting (100 = highest risk):
    # 0.25*85 + 0.20*78 + 0.15*72 + 0.10*70 + 0.25*80 + 0.05*(100-45)
    # = 21.25 + 15.6 + 10.8 + 7.0 + 20.0 + 2.75 = 77.4
    assert row["overall_score"] == pytest.approx(77.4)
    assert row["bear_case_severity"] == "medium"
    assert row["status"] == "Investigate"  # >=70 and severity != high
    assert row["data_confidence"] == "high"
    assert "durable" in row["investment_thesis"]
    assert "Overall score" in row["ai_conclusion"]
    monitor_items = json.loads(row["key_things_to_monitor"])
    assert len(monitor_items) == 4


def test_run_committee_severe_bear_case_caps_status_to_watch(committee_db):
    conn, claim_ids = committee_db
    texts = _canned_responses(claim_ids)
    texts[1] = texts[1].replace("SEVERITY: medium", "SEVERITY: high")
    client = MagicMock()
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]

    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "persisted"
    assert result["status"] == "Watch"


def test_run_committee_refusal_persists_nothing_but_reports_cost(committee_db):
    conn, claim_ids = committee_db
    client = MagicMock()
    refusal_msg = SimpleNamespace(
        stop_reason="refusal", content=[],
        usage=SimpleNamespace(input_tokens=500, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    client.messages.stream.side_effect = [_stream_cm(refusal_msg)]

    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "refused"
    row = conn.execute(
        "SELECT * FROM committee_verdicts WHERE run_id = 'committee_run' AND ticker = 'TEST'"
    ).fetchone()
    assert row is None


def test_run_committee_invalid_persona_output_persists_nothing(committee_db):
    conn, claim_ids = committee_db
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    texts[0] = texts[0].replace("MANAGEMENT: 70\n", "")  # drop a required score
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]

    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "validation_failed"
    row = conn.execute(
        "SELECT * FROM committee_verdicts WHERE run_id = 'committee_run' AND ticker = 'TEST'"
    ).fetchone()
    assert row is None


def test_run_committee_hallucinated_claim_ref_fails_validation(committee_db):
    conn, claim_ids = committee_db
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    bogus_id = max(claim_ids.values()) + 1000
    texts[0] = texts[0].replace(
        f"[refs: {claim_ids['moat']}]", f"[refs: {bogus_id}]"
    )
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]

    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "validation_failed"
    assert str(bogus_id) in result["reason"]


def test_run_committee_no_valuation_row_is_an_explicit_error(committee_db):
    conn, claim_ids = committee_db
    conn.execute("DELETE FROM valuations WHERE ticker = 'TEST'")
    conn.commit()
    client = MagicMock()
    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "api_error"
    assert "no valuations row" in result["reason"]
    client.messages.stream.assert_not_called()


def test_run_committee_no_ai_analysis_is_an_explicit_error(committee_db):
    conn, claim_ids = committee_db
    conn.execute("DELETE FROM analysis_claims WHERE ticker = 'TEST'")
    conn.execute("DELETE FROM ai_analysis WHERE ticker = 'TEST'")
    conn.commit()
    client = MagicMock()
    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "api_error"
    assert "missing current ai_analysis type" in result["reason"]
    client.messages.stream.assert_not_called()


def test_run_committee_rejects_a_ticker_missing_some_analysis_types(committee_db):
    """Found by judge review: the prior check only required *some*
    analysis type to have claims — a ticker missing 3 of 4 required types
    (moat/management/risk entirely absent, only business_quality present)
    still produced a complete-looking, persisted verdict. All four types
    must exist (an ai_analysis row each), not all four have asserted
    claims specifically."""
    conn, claim_ids = committee_db
    conn.execute("DELETE FROM analysis_claims WHERE ticker = 'TEST' AND analysis_type != 'business_quality'")
    conn.execute("DELETE FROM ai_analysis WHERE ticker = 'TEST' AND analysis_type != 'business_quality'")
    conn.commit()
    client = MagicMock()

    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "api_error"
    assert "missing current ai_analysis type(s)" in result["reason"]
    assert "moat" in result["reason"] and "management" in result["reason"] and "risk" in result["reason"]
    client.messages.stream.assert_not_called()
    row = conn.execute(
        "SELECT * FROM committee_verdicts WHERE run_id = 'committee_run' AND ticker = 'TEST'"
    ).fetchone()
    assert row is None


def test_run_committee_proceeds_when_a_type_ran_but_found_only_insufficient_evidence(committee_db):
    """A type that ran and asserted nothing (every claim
    'insufficient_evidence') is a different, legitimate state from a type
    that never ran at all — must not be rejected the same way as a
    missing type."""
    conn, claim_ids = committee_db
    conn.execute(
        "UPDATE analysis_claims SET assertion_status = 'insufficient_evidence' WHERE ticker = 'TEST' AND analysis_type = 'risk'"
    )
    conn.commit()
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    # The bear canned response cites claim_ids['risk'], which just became
    # insufficient_evidence (no longer a valid ref target) — drop the tag,
    # same as a real persona would if told there's nothing to assert.
    texts[1] = texts[1].replace(f"[refs: {claim_ids['risk']}]", "")
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]

    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "persisted"


def test_fetch_claims_follows_cache_hit_chain_to_the_run_that_has_them(committee_db):
    """Found dry-running the committee stage against the real database:
    a cache-hit ai_analysis row copies `content` forward via
    reused_from_run_id but never copies analysis_claims — real AAPL/LIN/PEG
    data had 0 claims under their current run_id, all under the superseded
    run that originally parsed them. Reproduces that shape directly."""
    from moat.committee.committee import _fetch_claims_by_type

    conn, claim_ids = committee_db
    # Supersede the original ai_analysis rows with cache-hit copies under a
    # new run_id — content carried forward, analysis_claims deliberately
    # NOT copied (matching moat.analysis.persist.run_analysis()'s real
    # cache-hit path).
    for at in ("business_quality", "moat", "management", "risk"):
        conn.execute("UPDATE ai_analysis SET is_current = 0 WHERE ticker = 'TEST' AND analysis_type = ?", (at,))
        conn.execute(
            "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
            "prompt_version, cache_key, is_current, reused_from_run_id, created_at) VALUES "
            "('newer_run', 'TEST', ?, 'content', 'test-model', 'v1', 'key', 1, 'ai_run', ?)",
            (at, NOW),
        )
    conn.commit()

    claims = _fetch_claims_by_type("TEST", conn)
    assert {k: len(v) for k, v in claims.items()} == {
        "business_quality": 1, "moat": 1, "management": 1, "risk": 1,
    }


def test_run_committee_works_through_a_cache_hit_chain(committee_db):
    """End-to-end: run_committee() itself must not report 'no current
    ai_analysis claims' just because the current run is a cache-hit copy."""
    conn, claim_ids = committee_db
    for at in ("business_quality", "moat", "management", "risk"):
        conn.execute("UPDATE ai_analysis SET is_current = 0 WHERE ticker = 'TEST' AND analysis_type = ?", (at,))
        conn.execute(
            "INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, "
            "prompt_version, cache_key, is_current, reused_from_run_id, created_at) VALUES "
            "('newer_run', 'TEST', ?, 'content', 'test-model', 'v1', 'key', 1, 'ai_run', ?)",
            (at, NOW),
        )
    conn.commit()

    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]
    result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "persisted"


def test_run_committee_cache_hit_makes_no_api_calls(committee_db):
    """§A5: 'AI analysis / valuation / committee: only re-run when A5's
    cache key changes.' A second committee run against unchanged
    ai_analysis/valuations inputs must copy the prior verdict forward, not
    re-pay for all three persona calls — found missing entirely on the
    first real pilot run (three retries after unrelated bugs each re-billed
    the same company from scratch)."""
    conn, claim_ids = committee_db
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_1', ?, 'complete')", (NOW,))
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_2', ?, 'complete')", (NOW,))
    conn.commit()
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]

    first = run_committee("TEST", "committee_run_1", "valuation_run", "quality_run", conn, client)
    assert first["outcome"] == "persisted"
    assert client.messages.stream.call_count == 3

    client.messages.stream.reset_mock()
    second = run_committee("TEST", "committee_run_2", "valuation_run", "quality_run", conn, client)
    assert second["outcome"] == "cache_hit"
    assert second["cost_estimate"] == 0.0
    assert second["reused_from_run_id"] == "committee_run_1"
    client.messages.stream.assert_not_called()

    row = conn.execute(
        "SELECT * FROM committee_verdicts WHERE run_id = 'committee_run_2' AND ticker = 'TEST'"
    ).fetchone()
    assert row["overall_score"] == first["overall_score"]
    assert row["reused_from_run_id"] == "committee_run_1"


def test_run_committee_cache_miss_when_valuation_changes(committee_db):
    """A real content change (not just a new run_id) must invalidate the
    cache — bundle key hashes the actual valuation figures, not the
    valuation_run_id string."""
    conn, claim_ids = committee_db
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_1', ?, 'complete')", (NOW,))
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_2', ?, 'complete')", (NOW,))
    conn.commit()
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts] * 2

    first = run_committee("TEST", "committee_run_1", "valuation_run", "quality_run", conn, client)
    assert first["outcome"] == "persisted"

    conn.execute(
        "UPDATE valuations SET intrinsic_value_low = 999.0 "
        "WHERE ticker = 'TEST' AND method = 'owner_earnings_dcf' AND scenario = 'bear'"
    )
    conn.commit()

    second = run_committee("TEST", "committee_run_2", "valuation_run", "quality_run", conn, client)
    assert second["outcome"] == "persisted"
    assert client.messages.stream.call_count == 6  # 3 + 3, no cache hit


def test_run_committee_cache_miss_when_fcf_yield_key_assumptions_change(committee_db):
    """Found by judge review: the cache key hashed each valuation row's
    method/scenario/intrinsic-value/current-price but not key_assumptions
    — so a changed FCF yield or EV/EBIT (both shown to the Valuation
    Analyst, prompt.py's _format_valuation_block) didn't invalidate the
    cache. Independently reproduced before fixing: FCF yield 3%->12% and
    EV/EBIT 12x->35x held the same bundle key with DCF/price unchanged."""
    conn, claim_ids = committee_db
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_1', ?, 'complete')", (NOW,))
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_2', ?, 'complete')", (NOW,))
    conn.commit()
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts] * 2

    first = run_committee("TEST", "committee_run_1", "valuation_run", "quality_run", conn, client)
    assert first["outcome"] == "persisted"

    conn.execute(
        "UPDATE valuations SET key_assumptions = ? WHERE ticker = 'TEST' AND method = 'fcf_yield'",
        (json.dumps({"fcf_yield": 0.30, "market_cap": 1000.0}),),
    )
    conn.commit()

    second = run_committee("TEST", "committee_run_2", "valuation_run", "quality_run", conn, client)
    assert second["outcome"] == "persisted"
    assert client.messages.stream.call_count == 6


def test_run_committee_cache_miss_when_quant_scores_change(committee_db):
    """Closes GitHub #7: the cache key must cover quant_scores/
    quality_scores, not just ai_analysis + valuations."""
    conn, claim_ids = committee_db
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_1', ?, 'complete')", (NOW,))
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_2', ?, 'complete')", (NOW,))
    conn.commit()
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts] * 2

    first = run_committee("TEST", "committee_run_1", "valuation_run", "quality_run", conn, client)
    assert first["outcome"] == "persisted"

    conn.execute("UPDATE quant_scores SET value = 0.01, status = 'fail' WHERE ticker = 'TEST' AND metric = 'roic'")
    conn.execute("UPDATE quality_scores SET composite_score = 0.0 WHERE ticker = 'TEST' AND run_id = 'quality_run'")
    conn.commit()

    second = run_committee("TEST", "committee_run_2", "valuation_run", "quality_run", conn, client)
    assert second["outcome"] == "persisted"
    assert client.messages.stream.call_count == 6


def test_run_committee_cache_miss_when_data_confidence_changes(committee_db):
    conn, claim_ids = committee_db
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_1', ?, 'complete')", (NOW,))
    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_2', ?, 'complete')", (NOW,))
    conn.commit()
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts] * 2

    first = run_committee("TEST", "committee_run_1", "valuation_run", "quality_run", conn, client)
    assert first["outcome"] == "persisted"

    conn.execute("UPDATE fundamentals_annual SET confidence = 'low' WHERE ticker = 'TEST'")
    conn.commit()

    second = run_committee("TEST", "committee_run_2", "valuation_run", "quality_run", conn, client)
    assert second["outcome"] == "persisted"
    assert client.messages.stream.call_count == 6


def test_run_committee_makes_no_calls_when_no_budget_remains(committee_db):
    """cost_cap_remaining=0.0 means the stage's overall cap is already
    exhausted before this company starts — zero persona calls, not a
    partial company."""
    conn, claim_ids = committee_db
    client = MagicMock()

    result = run_committee(
        "TEST", "committee_run", "valuation_run", "quality_run", conn, client,
        cost_cap_remaining=0.0,
    )
    assert result["outcome"] == "cost_capped"
    assert client.messages.stream.call_count == 0
    row = conn.execute(
        "SELECT * FROM committee_verdicts WHERE run_id = 'committee_run' AND ticker = 'TEST'"
    ).fetchone()
    assert row is None


def test_run_committee_stops_mid_company_after_first_persona_exceeds_remaining_budget(committee_db):
    """cost_cap_remaining is checked before *each* persona call, not just
    once per company — a company whose first call alone exceeds what's
    left is halted before the second, bounding overspend to one persona
    call rather than up to three (judge review of the first pilot run)."""
    conn, claim_ids = committee_db
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts]

    result = run_committee(
        "TEST", "committee_run", "valuation_run", "quality_run", conn, client,
        cost_cap_remaining=0.00001,  # smaller than any single call's real cost
    )
    assert result["outcome"] == "cost_capped"
    assert client.messages.stream.call_count == 1
    row = conn.execute(
        "SELECT * FROM committee_verdicts WHERE run_id = 'committee_run' AND ticker = 'TEST'"
    ).fetchone()
    assert row is None


def test_run_committee_is_idempotent_per_run_id(committee_db):
    """Re-running under the same run_id replaces the row, not duplicates it —
    INSERT OR REPLACE against a PK with no nullable column (unlike
    valuations, committee_verdicts' PK never has this hazard, see
    committee.py's run_committee docstring)."""
    conn, claim_ids = committee_db
    client = MagicMock()
    texts = _canned_responses(claim_ids)
    client.messages.stream.side_effect = [_stream_cm(_fake_message(t)) for t in texts] * 2

    run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)
    run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM committee_verdicts WHERE run_id = 'committee_run' AND ticker = 'TEST'"
    ).fetchone()["n"]
    assert count == 1


def test_run_committee_records_the_input_runs_it_was_scored_on(committee_db):
    """Judge finding: verdicts persisted no input provenance, so the brief
    re-queried the latest runs and could show evidence the verdict never
    saw. Both the fresh and the cache-hit path must record it — the
    cache-hit row records *its own* call's inputs (identical content by
    bundle key), not the cached row's."""
    conn, claim_ids = committee_db
    client = MagicMock()
    client.messages.stream.side_effect = [
        _stream_cm(_fake_message(t)) for t in _canned_responses(claim_ids)
    ]
    run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, client)

    row = conn.execute("SELECT * FROM committee_verdicts WHERE run_id = 'committee_run'").fetchone()
    assert row["valuation_run_id"] == "valuation_run"
    assert row["quality_run_id"] == "quality_run"
    assert json.loads(row["ai_claims_run_ids"]) == {
        "business_quality": "ai_run", "moat": "ai_run", "management": "ai_run", "risk": "ai_run",
    }

    conn.execute("INSERT INTO pipeline_runs (run_id, started_at, status) VALUES ('committee_run_2', ?, 'complete')", (NOW,))
    result = run_committee("TEST", "committee_run_2", "valuation_run", "quality_run", conn, client)
    assert result["outcome"] == "cache_hit"
    hit = conn.execute("SELECT * FROM committee_verdicts WHERE run_id = 'committee_run_2'").fetchone()
    assert hit["valuation_run_id"] == "valuation_run"
    assert hit["quality_run_id"] == "quality_run"
    assert json.loads(hit["ai_claims_run_ids"])["moat"] == "ai_run"


def test_run_committee_transient_failure_is_a_per_company_api_error(committee_db):
    """After call_persona's retries are exhausted, run_committee reports an
    api_error for this company (persisting nothing) instead of raising —
    so one API blip no longer aborts the whole committee stage."""
    from moat.committee.caller import PersonaCallFailed

    conn, _ = committee_db
    with patch("moat.committee.committee.call_persona", side_effect=PersonaCallFailed("quality: overloaded")):
        result = run_committee("TEST", "committee_run", "valuation_run", "quality_run", conn, MagicMock())
    assert result["outcome"] == "api_error"
    assert result["transient"] is True
    assert conn.execute("SELECT COUNT(*) FROM committee_verdicts").fetchone()[0] == 0
