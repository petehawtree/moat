"""Offline tests for XBRL extraction logic — no network calls.

Covers the two bugs found while validating against real SEC data:
  1. tags must be merged across all candidates, not just the first present
     (companies switch XBRL tags over time, e.g. Apple's revenue tag).
  2. quarterly footnote figures tagged on a 10-K must not be mistaken for
     annual figures.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.db.connection import get_connection, init_db
from moat.ingest.fundamentals_edgar import (
    FLAG_NWC_UNAVAILABLE,
    extract_annual_fundamentals,
    persist_annual_fundamentals,
)


def _usd_row(start, end, val, filed="2020-01-01", form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form, "fy": 2020, "fp": "FY"}


def _instant_row(end, val, filed="2020-01-01", form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form, "fy": 2020, "fp": "FY"}


def test_revenue_merges_across_tag_switch():
    """A company reporting under 'Revenues' pre-2019 and the new ASC 606 tag
    from 2019 on should get both years, not just whichever tag matched first.
    """
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [_usd_row("2017-01-01", "2017-12-31", 100)]}},
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": [_usd_row("2019-01-01", "2019-12-31", 150)]}
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            _usd_row("2017-01-01", "2017-12-31", 10),
                            _usd_row("2019-01-01", "2019-12-31", 20),
                        ]
                    }
                },
            }
        }
    }
    rows = extract_annual_fundamentals(facts)
    years = {r["fiscal_year"] for r in rows}
    assert years == {2017, 2019}


def test_quarterly_footnote_entries_excluded_from_annual():
    """A 10-K's XBRL can carry a quarterly duration under the same annual tag
    (e.g. selected quarterly financial data footnotes) — must not be treated
    as a full fiscal year.
    """
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _usd_row("2020-01-01", "2020-12-31", 1000),  # full year: keep
                            _usd_row("2020-10-01", "2020-12-31", 300),  # a quarter: drop
                        ]
                    }
                },
                "NetIncomeLoss": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 100)]}},
            }
        }
    }
    rows = extract_annual_fundamentals(facts)
    assert len(rows) == 1
    assert rows[0]["revenue"] == 1000


def test_restatement_picks_most_recently_filed():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            _usd_row("2020-01-01", "2020-12-31", 900, filed="2021-01-01"),
                            _usd_row("2020-01-01", "2020-12-31", 950, filed="2022-06-01"),  # restated, later filing
                        ]
                    }
                },
                "NetIncomeLoss": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 100)]}},
            }
        }
    }
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["revenue"] == 950


def test_year_dropped_without_revenue_or_income():
    facts = {
        "facts": {
            "us-gaap": {
                "OperatingIncomeLoss": {"units": {"USD": []}},
            }
        }
    }
    assert extract_annual_fundamentals(facts) == []


def _da_nwc_facts(extra_gaap: dict) -> dict:
    """Minimal facts payload with one clean fiscal year, plus whatever
    D&A/NWC tags a test wants to add.
    """
    gaap = {
        "Revenues": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 1000)]}},
        "NetIncomeLoss": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 100)]}},
        **extra_gaap,
    }
    return {"facts": {"us-gaap": gaap}}


def test_da_uses_primary_combined_tag():
    facts = _da_nwc_facts(
        {"DepreciationDepletionAndAmortization": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 50)]}}}
    )
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["depreciation_amortization"] == 50


def test_da_falls_back_to_accretion_variant_tag():
    """Primary combined tag absent for this period; the second-tier combined
    tag should still be picked up (same priority-merge as revenue/net income).
    """
    facts = _da_nwc_facts(
        {"DepreciationAmortizationAndAccretionNet": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 42)]}}}
    )
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["depreciation_amortization"] == 42


def test_da_falls_back_to_split_sum_when_no_combined_tag():
    """Neither combined tag present: sum the split Depreciation +
    AmortizationOfIntangibleAssets tags (§A16.2's third tier).
    """
    facts = _da_nwc_facts(
        {
            "Depreciation": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 30)]}},
            "AmortizationOfIntangibleAssets": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 8)]}},
        }
    )
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["depreciation_amortization"] == 38


def test_da_split_sum_treats_missing_amortization_as_zero():
    """A filer with no amortizable intangibles has no AmortizationOfIntangibleAssets
    tag at all — that's legitimately zero, not missing."""
    facts = _da_nwc_facts({"Depreciation": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 30)]}}})
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["depreciation_amortization"] == 30


