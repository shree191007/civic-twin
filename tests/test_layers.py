"""Acceptance tests for spec 02 sections 6-10 — the four portfolios plus services."""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from civictwin.config import DEFAULT, Config
from civictwin.engine.contract import HazardScenario, Overlay
from civictwin.engine.coordinator import Engine
from civictwin.ontology import Service, Township
from civictwin.synth.township import generate

DEENERGISE_DEPTH_M = 0.15


@pytest.fixture(scope="module")
def town() -> Township:
    return generate(42)


@pytest.fixture(scope="module")
def engine(town: Township) -> Engine:
    return Engine(town)


def calm(town: Township) -> HazardScenario:
    """A scenario with no rain, so only the overlay drives the outcome."""
    return HazardScenario(
        id="calm",
        seed=7,
        rain_mm=0.0,
        field_seed=1,
        onset_hour=0,
        asset_draws={a: 1.0 for a in town.assets},
    )


def run(engine: Engine, town: Township, overlay: Overlay, cfg: Config | None = None):
    eng = engine if cfg is None else Engine(town, cfg)
    return eng.simulate(
        calm(town),
        replace(overlay, flood_enabled=False),
        record=True,
        _skip_amplification=True,
    )


def first_time_below(frames, zone: str, service: Service, threshold: float) -> float:
    for f in frames:
        if f.zones[zone][service.value] < threshold:
            return f.t
    return math.inf


# ------------------------------------------------------------------- energy


def test_energy_isolation(engine: Engine, town: Township) -> None:
    res = run(engine, town, Overlay(forced_failures=("S2",)))
    frame = res.timeline[1]
    s2_zones = {z.id for z in town.zones if z.substation == "S2"}
    assert s2_zones
    for z in town.zones:
        level = frame.zones[z.id][Service.ENERGY.value]
        if z.id in s2_zones:
            assert level == 0.0, f"{z.id} should be dark"
        else:
            assert level == 1.0, f"{z.id} should be unaffected"


def test_energy_deenergisation(engine: Engine, town: Township) -> None:
    """Standing water over a feeder's area switches it off even undamaged."""
    layer = engine._base.energy
    state = engine.new_state()
    edges = layer._feeder_edges["F1"]
    flooded = int(len(edges) * 0.3) + 1
    for eid in edges[:flooded]:
        state.road_depth[eid] = DEENERGISE_DEPTH_M + 0.1
    layer.step(state, DEFAULT)
    assert state.power_available["F1"] == 0.0
    assert state.reason["F1"] == "deenergised"
    f1_zones = [z.id for z in town.zones if z.feeder == "F1"]
    for zid in f1_zones:
        assert state.zone_service[zid][Service.ENERGY] == 0.0


def test_load_transfer(engine: Engine, town: Township) -> None:
    """A tie to a feeder with spare capacity restores part of the load."""
    without = run(engine, town, Overlay(forced_failures=("S2",)))
    with_tie = run(engine, town, Overlay(forced_failures=("S2",), interventions=("tie:F2-F4",)))
    f4_zones = [z.id for z in town.zones if z.feeder == "F4"]
    for zid in f4_zones:
        before = without.timeline[1].zones[zid][Service.ENERGY.value]
        after = with_tie.timeline[1].zones[zid][Service.ENERGY.value]
        assert before == 0.0
        assert after > 0.0, f"{zid} got no transferred power"
    assert with_tie.weighted_loss_ph < without.weighted_loss_ph


# -------------------------------------------------------------------- comms


def test_battery_delay(engine: Engine, town: Township) -> None:
    """T5 has a 4 h battery; carrying its design load it lasts about four steps."""
    res = run(engine, town, Overlay(forced_failures=("S2",)))
    assert town.assets["T5"].backup_hours == 4.0
    drop = next(f.t for f in res.timeline if f.func.get("T5", 1.0) == 0.0)
    assert 3.0 <= drop <= 5.0, f"battery lasted until t={drop}"
    assert res.timeline[int(drop)].reason.get("T5") == "battery"


def test_battery_load_dependence(engine: Engine, town: Township) -> None:
    """Heavier demand drains the same battery sooner."""
    calm_cfg = replace(DEFAULT, comms=replace(DEFAULT.comms, demand_surge_factor=1.0))
    surge_cfg = replace(DEFAULT, comms=replace(DEFAULT.comms, demand_surge_factor=2.5))
    quiet = run(engine, town, Overlay(forced_failures=("S2",)), cfg=calm_cfg)
    busy = run(engine, town, Overlay(forced_failures=("S2",)), cfg=surge_cfg)
    t_quiet = next(f.t for f in quiet.timeline if f.func.get("T5", 1.0) == 0.0)
    t_busy = next(f.t for f in busy.timeline if f.func.get("T5", 1.0) == 0.0)
    assert t_busy < t_quiet


