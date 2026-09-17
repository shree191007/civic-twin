"""Acceptance tests for spec 03 section 4 — hidden single points of failure."""
from __future__ import annotations

import pytest

from civictwin.analysis.montecarlo import ScenarioSet
from civictwin.analysis.spof import Spof, design_storm, detect
from civictwin.config import DEFAULT
from civictwin.engine.coordinator import Engine
from civictwin.ontology import AssetKind, Township


@pytest.fixture(scope="module")
def spofs(town: Township, engine: Engine, train_set: ScenarioSet) -> list[Spof]:
    return detect(town, DEFAULT, train_set.scenarios, engine._base.transport)


def test_shared_dependency_finds_s2(spofs: list[Spof]) -> None:
    """T5 and T6 look like two towers; they are one substation."""
    hits = [
        s
        for s in spofs
        if s.kind == "shared_dependency"
        and s.shared_asset == "S2"
        and {"T5", "T6"} <= set(s.redundant_group)
    ]
    assert hits, "no shared_dependency SPOF naming S2 for the T5/T6 group"
    assert hits[0].affected_population > 0
    assert "S2" in hits[0].explanation


def test_colocation_finds_b1(spofs: list[Spof], town: Township) -> None:
    """The primary and backup fibre ride the same bridge."""
    hits = [
        s
        for s in spofs
        if s.kind == "colocation"
        and s.shared_asset == "B1"
        and {"FIBRE_1", "FIBRE_2"} <= set(s.redundant_group)
    ]
    assert hits, "no colocation SPOF naming B1 for the fibre pair"
    assert town.assets["B1"].kind is AssetKind.BRIDGE


def test_recovery_spof_finds_bridge(spofs: list[Spof], town: Township) -> None:
    recovery = [s for s in spofs if s.kind == "recovery"]
    assert recovery, "no recovery SPOFs at all"
    bridges = {
        a.id for a in town.assets.values() if a.kind is AssetKind.BRIDGE
    }
    named = {s.shared_asset for s in recovery} & bridges
    assert named, f"no recovery SPOF names a bridge: {[s.shared_asset for s in recovery]}"


def test_spof_dedup(spofs: list[Spof]) -> None:
    keys = [(s.kind, s.shared_asset, s.service.value) for s in spofs]
    assert len(keys) == len(set(keys))


def test_all_four_detectors_fire(spofs: list[Spof]) -> None:
    kinds = {s.kind for s in spofs}
    assert {"shared_dependency", "colocation", "recovery"} <= kinds
    assert "common_cause" in kinds


def test_ranked_by_population_times_probability(spofs: list[Spof]) -> None:
    scores = [s.rank_score for s in spofs]
    assert scores == sorted(scores, reverse=True)
    assert len(spofs) <= 20
    for s in spofs:
        assert 0.0 <= s.design_storm_failure_prob <= 1.0


def test_design_storm_is_the_worst_in_the_set(train_set: ScenarioSet) -> None:
    storm = design_storm(train_set.scenarios)
    assert storm.rain_mm == max(s.rain_mm for s in train_set.scenarios)
