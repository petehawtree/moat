"""Independent synthetic audits of public secondary suppression.

The feasibility checker deliberately does not import or reuse reporting's
suppression helpers. It reconstructs the linear identities a reader knows
from published totals and margins, applies the public 0..4 small-cell bound,
and enumerates feasible integer values for every protected source cell.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pytest

from moat.evals.constants import TIER_C_OUTCOMES
from moat.evals.reporting import (
    public_paired_tier_a,
    public_tier_a,
    public_tier_c,
    public_valuation,
)


SMALL = "suppressed_lt_5"
SECONDARY = "withheld_secondary_suppression"


@dataclass
class LinearDisclosureAudit:
    upper: int
    domains: dict[str, tuple[int, ...]] = field(default_factory=dict)
    equations: list[tuple[dict[str, int], int]] = field(default_factory=list)

    def variable(self, name: str, published) -> None:
        if isinstance(published, int) and not isinstance(published, bool):
            domain = (published,)
        elif published == SMALL:
            domain = tuple(range(5))
        elif published == SECONDARY:
            domain = tuple(range(self.upper + 1))
        else:
            raise AssertionError(f"unsupported published count at {name}: {published!r}")
        self.domains[name] = domain

    def equality(self, coefficients: dict[str, int], rhs: int = 0) -> None:
        self.equations.append((coefficients, rhs))

    def feasible_values(self, target: str) -> set[int]:
        return {
            value for value in self.domains[target]
            if self._feasible({target: value})
        }

    def _feasible(self, assigned: dict[str, int]) -> bool:
        def equations_allow_partial() -> bool:
            for coefficients, rhs in self.equations:
                known = sum(
                    coefficient * assigned[name]
                    for name, coefficient in coefficients.items()
                    if name in assigned
                )
                unknown = [
                    (name, coefficient)
                    for name, coefficient in coefficients.items()
                    if name not in assigned
                ]
                minimum = known + sum(
                    min(coefficient * value for value in self.domains[name])
                    for name, coefficient in unknown
                )
                maximum = known + sum(
                    max(coefficient * value for value in self.domains[name])
                    for name, coefficient in unknown
                )
                if not minimum <= rhs <= maximum:
                    return False
            return True

        if not equations_allow_partial():
            return False
        if len(assigned) == len(self.domains):
            return True
        name = min(
            (candidate for candidate in self.domains if candidate not in assigned),
            key=lambda candidate: len(self.domains[candidate]),
        )
        for value in self.domains[name]:
            assigned[name] = value
            if self._feasible(assigned):
                assigned.pop(name)
                return True
        assigned.pop(name)
        return False


def _assert_each_small_cell_ambiguous(
    audit: LinearDisclosureAudit, private_values: dict[str, int]
) -> None:
    protected = [name for name, value in private_values.items() if value < 5]
    assert protected, "fixture must exercise at least one protected cell"
    for name in protected:
        feasible = audit.feasible_values(name)
        assert private_values[name] in feasible
        assert len(feasible) > 1, f"{name} is recoverable as {next(iter(feasible))}"


def _tier_a_summary(
    *, excluded: int, abstentions: tuple[int, int, int],
    pass_counts: tuple[int, int, int] = (5, 5, 5),
    fail_counts: tuple[int, int, int] = (5, 5, 5),
) -> dict:
    labels = ("Wide", "Narrow", "None")
    table = {
        "pass": dict(zip(labels, pass_counts)),
        "fail": dict(zip(labels, fail_counts)),
        "abstain": dict(zip(labels, abstentions)),
    }
    labelled = sum(sum(cells.values()) for cells in table.values())
    universe = labelled + excluded
    screen_abstentions = sum(abstentions)
    label_margins = {
        label: sum(cells[label] for cells in table.values())
        for label in labels
    }
    return {
        "orientation": "primary",
        "universe_count": universe,
        "benchmark_labelled_count": labelled,
        "screen_scored_count": universe - screen_abstentions,
        "screen_abstention_count": screen_abstentions,
        "screen_coverage_rate": (universe - screen_abstentions) / universe,
        "excluded_count": excluded,
        "pass_fail_abstain_by_moat": table,
        "metrics": {},
        "wide_precision_bootstrap_interval": None,
        "moat_distribution_by_run_derived_stratum": {
            "synthetic_all": label_margins,
        },
    }


def test_tier_a_coverage_exclusion_is_not_recoverable_by_subtraction():
    private = _tier_a_summary(
        excluded=3,
        pass_counts=(20, 15, 10),
        fail_counts=(15, 12, 5),
        abstentions=(10, 5, 5),
    )
    public = public_tier_a(private)
    audit = LinearDisclosureAudit(upper=private["universe_count"])
    values = {
        "universe": private["universe_count"],
        "labelled": private["benchmark_labelled_count"],
        "excluded": private["excluded_count"],
        "scored": private["screen_scored_count"],
        "abstention": private["screen_abstention_count"],
    }
    audit.variable("universe", public["universe_count"])
    audit.variable("labelled", public["benchmark_labelled_count"])
    audit.variable("excluded", public["excluded_count"])
    audit.variable("scored", public["screen_scored_count"])
    audit.variable("abstention", public["screen_abstention_count"])
    audit.equality({"universe": 1, "labelled": -1, "excluded": -1})
    audit.equality({"universe": 1, "scored": -1, "abstention": -1})

    table_cells = {}
    for outcome, cells in private["pass_fail_abstain_by_moat"].items():
        for label, value in cells.items():
            name = f"outcome.{outcome}.{label}"
            values[name] = value
            table_cells[name] = 1
            audit.variable(
                name,
                public["pass_fail_abstain_by_moat"][outcome][label],
            )
    audit.equality({"labelled": -1, **table_cells})

    stratum_cells = {}
    for stratum, cells in private["moat_distribution_by_run_derived_stratum"].items():
        for label, value in cells.items():
            name = f"stratum.{stratum}.{label}"
            values[name] = value
            stratum_cells[name] = 1
            audit.variable(
                name,
                public["moat_distribution_by_run_derived_stratum"][stratum][label],
            )
    audit.equality({"labelled": -1, **stratum_cells})
    _assert_each_small_cell_ambiguous(audit, values)


def test_tier_a_two_hidden_abstention_cells_are_not_jointly_recoverable():
    private = _tier_a_summary(excluded=5, abstentions=(5, 4, 4))
    public = public_tier_a(private)
    audit = LinearDisclosureAudit(upper=private["benchmark_labelled_count"])
    values = {f"abstain.{label}": value for label, value in zip(("Wide", "Narrow", "None"), (5, 4, 4))}
    for label in ("Wide", "Narrow", "None"):
        audit.variable(
            f"abstain.{label}",
            public["pass_fail_abstain_by_moat"]["abstain"][label],
        )
    audit.variable("abstention_total", public["screen_abstention_count"])
    audit.equality(
        {
            "abstention_total": -1,
            "abstain.Wide": 1,
            "abstain.Narrow": 1,
            "abstain.None": 1,
        }
    )
    _assert_each_small_cell_ambiguous(audit, values)


def _valuation_summary(table: list[list[int]]) -> dict:
    classes = ("Cheap", "Fair", "Expensive")
    rows = [sum(row) for row in table]
    columns = [sum(table[row][column] for row in range(3)) for column in range(3)]
    total = sum(rows)
    agreement = sum(table[index][index] for index in range(3))
    return {
        "cohort": "analyst",
        "view": "as_run",
        "orientation": "primary",
        "eligible_count": total,
        "scored_count": total,
        "abstention_count": 0,
        "coverage_rate": 1.0,
        "moat_by_benchmark_table": {
            moat: dict(zip(classes, table[index]))
            for index, moat in enumerate(classes)
        },
        "exact_agreement": {
            "numerator": agreement, "denominator": total,
            "value": agreement / total, "kind": "descriptive",
        },
        "false_cheap_rate": {
            "numerator": table[0][1] + table[0][2], "denominator": rows[0],
            "value": (table[0][1] + table[0][2]) / rows[0],
            "kind": "conservative_descriptive",
        },
        "ordinal_spearman_rho": {"denominator": total, "value": 0.1, "kind": "exploratory"},
        "point_fair_value_direction_agreement": {
            "numerator": 20, "denominator": total, "value": 20 / total,
            "kind": "secondary_diagnostic",
        },
        "benchmark_class_distribution": dict(zip(classes, columns)),
        "moat_class_distribution": dict(zip(classes, rows)),
    }


def _audit_valuation(private: dict, public: dict) -> tuple[LinearDisclosureAudit, dict[str, int]]:
    classes = ("Cheap", "Fair", "Expensive")
    audit = LinearDisclosureAudit(upper=private["scored_count"])
    values = {}
    for moat in classes:
        for benchmark in classes:
            name = f"cell.{moat}.{benchmark}"
            values[name] = private["moat_by_benchmark_table"][moat][benchmark]
            audit.variable(name, public["moat_by_benchmark_table"][moat][benchmark])
    for moat in classes:
        name = f"row.{moat}"
        audit.variable(name, public["moat_class_distribution"][moat])
        audit.equality({name: -1, **{f"cell.{moat}.{benchmark}": 1 for benchmark in classes}})
    for benchmark in classes:
        name = f"column.{benchmark}"
        audit.variable(name, public["benchmark_class_distribution"][benchmark])
        audit.equality({name: -1, **{f"cell.{moat}.{benchmark}": 1 for moat in classes}})
    audit.variable("agreement", public["exact_agreement"]["numerator"])
    audit.equality({"agreement": -1, **{f"cell.{value}.{value}": 1 for value in classes}})
    return audit, values


def test_valuation_cells_are_not_recoverable_from_margins_and_agreement():
    private = _valuation_summary([[3, 5, 8], [7, 1, 5], [3, 6, 5]])
    public = public_valuation({"as_run_analyst": private})["as_run_analyst"]
    audit, values = _audit_valuation(private, public)
    _assert_each_small_cell_ambiguous(audit, values)


def _tier_c_summary(rows: dict[str, list[int]]) -> dict:
    outcomes_by_field = {
        field: dict(zip(TIER_C_OUTCOMES, values))
        for field, values in rows.items()
    }
    totals = {
        outcome: sum(values[index] for values in rows.values())
        for index, outcome in enumerate(TIER_C_OUTCOMES)
    }
    comparison_count = sum(totals.values())
    comparable = totals["Match"] + totals["Probable defect"]
    return {
        "lead_finding": "synthetic",
        "comparison_count": comparison_count,
        "outcome_distribution": totals,
        "comparable_count": comparable,
        "comparable_coverage_rate": comparable / comparison_count if comparison_count else None,
        "extraction_accuracy": {
            "numerator_match": totals["Match"],
            "denominator_match_plus_probable_defect": comparable,
            "value": totals["Match"] / comparable if comparable else None,
            "context": "synthetic",
        },
        "outcomes_by_field": outcomes_by_field,
        "roe_average_equity_hypothesis": {"Match": 5},
        "formulas": {"synthetic": "no proprietary values"},
    }


def _audit_tier_c(private: dict, public: dict) -> tuple[LinearDisclosureAudit, dict[str, int]]:
    audit = LinearDisclosureAudit(upper=private["comparison_count"])
    values = {}
    for field, cells in private["outcomes_by_field"].items():
        for outcome, value in cells.items():
            name = f"cell.{field}.{outcome}"
            values[name] = value
            audit.variable(name, public["outcomes_by_field"][field][outcome])
    for outcome in TIER_C_OUTCOMES:
        total = f"total.{outcome}"
        audit.variable(total, public["outcome_distribution"][outcome])
        audit.equality({total: -1, **{f"cell.{field}.{outcome}": 1 for field in private["outcomes_by_field"]}})
    audit.variable("comparison_total", public["comparison_count"])
    audit.equality({"comparison_total": -1, **{f"total.{outcome}": 1 for outcome in TIER_C_OUTCOMES}})
    return audit, values


def test_tier_c_zero_is_not_recoverable_from_global_outcome_total():
    private = _tier_c_summary({
        "field_one": [5, 5, 5, 5, 5],
        "field_two": [0, 5, 5, 5, 5],
    })
    public = public_tier_c(private)
    audit, values = _audit_tier_c(private, public)
    _assert_each_small_cell_ambiguous(audit, values)


def test_paired_missing_transition_keys_are_fixed_zero_suppressed():
    public = public_paired_tier_a({
        "claim_boundary": "synthetic",
        "paired_common_count": 12,
        "entrants": 0,
        "exits": 0,
        "outcome_transitions": {"pass_to_pass": 12},
        "mean_composite_score_delta": 0.0,
        "score_delta_denominator": 12,
        "pass_rate_delta_paired_bootstrap_interval": None,
        "mean_score_delta_paired_bootstrap_interval": None,
    })
    assert len(public["outcome_transitions"]) == 9
    assert public["outcome_transitions"]["fail_to_abstain"] == SMALL
    assert public["outcome_transitions"]["pass_to_pass"] == SECONDARY
    assert public["entrants"] == SMALL
    assert public["mean_composite_score_delta"] is None


@pytest.mark.parametrize(
    "rows",
    [
        {"zero_row": [0, 0, 0, 0, 0], "large_row": [5, 5, 5, 5, 5]},
        {"first": [0, 5, 5, 5, 5], "second": [0, 6, 6, 6, 6]},
        {"single_nonzero": [1, 0, 0, 0, 0]},
        {"threshold_edges": [4, 5, 4, 5, 4]},
    ],
)
def test_secondary_suppression_edge_tables(rows):
    private = _tier_c_summary(rows)
    public = public_tier_c(private)
    audit, values = _audit_tier_c(private, public)
    _assert_each_small_cell_ambiguous(audit, values)


def test_secondary_suppression_random_small_tables():
    random_source = random.Random(61001)
    for _ in range(100):
        rows = {
            f"field_{row}": [random_source.randrange(0, 8) for _ in TIER_C_OUTCOMES]
            for row in range(random_source.randrange(2, 5))
        }
        if all(value >= 5 for values in rows.values() for value in values):
            rows["field_0"][0] = 4
        private = _tier_c_summary(rows)
        public = public_tier_c(private)
        assert public == public_tier_c(private)
        audit, values = _audit_tier_c(private, public)
        _assert_each_small_cell_ambiguous(audit, values)