def test_da_combined_tag_wins_over_split_tags_when_both_present():
    """A filer that tags both the combined line and the split components
    (a real occurrence — some XBRL taxonomies double-tag) must not have its
    D&A double-counted by summing the split tags on top of the combined one.
    """
    facts = _da_nwc_facts(
        {
            "DepreciationDepletionAndAmortization": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 50)]}},
            "Depreciation": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 30)]}},
            "AmortizationOfIntangibleAssets": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 8)]}},
        }
    )
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["depreciation_amortization"] == 50


def test_nwc_present_uses_summary_tag():
    facts = _da_nwc_facts(
        {"IncreaseDecreaseInOperatingCapital": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 12)]}}}
    )
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["working_capital_change"] == 12
    assert rows[0]["quality_flags"] is None


def test_nwc_absent_is_null_and_flagged_not_guessed():
    """No summary NWC tag: NULL + flagged, never reconstructed by summing
    the fragment tags (AR/inventory/AP deltas) — §A16.2's 'flag, don't guess'
    rule. This fixture includes fragment tags precisely to prove they're
    ignored, not just absent-by-coincidence.
    """
    facts = _da_nwc_facts(
        {
            "IncreaseDecreaseInAccountsReceivable": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 5)]}},
            "IncreaseDecreaseInAccountsPayable": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", -3)]}},
        }
    )
    rows = extract_annual_fundamentals(facts)
    assert rows[0]["working_capital_change"] is None
    assert rows[0]["quality_flags"] == FLAG_NWC_UNAVAILABLE


def test_foreign_private_issuer_20f_yields_no_rows():
    """20-F filers (foreign private issuers) are out of scope — the form
    filter should exclude them rather than misreading their figures."""
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 500, form="20-F")]}},
                "NetIncomeLoss": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 50, form="20-F")]}},
            }
        }
    }
    assert extract_annual_fundamentals(facts) == []


# ---------------------------------------------------------------------------
# GitHub #9: capex / D&A tags the ingest didn't try
# ---------------------------------------------------------------------------

