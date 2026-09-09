"""W4: parse, resolve and validate the raw API response (Sprint 3).

Consumes a CallResult (W3) and produces a ParsedResponse ready for W5 to persist.

The API returns interleaved content blocks:
  {"type": "text", "text": "## BUSINESS QUALITY\n\nCLAIM: ", "citations": null}
  {"type": "text", "text": "<claim body>", "citations": [{...}]}
  {"type": "text", "text": "\n\nCLAIM: ", "citations": null}
  ...

Claims are parsed from that stream, not inferred from block boundaries alone.
Each asserted CLAIM must carry at least one citation; coverage must equal 1.0.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path

from moat.analysis.caller import CallResult
from moat.analysis.prompt import ANALYSIS_TYPES

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RawCitation:
    document_index: int
    start_char: int
    end_char: int
    cited_text: str
    # Resolved fields — populated by resolve()
    accession_number: str = ""
    section_id: str = ""
    doc_sha256: str = ""
    norm_version: str = ""
    quote_sha256: str = ""
    prefix: str = ""
    suffix: str = ""


@dataclasses.dataclass
class ParsedClaim:
    analysis_type: str
    claim_order: int           # 1-based within the analysis_type
    claim_text: str
    assertion_status: str      # 'asserted' | 'insufficient_evidence'
    citations: list[RawCitation]


@dataclasses.dataclass
class ParsedResponse:
    ticker: str
    accession: str
    claims: list[ParsedClaim]
    claim_coverage: float      # asserted claims with ≥1 citation ÷ asserted claims
    validation_errors: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors


# ---------------------------------------------------------------------------
# Section headers recognised in the model output
# ---------------------------------------------------------------------------

_HEADER_TO_TYPE = {
    "BUSINESS QUALITY": "business_quality",
    "MOAT":             "moat",
    "MANAGEMENT":       "management",
    "RISK":             "risk",
}

_HEADER_RE = re.compile(
    r"##\s+(" + "|".join(re.escape(h) for h in _HEADER_TO_TYPE) + r")\b",
    re.IGNORECASE,
)

_CLAIM_PREFIX = "CLAIM:"
_IE_PREFIX    = "INSUFFICIENT EVIDENCE:"

# Sprint 3.1 (item 5b): the model volunteers a markdown horizontal rule
# between the four protocol sections — not requested by the prompt, but a
# common enough model habit (confirmed on AAPL, KO, JPM in the Sprint 3
# pilot). It routinely lands inside the same content block as the section's
# last cited claim (citation blocks aren't split by _SPLIT_RE, so the
# trailing dashes end up glued onto claim_text via a plain .strip(), which
# only trims whitespace). Stripped at the one place all claim text is
# finalized (_add()) rather than added to _SPLIT_RE, which drives citation
# vs. plain-block dispatch and isn't the layer this cosmetic issue lives at.
_TRAILING_DIVIDER_RE = re.compile(r"\n*-{3,}\s*\Z")


# ---------------------------------------------------------------------------
# Step 1 — reconstruct a flat token stream from content blocks
# ---------------------------------------------------------------------------

def _reconstruct_stream(content_blocks: list[dict]) -> list[tuple[str, list[dict]]]:
    """Return a list of (text_fragment, citations_list) pairs.

    Each block contributes one pair. The citations list is empty for blocks
    that carry no citations (connective text, headers, etc.).
    """
    stream = []
    for block in content_blocks:
        if block.get("type") != "text":
            continue
        text = block.get("text") or ""
        citations = block.get("citations") or []
        stream.append((text, citations))
    return stream


# ---------------------------------------------------------------------------
# Step 2 — parse claims from the stream
# ---------------------------------------------------------------------------

# Splitting regex: captures section headers, CLAIM:, INSUFFICIENT EVIDENCE:
# so they appear as elements in re.split's output list.
_SPLIT_RE = re.compile(
    r"(##\s+(?:BUSINESS QUALITY|MOAT|MANAGEMENT|RISK)\b[^\n]*"
    r"|INSUFFICIENT EVIDENCE:"
    r"|CLAIM:)",
    re.IGNORECASE,
)


def _make_raw_cite(c: dict) -> RawCitation:
    return RawCitation(
        document_index=c["document_index"],
        start_char=c["start_char_index"],
        end_char=c["end_char_index"],
        cited_text=c.get("cited_text", ""),
    )


def _parse_claims(stream: list[tuple[str, list[dict]]]) -> list[ParsedClaim]:
    """Walk the stream block-by-block and extract structured claims.

    Cited blocks (non-empty citations list) are always claim bodies.
    Non-cited blocks are split by delimiter (## HEADER, CLAIM:, INSUFFICIENT
    EVIDENCE:) so compound blocks like '## MOAT\\n\\nCLAIM: ' are handled
    correctly without merging adjacent citationless blocks first.

    State: current_type tracks the active analysis section; pending_claim is
    True when a CLAIM: marker has been seen and the body has not yet arrived.
    """
    claims: list[ParsedClaim] = []
    current_type: str | None = None
    type_claim_counts: dict[str, int] = {}
    pending_claim: bool = False

    def _add(text: str, status: str, citations: list[RawCitation]) -> None:
        nonlocal pending_claim
        text = _TRAILING_DIVIDER_RE.sub("", text).rstrip()
        if not current_type or not text:
            return
        type_claim_counts[current_type] = type_claim_counts.get(current_type, 0) + 1
        claims.append(ParsedClaim(
            analysis_type=current_type,
            claim_order=type_claim_counts[current_type],
            claim_text=text,
            assertion_status=status,
            citations=citations,
        ))
        pending_claim = False

    for text, cites in stream:
        if cites:
            raw_cites = [_make_raw_cite(c) for c in cites if c.get("type") == "char_location"]

            if _SPLIT_RE.search(text):
                # Compound cited block: contains protocol markers (CLAIM:, header, etc.).
                # Cancel any outer pending_claim — we can't safely bind the pre-marker
                # text to the claim waiting for a body. Only bind citations to claim
                # bodies that follow an explicit CLAIM: marker within this block.
                pending_claim = False
                parts = _SPLIT_RE.split(text)
                i = 0
                while i < len(parts):
                    fragment = parts[i]
                    header_m = _HEADER_RE.match(fragment.strip())
                    if header_m:
                        current_type = _HEADER_TO_TYPE[header_m.group(1).upper()]
                        type_claim_counts.setdefault(current_type, 0)
                        i += 1
                        continue
                    frag_upper = fragment.strip().upper()
                    if frag_upper == _CLAIM_PREFIX:
                        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                        if body:
                            _add(body, "asserted", raw_cites)
                            i += 2
                        else:
                            pending_claim = True
                            i += 1
                        continue
                    if frag_upper == _IE_PREFIX:
                        ie_body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                        if ie_body:
                            _add(ie_body, "insufficient_evidence", [])
                            i += 2
                        else:
                            i += 1
                        continue
                    i += 1
            else:
                # Normal case: entire cited block is the body for the pending claim.
                body = text.strip()
                if body and pending_claim:
                    _add(body, "asserted", raw_cites)
                else:
                    pending_claim = False
            continue

        # Non-cited block: split by delimiters so compound blocks are handled.
        # re.split with a capturing group keeps the delimiters in the list.
        # Result: [before, delim1, between1, delim2, between2, ...]
        parts = _SPLIT_RE.split(text)

        i = 0
        while i < len(parts):
            fragment = parts[i]

            header_m = _HEADER_RE.match(fragment.strip())
            if header_m:
                current_type = _HEADER_TO_TYPE[header_m.group(1).upper()]
                type_claim_counts.setdefault(current_type, 0)
                pending_claim = False
                i += 1
                continue

            frag_upper = fragment.strip().upper()

            if frag_upper == _CLAIM_PREFIX:
                # Look ahead for an inline body (the next non-empty fragment
                # before the next delimiter).
                body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                if body:
                    # Body is inline in this non-cited block → uncited claim.
                    _add(body, "asserted", [])
                    i += 2  # consume the body fragment too
                else:
                    # Body is in the next CITED block.
                    pending_claim = True
                    i += 1
                continue

            if frag_upper == _IE_PREFIX:
                ie_body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                if ie_body:
                    _add(ie_body, "insufficient_evidence", [])
                    i += 2
                else:
                    i += 1
                continue

            # Plain text fragment: if we're waiting for a claim body and this
            # fragment is non-empty (and not a delimiter itself), it's an
            # uncited claim body that arrived in a non-cited block.
            if pending_claim and fragment.strip() and current_type:
                _add(fragment.strip(), "asserted", [])

            i += 1

    return claims


# ---------------------------------------------------------------------------
# Step 3 — resolve citations against the stored document text
# ---------------------------------------------------------------------------

def resolve_citations(
    claims: list[ParsedClaim],
    document_map: dict[int, int],   # document_index → filing_document_id
    conn,
    context_chars: int = 48,
) -> list[str]:
    """Resolve each RawCitation to durable anchor fields.

    Performs byte-equality check: the cited_text from the API must match
    the character range [start_char:end_char] in the stored section text exactly.
    Returns a list of validation error strings (empty = all OK).
    """
    errors: list[str] = []

    # Load section texts keyed by filing_document_id.
    fdi_to_info: dict[int, dict] = {}
    for doc_idx, fdi in document_map.items():
        if fdi in fdi_to_info:
            continue
        row = conn.execute(
            "SELECT local_path, doc_sha256, norm_version, section_id, accession_number "
            "FROM filing_documents WHERE filing_document_id = ?",
            (fdi,),
        ).fetchone()
        if not row:
            errors.append(f"filing_document_id {fdi} not found in filing_documents")
            continue
        path = Path(row["local_path"])
        if not path.exists():
            errors.append(f"section file missing: {row['local_path']}")
            continue
        fdi_to_info[fdi] = {
            "text":             path.read_text(encoding="utf-8"),
            "doc_sha256":       row["doc_sha256"],
            "norm_version":     row["norm_version"],
            "section_id":       row["section_id"],
            "accession_number": row["accession_number"],
        }

    for claim in claims:
        for cite in claim.citations:
            doc_idx = cite.document_index
            if doc_idx not in document_map:
                errors.append(
                    f"document_index {doc_idx} in citation not in document_map "
                    f"(claim: {claim.claim_text[:60]!r})"
                )
                continue

            fdi = document_map[doc_idx]
            if fdi not in fdi_to_info:
                continue  # error already recorded above

            info = fdi_to_info[fdi]
            text = info["text"]
            start, end = cite.start_char, cite.end_char

            if start < 0 or end > len(text) or end <= start:
                errors.append(
                    f"citation offsets [{start}:{end}] out of range "
                    f"(text length {len(text)}) in document_index {doc_idx}"
                )
                continue

            extracted = text[start:end]
            if extracted != cite.cited_text:
                errors.append(
                    f"byte-equality check failed for claim {claim.claim_order} "
                    f"({claim.analysis_type}): "
                    f"offset range gives {extracted[:80]!r}, "
                    f"API reported {cite.cited_text[:80]!r}"
                )
                continue

            # Populate resolved fields.
            cite.accession_number = info["accession_number"]
            cite.section_id       = info["section_id"]
            cite.doc_sha256       = info["doc_sha256"]
            cite.norm_version     = info["norm_version"]
            cite.quote_sha256     = hashlib.sha256(cite.cited_text.encode()).hexdigest()
            cite.prefix           = text[max(0, start - context_chars): start]
            cite.suffix           = text[end: end + context_chars]

    return errors


# ---------------------------------------------------------------------------
# Step 4 — validate coverage
# ---------------------------------------------------------------------------

def _compute_coverage(claims: list[ParsedClaim]) -> float:
    asserted = [c for c in claims if c.assertion_status == "asserted"]
    if not asserted:
        return 1.0
    cited = [c for c in asserted if c.citations]
    return len(cited) / len(asserted)


def _validate(
    claims: list[ParsedClaim],
    resolution_errors: list[str],
    result: CallResult,
) -> list[str]:
    errors = list(resolution_errors)

    if result.stop_reason == "max_tokens":
        errors.append("response was truncated (stop_reason=max_tokens)")

    found_types = {c.analysis_type for c in claims}
    for at in ANALYSIS_TYPES:
        if at not in found_types:
            errors.append(f"missing analysis section: {at}")

    asserted = [c for c in claims if c.assertion_status == "asserted"]
    uncited  = [c for c in asserted if not c.citations]
    for c in uncited:
        errors.append(
            f"asserted claim without citation "
            f"({c.analysis_type} #{c.claim_order}): {c.claim_text[:80]!r}"
        )

    return errors


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_and_validate(result: CallResult, conn) -> ParsedResponse:
    """Parse a CallResult and validate all citations.

    Returns a ParsedResponse. If is_valid is False, validation_errors
    describes every problem — W5 must not write ai_analysis rows.
    """
    stream = _reconstruct_stream(result.content_blocks)
    claims = _parse_claims(stream)

    # document_map keys are strings when deserialised from JSON
    doc_map = {int(k): v for k, v in result.document_map.items()}

    resolution_errors = resolve_citations(claims, doc_map, conn)
    validation_errors = _validate(claims, resolution_errors, result)

    coverage = _compute_coverage(claims)

    return ParsedResponse(
        ticker=result.ticker,
        accession=result.accession,
        claims=claims,
        claim_coverage=coverage,
        validation_errors=validation_errors,
    )
