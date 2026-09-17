"""Tests for spec-patch sections 9 and 10 — operating states and dependency behaviour."""
from __future__ import annotations

import pytest

from civictwin.config import DEFAULT
from civictwin.engine.contract import HazardScenario, Overlay
from civictwin.engine.coordinator import Engine
from civictwin.engine.dependency import DependencyGate
from civictwin.engine.states import CRITICAL_RESERVE_H, classify
from civictwin.ontology import Link, LinkKind, OperatingState, Township
from civictwin.synth.township import generate


def calm(town: Township) -> HazardScenario:
    return HazardScenario(
        id="calm", seed=7, rain_mm=0.0, field_seed=1, onset_hour=0,
        asset_draws={a: 1.0 for a in town.assets},
    )


# --------------------------------------------------------------- section 9


def test_states_cover_the_operator_vocabulary() -> None:
    assert classify(1.0) is OperatingState.OPERATIONAL
    assert classify(0.8) is OperatingState.DEGRADED
    assert classify(1.0, on_backup=True, reserve_left_h=10.0) is OperatingState.BACKUP
    assert classify(0.1) is OperatingState.CRITICAL
    assert classify(0.0) is OperatingState.FAILED


def test_thin_reserve_is_critical_not_backup() -> None:
    """An asset one step from falling over must not read as comfortably on backup."""
    assert classify(1.0, on_backup=True, reserve_left_h=CRITICAL_RESERVE_H + 1) is (
        OperatingState.BACKUP
    )
    assert classify(1.0, on_backup=True, reserve_left_h=CRITICAL_RESERVE_H) is (
        OperatingState.CRITICAL
    )


def test_cascade_walks_the_states_in_order(town: Township, engine: Engine) -> None:
    """The tower goes operational, backup, critical, failed — in that order."""
    result = engine.simulate(
        calm(town),
        Overlay(forced_failures=("S2",), flood_enabled=False),
        record=True,
        _skip_amplification=True,
    )
    seen: list[str] = []
    for frame in result.timeline or ():
        state = frame.state.get("T5", OperatingState.OPERATIONAL.value)
        if not seen or seen[-1] != state:
            seen.append(state)
    assert seen == ["backup", "critical", "failed"]


def test_damage_and_operating_state_are_different_things(
    town: Township, engine: Engine
) -> None:
    """A pump on a generator is undamaged but not operational."""
    result = engine.simulate(
        calm(town),
        Overlay(forced_failures=("S1",), flood_enabled=False),
        record=True,
        _skip_amplification=True,
    )
    frame = result.timeline[1]
    assert "X1" not in frame.damage, "X1 should be undamaged"
    assert frame.state.get("X1") == OperatingState.BACKUP.value


# -------------------------------------------------------------- section 10


def _gate_for(link: Link) -> DependencyGate:
    gate = DependencyGate()
    gate.reset([link])
    return gate


def test_plain_link_passes_supply_straight_through() -> None:
    """Defaults must behave exactly like a bare graph edge."""
    link = Link("A", "B", LinkKind.POWERS)
    gate = _gate_for(link)
    assert gate.availability(link, 1.0, 0.0, 1.0) == 1.0
    assert gate.availability(link, 0.4, 1.0, 1.0) == 0.4
    assert gate.availability(link, 0.0, 2.0, 1.0) == 0.0


def test_threshold_rejects_a_brownout() -> None:
    link = Link("S2", "P2", LinkKind.POWERS, threshold=0.6)
    gate = _gate_for(link)
    assert gate.availability(link, 0.8, 0.0, 1.0) == 0.8
    assert gate.availability(link, 0.5, 1.0, 1.0) == 0.0


def test_delay_holds_the_loss_off() -> None:
    link = Link("S2", "P2", LinkKind.POWERS, delay_h=2.0)
    gate = _gate_for(link)
    assert gate.availability(link, 0.0, 0.0, 1.0) == 1.0
    assert gate.availability(link, 0.0, 1.0, 1.0) == 1.0
    assert gate.availability(link, 0.0, 2.0, 1.0) == 0.0


def test_reserve_carries_the_dependent_then_runs_out() -> None:
    link = Link("K3", "H2", LinkKind.SUPPLIES_WATER, backup_capacity_h=3.0)
    gate = _gate_for(link)
    for hour in range(3):
        assert gate.availability(link, 0.0, float(hour), 1.0) == 1.0, hour
        assert gate.reserve_in_use(link) or hour == 2
    assert gate.availability(link, 0.0, 3.0, 1.0) == 0.0