def test_backhaul_cut(engine: Engine, town: Township) -> None:
    """B1 carries both fibres, so losing it takes the far bank off the air."""
    res = run(engine, town, Overlay(forced_failures=("B1",)))
    frame = res.timeline[2]
    assert frame.func["X2"] == 0.0
    far_bank_zones = [z.id for z in town.zones if set(z.towers) & {"T5", "T6", "T7", "T8"}]
    assert far_bank_zones
    for zid in far_bank_zones:
        assert frame.zones[zid][Service.COMMS.value] == 0.0, zid
    near_bank = [z.id for z in town.zones if z.id not in far_bank_zones]
    assert any(frame.zones[z][Service.COMMS.value] > 0.0 for z in near_bank)


# -------------------------------------------------------------------- water


def test_water_tank_drain(engine: Engine, town: Township) -> None:
    """Tank storage then household storage delay the loss of supply."""
    res = run(engine, town, Overlay(forced_failures=("P2",)))
    tank_hours = town.assets["K3"].capacity
    zone = next(z for z in town.zones if z.tank == "K3")
    drop = first_time_below(res.timeline, zone.id, Service.WATER, 1.0)
    assert drop >= tank_hours, "supply failed before the reservoir emptied"
    assert drop >= zone.water_storage_hours
    assert drop <= tank_hours + zone.water_storage_hours + DEFAULT.sim.dt_h


def test_scada_manual_penalty(engine: Engine, town: Township) -> None:
    """Losing X2 forces P2 to manual operation for the penalty period."""
    assert town.assets["P2"].scada_controlled
    res = run(engine, town, Overlay(forced_failures=("X2",)))
    penalty = DEFAULT.response.manual_operation_penalty_h
    # SCADA loss is seen one step late: `response` runs before `comms`, so the
    # crew is only tasked from the step after the exchange actually goes down
    # (spec 02 section 2, the documented feedback rule).
    down = [f.t for f in res.timeline if f.func.get("P2", 1.0) == 0.0]
    assert down, "P2 never went to manual operation"
    assert down[0] <= DEFAULT.sim.dt_h
    assert max(down) == pytest.approx(down[0] + penalty - DEFAULT.sim.dt_h)
    assert any(f.reason.get("P2") == "scada" for f in res.timeline)
    recovered = next(f for f in res.timeline if f.t > max(down))
    assert recovered.func.get("P2", 1.0) > 0.0


# ----------------------------------------------------------------- services


def test_hospital_cross_portfolio(engine: Engine, town: Township) -> None:
    """Trap 3: H2 is dry and powered, yet S2 still takes it out."""
    res = run(engine, town, Overlay(forced_failures=("S2",)))
    assert "S2" not in town.providers("H2", None), "H2 must not be powered by S2"
    assert all(f.flood.get("H2", 0.0) == 0.0 for f in res.timeline)
    assert "S2" in town.upstream("H2")
    dead = [f for f in res.timeline if f.func.get("H2", 1.0) == 0.0]
    assert dead, "H2 never lost capability"
    assert dead[0].reason.get("H2") == "water"


def test_transport_bridge_closure(engine: Engine, town: Township) -> None:
    """With both bridges gone the banks cannot reach each other's hospitals."""
    res = run(engine, town, Overlay(forced_failures=("B1", "B2")))
    transport = engine._base.transport
    h2_node = town.assets["H2"].node
    near_bank_zones = [
        z for z in town.zones if not transport.same_component(z.node, h2_node)
    ]
    assert near_bank_zones, "the bridges did not split the network"
    for z in near_bank_zones:
        assert math.isinf(transport.travel_time(z.node, h2_node))
    assert any(f.closed_roads for f in res.timeline)


def test_mobility_baseline_is_one(engine: Engine, town: Township) -> None:
    res = run(engine, town, Overlay())
    for frame in res.timeline:
        for zid, services in frame.zones.items():
            assert services[Service.MOBILITY.value] == 1.0, (zid, frame.t)


def test_clinics_count_less_than_hospitals(engine: Engine, town: Township) -> None:
    """A clinic can only ever supply a fraction of hospital-grade health access."""
    from civictwin.engine.layers.services import CLINIC_WEIGHT

    res = run(engine, town, Overlay(forced_failures=("H1", "H2")))
    frame = res.timeline[1]
    levels = [v[Service.HEALTH.value] for v in frame.zones.values()]
    assert max(levels) <= CLINIC_WEIGHT + 1e-9
