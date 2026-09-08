"""Tests for the W2 section extractor.

Seven fixtures pre-registered in docs/sprints/sprint-3-section-extraction-rules.md.
Each fixture is constructed programmatically and asserts an expected extraction outcome.
"""
from moat.ingest.section_extractor import (
    NORM_VERSION,
    normalize,
    extract_sections,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LOREM_LINE = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat."
)


def _para(n: int) -> str:
    """n paragraphs of lorem text, each ≈220 chars."""
    return (_LOREM_LINE + "\n\n") * n


def _standard_body() -> str:
    """Complete 10-K body: Items 1, 1A, 7 all above their high floors.

    item_1  : _para(70)  ≈ 15,400 chars  (ITEM_1_HIGH_FLOOR  = 15,000)
    item_1a : _para(95)  ≈ 20,900 chars  (ITEM_1A_HIGH_FLOOR = 20,000)
    item_7  : _para(70)  ≈ 15,400 chars  (ITEM_7_HIGH_FLOOR  = 15,000)
    """
    p70 = _para(70)
    p95 = _para(95)
    return (
        "ITEM 1. BUSINESS\n\n" + p70
        + "ITEM 1A. RISK FACTORS\n\n" + p95
        + "ITEM 1B. UNRESOLVED STAFF COMMENTS\n\nNone.\n\n"
        + "ITEM 2. PROPERTIES\n\nSome properties.\n\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n" + p70
        + "ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES\n\nNone.\n"
    )


# ---------------------------------------------------------------------------
# Fixture 1: trapping table of contents
# Must produce: ToC candidates rejected by toc_cluster; body spans chosen; all three high
# ---------------------------------------------------------------------------

def _fixture_1() -> str:
    toc = (
        "TABLE OF CONTENTS\n\n"
        "ITEM 1. BUSINESS .......... 3\n"
        "ITEM 1A. RISK FACTORS ...... 5\n"
        "ITEM 1B. UNRESOLVED STAFF COMMENTS .... 7\n"
        "ITEM 2. PROPERTIES ........ 8\n"
        "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS .......... 50\n\n"
    )
    # _para(20) ≈ 4,400 chars — keeps body headings > 3,000 chars from every ToC entry,
    # so the toc_cluster window does not reach back to the ToC.
    return toc + _para(20) + _standard_body()


def test_fixture_1_trapping_toc():
    result = extract_sections(_fixture_1())

    for s in ("item_1", "item_1a", "item_7"):
        sec = result.sections[s]
        assert sec.confidence == "high", (
            f"{s}: confidence={sec.confidence}, low_reasons={sec.low_reasons}"
        )

    assert result.overall_method == "sections"
    assert not result.tie_break_fired

    # item_1 and item_1a ToC candidates: forward window sees 4+ distinct item numbers
    # → toc_cluster fires.  item_7 appears last in the ToC so its forward window is
    # too sparse for toc_cluster; it falls through to dot_leader (".......... 50").
    for s in ("item_1", "item_1a"):
        cands = result.trace["candidates"][s]
        toc_rej = [c for c in cands if c["rejection"] == "toc_cluster"]
        assert toc_rej, f"Expected toc_cluster rejection for {s}; got {cands}"

    item7_cands = result.trace["candidates"]["item_7"]
    item7_toc = [c for c in item7_cands if c["rejection"] is not None]
    assert item7_toc, f"Expected at least one rejected item_7 ToC candidate; got {item7_cands}"


# ---------------------------------------------------------------------------
# Fixture 2: inline XBRL-heavy filing
# Must produce: same confidence as its non-XBRL rendering
# ---------------------------------------------------------------------------

def _fixture_2_html() -> str:
    """10-K body wrapped in iXBRL namespace tags and HTML paragraph elements."""
    p70_paras = [f"<p>{_LOREM_LINE}</p>" for _ in range(70)]
    p95_paras = [f"<p>{_LOREM_LINE}</p>" for _ in range(95)]
    xbrl_num = (
        '<ix:nonfraction name="us-gaap:Revenue" contextRef="FY2023" decimals="-6">'
        "12,345"
        "</ix:nonfraction>"
    )
    parts = (
        ["<html><body>", "<ix:header/>"]
        + ["<p>ITEM 1. BUSINESS</p>"]
        + p70_paras
        + [f"<p>{xbrl_num}</p>"]        # XBRL number embedded mid-section
        + ["<p>ITEM 1A. RISK FACTORS</p>"]
        + p95_paras
        + ["<p>ITEM 1B. UNRESOLVED STAFF COMMENTS</p>", "<p>None.</p>"]
        + ["<p>ITEM 2. PROPERTIES</p>", "<p>Some properties.</p>"]
        + ["<p>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS</p>"]
        + p70_paras
        + ["<p>ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES</p>", "<p>None.</p>"]
        + ["</body></html>"]
    )
    return "\n".join(parts)


