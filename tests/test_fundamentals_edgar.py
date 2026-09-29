"""Offline tests for XBRL extraction logic — no network calls.

Covers the two bugs found while validating against real SEC data:
  1. tags must be merged across all candidates, not just the first present
     (companies switch XBRL tags over time, e.g. Apple's revenue tag).
  2. quarterly footnote figures tagged on a 10-K must not be mistaken for
     annual figures.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.ingest.fundamentals_edgar import FLAG_NWC_UNAVAILABLE, extract_annual_fundamentals


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
