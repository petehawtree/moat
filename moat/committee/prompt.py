"""Context assembly + prompt construction for the three PRD §7 personas
(Sprint 5).

Unlike Sprint 3's W3 (moat/analysis/prompt.py), these calls do not read raw
filing text and do not request the citations API feature — they synthesize
over *already-extracted* evidence (`analysis_claims`, one company's
`valuations` row set) that Sprint 3/4 already grounded and cited. Rebuilding
a second citation-extraction layer on top of that was explicitly decided
against (sprint-5-plan.md decision 1, §A19.6): the brief surfaces each
persona statement's referenced claim's *original* quote (from `citations`)
inline for a human to eyeball, rather than asking the model to re-prove
support for something Sprint 3 already proved once.

Every underlying claim is given a stable bracketed id — its own `claim_id`
— and each persona is instructed to tag which claim(s) support each
STATEMENT it writes, e.g. `STATEMENT: ... [refs: 42, 57]`. Parsing that back
out is `moat/committee/parser.py`'s job; resolving `[refs: N]` to a quote for
display is the dashboard's (C7).
"""
from __future__ import annotations

import json

PROTOCOL_VERSION = "v1"

PERSONAS = ("quality", "bear", "valuation")

# Which of PRD §8's six weighted components each persona is responsible for.
# Quality Analyst ("is this an exceptional business?") naturally covers the
# three ai_analysis angles closest to that question; Valuation Analyst
# ("are we paying a sensible price?") covers pricing and the balance-sheet/
# cash-flow metrics priced off of; Bear Analyst ("why could the thesis be
# wrong?") produces risk_score *and* a separate bear_case_severity that
# feeds assign_status() directly rather than compute_overall_score — a
# severe bear case caps the verdict without needing to be smuggled into the
# weighted sum as an artificially large risk_score (committee.py's existing
# assign_status(overall_score, bear_case_severity) signature already implied
# this split before this module was written; honored here, not re-decided).
PERSONA_SCORE_COMPONENTS = {
    "quality":   ("business_quality_score", "competitive_moat_score", "management_score"),
    "valuation": ("valuation_score", "financial_strength_score"),
    "bear":      ("risk_score",),
}

# Public: parser.py needs this to map a persona's raw "LABEL: number" score
# lines back to the committee_verdicts column each one belongs to.
SCORE_LABELS = {
    "business_quality_score":   "BUSINESS_QUALITY",
    "competitive_moat_score":   "COMPETITIVE_MOAT",
    "management_score":         "MANAGEMENT",
    "valuation_score":          "VALUATION",
    "financial_strength_score": "FINANCIAL_STRENGTH",
    "risk_score":                "RISK",
}

_ANALYSIS_TYPE_LABELS = {
    "business_quality": "BUSINESS QUALITY",
    "moat":              "MOAT",
    "management":        "MANAGEMENT",
    "risk":              "RISK",
}


def _format_claims_block(claims: list[dict]) -> str:
    """Render one analysis_type's claims as bracketed-id lines the model can
    reference back by number. `claims` is already ordered by claim_order.
    """
    lines = []
    for c in claims:
        if c["assertion_status"] == "insufficient_evidence":
            lines.append(f"(insufficient evidence: {c['claim_text']})")
        else:
            lines.append(f"[{c['claim_id']}] {c['claim_text']}")
    return "\n".join(lines) if lines else "(no claims available)"


def _format_valuation_block(valuation_rows: list[dict]) -> str:
    """valuation_rows: this ticker's rows from the latest `valuations` run
    (dicts with method/scenario/intrinsic_value_low/high/current_price/
    margin_of_safety_pct/key_assumptions — same shape the dashboard reads).
    """
    by_method: dict[str, list[dict]] = {}
    for r in valuation_rows:
        by_method.setdefault(r["method"], []).append(r)

    lines = []
    current_price = valuation_rows[0]["current_price"] if valuation_rows else None
    if current_price is not None:
        lines.append(f"Current price: ${current_price:,.2f}")

    dcf_rows = {r["scenario"]: r for r in by_method.get("owner_earnings_dcf", [])}
    for scenario in ("bear", "base", "bull"):
        r = dcf_rows.get(scenario)
        if r is None:
            continue
        low = r["intrinsic_value_low"]
        if low is None:
            lines.append(f"DCF {scenario}: unavailable — {json.loads(r['key_assumptions']).get('reason', 'no data')}")
        elif low <= 0:
            lines.append(f"DCF {scenario}: ${low:,.2f} — bear case negative, not investable on this basis")
        else:
            mos = r["margin_of_safety_pct"]
            mos_str = f"{mos:+.1%}" if mos is not None else "n/a"
            lines.append(f"DCF {scenario}: ${low:,.2f} (margin of safety vs. current price: {mos_str})")

    for method, label in (("fcf_yield", "FCF yield"), ("ev_ebit", "EV/EBIT")):
        rows = by_method.get(method)
        if not rows:
            continue
        ka = json.loads(rows[0]["key_assumptions"])
        if ka.get("status") == "unavailable":
            lines.append(f"{label}: unavailable — {ka.get('reason')}")
        elif method == "fcf_yield":
            lines.append(f"{label}: {ka['fcf_yield']:.1%}")
        else:
            lines.append(f"{label}: {ka['ev_ebit_multiple']:.1f}x")

    pe_rows = by_method.get("pe_historical")
    if pe_rows:
        ka = json.loads(pe_rows[0]["key_assumptions"])
        if ka.get("status") == "unavailable":
            lines.append(f"P/E vs. own history: unavailable — {ka.get('reason')}")
        else:
            cur = ka.get("current")
            cur_str = f"{cur:.1f}x" if cur is not None else "n/a (loss-making)"
            lines.append(
                f"P/E vs. own history: current {cur_str}, range "
                f"[{ka['low']:.1f}x – {ka['high']:.1f}x] over {ka['years_covered']}yr"
                + (" (LOW CONFIDENCE — thin price history)" if ka.get("low_confidence") else "")
            )
    return "\n".join(lines) if lines else "(no valuation data available)"


