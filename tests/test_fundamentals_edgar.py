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

from moat.ingest.fundamentals_edgar import extract_annual_fundamentals


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
