"""Investment Committee (PRD §7) and final weighted score (PRD §8).

Sprint 5. Three persona LLM calls (Quality / Bear / Valuation Analyst,
moat/committee/prompt.py + caller.py) consolidate a company's already-cited
`ai_analysis` claims and already-computed `valuations` into one
`committee_verdicts` row: the PRD §8 weighted score, an Investigate/Watch/
Reject status, and the PRD §10 Investment Brief content (moat/committee/
parser.py handles turning one persona's raw text into scores + statements).

No new citation-extraction layer here — see prompt.py's module docstring
and sprint-5-plan.md decision 1 (§A19.6): a persona STATEMENT tags the
`analysis_claims.claim_id`s it draws on, and the dashboard resolves those
back to their original filing quote for the reader to eyeball inline,
rather than this stage re-proving entailment itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from moat.analysis.pricing import DEFAULT_MODEL
from moat.committee.caller import call_persona
from moat.committee.parser import parse_persona_response
from moat.committee.prompt import PERSONAS, build_context_block

WEIGHTS = {
    "business_quality": 0.25,
    "competitive_moat": 0.20,
    "financial_strength": 0.15,
    "management": 0.10,
    "valuation": 0.25,
    "risk": 0.05,
}

# committee_verdicts column name -> WEIGHTS key
_COLUMN_TO_WEIGHT_KEY = {
    "business_quality_score":   "business_quality",
    "competitive_moat_score":   "competitive_moat",
    "financial_strength_score": "financial_strength",
    "management_score":         "management",
    "valuation_score":          "valuation",
    "risk_score":                "risk",
}


def compute_overall_score(component_scores: dict[str, float]) -> float:
    """Weighted sum per PRD §8. Raises if any component is missing —
    an incomplete score should be visibly blocked, not silently partial.

    `component_scores` keys are committee_verdicts column names
    (e.g. 'business_quality_score'), not WEIGHTS' own shorter keys —
    matches what the three personas actually produce (prompt.py's
    PERSONA_SCORE_COMPONENTS), so callers don't have to translate twice.
    """
    missing = set(_COLUMN_TO_WEIGHT_KEY) - set(component_scores)
    if missing:
        raise ValueError(f"Missing score components: {missing}")
    return sum(component_scores[col] * WEIGHTS[key] for col, key in _COLUMN_TO_WEIGHT_KEY.items())


# Starting-point thresholds (0-100 scale) — a judgment call, same posture as
# Sprint 4's DISCOUNT_RATE and the screen's QUALITY_SCORE_PASS_THRESHOLD/
# SECTOR_RELATIVE_TOP_TERCILE_PCT: a documented starting number, meant to be
# revisited against a real pilot read (sprint-5-plan.md C4), not treated as
# final because it's written down here.
INVESTIGATE_THRESHOLD = 70.0
WATCH_THRESHOLD = 50.0

_VALID_SEVERITIES = ("low", "medium", "high")


def assign_status(overall_score: float, bear_case_severity: str) -> str:
    """Map score + bear-case strength to Investigate / Watch / Reject.

    `bear_case_severity` is deliberately NOT folded into `overall_score`'s
    weighted sum (it isn't one of PRD §8's six components) — it's a
    separate, categorical check applied on top, per PRD §1's "challenge
    every thesis": a severe bear case caps the verdict at Watch regardless
    of how strong the rest of the committee's case is, but never on its own
    rejects a company the rest of the evidence supports, and never rescues
    a company whose score alone is too low. One-tier demotion only —
    letting the Bear Analyst alone veto to Reject would conflict with PRD
    §7's "a consolidated assessment produces the final research ranking,"
    not one persona acting unilaterally.
    """
    if bear_case_severity not in _VALID_SEVERITIES:
        raise ValueError(f"bear_case_severity must be one of {_VALID_SEVERITIES}, got {bear_case_severity!r}")
    if overall_score >= INVESTIGATE_THRESHOLD:
        return "Watch" if bear_case_severity == "high" else "Investigate"
    if overall_score >= WATCH_THRESHOLD:
        return "Watch"
    return "Reject"


# ---------------------------------------------------------------------
# Data gathering — everything downstream of Sprint 1-4's own outputs.
# ---------------------------------------------------------------------

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def roll_up_data_confidence(ticker: str, conn) -> str:
    """Worst-case confidence tier (A4) across every ingested fundamentals
    row for this ticker — not the latest year alone, since the DCF/screen
    both draw on a multi-year trailing window (§A16). No rows at all is
    treated as the worst case, not silently defaulted to 'high'.
    """
    rows = conn.execute(
        "SELECT confidence FROM fundamentals_annual WHERE ticker = ?", (ticker,)
    ).fetchall()
    confidences = [r["confidence"] for r in rows if r["confidence"]]
    if not confidences:
        return "low"
    return min(confidences, key=lambda c: _CONFIDENCE_RANK.get(c, -1))


def _resolve_claims_run_id(ticker: str, analysis_type: str, run_id: str, conn) -> str:
    """Find the run_id that actually holds this (ticker, analysis_type)'s
    `analysis_claims` rows.

    Found dry-running this stage against the real database (§A19.1): a
    cache-hit `ai_analysis` row (moat/analysis/persist.py's run_analysis())
    copies `content` forward to the new run_id via `reused_from_run_id`,
    but never copies the parsed `analysis_claims` rows themselves — those
    still sit under whichever run_id originally parsed them. Querying
    `analysis_claims` by the *current* ai_analysis.run_id alone therefore
    returns zero rows for any cache-hit-refreshed ticker, even though the
    claims are exactly as valid as a freshly-parsed one (that's the whole
    point of a cache hit: the content, and the claims behind it, are
    unchanged). Confirmed on real data: AAPL/LIN/PEG's current
    `item6_batch_20260909` rows are all cache-hit copies with 0 claims
    under that run_id; AAPL's real 34 claims sit under the superseded
    `1b3a64e5-...` run that originally parsed them.

    Walks the `reused_from_run_id` chain (bounded by a seen-set against a
    cycle) until a run_id with real claims is found, or the chain runs out
    — in which case the original run_id is returned and the caller
    legitimately gets zero claims (a real gap, not this function's to
    paper over further).
    """
    seen: set[str] = set()
    current = run_id
    while current and current not in seen:
        seen.add(current)
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM analysis_claims WHERE ticker = ? AND analysis_type = ? AND run_id = ?",
            (ticker, analysis_type, current),
        ).fetchone()["n"]
        if count > 0:
            return current
        next_row = conn.execute(
            "SELECT reused_from_run_id FROM ai_analysis WHERE ticker = ? AND analysis_type = ? AND run_id = ?",
            (ticker, analysis_type, current),
        ).fetchone()
        current = next_row["reused_from_run_id"] if next_row else None
    return run_id


def _fetch_claims_by_type(ticker: str, conn) -> dict[str, list[dict]]:
    claims_by_type: dict[str, list[dict]] = {}
    current_rows = conn.execute(
        "SELECT run_id, analysis_type FROM ai_analysis WHERE ticker = ? AND is_current = 1",
        (ticker,),
    ).fetchall()
    for row in current_rows:
        claims_run_id = _resolve_claims_run_id(ticker, row["analysis_type"], row["run_id"], conn)
        claim_rows = conn.execute(
            "SELECT claim_id, claim_text, assertion_status FROM analysis_claims "
            "WHERE run_id = ? AND ticker = ? AND analysis_type = ? ORDER BY claim_order",
            (claims_run_id, ticker, row["analysis_type"]),
        ).fetchall()
        claims_by_type[row["analysis_type"]] = [dict(c) for c in claim_rows]
    return claims_by_type


def _fetch_valuation_rows(ticker: str, valuation_run_id: str, conn) -> list[dict]:
    rows = conn.execute(
        "SELECT method, scenario, intrinsic_value_low, intrinsic_value_high, "
        "current_price, margin_of_safety_pct, key_assumptions "
        "FROM valuations WHERE run_id = ? AND ticker = ?",
        (valuation_run_id, ticker),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_quant_rows(ticker: str, quality_run_id: str, conn) -> list[dict]:
    rows = conn.execute(
        "SELECT metric, value, status, sector_percentile, sector_peer_group "
        "FROM quant_scores WHERE run_id = ? AND ticker = ? ORDER BY metric",
        (quality_run_id, ticker),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_quality_row(ticker: str, quality_run_id: str, conn) -> dict | None:
    row = conn.execute(
        "SELECT composite_score, metrics_assessed, metrics_passed, notes "
        "FROM quality_scores WHERE run_id = ? AND ticker = ?",
        (quality_run_id, ticker),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------
# PRD §10 fields template-stitched from the three persona views — no 4th
# consolidation LLM call (sprint-5-plan.md decision 3).
# ---------------------------------------------------------------------

def _stitch_investment_thesis(quality_resp, valuation_resp) -> str:
    """'bull case' in the brief UI is this same text under PRD §10's
    section heading — Quality + Valuation Analyst together make the
    affirmative case; there's no separately-generated bull-case content.
    """
    return f"{quality_resp.verdict}\n\n{valuation_resp.verdict}"


def _stitch_ai_conclusion(quality_resp, bear_resp, valuation_resp, overall_score: float, status: str) -> str:
    return (
        f"Overall score: {overall_score:.1f}/100 — {status}.\n\n"
        f"Quality Analyst: {quality_resp.verdict}\n\n"
        f"Bear Analyst: {bear_resp.verdict}\n\n"
        f"Valuation Analyst: {valuation_resp.verdict}"
    )


def _stitch_key_things_to_monitor(bear_resp) -> str:
    """JSON array of the Bear Analyst's own STATEMENTs — the bear case
    itself is what's worth watching for whether it starts materializing.
    Authored content (PRD §10), not Sprint 6's live monitoring logic.
    """
    return json.dumps([s.text for s in bear_resp.statements])


# ---------------------------------------------------------------------
# Per-company orchestration
# ---------------------------------------------------------------------

def run_committee(
    ticker: str,
    run_id: str,
    valuation_run_id: str,
    quality_run_id: str,
    conn,
    client,
    model_id: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict:
    """Three persona calls -> one committee_verdicts row. Returns a status
    dict, same convention as moat.analysis.persist.run_analysis(), so the
    stage loop in run_pipeline.py can track cost/cap the same way.

    No partial company writes: if any persona refuses or fails validation,
    nothing is persisted for this ticker — but cost already spent on the
    personas that *did* run is still returned, since it's real spend the
    cap must still account for.
    """
    company_row = conn.execute(
        "SELECT name, sector FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()
    if company_row is None:
        return {"ticker": ticker, "outcome": "api_error", "reason": "no company row", "cost_estimate": 0.0}
    company_row = dict(company_row)

    claims_by_type = _fetch_claims_by_type(ticker, conn)
    if not any(claims_by_type.values()):
        return {"ticker": ticker, "outcome": "api_error", "reason": "no current ai_analysis claims", "cost_estimate": 0.0}

    valuation_rows = _fetch_valuation_rows(ticker, valuation_run_id, conn)
    if not valuation_rows:
        return {"ticker": ticker, "outcome": "api_error", "reason": "no valuations row in the latest run", "cost_estimate": 0.0}

    quant_rows = _fetch_quant_rows(ticker, quality_run_id, conn)
    quality_row = _fetch_quality_row(ticker, quality_run_id, conn)
    data_confidence = roll_up_data_confidence(ticker, conn)

    known_claim_ids = {
        c["claim_id"]
        for claims in claims_by_type.values()
        for c in claims
        if c["assertion_status"] == "asserted"
    }

    context_block = build_context_block(
        ticker, company_row, claims_by_type, valuation_rows, quant_rows, quality_row, data_confidence,
    )

    total_cost = 0.0
    responses: dict[str, object] = {}
    for persona in PERSONAS:
        result = call_persona(client, ticker, persona, context_block, model_id=model_id, dry_run=dry_run)
        total_cost += result.cost_estimate

        if dry_run:
            continue
        if result.stop_reason == "refusal":
            return {"ticker": ticker, "outcome": "refused", "reason": f"{persona} persona refused", "cost_estimate": total_cost}

        parsed = parse_persona_response(persona, result.raw_text, known_claim_ids)
        if not parsed.is_valid:
            return {
                "ticker": ticker, "outcome": "validation_failed",
                "reason": f"{persona}: {parsed.validation_errors}", "cost_estimate": total_cost,
            }
        responses[persona] = parsed

    if dry_run:
        return {"ticker": ticker, "outcome": "dry_run", "cost_estimate": 0.0}

    component_scores: dict[str, float] = {}
    for resp in responses.values():
        component_scores.update(resp.scores)
    overall = compute_overall_score(component_scores)
    status = assign_status(overall, responses["bear"].severity)

    investment_thesis = _stitch_investment_thesis(responses["quality"], responses["valuation"])
    ai_conclusion = _stitch_ai_conclusion(
        responses["quality"], responses["bear"], responses["valuation"], overall, status
    )
    key_things_to_monitor = _stitch_key_things_to_monitor(responses["bear"])

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO committee_verdicts (
            run_id, ticker, quality_analyst_view, bear_analyst_view, valuation_analyst_view,
            bear_case_severity, business_quality_score, competitive_moat_score,
            financial_strength_score, management_score, valuation_score, risk_score,
            overall_score, status, data_confidence, investment_thesis,
            key_things_to_monitor, ai_conclusion, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, ticker,
            responses["quality"].raw_text, responses["bear"].raw_text, responses["valuation"].raw_text,
            responses["bear"].severity,
            component_scores["business_quality_score"], component_scores["competitive_moat_score"],
            component_scores["financial_strength_score"], component_scores["management_score"],
            component_scores["valuation_score"], component_scores["risk_score"],
            overall, status, data_confidence, investment_thesis,
            key_things_to_monitor, ai_conclusion, now,
        ),
    )
    conn.commit()
    return {"ticker": ticker, "outcome": "persisted", "cost_estimate": total_cost, "overall_score": overall, "status": status}