def _fy(val):
    return {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", val)]}}


def test_capex_falls_back_to_productive_assets_tag():
    """NVDA (FY2022+), FTNT, LRCX, WAT (FY2023+) tag capex only as
    PaymentsToAcquireProductiveAssets."""
    facts = _da_nwc_facts({
        "PaymentsToAcquireProductiveAssets": _fy(70),
        "NetCashProvidedByUsedInOperatingActivities": _fy(300),
    })
    row = extract_annual_fundamentals(facts)[0]
    assert row["capex"] == 70
    assert row["free_cash_flow"] == 230


def test_capex_ppe_tag_wins_over_productive_assets():
    """Productive assets includes intangibles; a PP&E-only figure is preferred."""
    facts = _da_nwc_facts({
        "PaymentsToAcquirePropertyPlantAndEquipment": _fy(60),
        "PaymentsToAcquireProductiveAssets": _fy(70),
    })
    assert extract_annual_fundamentals(facts)[0]["capex"] == 60


def test_capex_sums_oil_and_gas_and_other_ppe():
    """EOG reports capex split across two tags with no total."""
    facts = _da_nwc_facts({
        "PaymentsToAcquireOilAndGasPropertyAndEquipment": _fy(500),
        "PaymentsToAcquireOtherPropertyPlantAndEquipment": _fy(40),
    })
    assert extract_annual_fundamentals(facts)[0]["capex"] == 540


def test_capex_oil_and_gas_sum_treats_missing_other_ppe_as_zero():
    facts = _da_nwc_facts({"PaymentsToAcquireOilAndGasPropertyAndEquipment": _fy(500)})
    assert extract_annual_fundamentals(facts)[0]["capex"] == 500


def test_capex_other_ppe_alone_is_not_treated_as_total_capex():
    """'Other PP&E' is a fragment of an oil-and-gas filer's capex, not the total."""
    facts = _da_nwc_facts({"PaymentsToAcquireOtherPropertyPlantAndEquipment": _fy(40)})
    row = extract_annual_fundamentals(facts)[0]
    assert row["capex"] is None
    assert row["free_cash_flow"] is None


def test_capex_single_tag_wins_over_oil_and_gas_sum():
    facts = _da_nwc_facts({
        "PaymentsToAcquirePropertyPlantAndEquipment": _fy(600),
        "PaymentsToAcquireOilAndGasPropertyAndEquipment": _fy(500),
        "PaymentsToAcquireOtherPropertyPlantAndEquipment": _fy(40),
    })
    assert extract_annual_fundamentals(facts)[0]["capex"] == 600


def test_capex_ignores_net_of_proceeds_tag():
    """WAT pre-FY2023: PaymentsForProceedsFromProductiveAssets is purchases
    *net of* disposal proceeds and would understate capex — left unknown."""
    facts = _da_nwc_facts({"PaymentsForProceedsFromProductiveAssets": _fy(50)})
    assert extract_annual_fundamentals(facts)[0]["capex"] is None


def test_da_falls_back_to_depreciation_and_amortization_tag():
    """CASY tags D&A only as DepreciationAndAmortization."""
    facts = _da_nwc_facts({"DepreciationAndAmortization": _fy(25)})
    assert extract_annual_fundamentals(facts)[0]["depreciation_amortization"] == 25


def test_da_split_tier_wins_over_last_resort_tag():
    """The new last-resort tag must not change any figure an earlier tier
    already produced."""
    facts = _da_nwc_facts({
        "Depreciation": _fy(30),
        "AmortizationOfIntangibleAssets": _fy(8),
        "DepreciationAndAmortization": _fy(25),
    })
    assert extract_annual_fundamentals(facts)[0]["depreciation_amortization"] == 38


# ---------------------------------------------------------------------------
# GitHub #10: D&A tier order, negative rejection, cross-check flag
# ---------------------------------------------------------------------------

def _da_row(extra_gaap):
    rows = extract_annual_fundamentals(_da_nwc_facts(extra_gaap))
    assert len(rows) == 1
    return rows[0]


def _flags(row):
    return (row["quality_flags"] or "").split(",")


def test_da_nee_fy2025_amortization_alone_does_not_pre_empt_da_tag():
    """NEE FY2025: only AmortizationOfIntangibleAssets (65,000,000) plus
    DepreciationAndAmortization (6,580,000,000), no Depreciation. Stored
    before: 65,000,000 (amortization alone). Expected: 6,580,000,000."""
    row = _da_row({
        "AmortizationOfIntangibleAssets": _fy(65_000_000),
        "DepreciationAndAmortization": _fy(6_580_000_000),
    })
    assert row["depreciation_amortization"] == 6_580_000_000
    assert "da_negative_rejected" not in _flags(row)
    assert "da_below_da_tag" not in _flags(row)


def test_da_cbre_fy2019_amortization_alone_does_not_pre_empt_da_tag():
    """CBRE FY2019: AmortizationOfIntangibleAssets 225,700,000 and
    DepreciationAndAmortization 439,224,000. Stored before: 225,700,000.
    Expected: 439,224,000."""
    row = _da_row({
        "AmortizationOfIntangibleAssets": _fy(225_700_000),
        "DepreciationAndAmortization": _fy(439_224_000),
    })
    assert row["depreciation_amortization"] == 439_224_000


def test_da_peg_fy2019_amortization_alone_is_unknown():
    """PEG FY2019: only AmortizationOfIntangibleAssets (108,000,000).
    Stored before: 108,000,000. Expected: None. Accepted trade-off: unknown
    over wrong (§A13); amortization alone is not D&A."""
    row = _da_row({"AmortizationOfIntangibleAssets": _fy(108_000_000)})
    assert row["depreciation_amortization"] is None


def test_da_aes_fy2025_negative_accretion_tag_rejected_split_wins():
    """AES FY2025: DepreciationAmortizationAndAccretionNet -1,457,000,000
    stored before. Expected: split sum 1,330,000,000 + 94,000,000 =
    1,424,000,000, da_negative_rejected, and no da_below_da_tag
    (1,424 / 1,457 = 0.977 >= 0.8)."""
    row = _da_row({
        "DepreciationAmortizationAndAccretionNet": _fy(-1_457_000_000),
        "Depreciation": _fy(1_330_000_000),
        "AmortizationOfIntangibleAssets": _fy(94_000_000),
        "DepreciationAndAmortization": _fy(1_457_000_000),
    })
    assert row["depreciation_amortization"] == 1_424_000_000
    assert "da_negative_rejected" in _flags(row)
    assert "da_below_da_tag" not in _flags(row)


def test_da_wrb_fy2024_negative_accretion_net_falls_to_depreciation():
    """WRB FY2024: DepreciationAmortizationAndAccretionNet -170,638,000
    (bond accretion dominates) stored before. Expected: Depreciation alone,
    55,000,000, with da_negative_rejected."""
    row = _da_row({
        "DepreciationAmortizationAndAccretionNet": _fy(-170_638_000),
        "Depreciation": _fy(55_000_000),
    })
    assert row["depreciation_amortization"] == 55_000_000
    assert "da_negative_rejected" in _flags(row)


def test_da_luv_fy2007_negative_last_resort_tag_is_unknown():
    """LUV FY2007: DepreciationAndAmortization -555,000,000 only. Stored
    before: -555,000,000. Expected: None with da_negative_rejected."""
    row = _da_row({"DepreciationAndAmortization": _fy(-555_000_000)})
    assert row["depreciation_amortization"] is None
    assert "da_negative_rejected" in _flags(row)


def test_da_adsk_fy2018_split_sum_above_crosscheck_ratio_not_flagged():
    """ADSK FY2018 (2017-02-01 to 2018-01-31): Depreciation 67,600,000 +
    AmortizationOfIntangibleAssets 20,200,000 = 87,800,000 against
    DepreciationAndAmortization 108,400,000. Ratio 87.8/108.4 = 0.810 is
    above 0.8, so NOT flagged."""
    period = ("2017-02-01", "2018-01-31")

    def fact(val):
        return {"units": {"USD": [_usd_row(*period, val)]}}

    facts = {"facts": {"us-gaap": {
        "Revenues": fact(2_000_000_000),
        "NetIncomeLoss": fact(100_000_000),
        "Depreciation": fact(67_600_000),
        "AmortizationOfIntangibleAssets": fact(20_200_000),
        "DepreciationAndAmortization": fact(108_400_000),
    }}}
    row = extract_annual_fundamentals(facts)[0]
    assert row["depreciation_amortization"] == 87_800_000
    assert "da_below_da_tag" not in _flags(row)


def test_da_crosscheck_boundary_is_strictly_below_80_percent():
    """Chosen 79 vs tag 100 -> flagged; chosen 80 vs tag 100 -> not flagged.
    The value is never overridden either way."""
    low = _da_row({"DepreciationDepletionAndAmortization": _fy(79), "DepreciationAndAmortization": _fy(100)})
    assert low["depreciation_amortization"] == 79
    assert "da_below_da_tag" in _flags(low)
    edge = _da_row({"DepreciationDepletionAndAmortization": _fy(80), "DepreciationAndAmortization": _fy(100)})
    assert edge["depreciation_amortization"] == 80
    assert "da_below_da_tag" not in _flags(edge)


def test_da_negative_amortization_component_treated_as_absent():
    """A negative AmortizationOfIntangibleAssets (-5) is rejected: D&A is
    depreciation alone (30), flagged da_negative_rejected."""
    row = _da_row({"Depreciation": _fy(30), "AmortizationOfIntangibleAssets": _fy(-5)})
    assert row["depreciation_amortization"] == 30
    assert "da_negative_rejected" in _flags(row)


def test_da_flags_do_not_disqualify_share_counts_in_screen():
    """The D&A flags must not trip quant_screen's share-count filter, which
    matches on `share_count_unit_outlier` only."""
    from moat.screen.quant_screen import _usable_share_row

    assert _usable_share_row({"quality_flags": "da_negative_rejected,da_below_da_tag"})
    assert not _usable_share_row({"quality_flags": "share_count_unit_outlier"})


def test_da_negative_below_winning_tier_is_not_flagged():
    """A negative source ranked below the one that won was never in play, so
    nothing was rejected and no flag is raised (GitHub #10)."""
    facts = _da_nwc_facts({
        "DepreciationDepletionAndAmortization": _fy(50),
        "DepreciationAndAmortization": _fy(-50),
    })
    row = extract_annual_fundamentals(facts)[0]
    assert row["depreciation_amortization"] == 50
    assert "da_negative_rejected" not in (row["quality_flags"] or "")


# --- ROE on average equity (GitHub #16, PRD_ADDENDUM §A27) --------------

def _roe_facts(years, equity_by_end=None):
    """`years`: list of (start, end, net_income). Revenue is added per period so
    the rows are emitted. `equity_by_end`: {end: equity} instants."""
    facts = {
        "Revenues": {"units": {"USD": [_usd_row(s, e, 1000) for s, e, _ in years]}},
        "NetIncomeLoss": {"units": {"USD": [_usd_row(s, e, ni) for s, e, ni in years]}},
    }
    if equity_by_end:
        facts["StockholdersEquity"] = {
            "units": {"USD": [_instant_row(e, v) for e, v in equity_by_end.items()]}
        }
    return {"facts": {"us-gaap": facts}}


_ADBE_FACTS = _roe_facts(
    [
        ("2022-12-03", "2023-12-01", 5_428_000_000),
        ("2023-12-02", "2024-11-29", 5_560_000_000),
        ("2024-11-30", "2025-11-28", 7_130_000_000),
    ],
    {"2023-12-01": 16_518_000_000, "2024-11-29": 14_105_000_000, "2025-11-28": 11_623_000_000},
)


def test_roe_uses_average_equity_adbe_regression():
    """Real SEC companyfacts values for Adobe. The old ending-equity ROE was
    7,130 / 11,623 = 0.6134 for FY2025; Morningstar reports 55.4 / 36.3, which
    is average equity. FY2023 is the first stored year, so it has no prior
    equity and roe is None (no fallback to ending equity, §A4).
    """
    by_year = {r["fiscal_year"]: r for r in extract_annual_fundamentals(_ADBE_FACTS)}
    # 7,130 / ((14,105 + 11,623) / 2) = 7,130 / 12,864 = 0.55426
    assert by_year[2025]["roe"] == pytest.approx(0.5543, abs=1e-4)
    # 5,560 / ((16,518 + 14,105) / 2) = 5,560 / 15,311.5 = 0.36313
    assert by_year[2024]["roe"] == pytest.approx(0.3631, abs=1e-4)
    assert by_year[2023]["roe"] is None
    assert by_year[2023]["stockholders_equity"] == 16_518_000_000
    assert by_year[2024]["stockholders_equity"] == 14_105_000_000
    assert by_year[2025]["stockholders_equity"] == 11_623_000_000


def test_roe_none_across_a_fiscal_year_gap():
    facts = _roe_facts(
        [("2019-01-01", "2019-12-31", 10), ("2021-01-01", "2021-12-31", 20)],
        {"2019-12-31": 100, "2021-12-31": 120},
    )
    by_year = {r["fiscal_year"]: r for r in extract_annual_fundamentals(facts)}
    assert set(by_year) == {2019, 2021}
    assert by_year[2021]["roe"] is None


def test_roe_none_when_prior_year_equity_missing():
    facts = _roe_facts(
        [("2019-01-01", "2019-12-31", 10), ("2020-01-01", "2020-12-31", 20)],
        {"2020-12-31": 120},  # FY2019 row exists but has no StockholdersEquity
    )
    by_year = {r["fiscal_year"]: r for r in extract_annual_fundamentals(facts)}
    assert set(by_year) == {2019, 2020}
    assert by_year[2020]["roe"] is None


def test_roe_none_when_average_equity_is_zero():
    facts = _roe_facts(
        [("2019-01-01", "2019-12-31", 10), ("2020-01-01", "2020-12-31", 20)],
        {"2019-12-31": 100, "2020-12-31": -100},  # (100 + -100) / 2 = 0
    )
    by_year = {r["fiscal_year"]: r for r in extract_annual_fundamentals(facts)}
    assert by_year[2020]["roe"] is None


def test_stockholders_equity_and_roe_round_trip_through_db(tmp_path):
    db_path = tmp_path / "moat.db"
    init_db(db_path=db_path)
    conn = get_connection(db_path=db_path)
    conn.execute(
        "INSERT INTO companies (ticker, name, sector, universe, is_active, added_date) "
        "VALUES ('ADBE', 'Adobe', 'Tech', 'sp500', 1, '2026-01-01')"
    )
    persist_annual_fundamentals("ADBE", extract_annual_fundamentals(_ADBE_FACTS), conn)
    stored = {
        r["fiscal_year"]: r
        for r in conn.execute("SELECT fiscal_year, stockholders_equity, roe FROM fundamentals_annual")
    }
    conn.close()
    assert stored[2025]["stockholders_equity"] == 11_623_000_000
    assert stored[2025]["roe"] == pytest.approx(0.5543, abs=1e-4)
    assert stored[2023]["stockholders_equity"] == 16_518_000_000
    assert stored[2023]["roe"] is None


def test_migration_adds_stockholders_equity_to_old_fundamentals_table(tmp_path):
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(db_path)
    raw.execute(
        """CREATE TABLE fundamentals_annual (
            ticker TEXT NOT NULL, fiscal_year INTEGER NOT NULL, roe REAL,
            source TEXT NOT NULL, confidence TEXT NOT NULL, retrieved_at TEXT NOT NULL,
            PRIMARY KEY (ticker, fiscal_year))"""
    )
    raw.execute(
        "INSERT INTO fundamentals_annual (ticker, fiscal_year, roe, source, confidence, retrieved_at) "
        "VALUES ('T', 2024, 0.2, 'sec_edgar', 'high', 'x')"
    )
    raw.commit()
    raw.close()

    migrated = init_db(db_path=db_path)
    assert "fundamentals_annual.stockholders_equity" in migrated
    conn = get_connection(db_path=db_path)
    row = conn.execute("SELECT stockholders_equity, roe FROM fundamentals_annual").fetchone()
    conn.close()
    assert row["stockholders_equity"] is None  # old rows stay NULL until re-ingest
    assert row["roe"] == 0.2


def test_roe_opening_equity_found_across_52_53_week_label_skip():
    """AVY-shaped: year ends 2019-12-28 then 2021-01-02 (371 days apart) are
    labelled fiscal_year 2019 and 2021, skipping 2020. Opening equity is
    matched by date, not by fiscal_year - 1, so ROE still exists (GitHub #16).
    100 / ((400 + 600) / 2) = 0.2.
    """
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _usd_row("2018-12-30", "2019-12-28", 1000),
            _usd_row("2019-12-29", "2021-01-02", 1000),
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _usd_row("2018-12-30", "2019-12-28", 80),
            _usd_row("2019-12-29", "2021-01-02", 100),
        ]}},
        "StockholdersEquity": {"units": {"USD": [
            _instant_row("2019-12-28", 400),
            _instant_row("2021-01-02", 600),
        ]}},
    }}}
    rows = {r["fiscal_year"]: r for r in extract_annual_fundamentals(facts)}
    assert rows[2021]["roe"] == pytest.approx(0.2)


