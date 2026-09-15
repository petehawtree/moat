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
_STATEMENT_LINE_RE = re.compile(
    r"^STATEMENT:\s*(.+?)(?:\s*\[refs:\s*([\d,\s]+)\])?\s*$", re.M
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
        refs = [int(r.strip()) for r in refs_raw.split(",")] if refs_raw else []
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
        refs = [int(r.strip()) for r in refs_raw.split(",")] if refs_raw else []
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
