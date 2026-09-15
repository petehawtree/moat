"""Parse one persona's raw response text (Sprint 5).

Much simpler than Sprint 3's W4 (moat/analysis/parser.py): there is no
citations API stream to reconstruct, because these calls don't request
citations at all (see prompt.py's module docstring for why). The response
is plain text in a fixed section format; this module regex-parses it and
validates that every STATEMENT carrying `[refs: ...]` references claim ids
that actually exist in the claims this persona was given — the one
correctness property worth enforcing, matching this codebase's "verify
before persisting" instinct without reinventing citation-anchor resolution.
"""
from __future__ import annotations

import dataclasses
import re

from moat.committee.prompt import PERSONA_SCORE_COMPONENTS, SCORE_LABELS


@dataclasses.dataclass
class ParsedStatement:
    text: str
    refs: list[int]


@dataclasses.dataclass
class ParsedPersonaResponse:
    persona: str
    verdict: str
    scores: dict[str, float]          # PRD §8 column name -> 0-100
    severity: str | None              # bear only
    statements: list[ParsedStatement]
    raw_text: str
    is_valid: bool
    validation_errors: list[str]


_VERDICT_RE = re.compile(r"##\s*VERDICT\s*\n(.*?)(?=\n##\s*SCORES|\Z)", re.S | re.I)
_SCORES_RE = re.compile(r"##\s*SCORES\s*\n(.*?)(?=\n##\s*STATEMENTS|\Z)", re.S | re.I)
_STATEMENTS_RE = re.compile(r"##\s*STATEMENTS\s*\n(.*)\Z", re.S | re.I)
_SCORE_LINE_RE = re.compile(r"^([A-Z_]+)\s*:\s*(\d+(?:\.\d+)?)\s*$", re.M)
_SEVERITY_LINE_RE = re.compile(r"^SEVERITY\s*:\s*(low|medium|high)\s*$", re.M | re.I)
# The bracket content is captured permissively (`.*?`, not `[\d,\s]+`) —
# found running the first real pilot against live data: a persona emitted
# non-numeric refs like `[refs: roic]` and `[refs: DCF bear]` (referencing a
# quantitative concept by name instead of a claim id, ignoring the system
# prompt's own rule). A digits-only character class simply fails to match
# those brackets at all, so `(.+?)` swallows the literal "[refs: roic]" text
# into the STATEMENT itself — invisible to validation, the opposite of
# "reject a hallucinated ref" this parser exists to do. Capturing broadly
# and validating each comma-separated token below (parse_persona_response)
# is what actually catches it.
_STATEMENT_LINE_RE = re.compile(
    r"^STATEMENT:\s*(.+?)(?:\s*\[refs:\s*(.*?)\s*\])?\s*$", re.M
)


def extract_verdict(raw_text: str) -> str:
    """Best-effort VERDICT extraction with no validation — for the dashboard
    (C7), which renders whatever a stored persona response actually
    contains rather than refusing to display something imperfect."""
    m = _VERDICT_RE.search(raw_text)
    return m.group(1).strip() if m else ""


def extract_statements(raw_text: str) -> list[ParsedStatement]:
    """Best-effort STATEMENT/[refs: ...] extraction with no validation —
    same rationale as extract_verdict(). This is what the brief UI (C7)
    uses to resolve each `[refs: N]` back to its original filing quote for
    inline display (sprint-5-plan.md decision 1, §A19.6)."""
    statements_m = _STATEMENTS_RE.search(raw_text)
    block = statements_m.group(1) if statements_m else ""
    out = []
    for m in _STATEMENT_LINE_RE.finditer(block):
        text = m.group(1).strip()
        refs_raw = m.group(2)
        # Best-effort: silently drop a blank or non-numeric token (e.g. a
        # stray "[refs: roic]") rather than erroring — this function has no
        # validation contract, unlike parse_persona_response below.
        refs = (
            [int(r.strip()) for r in refs_raw.split(",") if r.strip().isdigit()]
            if refs_raw else []
        )
        out.append(ParsedStatement(text=text, refs=refs))
    return out


