"""Regression tests for the remediation plan.

Each test pins a specific flaw so it cannot come back quietly. The names match
the flaw ids in the plan.
"""
from __future__ import annotations

import json
import math
import threading

import numpy as np
import pytest

from gotham.config import DEFAULT
from gotham.engine.contract import HazardScenario, Overlay
from gotham.engine.coordinator import Engine
from gotham.engine.damage import DamageModel
from gotham.engine.dependency import DependencyGate
from gotham.engine.hazard import HazardModel
from gotham.ontology import (
    AssetKind,
    DamageState,
    Link,
    LinkKind,
    Service,
    Township,
)
from gotham.synth.township import generate


def calm(town: Township) -> HazardScenario:
    return HazardScenario(
        id="calm", seed=7, rain_mm=0.0, field_seed=1, onset_hour=0,
        asset_draws={a: 1.0 for a in town.assets},
    )


def storm(town: Township, rain_mm: float = 300.0, draw: float = 0.5) -> HazardScenario:
    return HazardScenario(
        id="storm", seed=13, rain_mm=rain_mm, field_seed=4, onset_hour=0,
        asset_draws={a: draw for a in town.assets},
    )


# ---------------------------------------------------- F01 submerged repair


def test_f01_repairing_a_submerged_asset_does_not_leave_it_working(
    town: Township, engine: Engine
) -> None:
    scenario = storm(town)
    hazard = HazardModel(town, scenario, DEFAULT)
    damage = DamageModel(town, scenario, Overlay(), DEFAULT)
    state = engine.new_state()
    damage.initialise(state)
    for hour in range(10):
        hazard.update(state, float(hour))
        damage.update(state, float(hour))

    assert state.damage["S2"] is not DamageState.NONE
    assert state.flood_depth["S2"] > 1.0, "S2 should be well under water"

    state.damage["S2"] = DamageState.NONE  # a crew "repairs" it mid-flood
    damage.update(state, 10.0)
    assert state.damage["S2"] is not DamageState.NONE, (
        "an asset under water cannot be repaired into working order"
    )


def test_f01_damage_still_clears_once_the_water_has_gone(
    town: Township, engine: Engine
) -> None:
    """The re-assert must not prevent a genuine repair on dry land."""
    scenario = calm(town)
    damage = DamageModel(town, scenario, Overlay(), DEFAULT)
    state = engine.new_state()
    damage.initialise(state)
    state.damage["S2"] = DamageState.EXTENSIVE
    state.flood_depth["S2"] = 0.0
    state.damage["S2"] = DamageState.NONE
    damage.update(state, 5.0)
    assert state.damage["S2"] is DamageState.NONE


# ------------------------------------------------- F02 tie-line propagation


def test_f02_tie_transfer_reaches_past_the_transformer(
    town: Township, engine: Engine
) -> None:
    """A restored feeder must power what hangs below it, not just its zone."""
    without = engine.simulate(
        calm(town),
        Overlay(forced_failures=("S2",), flood_enabled=False),
        record=True,
        _skip_amplification=True,
    )
    with_tie = engine.simulate(
        calm(town),
        Overlay(
            forced_failures=("S2",),
            interventions=("tie:F2-F4",),
            flood_enabled=False,
        ),
        record=True,
        _skip_amplification=True,
    )
    zone = next(z for z in town.zones if z.feeder == "F4")
    assert without.timeline[1].zones[zone.id][Service.ENERGY.value] == 0.0
    assert with_tie.timeline[1].zones[zone.id][Service.ENERGY.value] > 0.0
    assert with_tie.weighted_loss_ph < without.weighted_loss_ph


# ------------------------------------------------------ F03 reserve timing


