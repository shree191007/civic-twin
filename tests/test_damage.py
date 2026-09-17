"""Acceptance tests for spec 02 section 5 — fragility, damage, and repairs."""
from __future__ import annotations

import numpy as np
import pytest

from gotham.config import DEFAULT
from gotham.engine.contract import HazardScenario, Overlay
from gotham.engine.coordinator import Engine
from gotham.engine.damage import DamageModel, exceedance_probability, road_speed_factor
from gotham.engine.hazard import HazardModel, sample_scenarios
from gotham.ontology import DamageState, Township, damage_index
from gotham.synth.township import generate


@pytest.fixture(scope="module")
def town() -> Township:
    return generate(42)


def _scenario(town: Township, rain_mm: float, draw: float = 0.5) -> HazardScenario:
    return HazardScenario(
        id=f"d{rain_mm}",
        seed=13,
        rain_mm=rain_mm,
        field_seed=4,
        onset_hour=0,
        asset_draws={a: draw for a in town.assets},
    )


def test_fragility_monotone() -> None:
    mult = DEFAULT.damage.state_multipliers
    depths = np.linspace(0.05, 6.0, 40)
    for k in range(len(mult)):
        probs = [exceedance_probability(float(d), 1.0, 0.4, mult[k]) for d in depths]
        assert all(b >= a - 1e-12 for a, b in zip(probs, probs[1:])), "not increasing in depth"
    for d in (0.5, 1.0, 2.0, 4.0):
        by_state = [exceedance_probability(d, 1.0, 0.4, m) for m in mult]
        assert all(b <= a + 1e-12 for a, b in zip(by_state, by_state[1:])), "not decreasing in k"
    assert exceedance_probability(0.0, 1.0, 0.4, 1.0) == 0.0


def test_damage_monotone_in_time(town: Township) -> None:
    scenario = _scenario(town, 220.0)
    engine = Engine(town)
    hazard = HazardModel(town, scenario, DEFAULT)
    damage = DamageModel(town, scenario, Overlay(), DEFAULT)
    state = engine.new_state()
    damage.initialise(state)
    previous = {a: 0 for a in town.assets}
    for i in range(73):
        t = float(i)
        hazard.update(state, t)
        damage.update(state, t)
        for aid, ds in state.damage.items():
            assert damage_index(ds) >= previous[aid], f"{aid} healed at t={t}"
            previous[aid] = damage_index(ds)


def test_common_random_numbers(town: Township) -> None:
    scenario = sample_scenarios(town, DEFAULT, 1, 21)[0]
    engine = Engine(town)
    a = engine.simulate(scenario, Overlay(), _skip_amplification=True)
    b = engine.simulate(
        scenario, Overlay(interventions=("backup:T1:+12h",)), _skip_amplification=True
    )
    # T1's battery cannot change whether anything takes structural flood damage
    shared = set(a.damaged_assets) | set(b.damaged_assets)
    for aid in shared:
        assert a.damaged_assets.get(aid) == b.damaged_assets.get(aid), aid


def test_hosted_on_propagates(town: Township) -> None:
    engine = Engine(town)
    scenario = _scenario(town, 0.0)
    state = engine.new_state()
    damage = DamageModel(town, scenario, Overlay(forced_failures=("B1",)), DEFAULT)
    damage.initialise(state)
    assert state.damage["B1"] is DamageState.COMPLETE
    assert state.damage["FIBRE_1"] is DamageState.COMPLETE
    assert state.damage["FIBRE_2"] is DamageState.COMPLETE


def test_invulnerable(town: Township) -> None:
    scenario = _scenario(town, 400.0, draw=0.01)
    engine = Engine(town)
    hazard = HazardModel(town, scenario, DEFAULT)
    damage = DamageModel(town, scenario, Overlay(invulnerable=("S2",)), DEFAULT)
    state = engine.new_state()
    damage.initialise(state)
    for i in range(73):
        hazard.update(state, float(i))
        damage.update(state, float(i))
    assert state.damage["S2"] is DamageState.NONE
    assert any(ds is not DamageState.NONE for ds in state.damage.values())


def test_forced_failure(town: Township) -> None:
    engine = Engine(town)
    state = engine.new_state()
    damage = DamageModel(town, _scenario(town, 0.0), Overlay(forced_failures=("P2",)), DEFAULT)
    damage.initialise(state)
    assert state.damage["P2"] is DamageState.COMPLETE
    damage.update(state, 0.0)
    assert state.damage["P2"] is DamageState.COMPLETE


def test_not_damageable_assets_never_flood(town: Township) -> None:
    scenario = _scenario(town, 500.0, draw=0.001)
    engine = Engine(town)
    hazard = HazardModel(town, scenario, DEFAULT)
    damage = DamageModel(town, scenario, Overlay(), DEFAULT)
    state = engine.new_state()
    damage.initialise(state)
    for i in range(73):
        hazard.update(state, float(i))
        damage.update(state, float(i))
    # FIBRE_3 has no bridge host, so it can only fail through direct damage,
    # and its fragility median of 99 m means it never does.
    assert state.damage["FIBRE_3"] is DamageState.NONE


def test_road_speed_factor() -> None:
    assert road_speed_factor(0.0, DEFAULT) == 1.0
    assert road_speed_factor(0.04, DEFAULT) == 1.0
    assert road_speed_factor(0.30, DEFAULT) == 0.0
    assert road_speed_factor(0.45, DEFAULT) == 0.0
    mid = road_speed_factor(0.15, DEFAULT)
    assert 0.15 <= mid <= 1.0
    assert road_speed_factor(0.2, DEFAULT) < mid


def test_repair_hours_scale_with_damage(town: Township) -> None:
    damage = DamageModel(town, _scenario(town, 100.0), Overlay(), DEFAULT)
    hours = [
        damage.repair_hours("S2", ds)
        for ds in (
            DamageState.NONE,
            DamageState.SLIGHT,
            DamageState.MODERATE,
            DamageState.EXTENSIVE,
            DamageState.COMPLETE,
        )
    ]
    assert hours[0] == 0.0
    assert all(b > a for a, b in zip(hours[1:], hours[2:]))
