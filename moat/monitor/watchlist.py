"""Monitoring / watchlist (PRD §11): diff current run against the previous one.

Sprint 6 scope. Five trigger types from the PRD, each a row type in
watchlist_events:
  - entered_top_rank
  - price_crossed_threshold
  - earnings_change        (materially changes the thesis)
  - management_change
  - financial_deterioration
"""
from __future__ import annotations

TRIGGER_TYPES = [
    "entered_top_rank",
    "price_crossed_threshold",
    "earnings_change",
    "management_change",
    "financial_deterioration",
]


def diff_runs(current_run_id: str, previous_run_id: str, conn) -> list[dict]:
    """Compare committee_verdicts + valuations between two runs, return
    a list of trigger events.

    TODO (Sprint 6).
    """
    raise NotImplementedError


def persist_events(events: list[dict], conn) -> None:
    raise NotImplementedError
