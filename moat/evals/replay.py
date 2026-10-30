"""Frozen §12.2 deterministic per-fix replay framework."""

from __future__ import annotations

import json
from pathlib import Path

from .benchmark import canonical_ticker
from .constants import KNOWN_QUANT_METRICS, KNOWN_QUANT_STATUSES
from .errors import PreflightError
from .valuation import classify_moat_valuation

_REPLAY_KEYS = {
    "fix_id", "ticker", "stage", "before", "counterfactual", "evidence",
    "other_inputs_held_constant",
}


def load_replay_inputs(path: Path) -> list[dict]:
    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PreflightError("per-fix replay input is missing or unreadable") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PreflightError("per-fix replay input contains invalid JSON") from exc
        if not isinstance(record, dict) or set(record) != _REPLAY_KEYS:
            raise PreflightError("per-fix replay record has an unknown schema")
        if record["stage"] not in {"screen", "valuation"}:
            raise PreflightError("per-fix replay record has an unknown stage")
        if not all(isinstance(record[key], str) and record[key] for key in ("fix_id", "ticker", "evidence")):
            raise PreflightError("per-fix replay identifiers/evidence are invalid")
        record["ticker"] = canonical_ticker(record["ticker"])
        if record["other_inputs_held_constant"] is not True:
            raise PreflightError("per-fix replay must attest that all other inputs were held constant")
        if not isinstance(record["before"], dict) or not isinstance(record["counterfactual"], dict):
            raise PreflightError("per-fix replay state must be an object")
        records.append(record)
    if not records:
        raise PreflightError("per-fix replay input contains no records")
    return records


def _screen_state(state: dict) -> dict:
    if set(state) != {"metric_statuses"} or not isinstance(state["metric_statuses"], dict):
        raise PreflightError("screen replay state has an unknown schema")
    statuses = state["metric_statuses"]
    if set(statuses) != KNOWN_QUANT_METRICS:
        raise PreflightError("screen replay must contain exactly the eight v1 metrics")
    if any(value not in KNOWN_QUANT_STATUSES for value in statuses.values()):
        raise PreflightError("screen replay contains an unknown metric status")
    assessed = sum(value in {"pass", "fail"} for value in statuses.values())
    passed = sum(value == "pass" for value in statuses.values())
    score = 100.0 * passed / assessed if assessed else 0.0
    return {
        "metrics_assessed": assessed,
        "metrics_passed": passed,
        "composite_score": score,
        "passed_screen": assessed >= 6 and score >= 50.0,
    }


def _valuation_state(state: dict) -> dict:
    if set(state) != {"bear", "base", "bull", "price"}:
        raise PreflightError("valuation replay state has an unknown schema")
    rows = [
        {
            "method": "owner_earnings_dcf",
            "scenario": scenario,
            "intrinsic_value_low": state[scenario],
            "intrinsic_value_high": state[scenario],
        }
        for scenario in ("bear", "base", "bull")
    ]
    return classify_moat_valuation(rows, state["price"])


def evaluate_replays(records: list[dict] | None) -> tuple[list[dict], dict]:
    if records is None:
        return [], {
            "status": "unavailable",
            "reason": "isolated_historical_replay_inputs_not_supplied",
            "claim_boundary": "no per-fix causal result emitted",
        }
    traces = []
    for record in records:
        compute = _screen_state if record["stage"] == "screen" else _valuation_state
        before = compute(record["before"])
        counterfactual = compute(record["counterfactual"])
        if record["stage"] == "screen":
            transition = f"{'pass' if before['passed_screen'] else 'fail'}_to_{'pass' if counterfactual['passed_screen'] else 'fail'}"
            downstream_metric = "passed_screen"
        else:
            transition = f"{before['classification']}_to_{counterfactual['classification']}"
            downstream_metric = "valuation_classification"
        traces.append({
            "fix_id": record["fix_id"],
            "ticker": record["ticker"],
            "stage": record["stage"],
            "evidence": record["evidence"],
            "other_inputs_held_constant": True,
            "before_value": before,
            "isolated_counterfactual_value": counterfactual,
            "downstream_metric": downstream_metric,
            "final_transition": transition,
        })
    return traces, {
        "status": "available",
        "trace_count": len(traces),
        "fix_count": len({row["fix_id"] for row in traces}),
        "transition_counts": {
            transition: sum(row["final_transition"] == transition for row in traces)
            for transition in sorted({row["final_transition"] for row in traces})
        },
        "claim_boundary": "causal only for traces whose evidence proves all other inputs were held fixed",
    }
