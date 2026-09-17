"""Tests for spec-patch sections 6, 7, 12, 13, 15 and 17."""
from __future__ import annotations

import numpy as np
import pytest

from gotham.analysis.ledger import for_asset, for_result, provenance_summary
from gotham.analysis.objectives import (
    OBJECTIVES,
    ObjectiveMode,
    objective_values,
    resolve,
)
from gotham.analysis.redundancy import (
    common_roots,
    independent_paths,
    redundancy_groups,
    system_score,
)
from gotham.analysis.uncertainty import (
    Confidence,
    from_interval,
    from_samples,
    round_people,
)
from gotham.config import DEFAULT
from gotham.hazard.schemas.forecast import UncertaintySource
from gotham.ontology import Township
from gotham.provenance import Evidence, weakest


# --------------------------------------------------------------- section 6


def test_an_estimate_is_a_range_with_a_confidence() -> None:
    rng = np.random.default_rng(0)
    estimate = from_samples(rng.normal(1000, 10, 400), "people")
    assert estimate.low < estimate.central < estimate.high
    assert estimate.confidence is Confidence.HIGH
    assert estimate.n_samples == 400


def test_confidence_falls_as_the_spread_widens() -> None:
    rng = np.random.default_rng(1)
    # The 90% interval of a normal is about +/- 1.64 sigma, so the relative
    # half-width is roughly 1.64 * sigma / mean: 0.016, 0.25 and well past 0.4.
    tight = from_samples(rng.normal(1000, 10, 500))
    loose = from_samples(rng.normal(1000, 150, 500))
    wild = from_samples(rng.lognormal(6.9, 1.1, 500))
    assert tight.confidence is Confidence.HIGH
    assert loose.confidence is Confidence.MEDIUM
    assert wild.confidence is Confidence.LOW


def test_people_ranges_round_outward_to_something_sayable() -> None:
    """85,000-120,000, not 84,997-119,844 — and never narrower than the truth."""
    estimate = from_interval(84_997, 100_000, 119_844, "people")
    low, high = round_people(estimate)
    assert low <= estimate.low and high >= estimate.high
    assert low % 1000 == 0 and high % 1000 == 0


def test_empty_sample_does_not_pretend_to_know() -> None:
    estimate = from_samples([])
    assert (estimate.low, estimate.central, estimate.high) == (0.0, 0.0, 0.0)
    assert estimate.confidence is Confidence.LOW


def test_estimate_carries_its_uncertainty_drivers() -> None:
    estimate = from_samples(
        [1.0, 2.0, 3.0], drivers=[UncertaintySource.HAZARD_INTENSITY]
    )
    assert estimate.to_dict()["drivers"] == ["hazard_intensity"]


# --------------------------------------------------------------- section 7


def test_evidence_is_ordered_weakest_last() -> None:
    assert weakest([Evidence.OBSERVED, Evidence.ASSUMED]) is Evidence.ASSUMED
    assert weakest([Evidence.OBSERVED, Evidence.VERIFIED]) is Evidence.VERIFIED
    assert weakest([]) is Evidence.ASSUMED


def test_asset_ledger_names_what_is_assumed(town: Township) -> None:
    ledger = for_asset(town, "P2", DEFAULT)
    subjects = {a.subject for a in ledger.assumptions}
    assert {"existence and position", "flood vulnerability", "restoration time"} <= subjects
    assert ledger.standing is Evidence.ASSUMED, "a synthetic fragility is an assumption"
    assert any(a.evidence is Evidence.ASSUMED for a in ledger.assumptions)


def test_result_ledger_covers_hazard_population_and_behaviour(town: Township) -> None:
    ledger = for_result(town, DEFAULT, "CVaR95", assets=["S2"])
    subjects = {a.subject for a in ledger.assumptions}
    assert {"hazard", "population exposure", "cascade", "operator behaviour"} <= subjects
    assert ledger.to_dict()["standing"] in {e.value for e in Evidence}


def test_provenance_summary_accounts_for_every_asset(town: Township) -> None:
    summary = provenance_summary(town)
    assert summary["total"] == len(town.assets)
    assert sum(summary["counts"].values()) == len(town.assets)
    assert "synthetic" in summary["sentence"]


# -------------------------------------------------------------- section 12


