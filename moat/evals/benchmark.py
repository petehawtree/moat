"""Strict loader for the private normalized Tier A/Tier C JSONL source."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .constants import (
    BENCHMARK_FIELDS, CANONICAL_ALIASES, CAPTURE_STRATA, COVERAGE_TIERS,
    MOAT_LABELS, REQUIRED_BENCHMARK_FIELDS,
)
from .errors import PreflightError
from .util import is_finite_number

_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")


def canonical_ticker(value: str) -> str:
    if not isinstance(value, str):
        raise PreflightError("benchmark contains a non-string identifier")
    ticker = value.strip().upper()
    ticker = CANONICAL_ALIASES.get(ticker, ticker)
    if not _TICKER.fullmatch(ticker):
        raise PreflightError("benchmark contains an unmapped identifier shape")
    return ticker


def load_benchmark(path: Path) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise PreflightError("tier-a benchmark file is missing or unreadable") from exc
    with fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PreflightError("tier-a benchmark contains invalid JSON") from exc
            if not isinstance(record, dict):
                raise PreflightError("tier-a benchmark contains a non-object record")
            unknown = set(record) - BENCHMARK_FIELDS
            missing = REQUIRED_BENCHMARK_FIELDS - set(record)
            if unknown or missing:
                raise PreflightError(
                    f"tier-a benchmark schema has {len(missing)} missing and {len(unknown)} unknown field(s)"
                )
            ticker = canonical_ticker(record["ticker"])
            if ticker in seen:
                raise PreflightError("tier-a benchmark has duplicate canonical identifiers")
            seen.add(ticker)
            label = record["economic_moat"]
            if label is not None and label not in MOAT_LABELS:
                raise PreflightError("tier-a benchmark contains an unknown moat category")
            if record["coverage_tier"] not in COVERAGE_TIERS:
                raise PreflightError("tier-a benchmark contains an unknown coverage tier")
            if record["stratum"] not in CAPTURE_STRATA:
                raise PreflightError("tier-a benchmark contains an unknown capture stratum")
            try:
                date.fromisoformat(record["snapshot_date"])
            except (TypeError, ValueError) as exc:
                raise PreflightError("tier-a benchmark contains an invalid snapshot date") from exc
            for key, value in record.items():
                if isinstance(value, float) and not is_finite_number(value):
                    raise PreflightError("tier-a benchmark contains a non-finite numeric value")
            normalized = dict(record)
            normalized["ticker"] = ticker
            normalized["record_id"] = line_number
            records.append(normalized)
    if not records:
        raise PreflightError("tier-a benchmark contains no records")
    return records