def test_roe_first_stored_year_uses_comparative_balance_sheet():
    """A 10-K carries the prior year-end balance sheet, so the first year with
    revenue still has opening equity even though no row exists for the year
    before. 50 / ((300 + 200) / 2) = 0.2.
    """
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 1000)]}},
        "NetIncomeLoss": {"units": {"USD": [_usd_row("2020-01-01", "2020-12-31", 50)]}},
        "StockholdersEquity": {"units": {"USD": [
            _instant_row("2019-12-31", 300),
            _instant_row("2020-12-31", 200),
        ]}},
    }}}
    rows = extract_annual_fundamentals(facts)
    assert len(rows) == 1
    assert rows[0]["roe"] == pytest.approx(0.2)


# ---------------------------------------------------------------------
# total_debt tiers (GitHub #1). Real companyfacts values at each company's
# latest fiscal year-end; "before" is what the NonCurrent + Current sum stored.
# ---------------------------------------------------------------------

def _debt_row(instants: dict, end: str = "2025-12-31") -> dict:
    start = f"{int(end[:4]) - 1}{end[4:]}"
    gaap = {
        "Revenues": {"units": {"USD": [_usd_row(start, end, 1000)]}},
        "NetIncomeLoss": {"units": {"USD": [_usd_row(start, end, 100)]}},
    }
    for tag, val in instants.items():
        gaap[tag] = {"units": {"USD": [_instant_row(end, val)]}}
    return extract_annual_fundamentals({"facts": {"us-gaap": gaap}})[-1]


