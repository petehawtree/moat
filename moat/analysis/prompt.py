"""Prompt construction for the W3 combined citation-enabled request (Sprint 3).

One request per company, four analyses, plain-text document blocks.
Plain text (not HTML) is what returns char_location citations.
"""
from __future__ import annotations

PROTOCOL_VERSION = "v1"

ANALYSIS_TYPES = ("business_quality", "moat", "management", "risk")

# The three sections a filing is expected to supply. Single source of truth —
# import this rather than re-typing the literal; a bundle key's cache
# behavior depends on gap_sections being computed identically everywhere.
REQUIRED_SECTIONS = ("item_1", "item_1a", "item_7")

_SECTION_LABELS = {
    "item_1":  "Item 1: Business",
    "item_1a": "Item 1A: Risk Factors",
    "item_7":  "Item 7: MD&A",
    "full":    "Full Filing",
}


def compute_gap_sections(section_texts: dict[str, str]) -> list[str]:
    """Return the REQUIRED_SECTIONS missing from section_texts, sorted.

    Empty when a full_fallback was used ("full" in section_texts) — the full
    document covers everything, so there's no gap to call out. Callers must
    use this (not a re-typed literal) so build_request() always sees the same
    gap_sections for the same inputs — the prompt (and its sha256, and the
    bundle cache key derived from it) depends on it.
    """
    if "full" in section_texts:
        return []
    return sorted(set(REQUIRED_SECTIONS) - set(section_texts))


SYSTEM_PROMPT = """\
You are an investment analyst reviewing a company's SEC 10-K annual report.
Produce four analyses: business quality, moat, management, and risk.

## Output format — follow exactly

## BUSINESS QUALITY
CLAIM: <one specific, atomic, citable statement about the business>
CLAIM: <another>
INSUFFICIENT EVIDENCE: <what you could not find or could not cite>

## MOAT
CLAIM: <...>

## MANAGEMENT
NOTE: 10-K only; not an assessment of compensation, incentives, governance or track record.
CLAIM: <...>

## RISK
CLAIM: <...>

## Rules

1. Make 5–8 CLAIM lines per section.
2. Each CLAIM must be one atomic, self-contained statement directly supported by \
the provided documents. Cite every CLAIM.
3. Write no narrative prose. Every statement is either a CLAIM or an \
INSUFFICIENT EVIDENCE — nothing else.
4. Use INSUFFICIENT EVIDENCE: when a material topic cannot be directly cited. \
These are not failures; they are honest disclosures.
5. Do not number the CLAIM lines.
6. Management analysis covers only what is stated in the 10-K.
7. Never invent facts. If it is not in the documents, use INSUFFICIENT EVIDENCE.

## Worked example (abbreviated)

## BUSINESS QUALITY
CLAIM: The company generated $394 billion in net sales in fiscal 2024, up from $383 billion in 2023.
CLAIM: The iPhone segment accounts for approximately 52% of total revenue.
INSUFFICIENT EVIDENCE: Breakdown of gross margin by product line is not disclosed.

## MOAT
CLAIM: The company operates a closed ecosystem where hardware, software and services \
reinforce switching costs.
"""

_ANALYSIS_REQUEST = """\
Analyse {ticker}'s 10-K for the period ending {period} and produce the four sections \
(BUSINESS QUALITY, MOAT, MANAGEMENT, RISK) using the format specified in the system prompt.\
"""


def build_request(
    sections: dict[str, str],
    ticker: str,
    period: str,
    filing_document_ids: dict[str, int],
    gap_sections: list[str] | None = None,
) -> tuple[list[dict], dict[int, int]]:
    """Build the user-message content array and document_map for the API call.

    sections: {section_id: normalized_text} — only the sections to include
    filing_document_ids: {section_id: filing_document_id}
    gap_sections: section_ids that are incorporated by reference (unavailable for
      citation); a plain-text notice is injected before the analysis request.

    Returns:
      content: list of document blocks + final text instruction
      document_map: {document_index (0-based): filing_document_id}
        — must be stored in analysis_attempts so W4 can resolve citations
    """
    content: list[dict] = []
    document_map: dict[int, int] = {}

    # Preferred section order for the model: narrative first, risk second, MDA last.
    section_order = [s for s in ("item_1", "item_1a", "item_7", "full") if s in sections]

    for idx, section_id in enumerate(section_order):
        content.append({
            "type": "document",
            "source": {
                "type": "text",
                "media_type": "text/plain",
                "data": sections[section_id],
            },
            "title": f"{ticker} 10-K ({period}) — {_SECTION_LABELS[section_id]}",
            "citations": {"enabled": True},
        })
        document_map[idx] = filing_document_ids[section_id]

    if gap_sections:
        gap_lines = "\n".join(
            f"- {_SECTION_LABELS.get(s, s)}" for s in gap_sections
        )
        content.append({
            "type": "text",
            "text": (
                f"NOTE: The following section(s) are incorporated by reference "
                f"in this filing and are not available for citation:\n{gap_lines}\n"
                f"Use INSUFFICIENT EVIDENCE for any claim that would require them."
            ),
        })

    content.append({
        "type": "text",
        "text": _ANALYSIS_REQUEST.format(ticker=ticker, period=period),
    })

    return content, document_map
