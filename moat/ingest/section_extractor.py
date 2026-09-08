"""Section extractor for SEC 10-K filings.

Rules and thresholds pre-registered in docs/sprints/sprint-3-section-extraction-rules.md.
Changing any threshold requires a measured distribution recorded in sprint-3.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

NORM_VERSION = "v1"

# Pre-registered thresholds (sprint-3-section-extraction-rules.md §4)
ITEM_1_HARD_FLOOR  = 5_000
ITEM_1_HIGH_FLOOR  = 15_000
ITEM_1A_HARD_FLOOR = 5_000
ITEM_1A_HIGH_FLOOR = 20_000
ITEM_7_HARD_FLOOR  = 5_000
ITEM_7_HIGH_FLOOR  = 15_000
SECTION_MAX_RATIO  = 0.60
ALPHA_RATIO_MIN    = 0.55

_TOC_CLUSTER_WINDOW    = 3_000
_TOC_CLUSTER_THRESHOLD = 4      # ≥ N distinct item numbers in window → ToC
_DOT_LEADER_WINDOW     = 120
_PART_BEFORE_WINDOW    = 80
_PART_AFTER_WINDOW     = 200
_INCORP_WINDOW         = 500

# Longest phrases first so endswith checks terminate early on a hit
_CROSS_REF_PHRASES = (
    "described in", "included in", "pursuant to", "refer to",
    "referred to", "under", "see", "in",
)

_D = r"[-–—]"  # hyphen, en-dash, em-dash

_PAT: dict[str, re.Pattern] = {
    "item_1":  re.compile(rf"ITEM\s+1(?![0-9A-Za-z])\.?\s*(?:{_D})?\s*(BUSINESS\b)?", re.I),
    "item_1a": re.compile(rf"ITEM\s+1A(?!\w)\.?\s*(?:{_D})?\s*(RISK\b)?", re.I),
    "item_1b": re.compile(rf"ITEM\s+1B(?!\w)\.?\s*(?:{_D})?\s*(UNRESOLVED\b)?", re.I),
    "item_1c": re.compile(rf"ITEM\s+1C(?!\w)\.?\s*(?:{_D})?\s*(CYBERSECURITY\b)?", re.I),
    "item_2":  re.compile(rf"ITEM\s+2(?![0-9A-Za-z])\.?\s*(?:{_D})?\s*(PROPERTIES\b)?", re.I),
    "item_7":  re.compile(rf"ITEM\s+7(?![0-9A-Za-z])\.?\s*(?:{_D})?\s*(MANAGEMENT\b)?", re.I),
    "item_7a": re.compile(rf"ITEM\s+7A(?!\w)\.?\s*(?:{_D})?\s*(QUANTITATIVE\b)?", re.I),
    "item_8":  re.compile(rf"ITEM\s+8(?![0-9A-Za-z])\.?\s*(?:{_D})?\s*(FINANCIAL\b)?", re.I),
}
_PAT_ANY_ITEM = re.compile(r"ITEM\s+(\d+[A-C]?)", re.I)

_PRIMARY = ("item_1", "item_1a", "item_7")

# End-boundary ladder for each primary section.
# (ordered_boundary_keys, max_rung_that_counts_as_heading_match_for_high_criterion_2)
_END_LADDERS: dict[str, tuple[list[str], int]] = {
    "item_1":  (["item_1a"],                   0),  # always heading match
    "item_1a": (["item_1b", "item_1c", "item_2"], 0),  # only item_1b (rung 0) = heading match
    "item_7":  (["item_7a", "item_8"],          1),  # both rungs = heading match
}

_HARD_FLOORS = {
    "item_1": ITEM_1_HARD_FLOOR,
    "item_1a": ITEM_1A_HARD_FLOOR,
    "item_7": ITEM_7_HARD_FLOOR,
}
_HIGH_FLOORS = {
    "item_1": ITEM_1_HIGH_FLOOR,
    "item_1a": ITEM_1A_HIGH_FLOOR,
    "item_7": ITEM_7_HIGH_FLOOR,
}

_CROSS_REF_RE = re.compile(
    r"\b(?:described\s+in|included\s+in|pursuant\s+to|referred?\s+to|see|in|under)\s*$",
    re.I,
)


@dataclass
class _Cand:
    section: str
    pos: int
    end: int
    has_title: bool
    rejection: Optional[str] = None


@dataclass
class SectionResult:
    section_id: str
    confidence: str          # 'high' | 'low' | 'failed' | 'incorporated_by_reference'
    start: Optional[int]     # start of section content (after the heading match)
    end: Optional[int]       # end of section content
    length: Optional[int]
    end_boundary_key: Optional[str]   # which heading type terminated the section
    end_boundary_rung: Optional[int]  # rung index in the boundary ladder
    failed_reason: Optional[str]
    low_reasons: list = field(default_factory=list)
    high_criteria: dict = field(default_factory=dict)


@dataclass
class ExtractionResult:
    norm_version: str
    sections: dict            # str -> SectionResult
    tie_break_fired: bool
    overall_method: str       # 'sections' | 'full_fallback' | 'sections_partial'
    trace: dict               # JSON-serializable; for filing_documents.extraction_trace


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(html_or_text: str | bytes) -> str:
    """Strip HTML tags, resolve entities, fold Unicode, collapse whitespace.

    Newlines are preserved (cross_reference and dot_leader filters depend on them).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_or_text, "html.parser")
    text = soup.get_text(separator="\n")

    # Non-breaking and narrow-nbsp variants → regular space
    text = text.replace("\xa0", " ").replace(" ", " ").replace(" ", " ")

    # Unicode dashes → ASCII hyphen (so pattern's [-–—] still matches)
    text = text.translate({ord("–"): "-", ord("—"): "-", ord("‒"): "-"})

    # Curly quotes → ASCII
    text = text.translate(
        {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}
    )

    # Collapse runs of spaces/tabs (not newlines)
    text = re.sub(r"[ \t]+", " ", text)

    # Strip trailing space from each line
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    # Collapse 3+ consecutive newlines to double newline
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_sections(normalized_text: str) -> ExtractionResult:
    """Extract Items 1, 1A, 7 from normalized 10-K text.

    All offsets index the normalized text passed in. Call normalize() first
    if the input is raw HTML.
    """
    doc_len = len(normalized_text)

    # Step 1: find all candidates for all tracked item patterns
    all_cands: list[_Cand] = []
    for sec, pat in _PAT.items():
        for m in pat.finditer(normalized_text):
            grp = m.lastindex
            has_title = bool(grp and m.group(grp))
            all_cands.append(_Cand(sec, m.start(), m.end(), has_title))
    all_cands.sort(key=lambda c: c.pos)

    # Boundary positions: non-primary sections; not filtered
    bpos: dict[str, list[int]] = {}
    for c in all_cands:
        if c.section not in _PRIMARY:
            bpos.setdefault(c.section, []).append(c.pos)

    # Step 2: apply rejection filters to primary section candidates
    primary_cands: dict[str, list[_Cand]] = {s: [] for s in _PRIMARY}
    for c in all_cands:
        if c.section not in _PRIMARY:
            continue
        rej = _rejection(normalized_text, c)
        primary_cands[c.section].append(
            _Cand(c.section, c.pos, c.end, c.has_title, rej)
        )

    # Last position of any toc_cluster-rejected candidate (for high criterion 6)
    last_toc_pos = max(
        (c.pos for cs in primary_cands.values() for c in cs if c.rejection == "toc_cluster"),
        default=-1,
    )

    survivors: dict[str, list[_Cand]] = {
        s: [c for c in cs if c.rejection is None] for s, cs in primary_cands.items()
    }

    trace_cands = {
        s: [{"pos": c.pos, "end": c.end, "has_title": c.has_title, "rejection": c.rejection}
            for c in cs]
        for s, cs in primary_cands.items()
    }

    # Steps 3 & 4: find all valid assignments, pick the earliest
    valid = _valid_assignments(survivors, bpos, normalized_text, doc_len)

    if not valid:
        sections = {}
        for s in _PRIMARY:
            reason = "no_candidate" if not survivors[s] else "no_consistent_assignment"
            sections[s] = SectionResult(s, "failed", None, None, None, None, None, reason)
        return ExtractionResult(
            norm_version=NORM_VERSION,
            sections=sections,
            tie_break_fired=False,
            overall_method="full_fallback",
            trace={
                "norm_version": NORM_VERSION,
                "last_toc_cluster_pos": last_toc_pos,
                "candidates": trace_cands,
                "valid_assignments": [],
                "chosen": None,
                "overall_method": "full_fallback",
            },
        )

    # Sort by (item_1.pos, item_1a.pos, item_7.pos) to find earliest assignment
    valid.sort(key=lambda x: (x[0].pos, x[1].pos, x[2].pos))
    c1, c1a, c7 = valid[0]
    tie_break_fired = len(valid) > 1

    # Step 5: build per-section results
    sections = {}

    # item_1 ends at the start of the chosen item_1a heading
    sections["item_1"] = _section_result(
        "item_1", c1,
        end_pos=c1a.pos, end_key="item_1a", end_rung=0,
        text=normalized_text, doc_len=doc_len,
        last_toc_pos=last_toc_pos, tie_break_fired=tie_break_fired,
    )

    # item_1a and item_7 end at the first boundary marker after their content
    for s, cand in (("item_1a", c1a), ("item_7", c7)):
        end_pos, end_info = _find_end(bpos, s, cand.end, doc_len)
        end_key = end_info[0] if end_info else None
        end_rung = end_info[1] if end_info else None
        sections[s] = _section_result(
            s, cand,
            end_pos=end_pos, end_key=end_key, end_rung=end_rung,
            text=normalized_text, doc_len=doc_len,
            last_toc_pos=last_toc_pos, tie_break_fired=tie_break_fired,
        )

    # Determine overall extraction method
    confidences = {r.confidence for r in sections.values()}
    if confidences <= {"high"}:
        overall_method = "sections"
    elif "incorporated_by_reference" in confidences and confidences <= {"high", "incorporated_by_reference"}:
        overall_method = "sections_partial"
    else:
        overall_method = "full_fallback"

    trace = {
        "norm_version": NORM_VERSION,
        "last_toc_cluster_pos": last_toc_pos,
        "candidates": trace_cands,
        "valid_assignments": [
            {"item_1": v[0].pos, "item_1a": v[1].pos, "item_7": v[2].pos} for v in valid
        ],
        "tie_break_fired": tie_break_fired,
        "chosen": {"item_1": c1.pos, "item_1a": c1a.pos, "item_7": c7.pos},
        "sections": {s: _section_trace(r) for s, r in sections.items()},
        "overall_method": overall_method,
    }

    return ExtractionResult(
        norm_version=NORM_VERSION,
        sections=sections,
        tie_break_fired=tie_break_fired,
        overall_method=overall_method,
        trace=trace,
    )


