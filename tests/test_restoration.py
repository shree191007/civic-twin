"""Acceptance tests for spec 03 section 7 — restoration sequencing."""
from __future__ import annotations

import math

import pytest

from gotham.analysis import restoration as restoration_mod
from gotham.analysis.montecarlo import ScenarioSet
from gotham.analysis.restoration import compare_policies, plan_for
from gotham.config import DEFAULT
from gotham.engine.coordinator import Engine
from gotham.ontology import Township


@pytest.fixture(scope="module")
def hero(train_set: ScenarioSet):
    """A storm big enough to damage plenty but small enough to allow repairs."""
    return sorted(train_set.scenarios, key=lambda s: -s.rain_mm)[6]


@pytest.fixture(scope="module")
def plans(engine: Engine, hero, monkeypatch_module=None):
    original = restoration_mod.LOCAL_SEARCH_ITERATIONS
    restoration_mod.LOCAL_SEARCH_ITERATIONS = 25
    try:
        return compare_policies(engine, hero, DEFAULT, seed=3)
    finally:
        restoration_mod.LOCAL_SEARCH_ITERATIONS = original


def test_optimised_not_worse_than_greedy(plans) -> None:
    """The local search starts from the greedy order and only keeps wins."""
    assert (
        plans["optimised"].area_under_loss_ph
        <= plans["greedy_population"].area_under_loss_ph + 1e-9
    )


def test_order_respects_access(engine: Engine, plans, town: Township) -> None:
    """No crew is ever dispatched to an asset it could not reach."""
    for policy, plan in plans.items():
        for crew_id, asset_id in plan.order:
            assert asset_id in town.assets, (policy, asset_id)
            crew = next(c for c in town.crews if c.id == crew_id)
            assert town.assets[asset_id].portfolio is crew.portfolio, (
                f"{policy}: {crew_id} sent to a {town.assets[asset_id].portfolio.value} asset"
            )
            assert math.isfinite(
                town.shortest_path_hours(
                    town.assets[crew.depot].node, town.assets[asset_id].node
                )
            ), f"{policy}: {asset_id} is unreachable from depot {crew.depot}"


def test_policies_are_deterministic(engine: Engine, hero) -> None:
    a = plan_for(engine, hero, DEFAULT, "nearest")
    b = plan_for(engine, hero, DEFAULT, "nearest")
    assert a.to_dict() == b.to_dict()


def test_every_policy_is_reported(plans) -> None:
    assert set(plans) == {"nearest", "greedy_population", "optimised"}
    for policy, plan in plans.items():
        assert plan.policy == policy
        assert plan.area_under_loss_ph > 0.0
        assert plan.order, f"{policy} dispatched nobody"
