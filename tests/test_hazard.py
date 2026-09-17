"""Acceptance tests for spec 02 section 4 — the flood hazard model."""
from __future__ import annotations

import numpy as np
import pytest

from gotham.config import DEFAULT
from gotham.engine.contract import HazardScenario
from gotham.engine.coordinator import Engine
from gotham.engine.hazard import HazardModel, sample_scenarios, spatial_field
from gotham.ontology import AssetKind, Township
from gotham.synth.township import generate

TIMES = np.arange(0.0, 73.0, 1.0)


@pytest.fixture(scope="module")
def town() -> Township:
    return generate(42)


def _scenario(town: Township, rain_mm: float, field_seed: int = 11) -> HazardScenario:
    return HazardScenario(
        id=f"r{rain_mm}",
        seed=5,
        rain_mm=rain_mm,
        field_seed=field_seed,
        onset_hour=0,
        asset_draws={a: 0.5 for a in town.assets},
    )


def _depths(town: Township, rain_mm: float) -> dict[str, np.ndarray]:
    model = HazardModel(town, _scenario(town, rain_mm), DEFAULT)
    return {aid: model.depth_series(aid, TIMES) for aid in town.assets}


def test_no_rain_no_flood(town: Township) -> None:
    model = HazardModel(town, _scenario(town, 0.0), DEFAULT)
    engine = Engine(town)
    state = engine.new_state()
    for t in TIMES:
        model.update(state, float(t))
        assert max(state.flood_depth.values()) == 0.0
        assert max(state.road_depth.values()) == 0.0


def test_depth_monotone_in_rain(town: Township) -> None:
    low = _depths(town, 120.0)
    high = _depths(town, 200.0)
    for aid, series in low.items():
        assert np.all(high[aid] + 1e-9 >= series), aid


def test_hand_protects(town: Township) -> None:
    model = HazardModel(town, _scenario(town, 400.0), DEFAULT)
    high_ground = [a for a in town.assets.values() if a.hand_m >= 9.0]
    assert high_ground, "expected some high-ground assets"
    for a in high_ground:
        assert model.max_depth(a.id) == 0.0 or a.hand_m < 14.0
    # an asset exactly at the HAND ceiling can never take fluvial water
    ceiling = max(town.assets.values(), key=lambda a: a.hand_m)
    if ceiling.hand_m >= 14.0:
        assert np.max(model.depth_series(ceiling.id, TIMES)) == 0.0


def test_recession(town: Township) -> None:
    model = HazardModel(town, _scenario(town, 300.0), DEFAULT)
    assert model.duration_h < DEFAULT.sim.horizon_h
    for aid in town.assets:
        assert model.depth_series(aid, np.array([DEFAULT.sim.horizon_h]))[0] == 0.0


def test_field_deterministic(town: Township) -> None:
    a = spatial_field(np.array([100.0, 3000.0]), np.array([200.0, 4000.0]), 77, DEFAULT, 6000.0)
    b = spatial_field(np.array([100.0, 3000.0]), np.array([200.0, 4000.0]), 77, DEFAULT, 6000.0)
    c = spatial_field(np.array([100.0, 3000.0]), np.array([200.0, 4000.0]), 78, DEFAULT, 6000.0)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)
    assert np.all((a >= 0.3) & (a <= 1.8))


def test_pluvial_feedback(town: Township) -> None:
    """Stormwater pumps out -> drainage capacity falls -> basins pond deeper."""
    model = HazardModel(town, _scenario(town, 150.0), DEFAULT)
    engine = Engine(town)
    pumps = [a.id for a in town.assets.values() if a.kind is AssetKind.STORMWATER_PUMP]

    def depth_at(t: float, pump_functionality: float) -> float:
        state = engine.new_state()
        for pid in pumps:
            state.drain_factor[pid] = pump_functionality
        model.update(state, t)
        return max(state.flood_depth[pid] for pid in pumps)

    running = depth_at(1.0, 1.0)
    failed = depth_at(1.0, 0.0)
    assert failed >= 1.2 * max(running, 1e-9)


def test_sample_scenarios_are_reproducible(town: Township) -> None:
    a = sample_scenarios(town, DEFAULT, 8, 3)
    b = sample_scenarios(town, DEFAULT, 8, 3)
    assert [s.rain_mm for s in a] == [s.rain_mm for s in b]
    assert a[0].asset_draws == b[0].asset_draws
    assert all(0.0 <= v <= 1.0 for v in a[0].asset_draws.values())
    assert all(s.return_period_y and s.return_period_y > 0 for s in a)


def test_asset_draws_are_independent_of_asset_set(town: Township) -> None:
    """Adding an asset must not perturb the draws of existing ones."""
    scenarios = sample_scenarios(town, DEFAULT, 3, 9)
    smaller = Township(
        name=town.name,
        crs_epsg=town.crs_epsg,
        origin_lonlat=town.origin_lonlat,
        assets={k: v for k, v in town.assets.items() if k != "SH4"},
        links=[l for l in town.links if "SH4" not in (l.source, l.target)],
        zones=town.zones,
        roads=town.roads,
        nodes=town.nodes,
        crews=town.crews,
        extent_m=town.extent_m,
        seed=town.seed,
    )
    reduced = sample_scenarios(smaller, DEFAULT, 3, 9)
    for full, part in zip(scenarios, reduced):
        for aid, u in part.asset_draws.items():
            assert full.asset_draws[aid] == u