def test_debt_a_long_term_debt_only():
    """A (Agilent) FY2025: only LongTermDebt is tagged. Before: NULL.
    After: 3,050M, no flag."""
    row = _debt_row({"LongTermDebt": 3_050_000_000}, end="2025-10-31")
    assert row["total_debt"] == 3_050_000_000
    assert "debt_" not in (row["quality_flags"] or "")


def test_debt_adsk_long_term_debt_only():
    """ADSK FY2026: LongTermDebt 2,500M only. Before: NULL."""
    assert _debt_row({"LongTermDebt": 2_500_000_000}, end="2026-01-31")["total_debt"] == 2_500_000_000


def test_debt_amt_lease_inclusive_total_beats_current_fragment():
    """AMT FY2025: no lease-free total, only LongTermDebtCurrent 3,387.8M plus
    lease-inclusive tags. Before: 3,387.8M (debt/FCF 0.9x, passing). After:
    the lease-inclusive total 37,220.3M, flagged; the current-only figure is a
    fragment and ranks last."""
    row = _debt_row({
        "LongTermDebtCurrent": 3_387_800_000,
        "LongTermDebtAndCapitalLeaseObligations": 33_832_500_000,
        "LongTermDebtAndCapitalLeaseObligationsCurrent": 3_387_800_000,
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities": 37_220_300_000,
    })
    assert row["total_debt"] == 37_220_300_000
    assert "debt_includes_finance_leases" in row["quality_flags"]


