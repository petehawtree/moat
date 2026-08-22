"""Sprint 2.1: share-basis change detection and ingest validation.

Regression tests for the Sprint 2 dilution defect (docs/PRD_ADDENDUM.md §A10):
a genuine split restates prior periods across filings; real issuance doesn't.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.ingest.fundamentals_edgar import (
    FLAG_EPS_SHARES_MISMATCH,
    FLAG_SHARE_UNIT_OUTLIER,
    SHARES_TAG,
    check_row_quality,
    classify_basis_change,
    detect_share_basis_changes,
    extract_filings,
)
from moat.screen.quant_screen import _cagr, _detect_split_factors, _split_adjust


def _shares(start, end, val, filed, accn="0000000000-00-000000"):
    return {"start": start, "end": end, "val": val, "filed": filed, "accn": accn, "form": "10-K"}


def _facts(rows):
    return {"facts": {"us-gaap": {SHARES_TAG: {"units": {"shares": rows}}}}}


def test_split_detected_when_a_later_filing_restates_the_period():
    """WMT's shape: FY2022 filed as 2.805B, restated to 8.415B by the
    post-split FY2024 10-K. Same period, two filings, 3x apart."""
    facts = _facts([
        _shares("2021-02-01", "2022-01-31", 2_805_000_000, "2022-03-18", "acc-2022"),
        _shares("2021-02-01", "2022-01-31", 8_415_000_000, "2024-03-15", "acc-2024"),
    ])
    changes = detect_share_basis_changes(facts)
    assert len(changes) == 1
    c = changes[0]
    assert c["period_end_date"] == "2022-01-31"
    assert round(c["ratio"], 2) == 3.0
    assert c["change_type"] == "split"
    assert c["original_accession"] == "acc-2022"
    assert c["restated_accession"] == "acc-2024"


def test_real_issuance_produces_no_basis_change():
    """TKO/CRWV/ALAB's shape: the share count genuinely grew, so every
    filing agrees on every period and nothing is restated."""
    facts = _facts([
        _shares("2023-01-01", "2023-12-31", 82_800_000, "2024-02-01"),
        _shares("2024-01-01", "2024-12-31", 171_900_000, "2025-02-01"),
    ])
    assert detect_share_basis_changes(facts) == []


def test_quarterly_figures_in_a_10k_are_ignored():
    """A 10-K's XBRL carries quarterly durations under the same tag; those
    restate around a split too and would register bogus basis changes."""
    facts = _facts([
        _shares("2022-10-01", "2022-12-31", 100, "2023-02-01"),   # a quarter
        _shares("2022-10-01", "2022-12-31", 300, "2025-02-01"),   # restated quarter
    ])
    assert detect_share_basis_changes(facts) == []


def test_unit_correction_classified_separately_from_split():
    """Southwest filed FY2009 shares as 741, later restated to 741,000,000 —
    a unit fix, not a 1,000,000-for-1 split."""
    assert classify_basis_change(1_000_000.0) == "unit_correction"
    assert classify_basis_change(1_000.0) == "unit_correction"
    assert classify_basis_change(3.0) == "split"
    assert classify_basis_change(1.5) == "split"
    # Reverse splits are still splits — classification keys on magnitude, so
    # a 1-for-10 reverse split must not be mistaken for a unit fix.
    assert classify_basis_change(1 / 10) == "split"
    assert classify_basis_change(1 / 1_000_000) == "unit_correction"


def test_eps_shares_consistency_separates_unit_errors_from_structural_gaps():
    """The size of the miss says which figure to distrust — a power of 1000
    means the share count is in the wrong unit; anything else means net
    income and the EPS numerator differ structurally (NCI, preferred divs)."""
    clean = {"eps_diluted": 2.0, "shares_diluted": 100_000_000, "net_income": 200_000_000}
    assert check_row_quality(clean) == []

    # Southwest FY2007: diluted shares filed as `768` (millions).
    unit_error = {"eps_diluted": 0.84, "shares_diluted": 768, "net_income": 645_000_000}
    assert check_row_quality(unit_error) == [FLAG_SHARE_UNIT_OUTLIER]

    # Northern Trust FY2008: a corrupt 224-trillion share count, which lands
    # 2.2% off a clean 1e6 — must still read as a unit problem.
    ntrs = {"eps_diluted": 3.47, "shares_diluted": 224_053_430_000_000, "net_income": 794_800_000}
    assert check_row_quality(ntrs) == [FLAG_SHARE_UNIT_OUTLIER]

    # TKO FY2025: large noncontrolling interests. EPS isn't comparable, but
    # the share count is sound and must survive — it carries the real merger
    # dilution the Sprint 2 defect erased.
    tko = {"eps_diluted": 2.26, "shares_diluted": 194_011_072, "net_income": 195_403_000}
    assert check_row_quality(tko) == [FLAG_EPS_SHARES_MISMATCH]


def test_extract_filings_builds_resolvable_urls():
    facts = _facts([_shares("2023-01-01", "2023-12-31", 100, "2024-02-01", "0000104169-24-000056")])
    filings = extract_filings(facts, "0000104169")
    assert len(filings) == 1
    f = filings[0]
    assert f["accession_number"] == "0000104169-24-000056"
    assert f["filing_date"] == "2024-02-01"
    assert f["document_url"].startswith("https://www.sec.gov/Archives/edgar/data/104169/")
    assert f["document_url"].endswith("0000104169-24-000056-index.htm")


# --- the screen-side gate -------------------------------------------------

WMT_SHAPE = [(2021, 2_847_000_000), (2022, 8_415_000_000), (2026, 8_022_000_000)]
TKO_SHAPE = [(2023, 82_800_000), (2024, 171_900_000), (2025, 194_000_000)]


def test_corroborated_jump_is_adjusted():
    factors = _detect_split_factors(WMT_SHAPE, corroborated_years={2022})
    adjusted = _cagr(_split_adjust(WMT_SHAPE, factors, invert=False))
    assert adjusted is not None and adjusted < 0  # buybacks, correctly


def test_uncorroborated_jump_is_left_alone():
    """The Sprint 2 defect: TKO's merger-driven share growth was erased.
    With no restatement to corroborate it, the dilution must stand."""
    factors = _detect_split_factors(TKO_SHAPE, corroborated_years=set())
    raw = _cagr(TKO_SHAPE)
    adjusted = _cagr(_split_adjust(TKO_SHAPE, factors, invert=False))
    assert adjusted == raw
    assert adjusted > 0.4  # ~53%/yr real dilution, not the ~6% Sprint 2 reported


def test_ungated_mode_reproduces_sprint_2_behaviour():
    """corroborated_years=None restores jump-only detection, used solely for
    regression comparison against the Sprint 2 numbers."""
    factors = _detect_split_factors(TKO_SHAPE, corroborated_years=None)
    adjusted = _cagr(_split_adjust(TKO_SHAPE, factors, invert=False))
    assert adjusted < 0.1  # the old, wrong answer