def _format_quant_block(quant_rows: list[dict], quality_row: dict | None) -> str:
    lines = []
    for r in quant_rows:
        if r["status"] in ("unavailable", "not_applicable"):
            lines.append(f"{r['metric']}: {r['status']}")
            continue
        if r["value"] is None:
            # A metric can reach a verdict without a comparable number —
            # e.g. debt outstanding with no positive FCF to service it
            # fails the debt metric outright even though debt/FCF itself
            # is left None rather than divided by a non-positive
            # denominator (moat/screen/quant_screen.py's
            # _absolute_floor_pass docstring). Found running this pilot
            # against real data (§A19.1): 141/8-metric rows in the current
            # quality run are exactly this shape, mostly 'debt: fail'.
            lines.append(f"{r['metric']}: {r['status']} (no comparable ratio computed)")
            continue
        pct = f", {r['sector_percentile']:.0f}th percentile in {r['sector_peer_group']}" if r["sector_percentile"] is not None else ""
        lines.append(f"{r['metric']}: {r['value']:.4f} ({r['status']}{pct})")
    if quality_row is not None:
        lines.append(
            f"composite_score: {quality_row['composite_score']:.1f} "
            f"({quality_row['metrics_passed']}/{quality_row['metrics_assessed']} assessable metrics passed)"
        )
    return "\n".join(lines) if lines else "(no quant screen data available)"


def build_context_block(
    ticker: str,
    company_row: dict,
    claims_by_type: dict[str, list[dict]],
    valuation_rows: list[dict],
    quant_rows: list[dict],
    quality_row: dict | None,
    data_confidence: str,
) -> str:
    """One shared context block, identical across all three persona calls
    for this company — everything downstream of Sprint 3/4's own outputs,
    nothing re-derived.
    """
    sections = [
        f"COMPANY: {ticker} — {company_row['name']} ({company_row['sector'] or 'no GICS sector'})",
        f"DATA CONFIDENCE: {data_confidence} (worst tier across this company's ingested fundamentals — see PRD_ADDENDUM.md §A4)",
        "",
    ]
    for at in ("business_quality", "moat", "management", "risk"):
        sections.append(f"=== {_ANALYSIS_TYPE_LABELS[at]} (prior filing-grounded analysis) ===")
        sections.append(_format_claims_block(claims_by_type.get(at, [])))
        sections.append("")
    sections.append("=== QUANTITATIVE SCREEN (sector-relative, docs/PRD_ADDENDUM.md §A2) ===")
    sections.append(_format_quant_block(quant_rows, quality_row))
    sections.append("")
    sections.append("=== VALUATION (Owner Earnings DCF + cross-checks, docs/PRD_ADDENDUM.md §A16) ===")
    sections.append(_format_valuation_block(valuation_rows))
    return "\n".join(sections)