def test_debt_tdg_total_beats_current_fragment():
    """TDG FY2025: LongTermDebt 29,291M, LongTermDebtCurrent 124M, no
    NonCurrent tag. Before: 124M (236x understated). After: 29,291M."""
    row = _debt_row({"LongTermDebt": 29_291_000_000, "LongTermDebtCurrent": 124_000_000,
                     "LongTermDebtAndCapitalLeaseObligations": 29_167_000_000}, end="2025-09-30")
    assert row["total_debt"] == 29_291_000_000
    assert "debt_" not in (row["quality_flags"] or "")


def test_debt_mcd_split_beats_noncurrent_only_long_term_debt():
    """MCD FY2025: LongTermDebt 39,973M equals NonCurrent and omits the 725M
    current portion. Largest lease-free total: 39,973 + 725 = 40,698M."""
    row = _debt_row({"LongTermDebt": 39_973_000_000, "LongTermDebtNoncurrent": 39_973_000_000,
                     "LongTermDebtCurrent": 725_000_000})
    assert row["total_debt"] == 40_698_000_000


def test_debt_csgp_partial_long_term_debt_does_not_win():
    """CSGP FY2025: LongTermDebt 140M is partial against NonCurrent 993M."""
    row = _debt_row({"LongTermDebt": 140_000_000, "LongTermDebtNoncurrent": 993_000_000})
    assert row["total_debt"] == 993_000_000