# ---------------------------------------------------------------------------
# Rejection filters (step 2)
# ---------------------------------------------------------------------------

def _rejection(text: str, cand: _Cand) -> Optional[str]:
    if _is_toc_cluster(text, cand.pos):
        return "toc_cluster"
    if _has_dot_leader(text, cand.end):
        return "dot_leader"
    if _is_cross_reference(text, cand.pos):
        return "cross_reference"
    if _is_part_header_only(text, cand.pos, cand.end):
        return "part_header_only"
    return None


def _is_toc_cluster(text: str, pos: int) -> bool:
    # Forward-only: a ToC entry is immediately followed by other item headings
    # in the same compact block. A real section header at the start of content
    # is not — the next 6,000 chars are narrative, not more item numbers.
    #
    # Only headings count toward the cluster: an inline cross-reference in
    # real prose ("As described in Item 1A, Item 7, Item 8, and Item 14
    # below") mentions several items too, but each is embedded in a sentence
    # rather than starting its own line — _is_cross_reference() (the same
    # check used to reject a candidate's own position) filters those out.
    # (A backward-looking window was tried and rejected: short boundary
    # sections like Item 1B/Item 2 routinely sit within a few hundred chars
    # of a genuine Item 7 heading, so looking behind false-positives on
    # ordinary compact filings — see Sprint 3 Round 6 review, finding 7.)
    end = min(len(text), pos + _TOC_CLUSTER_WINDOW * 2)
    distinct = {
        m.group(1).upper()
        for m in _PAT_ANY_ITEM.finditer(text, pos, end)
        if not _is_cross_reference(text, m.start())
    }
    return len(distinct) >= _TOC_CLUSTER_THRESHOLD