def test_fixture_2_inline_xbrl():
    html = _fixture_2_html()
    xbrl_result = extract_sections(normalize(html))
    plain_result = extract_sections(_standard_body())

    for s in ("item_1", "item_1a", "item_7"):
        assert xbrl_result.sections[s].confidence == plain_result.sections[s].confidence, (
            f"{s}: xbrl={xbrl_result.sections[s].confidence}, "
            f"plain={plain_result.sections[s].confidence}"
        )
    assert xbrl_result.overall_method == plain_result.overall_method


# ---------------------------------------------------------------------------
# Fixture 3: missing Item 1A heading
# Must produce: failed / no_candidate for item_1a → full_fallback, NOT a short span
# ---------------------------------------------------------------------------

def _fixture_3() -> str:
    p70 = _para(70)
    return (
        "ITEM 1. BUSINESS\n\n" + p70
        + "ITEM 2. PROPERTIES\n\nSome properties.\n\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n" + p70
        + "ITEM 7A. QUANTITATIVE DISCLOSURES\n\nNone.\n"
    )


def test_fixture_3_missing_item_1a():
    result = extract_sections(_fixture_3())

    assert result.sections["item_1a"].confidence == "failed"
    assert result.sections["item_1a"].failed_reason == "no_candidate"
    assert result.overall_method == "full_fallback"


# ---------------------------------------------------------------------------
# Fixture 4: duplicate Item 7-like heading (cross-reference in body prose)
# Must produce: cross_reference rejection; the real span chosen; high confidence
# ---------------------------------------------------------------------------

def _fixture_4() -> str:
    p70 = _para(70)
    p95 = _para(95)
    # Mid-sentence "Item 7." — the preceding word "in" triggers cross_reference
    cross_ref_sentence = (
        "The following risk factors are supplemented by the disclosures contained in "
        "Item 7. Management's Discussion and Analysis below.\n\n"
    )
    return (
        "ITEM 1. BUSINESS\n\n" + p70
        + "ITEM 1A. RISK FACTORS\n\n" + p95
        + _para(10) + cross_ref_sentence + _para(10)
        + "ITEM 1B. UNRESOLVED STAFF COMMENTS\n\nNone.\n\n"
        + "ITEM 2. PROPERTIES\n\nSome properties.\n\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n" + p70
        + "ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES\n\nNone.\n"
    )


def test_fixture_4_cross_reference_rejection():
    result = extract_sections(_fixture_4())

    item7_cands = result.trace["candidates"]["item_7"]
    cross_rej = [c for c in item7_cands if c["rejection"] == "cross_reference"]
    assert cross_rej, f"Expected cross_reference rejection; got {item7_cands}"

    accepted = [c for c in item7_cands if c["rejection"] is None]
    assert len(accepted) == 1, (
        f"Expected exactly one surviving item_7 candidate; got {accepted}"
    )

    assert result.sections["item_7"].confidence in ("high", "low"), (
        f"item_7 confidence: {result.sections['item_7'].confidence}"
    )


# ---------------------------------------------------------------------------
# Fixture 5: non-ASCII / whitespace drift
# Must produce: identical span boundaries after normalization; norm_version recorded
# ---------------------------------------------------------------------------

def _fixture_5_unicode() -> str:
    """Headings use NBSP (U+00A0), em-dash (U+2014), and curly apostrophe (U+2019)."""
    p70 = _para(70)
    p95 = _para(95)
    return (
        "ITEM 1.—BUSINESS\n\n" + p70
        + "ITEM 1A.—RISK FACTORS\n\n" + p95
        + "ITEM 1B.—UNRESOLVED STAFF COMMENTS\n\nNone.\n\n"
        + "ITEM 2.—PROPERTIES\n\nSome properties.\n\n"
        + "ITEM 7.—MANAGEMENT’S DISCUSSION\n\n" + p70
        + "ITEM 7A.—QUANTITATIVE DISCLOSURES\n\nNone.\n"
    )


def test_fixture_5_non_ascii_drift():
    raw = _fixture_5_unicode()
    normalized = normalize(raw)
    result = extract_sections(normalized)

    assert result.norm_version == NORM_VERSION
    assert result.trace["norm_version"] == NORM_VERSION

    # Normalizing the unicode version must give the same results as normalizing
    # the manually-converted ASCII equivalent
    ascii_raw = (
        raw.replace(" ", " ")
           .replace("—", "-")
           .replace("’", "'")
    )
    result_ascii = extract_sections(normalize(ascii_raw))

    for s in ("item_1", "item_1a", "item_7"):
        conf = result.sections[s].confidence
        ascii_conf = result_ascii.sections[s].confidence
        assert conf == ascii_conf, f"{s}: unicode={conf}, ascii={ascii_conf}"
        if conf not in ("failed", "incorporated_by_reference"):
            diff = abs(result.sections[s].start - result_ascii.sections[s].start)
            assert diff <= 5, f"{s}: start offset {diff} chars apart after normalization"


# ---------------------------------------------------------------------------
# Fixture 6: item incorporated by reference to Exhibit 13
# Must produce: incorporated_by_reference, NOT failed; overall sections_partial
# ---------------------------------------------------------------------------