def test_spent_reserve_does_not_silently_refill() -> None:
    """A drained roof tank is not full again the moment the mains flicker back."""
    link = Link("K3", "H2", LinkKind.SUPPLIES_WATER, backup_capacity_h=2.0)
    gate = _gate_for(link)
    gate.availability(link, 0.0, 0.0, 1.0)
    gate.availability(link, 0.0, 1.0, 1.0)
    assert gate.availability(link, 1.0, 2.0, 1.0) == 1.0  # supply back
    assert gate.availability(link, 0.0, 3.0, 1.0) == 0.0  # nothing left to draw on


def test_township_declares_dependency_behaviour(town: Township) -> None:
    """The synthetic township uses the behaviour, not just the defaults."""
    pump_supply = [
        ln
        for ln in town.links
        if ln.kind is LinkKind.POWERS and ln.target == "P2"
    ]
    assert pump_supply, "P2 has no power supply"
    link = pump_supply[0]
    assert link.threshold > 0.0
    assert link.delay_h > 0.0
    assert link.backup_capacity_h > 0.0

    hospital_water = [
        ln
        for ln in town.links
        if ln.kind is LinkKind.SUPPLIES_WATER and ln.target == "H2"
    ]
    assert hospital_water[0].backup_capacity_h > 0.0


def test_brownout_stops_a_pump_that_a_bare_edge_would_keep_running(
    town: Township,
) -> None:
    """The threshold is what makes a partial supply insufficient."""
    from civictwin.engine.layers.energy import EnergyLayer

    engine = Engine(town)
    layer = engine._base.energy
    state = engine.new_state()
    state.gate.reset(town.links)
    assert isinstance(layer, EnergyLayer)

    # Half supply: above zero, below the pump's threshold.
    state.damage["S2"] = state.damage["S2"]
    link = next(
        ln for ln in town.links if ln.kind is LinkKind.POWERS and ln.target == "P2"
    )
    assert state.gate.availability(link, 0.5, 0.0, 1.0) == 1.0  # inside the delay
    assert state.gate.availability(link, 0.5, 1.0, 1.0) == 1.0  # on the wet well
    assert state.gate.availability(link, 0.5, 2.0, 1.0) == 0.0  # both spent


def test_each_link_is_gated_exactly_once_per_step(
    town: Township, engine: Engine
) -> None:
    """A reserve must drain at one hour per hour, not two.

    The energy layer evaluates dependencies in one pass. An earlier version
    re-evaluated links below a tied feeder to push restored power downstream,
    which drew on each of those links' reserves twice in the same step.
    """
    counts: dict[str, int] = {}
    gate_cls = type(engine.new_state().gate)
    original = gate_cls.availability

    def counting(self, link, supplied, t, dt):  # type: ignore[no-untyped-def]
        counts[f"{link.key}@{t}"] = counts.get(f"{link.key}@{t}", 0) + 1
        return original(self, link, supplied, t, dt)

    gate_cls.availability = counting  # type: ignore[method-assign]
    try:
        engine.simulate(
            calm(town),
            Overlay(
                forced_failures=("S2",),
                interventions=("tie:F2-F4",),
                flood_enabled=False,
            ),
            _skip_amplification=True,
        )
    finally:
        gate_cls.availability = original  # type: ignore[method-assign]

    repeated = {k: n for k, n in counts.items() if n > 1}
    assert not repeated, f"these links were gated more than once in a step: {repeated}"


def test_reserve_lasts_its_stated_hours_under_a_tie(
    town: Township, engine: Engine
) -> None:
    """The pump's wet well should last as long as it says, tie or no tie."""
    link = next(
        ln for ln in town.links if ln.kind is LinkKind.POWERS and ln.target == "P2"
    )
    assert link.backup_capacity_h > 0.0

    result = engine.simulate(
        calm(town),
        Overlay(forced_failures=("S2",), flood_enabled=False),
        record=True,
        _skip_amplification=True,
    )
    on_reserve = [
        f.t for f in result.timeline or () if f.state.get("P2") in ("backup", "critical")
    ]
    if on_reserve:
        span = max(on_reserve) - min(on_reserve) + DEFAULT.sim.dt_h
        assert span <= link.backup_capacity_h + link.delay_h + DEFAULT.sim.dt_h