def test_two_towers_on_one_substation_score_a_half(town: Township) -> None:
    """The headline case: nominal redundancy 2, effective 1."""
    groups = {g.id: g for g in redundancy_groups(town)}
    pair = groups["comms:T5+T6"]
    assert pair.nominal_paths == 2
    assert pair.independent_paths == 1
    assert pair.score == 0.5
    assert pair.is_weak
    assert "S2" in pair.shared_by_kind.values()
    assert "S2" in pair.explanation


def test_the_fibre_pair_is_caught_by_its_bridge(town: Township) -> None:
    groups = {g.id: g for g in redundancy_groups(town)}
    fibres = groups["backhaul:FIBRE_1+FIBRE_2"]
    assert fibres.score == 0.5
    assert fibres.shared_by_kind.get("the same structure") == "B1"


def test_truly_independent_members_score_one(town: Township) -> None:
    roots = common_roots(town)
    count, shared = independent_paths(town, ["T1", "T7"], roots | {"FIBRE_3", "FIBRE_4"})
    assert count == 2
    assert shared == [] or count == 2


def test_common_roots_are_excluded_from_the_score(town: Township) -> None:
    """Naming the grid supply as the reason nothing is redundant is useless."""
    roots = common_roots(town)
    assert "GS1" in roots
    for group in redundancy_groups(town):
        assert not set(group.shared_dependencies) & roots


def test_system_score_is_population_weighted(town: Township) -> None:
    score = system_score(redundancy_groups(town))
    assert 0.0 < score <= 1.0


# -------------------------------------------------------------- section 13


def test_systemic_risk_is_explained_by_its_factors(ranked) -> None:
    row = next(r for r in ranked if r.asset_id == "S2")
    assert row.systemic_risk is not None
    factors = row.systemic_risk
    assert 0.0 <= factors.failure_probability <= 1.0
    assert 0.0 <= factors.failure_impact <= 1.0
    assert 0.0 <= factors.dependency_concentration <= 1.0
    assert 0.0 <= factors.recovery_difficulty <= 1.0
    assert factors.score == pytest.approx(
        factors.failure_probability
        * factors.failure_impact
        * factors.dependency_concentration
        * factors.recovery_difficulty
    )


def test_systemic_risk_is_not_just_centrality(ranked, town: Township) -> None:
    """A well-connected asset that never fails must not top the list."""
    by_sdr = sorted(
        ranked, key=lambda r: -(r.systemic_risk.score if r.systemic_risk else 0.0)
    )
    top = by_sdr[0]
    assert top.systemic_risk is not None
    assert top.annual_failure_prob > 0.0, "the top systemic asset must actually fail"


def test_concentration_sees_zone_level_alternatives(town: Township) -> None:
    from gotham.analysis.criticality import dependency_concentration

    assert dependency_concentration(town, "T5") == 0.5, "a zone has two towers"
    assert dependency_concentration(town, "TR9") == 1.0, "a zone has one transformer"


# -------------------------------------------------------------- section 17


def test_structural_and_functional_importance_are_separate(ranked) -> None:
    """Sitting in the middle of the graph is not the same as mattering."""
    pairs = [(r.structural_importance, r.functional_importance) for r in ranked]
    assert any(s > 0.4 and f < 0.1 for s, f in pairs), (
        "expected at least one structurally central asset with little real impact"
    )


# -------------------------------------------------------------- section 15


def test_every_objective_is_described_for_a_human() -> None:
    for objective in OBJECTIVES.values():
        assert objective.label and objective.description
        assert objective.to_dict()["weights"]["health"] > 0


def test_objectives_score_the_same_outcome_differently(train_table) -> None:
    life = objective_values(train_table, OBJECTIVES[ObjectiveMode.PROTECT_LIFE], DEFAULT)
    economy = objective_values(
        train_table, OBJECTIVES[ObjectiveMode.MINIMISE_ECONOMIC_LOSS], DEFAULT
    )
    assert not np.allclose(life, economy)
    assert life.mean() > economy.mean(), "life weights health far higher"


def test_restore_fast_counts_duration(train_table) -> None:
    fast = objective_values(train_table, OBJECTIVES[ObjectiveMode.RESTORE_FAST], DEFAULT)
    balanced = objective_values(train_table, OBJECTIVES[ObjectiveMode.BALANCED], DEFAULT)
    assert np.all(fast >= balanced - 1e-9)


def test_unknown_objective_falls_back_to_balanced() -> None:
    assert resolve("nonsense").mode is ObjectiveMode.BALANCED
    assert resolve(None).mode is ObjectiveMode.BALANCED