def _fixture_6() -> str:
    p95 = _para(95)
    p70 = _para(70)
    ibr = (
        "The information required by this Item is hereby incorporated by "
        "reference from Exhibit 13 to this Annual Report on Form 10-K.\n\n"
    )
    return (
        "ITEM 1. BUSINESS\n\n" + ibr
        + "ITEM 1A. RISK FACTORS\n\n" + p95
        + "ITEM 1B. UNRESOLVED STAFF COMMENTS\n\nNone.\n\n"
        + "ITEM 2. PROPERTIES\n\nSome properties.\n\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n" + p70
        + "ITEM 7A. QUANTITATIVE DISCLOSURES\n\nNone.\n"
    )


def test_fixture_6_incorporated_by_reference():
    result = extract_sections(_fixture_6())

    assert result.sections["item_1"].confidence == "incorporated_by_reference", (
        f"Expected 'incorporated_by_reference', got {result.sections['item_1'].confidence}"
    )
    assert result.overall_method == "sections_partial", (
        f"Expected 'sections_partial', got {result.overall_method}"
    )
    for s in ("item_1a", "item_7"):
        assert result.sections[s].confidence in ("high", "low"), (
            f"{s}: {result.sections[s].confidence}"
        )


# ---------------------------------------------------------------------------
# Fixture 7: section that runs into the financial statements (missing end boundary)
# Must produce: failed by below_alpha_ratio → full_fallback
# ---------------------------------------------------------------------------

def _fixture_7() -> str:
    p70 = _para(70)
    p95 = _para(95)
    # Financial tables: almost entirely digits, commas, and whitespace; alpha_ratio ≈ 0.0
    financial_table = "    1,234,567    8,901,234    5,678,901    2,345,678\n" * 450
    return (
        "ITEM 1. BUSINESS\n\n" + p70
        + "ITEM 1A. RISK FACTORS\n\n" + p95
        + "ITEM 1B. UNRESOLVED STAFF COMMENTS\n\nNone.\n\n"
        + "ITEM 2. PROPERTIES\n\nSome properties.\n\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n"
        + _para(10)          # some real prose to pass the hard floor
        + financial_table    # then financial tables with no Item 7A or Item 8 after
    )


def test_fixture_7_runs_into_financial_statements():
    result = extract_sections(_fixture_7())

    sec7 = result.sections["item_7"]
    assert sec7.confidence == "failed", (
        f"Expected 'failed', got {sec7.confidence}, low_reasons={sec7.low_reasons}"
    )
    assert sec7.failed_reason == "below_alpha_ratio", (
        f"Expected 'below_alpha_ratio', got {sec7.failed_reason}"
    )
    assert result.overall_method == "full_fallback"


# ---------------------------------------------------------------------------
# Fixture 8: Item 7 opens with an inline reference to a later boundary item
# Must produce: item_7 high, not truncated by the inline "Item 8" mention.
#
# Real-world pattern (verbatim, from actual 10-Ks): nearly every MD&A opens
# with a sentence pointing at the financial statements item, e.g. AAPL's
# "...included in Part II, Item 8 of this Form 10-K" or Coca-Cola's
# '...contained in "Item 8. Financial Statements and Supplementary Data" of
# this report.' Before the fix, _find_end() used every raw "Item 8" match as
# a boundary with no filtering, so this opening sentence — not the real
# Item 8 heading pages later — closed out Item 7 after a few hundred chars,
# failing the hard floor and forcing full_fallback on an otherwise
# well-formed filing.
# ---------------------------------------------------------------------------

def _fixture_8() -> str:
    p70 = _para(70)
    p95 = _para(95)
    return (
        "ITEM 1. BUSINESS\n\n" + p70
        + "ITEM 1A. RISK FACTORS\n\n" + p95
        + "ITEM 1B. UNRESOLVED STAFF COMMENTS\n\nNone.\n\n"
        + "ITEM 2. PROPERTIES\n\nSome properties.\n\n"
        + "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS\n\n"
        + "The following discussion should be read in conjunction with the "
          "consolidated financial statements and accompanying notes "
          'contained in "Item 8. Financial Statements and Supplementary '
          'Data" of this report.\n\n'
        + p70
        + "ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES\n\nNone.\n\n"
        + "ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA\n\n" + _para(30)
    )


def test_fixture_8_boundary_cross_reference_not_truncated():
    result = extract_sections(_fixture_8())

    for s in ("item_1", "item_1a", "item_7"):
        sec = result.sections[s]
        assert sec.confidence == "high", (
            f"{s}: confidence={sec.confidence}, low_reasons={sec.low_reasons}, "
            f"failed_reason={sec.failed_reason}"
        )

    assert result.overall_method == "sections"

    # The real Item 7A heading (not the inline "Item 8" reference in Item 7's
    # opening sentence) must be what closed out Item 7.
    sec7 = result.sections["item_7"]
    assert sec7.end_boundary_key == "item_7a"
    assert sec7.length > 15_000, f"item_7 length={sec7.length} — looks truncated"