def parse_persona_response(persona: str, raw_text: str, known_claim_ids: set[int]) -> ParsedPersonaResponse:
    """`known_claim_ids`: every claim_id this persona's context block
    actually offered — used to catch a hallucinated reference id before it
    reaches the dashboard as a broken link.
    """
    errors: list[str] = []

    verdict_m = _VERDICT_RE.search(raw_text)
    verdict = verdict_m.group(1).strip() if verdict_m else ""
    if not verdict:
        errors.append("missing or empty ## VERDICT section")

    scores_m = _SCORES_RE.search(raw_text)
    scores_block = scores_m.group(1) if scores_m else ""
    raw_scores = {m.group(1): float(m.group(2)) for m in _SCORE_LINE_RE.finditer(scores_block)}

    expected_labels = {SCORE_LABELS[col] for col in PERSONA_SCORE_COMPONENTS[persona]}
    label_to_col = {SCORE_LABELS[col]: col for col in PERSONA_SCORE_COMPONENTS[persona]}
    scores: dict[str, float] = {}
    for label in expected_labels:
        if label not in raw_scores:
            errors.append(f"missing score: {label}")
            continue
        value = raw_scores[label]
        if not (0.0 <= value <= 100.0):
            errors.append(f"score {label}={value} out of range [0, 100]")
        scores[label_to_col[label]] = max(0.0, min(100.0, value))

    severity = None
    if persona == "bear":
        sev_m = _SEVERITY_LINE_RE.search(scores_block)
        if sev_m:
            severity = sev_m.group(1).lower()
        else:
            errors.append("missing SEVERITY line (bear persona)")

    statements_m = _STATEMENTS_RE.search(raw_text)
    statements_block = statements_m.group(1) if statements_m else ""
    statements: list[ParsedStatement] = []
    for m in _STATEMENT_LINE_RE.finditer(statements_block):
        text = m.group(1).strip()
        refs_raw = m.group(2)
        refs: list[int] = []
        if refs_raw:
            for token in refs_raw.split(","):
                token = token.strip()
                if not token:
                    continue
                if not token.isdigit():
                    if persona == "valuation":
                        # Found running the first real pilot against live
                        # data: the Valuation Analyst — whose statements are
                        # grounded in the CONTEXT block's own quant/DCF
                        # figures, never in a claim_id (prompt.py's own rule
                        # 1 tells it so explicitly) — still sometimes tags a
                        # figure with a descriptive, non-numeric bracket like
                        # "[refs: DCF bear]". That's a stray label on an
                        # already-grounded number, not a hallucinated
                        # citation the way it would be for Quality/Bear
                        # (whose claim_id mechanism is the actual evidence
                        # trail) — 3/16 real companies hit this and were
                        # discarded (and re-billed) before this carve-out.
                        # Drop the tag silently rather than fail the whole
                        # response over a formatting slip on content that
                        # was never claim-grounded to begin with.
                        continue
                    # For quality/bear, a non-numeric ref IS exactly as
                    # untrustworthy as a hallucinated claim id — those
                    # personas' statements are supposed to trace to a real
                    # cited filing quote, and a malformed ref means they
                    # don't. Must fail validation, not be silently dropped
                    # or (the original bug) invisibly folded into the
                    # STATEMENT's own text by a regex that simply couldn't
                    # match it.
                    errors.append(f"STATEMENT has a non-numeric ref {token!r}: {text[:60]!r}")
                    continue
                refs.append(int(token))
        for r in refs:
            if r not in known_claim_ids:
                errors.append(f"STATEMENT references unknown claim id {r}: {text[:60]!r}")
        statements.append(ParsedStatement(text=text, refs=refs))

    if not statements:
        errors.append("no STATEMENT lines found")

    return ParsedPersonaResponse(
        persona=persona,
        verdict=verdict,
        scores=scores,
        severity=severity,
        statements=statements,
        raw_text=raw_text,
        is_valid=not errors,
        validation_errors=errors,
    )