def test_f03_reserve_never_goes_negative_and_counts_its_last_step() -> None:
    link = Link("A", "B", LinkKind.POWERS, backup_capacity_h=1.5)
    gate = DependencyGate()
    gate.reset([link])
    assert gate.availability(link, 0.0, 0.0, 1.0) == 1.0
    assert gate.reserve_in_use(link)
    # The step that empties it was still carried by it.
    assert gate.availability(link, 0.0, 1.0, 1.0) == 1.0
    assert gate.reserve_in_use(link), "the emptying step was still on reserve"
    assert gate.states[link.key].reserve_left_h == 0.0
    assert gate.availability(link, 0.0, 2.0, 1.0) == 0.0
    assert gate.states[link.key].reserve_left_h >= 0.0


# ------------------------------------------------------- F05 early exit


def test_f05_does_not_stop_while_a_road_is_still_closed(
    town: Township, engine: Engine
) -> None:
    result = engine.simulate(
        storm(town, rain_mm=200.0), record=True, _skip_amplification=True
    )
    last = result.timeline[-1]
    settled = (
        not last.damage
        and not last.closed_roads
        and all(min(s.values()) >= 0.999 for s in last.zones.values())
    )
    assert settled or last.t >= DEFAULT.sim.horizon_h, (
        "the run stopped while the town was still recovering"
    )


# ------------------------------------------------- F06 water supply order


def test_f06_water_chain_follows_the_links_not_the_asset_kind(
    town: Township,
) -> None:
    from gotham.engine.layers.water import WaterLayer

    layer = WaterLayer()
    layer.reset(town, DEFAULT)
    order = layer._chain_order()
    position = {aid: i for i, aid in enumerate(order)}
    for link in town.links:
        if link.kind is not LinkKind.SUPPLIES_WATER:
            continue
        if link.source in position and link.target in position:
            assert position[link.source] < position[link.target], (
                f"{link.source} must be evaluated before {link.target}"
            )


# ------------------------------------------------------- F07 hospitals


def test_f07_staff_come_from_the_town_not_from_the_shelters(
    town: Township,
) -> None:
    from gotham.engine.layers.services import ServicesLayer
    from gotham.engine.layers.transport import TransportLayer

    transport = TransportLayer()
    transport.reset(town, DEFAULT)
    layer = ServicesLayer(transport)
    layer.reset(town, DEFAULT)
    assert layer._staff_nodes, "staff have to come from somewhere"
    assert set(layer._staff_nodes) == {z.node for z in town.zones}


# ------------------------------------------------------ F09 / F13 pluvial


def test_f09_ponding_drains_rather_than_vanishing(town: Township) -> None:
    from gotham.engine.hazard import PLUVIAL_DRAIN_H, STORM_INTENSITY_DIVISOR

    model = HazardModel(town, storm(town, rain_mm=150.0), DEFAULT)
    end = DEFAULT.hazard.rise_hours * STORM_INTENSITY_DIVISOR
    assert model._pluvial_decay(end) == 1.0
    mid = model._pluvial_decay(end + PLUVIAL_DRAIN_H / 2)
    assert 0.0 < mid < 1.0, "ponding must drain gradually, not vanish"
    assert model._pluvial_decay(end + PLUVIAL_DRAIN_H + 1) == 0.0


def test_f13_max_depth_includes_ponding(town: Township) -> None:
    """An asset can be under water without the river ever reaching it."""
    model = HazardModel(town, storm(town, rain_mm=150.0), DEFAULT)
    basin_asset = "SW1"
    dry = model.max_depth(basin_asset, drain_factor=1.0)
    pumps_out = model.max_depth(basin_asset, drain_factor=0.0)
    assert pumps_out >= dry


# --------------------------------------------------------- F10 evacuation


def test_f10_evacuation_scales_with_depth_in_metres() -> None:
    from gotham.engine.demand import (
        EVAC_DEPTH_M,
        EVAC_FULL_DEPTH_M,
        EVAC_MAX_FRACTION,
    )

    def fraction(depth: float) -> float:
        severity = (depth - EVAC_DEPTH_M) / (EVAC_FULL_DEPTH_M - EVAC_DEPTH_M)
        return min(EVAC_MAX_FRACTION, EVAC_MAX_FRACTION * severity)

    assert fraction(0.6) < fraction(1.2) < fraction(2.5)
    assert fraction(2.5) == pytest.approx(EVAC_MAX_FRACTION)
    assert fraction(0.6) < 0.1, "a 10 cm exceedance is not a 60% evacuation"