def test_debt_now_convertible_notes_instrument_tier():
    """NOW FY2025: only ConvertibleLongTermNotesPayable 1,491M. Before: NULL."""
    row = _debt_row({"ConvertibleLongTermNotesPayable": 1_491_000_000})
    assert row["total_debt"] == 1_491_000_000
    assert "debt_from_instrument_tag" in row["quality_flags"]


def test_debt_dhi_notes_payable_instrument_tier():
    """DHI FY2025: only NotesPayable 5,965.5M. Before: NULL."""
    row = _debt_row({"NotesPayable": 5_965_500_000}, end="2025-09-30")
    assert row["total_debt"] == 5_965_500_000
    assert "debt_from_instrument_tag" in row["quality_flags"]


def test_debt_instrument_pieces_are_not_summed():
    """Senior/secured/unsecured pieces overlap across filers, so a company
    tagging only those (BXP, DLR) stays NULL rather than risk double-counting."""
    row = _debt_row({"SeniorNotes": 9_806_000_000, "SecuredDebt": 4_280_000_000})
    assert row["total_debt"] is None


def test_debt_no_tag_stays_null_not_zero():
    """F/PCAR report debt only in extension namespaces; absence is unknown."""
    assert _debt_row({})["total_debt"] is None


def test_debt_current_only_is_last_resort_and_flagged():
    row = _debt_row({"DebtCurrent": 50})
    assert row["total_debt"] == 50
    assert "debt_current_portion_only" in row["quality_flags"]


def test_debt_negative_values_ignored():
    row = _debt_row({"LongTermDebt": -5, "LongTermDebtNoncurrent": 40})
    assert row["total_debt"] == 40