_SYSTEM_PROMPTS = {
    "quality": """\
You are the Quality Analyst on an investment committee (PRD §7). Your question: \
is this an exceptional business? Assess business quality, competitive moat and \
management/capital allocation using ONLY the CONTEXT block provided — do not use \
outside knowledge of this company.

## Output format — follow exactly

## VERDICT
<one paragraph: your overall take on whether this is an exceptional business>

## SCORES
BUSINESS_QUALITY: <integer 0-100>
COMPETITIVE_MOAT: <integer 0-100>
MANAGEMENT: <integer 0-100>

## STATEMENTS
STATEMENT: <one atomic statement supporting your verdict> [refs: <claim id>, <claim id>]
STATEMENT: <another>

## Rules

1. Make 4-8 STATEMENT lines total, each citing the bracketed claim id(s) from the \
CONTEXT block that support it — e.g. [refs: 42] or [refs: 42, 57]. [refs: ...] takes \
ONLY the bracketed integer id(s) shown before a claim in the CONTEXT block — never a \
metric name, ticker, or any other word. The QUANTITATIVE SCREEN and VALUATION \
sections have no claim ids; reference a figure from either by name directly in the \
STATEMENT text, with no [refs: ...] tag at all.
2. A statement with no clear support in the CONTEXT block does not become a \
STATEMENT — say so in the VERDICT instead ("insufficient evidence on X").
3. Never invent facts not present in the CONTEXT block.
4. Scores are 0-100, where 100 is the strongest case a disciplined analyst could \
make from this evidence, not a generic industry average.
""",
    "bear": """\
You are the Bear Analyst on an investment committee (PRD §7). Your question: why \
could this thesis be wrong? Challenge the business, the moat, the management \
narrative and the financials using ONLY the CONTEXT block provided — do not use \
outside knowledge of this company. PRD §1: every candidate receives a bear-case \
analysis; your job is to find the real case against, not to be reflexively negative \
where the evidence doesn't support it.

## Output format — follow exactly

## VERDICT
<one paragraph: the strongest case that this thesis is wrong, or an honest \
statement that the evidence doesn't support a strong bear case>

## SCORES
RISK: <integer 0-100, where 100 is highest risk>
SEVERITY: <low|medium|high — how much this bear case alone should cap the overall verdict>

## STATEMENTS
STATEMENT: <one atomic risk/weakness> [refs: <claim id>, <claim id>]
STATEMENT: <another>

## Rules

1. Make 4-8 STATEMENT lines total, each citing the bracketed claim id(s) from the \
CONTEXT block that support it. [refs: ...] takes ONLY the bracketed integer id(s) \
shown before a claim — never a metric name, ticker, or any other word. The \
QUANTITATIVE SCREEN and VALUATION sections have no claim ids; reference a figure \
from either by name directly in the STATEMENT text, with no [refs: ...] tag at all.
2. A concern with no clear support in the CONTEXT block does not become a \
STATEMENT — say so in the VERDICT instead.
3. Never invent facts not present in the CONTEXT block.
4. SEVERITY is categorical, not derived from RISK alone: 'high' means this bear \
case alone should prevent an "Investigate" verdict regardless of how strong the \
rest of the committee's case is; 'low' means real but survivable risks; 'medium' \
is the default when risks are real but not thesis-breaking.
""",
    "valuation": """\
You are the Valuation Analyst on an investment committee (PRD §7). Your question: \
are we paying a sensible price? Assess the valuation and financial strength using \
ONLY the CONTEXT block provided — do not use outside knowledge of this company.

## Output format — follow exactly

## VERDICT
<one paragraph: is the current price sensible against this evidence, and how \
strong is the balance sheet/cash generation behind it>

## SCORES
VALUATION: <integer 0-100, where 100 is the most attractively priced case the evidence supports>
FINANCIAL_STRENGTH: <integer 0-100>

## STATEMENTS
STATEMENT: <one atomic statement supporting your verdict> [refs: <claim id>, <claim id>]
STATEMENT: <another>

## Rules

1. Make 4-8 STATEMENT lines total. Valuation/quant figures in the CONTEXT block \
(DCF, FCF yield, EV/EBIT, P/E, screen metrics) don't carry claim ids — reference \
them by name in the STATEMENT text itself rather than a [refs: ...] tag; only tag \
claim ids when referencing the qualitative CONTEXT sections.
2. PRD §1: use the bear-case (low) end of the DCF range, never the midpoint, when \
the STATEMENT concerns margin of safety.
3. Never invent facts not present in the CONTEXT block.
4. A company whose DCF or key metrics report "unavailable" or "LOW CONFIDENCE" \
should have that visibly reflected in FINANCIAL_STRENGTH/VALUATION, not silently \
ignored.
""",
}

_USER_REQUEST = "Assess {ticker} as the {persona_title} using the CONTEXT block above, following the system prompt's exact output format."

_PERSONA_TITLES = {"quality": "Quality Analyst", "bear": "Bear Analyst", "valuation": "Valuation Analyst"}


def build_request(persona: str, ticker: str, context_block: str) -> tuple[str, str]:
    """Returns (system_prompt, user_message) for one persona's call."""
    if persona not in _SYSTEM_PROMPTS:
        raise ValueError(f"unknown persona: {persona}")
    user_message = (
        f"{context_block}\n\n"
        f"{_USER_REQUEST.format(ticker=ticker, persona_title=_PERSONA_TITLES[persona])}"
    )
    return _SYSTEM_PROMPTS[persona], user_message