def _has_dot_leader(text: str, heading_end: int) -> bool:
    snippet = text[heading_end : heading_end + _DOT_LEADER_WINDOW]
    # Four or more consecutive dots
    if re.search(r"\.{4,}", snippet):
        return True
    # Run of whitespace, then 1–3 digits, then line end
    if re.search(r"\s{2,}\d{1,3}\s*\n", snippet):
        return True
    return False


def _is_cross_reference(text: str, pos: int) -> bool:
    line_start = text.rfind("\n", 0, pos) + 1
    before = text[line_start:pos].rstrip()
    if not before:
        return False
    if _CROSS_REF_RE.search(before):
        return True
    last = before[-1]
    return last == "," or (last.isalpha() and last.islower())


def _is_part_header_only(text: str, pos: int, heading_end: int) -> bool:
    before = text[max(0, pos - _PART_BEFORE_WINDOW) : pos]
    if not re.search(r"PART\s+I{1,2}(?!I)(?!\w)", before, re.I):
        return False
    after = text[heading_end : heading_end + _PART_AFTER_WINDOW]
    return bool(_PAT_ANY_ITEM.search(after))


# ---------------------------------------------------------------------------
# Assignment (steps 3 & 4)
# ---------------------------------------------------------------------------

def _find_end(
    bpos: dict[str, list[int]],
    section: str,
    after: int,
    doc_len: int,
) -> tuple[int, Optional[tuple[str, int]]]:
    """Earliest boundary position for the section's end-ladder that is > after."""
    ladder, _ = _END_LADDERS[section]
    best: Optional[tuple[int, str, int]] = None
    for rung, key in enumerate(ladder):
        for p in bpos.get(key, []):
            if p > after and (best is None or p < best[0]):
                best = (p, key, rung)
    if best:
        return best[0], (best[1], best[2])
    return doc_len, None