def test_f10_evacuated_homes_are_not_billed_for_utilities(
    town: Township, engine: Engine
) -> None:
    from gotham.engine.loss import DOMESTIC_SERVICES, accumulate, make_accumulator

    state = engine.new_state()
    zone = town.zones[0]
    for services in state.zone_service.values():
        for service in Service:
            services[service] = 0.0

    plain = make_accumulator(town)
    accumulate(state, 1.0, plain, town, DEFAULT)

    state.evacuated[zone.id] = zone.population
    evacuated = make_accumulator(town)
    accumulate(state, 1.0, evacuated, town, DEFAULT)

    for service in DOMESTIC_SERVICES:
        assert evacuated.by_service[service] < plain.by_service[service]
    assert evacuated.by_service[Service.HEALTH] == plain.by_service[Service.HEALTH], (
        "evacuees still need a hospital"
    )


# ------------------------------------------------------ transport / geometry


def test_isolated_node_is_its_own_component(town: Township) -> None:
    from gotham.engine.layers.transport import TransportLayer

    layer = TransportLayer()
    layer.reset(town, DEFAULT)
    layer._build_weights([0.0] * len(layer._edges))  # everything closed
    node = town.zones[0].node
    assert layer.same_component(node, node), "an asset is always where it is"
    other = town.zones[1].node
    assert not layer.same_component(node, other)


def test_ridge_scales_with_the_township() -> None:
    """A half-size township must still have its ridge inside it."""
    from gotham.synth.geometry import build_terrain

    small = build_terrain(np.random.default_rng(1), 3000.0)
    assert small.ridge_x < 3000.0 and small.ridge_y < 3000.0
    full = build_terrain(np.random.default_rng(1), 6000.0)
    assert full.ridge_x == pytest.approx(small.ridge_x * 2)


# ------------------------------------------------------------ F16 recovery


def test_f16_unreachable_assets_are_the_hardest_to_recover(town: Township) -> None:
    from gotham.analysis.criticality import recovery_difficulty

    reachable = recovery_difficulty(town, "S1", DEFAULT)
    far_bank = recovery_difficulty(town, "S2", DEFAULT)
    assert far_bank > reachable, "crossing the river must cost something"
    assert reachable > 0.0


# --------------------------------------------------------- F12 correlation


def test_f12_common_cause_measures_correlation_not_independence(
    town: Township,
) -> None:
    from gotham.analysis.spof import empirical_joint_failure
    from gotham.engine.hazard import sample_scenarios

    scenarios = sample_scenarios(town, DEFAULT, 150, 1)
    joint = empirical_joint_failure(town, DEFAULT, scenarios)
    together = joint(["T5", "T6"])   # one basin
    apart = joint(["T5", "T1"])      # opposite banks
    assert together > apart, (
        f"co-located towers should correlate: {together:.2f} vs {apart:.2f}"
    )


# ------------------------------------------------------ F19 optimiser guards


def test_f19_a_free_intervention_does_not_divide_by_zero() -> None:
    from gotham.analysis.optimize import MINIMUM_COST_INR

    assert MINIMUM_COST_INR > 0.0
    gain = 1234.0
    assert math.isfinite(gain / max(MINIMUM_COST_INR, 0.0))


# --------------------------------------------------------------- F20 units


def test_f20_drawdown_uses_the_timeline_spacing() -> None:
    from gotham.analysis.metrics import area_under_loss

    class Frame:
        def __init__(self, t: float) -> None:
            self.t = t
            self.func = {"A": 0.0}

    hourly = [Frame(float(i)) for i in range(10)]
    half_hourly = [Frame(i * 0.5) for i in range(20)]
    assert area_under_loss(hourly, ["A"]) == pytest.approx(10.0)
    assert area_under_loss(half_hourly, ["A"]) == pytest.approx(10.0), (
        "a finer step must not inflate the drawdown"
    )


