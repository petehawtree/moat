"""Independent synthetic audits of public secondary suppression.

The feasibility checker deliberately does not import or reuse reporting's
suppression helpers. It reconstructs the linear identities a reader knows
from published totals and margins, applies the public 0..4 small-cell bound,
and enumerates feasible integer values for every protected source cell.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

import pytest

from moat.evals.constants import TIER_C_OUTCOMES
from moat.evals.reporting import (
    _committee_status_transitions_need_suppression,
    public_committee,
    public_paired_committee,
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

    paired_private = {
        "claim_boundary": "synthetic",
        "paired_common_count": private["universe_count"],
        "entrants": 0,
        "exits": 0,
        "outcome_transitions": {
            "pass_to_pass": 48,
            "fail_to_fail": 32,
            "abstain_to_abstain": 20,
        },
        "mean_composite_score_delta": 0.0,
        "score_delta_denominator": private["screen_scored_count"],
        "pass_rate_delta_paired_bootstrap_interval": None,
        "mean_score_delta_paired_bootstrap_interval": None,
    }
    paired_public = public_paired_tier_a(paired_private, force_secondary=True)
    for name in ("paired_common_count", "entrants", "exits", "score_delta_denominator"):
        audit.variable(f"paired.{name}", paired_public[name])
    transition_names = []
    for name, published in paired_public["outcome_transitions"].items():
        variable = f"paired.transition.{name}"
        transition_names.append(variable)
        audit.variable(variable, published)
    audit.equality(
        {"paired.paired_common_count": -1, **{name: 1 for name in transition_names}}
    )
    audit.equality(
        {"universe": 1, "paired.paired_common_count": -1, "paired.exits": -1}
    )
    audit.equality({
        "scored": -1,
        **{
            f"paired.transition.{name}": 1
            for name in paired_public["outcome_transitions"]
            if name.startswith("pass_to_") or name.startswith("fail_to_")
        },
    })
    audit.equality({
        "abstention": -1,
        **{
            f"paired.transition.{name}": 1
            for name in paired_public["outcome_transitions"]
            if name.startswith("abstain_to_")
        },
    })
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
    assert public["universe_count"] == SECONDARY
    assert public["excluded_count"] == SECONDARY
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


def test_model_only_paired_tier_a_zeros_publish_only_without_benchmark_suppression():
    private = {
        "claim_boundary": "synthetic",
        "paired_common_count": 12,
        "entrants": 0,
        "exits": 0,
        "outcome_transitions": {"pass_to_pass": 12},
        "mean_composite_score_delta": 0.0,
        "score_delta_denominator": 12,
        "pass_rate_delta_paired_bootstrap_interval": None,
        "mean_score_delta_paired_bootstrap_interval": None,
    }
    public = public_paired_tier_a(private)
    assert len(public["outcome_transitions"]) == 9
    assert public["outcome_transitions"]["fail_to_abstain"] == 0
    assert public["outcome_transitions"]["pass_to_pass"] == 12
    assert public["entrants"] == 0
    assert public["mean_composite_score_delta"] == 0.0

    protected = public_paired_tier_a(private, force_secondary=True)
    assert protected["outcome_transitions"]["fail_to_abstain"] == SMALL
    assert protected["outcome_transitions"]["pass_to_pass"] == SECONDARY
    assert protected["entrants"] == SMALL
    assert protected["mean_composite_score_delta"] is None


def _committee_summary(support_by_status: dict[str, dict[str, int]]) -> dict:
    support_categories = ("strong", "weak", "ambiguous", "contradicted", "abstain")
    scored = sum(sum(cells.values()) for cells in support_by_status.values())
    external_abstentions = sum(cells["abstain"] for cells in support_by_status.values())
    investigate = support_by_status["Investigate"]
    investigate_supported = sum(
        investigate[category] for category in support_categories if category != "abstain"
    )
    association = {"denominator": 0, "value": None, "kind": "exploratory"}
    return {
        "eligible_screen_passer_count": scored,
        "committee_scored_count": scored,
        "committee_abstention_count": 0,
        "committee_coverage_rate": 1.0,
        "external_support_scored_count": scored - external_abstentions,
        "external_support_abstention_count": external_abstentions,
        "investigate_support_composition": investigate,
        "investigate_support_composition_denominator": investigate_supported,
        "investigate_contradiction_rate": {
            "numerator": investigate["contradicted"],
            "denominator": investigate_supported,
            "value": (
                investigate["contradicted"] / investigate_supported
                if investigate_supported else None
            ),
            "kind": "descriptive_conservative",
        },
        "support_composition_by_status": support_by_status,
        "support_composition_by_status_denominators": {
            status: sum(value for category, value in cells.items() if category != "abstain")
            for status, cells in support_by_status.items()
        },
        "research_priority_enrichment": {
            status: {
                "supported_numerator": cells["strong"] + cells["weak"],
                "denominator": sum(
                    value for category, value in cells.items() if category != "abstain"
                ),
                "supported_rate": None,
                "composition": {
                    category: cells[category]
                    for category in ("strong", "weak", "ambiguous", "contradicted")
                },
            }
            for status, cells in support_by_status.items()
        },
        "priority_support_spearman_rho": association,
        "watch_score_external_support_trend": association,
        "direct_status_star_rating_spearman_rho": association,
    }


def _paired_committee_summary() -> dict:
    return {
        "claim_boundary": "synthetic",
        "paired_common_count": 2,
        "baseline_only_count": 0,
        "candidate_only_count": 0,
        "status_transitions": {
            "Reject_to_Reject": 1,
            "Investigate_to_Investigate": 1,
        },
        "status_flip_count": 0,
        "status_flip_rate": 0.0,
        "bear_severity_transitions": {"low_to_low": 2},
        "bear_severity_change_count": 0,
        "bear_severity_change_rate": 0.0,
        "mean_overall_score_delta": 0.0,
    }


def test_model_only_paired_committee_small_cells_are_public():
    public = public_paired_committee(_paired_committee_summary())
    assert public["paired_common_count"] == 2
    assert public["baseline_only_count"] == 0
    assert public["candidate_only_count"] == 0
    assert public["status_transitions"]["Watch_to_Watch"] == 0
    assert public["status_flip_count"] == 0
    assert public["status_flip_rate"] == 0.0
    assert public["bear_severity_transitions"]["high_to_high"] == 0
    assert public["bear_severity_change_count"] == 0
    assert public["bear_severity_change_rate"] == 0.0
    assert public["mean_overall_score_delta"] == 0.0


def _status_support_feasible_values(
    paired_public: dict, run_public: dict, *, run: str, status: str, category: str,
) -> set[int]:
    statuses = ("Reject", "Watch", "Investigate")
    transition_names = [
        f"{left}_to_{right}" for left in statuses for right in statuses
    ]
    audit = LinearDisclosureAudit(upper=run_public["committee_scored_count"])
    for name in transition_names:
        audit.variable(name, paired_public["status_transitions"][name])
    audit.equality(
        {name: 1 for name in transition_names},
        paired_public["paired_common_count"],
    )
    audit.equality(
        {
            name: 1
            for name in transition_names
            if name.split("_to_")[0] != name.split("_to_")[1]
        },
        paired_public["status_flip_count"],
    )

    target = f"support.{run}.{status}.{category}"
    audit.variable(target, run_public["support_composition_by_status"][status][category])
    other = f"support.{run}.{status}.other"
    audit.domains[other] = tuple(range(17))  # four other protected cells, each 0..4
    only_total = paired_public[
        "baseline_only_count" if run == "baseline" else "candidate_only_count"
    ]
    only_status = f"{run}.only.{status}"
    audit.domains[only_status] = tuple(range(only_total + 1))
    if run == "baseline":
        margin = {
            f"{status}_to_{right}": 1 for right in statuses
        }
    else:
        margin = {
            f"{left}_to_{status}": 1 for left in statuses
        }
    audit.equality({**margin, only_status: 1, target: -1, other: -1})
    return audit.feasible_values(target)


def test_whole_report_auditor_includes_restored_committee_fields():
    support = {
        "Reject": {"strong": 1, "weak": 0, "ambiguous": 0, "contradicted": 0, "abstain": 0},
        "Watch": {"strong": 0, "weak": 0, "ambiguous": 0, "contradicted": 0, "abstain": 0},
        "Investigate": {"strong": 0, "weak": 1, "ambiguous": 0, "contradicted": 0, "abstain": 0},
    }
    private_run = _committee_summary(support)
    assert _committee_status_transitions_need_suppression(private_run)
    public_runs = {
        run: public_committee(private_run) for run in ("baseline", "candidate")
    }
    paired = public_paired_committee(
        _paired_committee_summary(), suppress_status_transitions=True
    )

    # Model-only aggregates remain exact; only the status table whose margins
    # touch benchmark support cells receives complementary suppression.
    assert paired["paired_common_count"] == 2
    assert paired["status_flip_count"] == 0
    assert paired["bear_severity_change_count"] == 0
    assert paired["mean_overall_score_delta"] == 0.0
    assert set(paired["status_transitions"].values()) == {SMALL}

    for run, status, category in itertools.product(
        ("baseline", "candidate"), support, next(iter(support.values()))
    ):
        actual = support[status][category]
        feasible = _status_support_feasible_values(
            paired, public_runs[run], run=run, status=status, category=category
        )
        assert actual in feasible
        assert len(feasible) > 1, f"{run}.{status}.{category} recovered as {actual}"


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