def _is_ibr(text: str, after: int, length: int) -> bool:
    """True if 'incorporated by reference' appears within the first _INCORP_WINDOW chars."""
    snippet = text[after : after + min(length, _INCORP_WINDOW)].lower()
    return "incorporated by reference" in snippet


def _valid_assignments(
    survivors: dict[str, list[_Cand]],
    bpos: dict[str, list[int]],
    text: str,
    doc_len: int,
) -> list[tuple[_Cand, _Cand, _Cand]]:
    """All valid (c1, c1a, c7) triples: strictly increasing positions, hard floors met."""
    result = []
    for c1 in survivors.get("item_1", []):
        for c1a in survivors.get("item_1a", []):
            if c1a.pos <= c1.pos:
                continue
            for c7 in survivors.get("item_7", []):
                if c7.pos <= c1a.pos:
                    continue

                end1 = c1a.pos  # item_1 ends at the start of item_1a
                end1a, _ = _find_end(bpos, "item_1a", c1a.end, doc_len)
                end7, _  = _find_end(bpos, "item_7",  c7.end,  doc_len)

                len1  = end1  - c1.end
                len1a = end1a - c1a.end
                len7  = end7  - c7.end

                # Hard floor satisfied (or section is incorporated by reference)
                ok1  = len1  >= ITEM_1_HARD_FLOOR  or _is_ibr(text, c1.end,  len1)
                ok1a = len1a >= ITEM_1A_HARD_FLOOR or _is_ibr(text, c1a.end, len1a)
                ok7  = len7  >= ITEM_7_HARD_FLOOR  or _is_ibr(text, c7.end,  len7)

                if ok1 and ok1a and ok7:
                    result.append((c1, c1a, c7))
    return result