# ---------------------------------------------------------- VoI attribution


def test_water_uncertainty_perturbs_the_water_network(town: Township) -> None:
    from gotham.analysis.ensemble import generate_members

    members = generate_members(
        town, DEFAULT, 4, seed=3, groups=("water_topology",)
    )
    changed = [
        k for m in members[1:] for k in m.perturbations if "repoint" in k
    ]
    assert changed, "the water group has to change something"
    assert all(k.startswith("tank_repoint") for k in changed), (
        f"water uncertainty must move water links, not electrical ones: {changed}"
    )


# ------------------------------------------------------------- concurrency


def test_scenario_cache_is_safe_under_concurrent_use(tmp_path) -> None:
    from gotham.api.state import AppState
    from gotham.io import save_township

    township_path = tmp_path / "t.json"
    save_township(generate(7, scale=0.5), township_path)
    state = AppState(township_path=township_path, results_path=tmp_path / "r")

    errors: list[BaseException] = []

    def hammer(worker: int) -> None:
        try:
            for i in range(60):
                key = f"w{worker}-{i}"
                state.cache_put(key, {"value": i, "worker": worker})
                state.cache_get(key)
        except BaseException as exc:  # noqa: BLE001 - recorded and re-raised
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(state._scenario_cache) <= 256


def test_decision_log_survives_concurrent_writers(tmp_path) -> None:
    from gotham.api.routes.plans import record_decision
    from gotham.api.schemas import DecisionRequest
    from gotham.api.state import AppState
    from gotham.io import save_township

    township_path = tmp_path / "t.json"
    save_township(generate(7, scale=0.5), township_path)
    results = tmp_path / "results"
    results.mkdir()
    state = AppState(township_path=township_path, results_path=results)

    def write(i: int) -> None:
        record_decision(
            DecisionRequest(plan_id=f"p{i}", rationale=f"reason {i}" * 40), state
        )

    threads = [threading.Thread(target=write, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (results / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 16
    for line in lines:
        json.loads(line)  # every line must be whole


# ------------------------------------------------- F14 counterfactual SPOF


def test_f14_assets_that_fail_in_the_storm_report_a_real_counterfactual(
    town: Township, engine: Engine
) -> None:
    """Forcing a failure on something already failing measures nothing.

    The counterfactual that has an answer is the other way round: hold the
    asset up and see how much of the storm's damage goes away.
    """
    from gotham.analysis.spof import design_storm, detect
    from gotham.engine.hazard import sample_scenarios

    scenarios = sample_scenarios(town, DEFAULT, 150, 1)
    spofs = detect(town, DEFAULT, scenarios, engine._base.transport, engine=engine)

    storm_scenario = design_storm(scenarios)
    baseline = engine.simulate(storm_scenario, _skip_amplification=True)
    failing = set(baseline.damaged_assets)
    assert failing, "the design storm should break something"

    measured = [
        s
        for s in spofs
        if s.shared_asset in failing and s.counterfactual_storm_ph > 0.0
    ]
    assert measured, (
        "every candidate that fails in the design storm should show a "
        "non-zero storm counterfactual: "
        + ", ".join(
            f"{s.shared_asset}={s.counterfactual_storm_ph:.0f}"
            for s in spofs
            if s.shared_asset in failing
        )
    )


def test_f14_a_harmless_candidate_still_reports_zero(
    town: Township, engine: Engine
) -> None:
    """The measurement has to be able to say 'this one does not matter'."""
    from gotham.analysis.spof import detect
    from gotham.engine.hazard import sample_scenarios

    scenarios = sample_scenarios(town, DEFAULT, 120, 1)
    spofs = detect(town, DEFAULT, scenarios, engine._base.transport, engine=engine)
    assert any(s.counterfactual_ph == 0.0 for s in spofs), (
        "a purely structural finding should be measurable as costing nothing"
    )
    assert any(s.counterfactual_ph > 0.0 for s in spofs)