# ---------------------------------------------------------------------------
# Section result computation (step 5)
# ---------------------------------------------------------------------------

def _section_result(
    section: str,
    cand: _Cand,
    end_pos: int,
    end_key: Optional[str],
    end_rung: Optional[int],
    text: str,
    doc_len: int,
    last_toc_pos: int,
    tie_break_fired: bool,
) -> SectionResult:
    span = text[cand.end : end_pos]
    length = len(span)
    non_ws = sum(1 for ch in span if not ch.isspace())
    alpha = sum(1 for ch in span if ch.isalpha())
    alpha_ratio = (alpha / non_ws) if non_ws > 0 else 0.0
    doc_ratio = length / doc_len if doc_len > 0 else 0.0

    hard_floor = _HARD_FLOORS[section]
    high_floor = _HIGH_FLOORS[section]
    _, max_high_rung = _END_LADDERS[section]

    # Incorporated-by-reference: short section that explicitly defers to an exhibit
    if length < hard_floor:
        if _is_ibr(text, cand.end, length):
            return SectionResult(
                section, "incorporated_by_reference",
                cand.end, end_pos, length, end_key, end_rung, None,
            )
        return SectionResult(
            section, "failed",
            cand.end, end_pos, length, end_key, end_rung, "below_hard_floor",
        )

    # Document-ratio and alpha-ratio are hard failures
    if doc_ratio > SECTION_MAX_RATIO:
        return SectionResult(
            section, "failed",
            cand.end, end_pos, length, end_key, end_rung, "above_max_ratio",
        )
    if alpha_ratio < ALPHA_RATIO_MIN:
        return SectionResult(
            section, "failed",
            cand.end, end_pos, length, end_key, end_rung, "below_alpha_ratio",
        )

    # Evaluate the six high criteria
    crit: dict[str, bool] = {}
    crit["single_or_no_tiebreak"] = not tie_break_fired
    crit["heading_boundaries"] = (
        end_key is not None  # found by heading, not document end
        and (end_rung is not None and end_rung <= max_high_rung)
    )
    crit["length_gte_high_floor"] = length >= high_floor
    crit["length_lte_max_ratio"] = doc_ratio <= SECTION_MAX_RATIO   # always True here
    crit["alpha_ratio"] = alpha_ratio >= ALPHA_RATIO_MIN             # always True here
    crit["after_last_toc_cluster"] = cand.pos > last_toc_pos

    low_reasons = [k for k, v in crit.items() if not v]
    confidence = "high" if not low_reasons else "low"

    return SectionResult(
        section_id=section,
        confidence=confidence,
        start=cand.end,
        end=end_pos,
        length=length,
        end_boundary_key=end_key,
        end_boundary_rung=end_rung,
        failed_reason=None,
        low_reasons=low_reasons,
        high_criteria=crit,
    )


def _section_trace(r: SectionResult) -> dict:
    return {
        "confidence": r.confidence,
        "start": r.start,
        "end": r.end,
        "length": r.length,
        "end_boundary_key": r.end_boundary_key,
        "end_boundary_rung": r.end_boundary_rung,
        "failed_reason": r.failed_reason,
        "low_reasons": r.low_reasons,
        "high_criteria": r.high_criteria,
    }
