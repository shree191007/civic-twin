"""Acceptance tests for spec 01 — ontology, config, township generation."""
from __future__ import annotations

import json
import math
import time
from collections import Counter

import pytest

from civictwin.config import Config, DEFAULT
from civictwin.io import load_township, save_township
from civictwin.ontology import (
    Asset,
    AssetKind,
    Link,
    LinkKind,
    Portfolio,
    Township,
)
from civictwin.provenance import Provenance
from civictwin.synth.township import generate

EXPECTED_COUNTS: dict[AssetKind, int] = {
    AssetKind.GRID_SUPPLY: 1,
    AssetKind.SUBSTATION: 3,
    AssetKind.FEEDER: 9,
    AssetKind.TRANSFORMER: 16,
    AssetKind.FUEL_STATION: 2,
    AssetKind.INTAKE: 1,
    AssetKind.TREATMENT: 1,
    AssetKind.PUMP: 3,
    AssetKind.TANK: 4,
    AssetKind.STORMWATER_PUMP: 2,
    AssetKind.EXCHANGE: 2,
    AssetKind.TOWER: 8,
    AssetKind.FIBRE: 4,
    AssetKind.BRIDGE: 2,
    AssetKind.UNDERPASS: 3,
    AssetKind.HOSPITAL: 2,
    AssetKind.CLINIC: 3,
    AssetKind.FIRE_STATION: 1,
    AssetKind.EOC: 1,
    AssetKind.SHELTER: 4,
    AssetKind.DEPOT: 3,
}


@pytest.fixture(scope="module")
def town() -> Township:
    return generate(42)


def test_generate_deterministic() -> None:
    a = generate(42).to_dict()
    b = generate(42).to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generate_valid(town: Township) -> None:
    assert town.validate() == []


def test_asset_counts(town: Township) -> None:
    counts = Counter(a.kind for a in town.assets.values())
    assert dict(counts) == EXPECTED_COUNTS
    assert len(town.assets) == 75
    assert len(town.crews) == 6


def test_zone_count_and_population(town: Township) -> None:
    assert len(town.zones) == 16
    total = sum(z.population for z in town.zones)
    assert 70_000 <= total <= 90_000
    for z in town.zones:
        assert 1800 <= z.population <= 9500
        assert 0.06 <= z.vulnerable_fraction <= 0.22


def test_upstream_cycle_safe(town: Township) -> None:
    t = generate(42)
    # P2 already has CONTROLS from X2; close the loop with X2 <- P2.
    t.links.append(Link("P2", "X2", LinkKind.CONTROLS))
    t.rebuild_caches()
    start = time.perf_counter()
    up = t.upstream("P2")
    assert time.perf_counter() - start < 1.0
    assert "X2" in up and "P2" in up
    assert "S2" in up


def test_trap_shared_substation(town: Township) -> None:
    shared = town.upstream("T5") & town.upstream("T6")
    assert "S2" in shared


def test_trap_fibre_colocation(town: Township) -> None:
    assert town.providers("FIBRE_1", {LinkKind.HOSTED_ON}) == ["B1"]
    assert town.providers("FIBRE_2", {LinkKind.HOSTED_ON}) == ["B1"]
    assert town.assets["B1"].kind is AssetKind.BRIDGE


def test_trap_hospital_water(town: Township) -> None:
    up = town.upstream("H2")
    assert {"K3", "P2", "S2"} <= up
    assert town.providers("K3", {LinkKind.SUPPLIES_WATER}) == ["P2"]
    assert "K3" in town.providers("H2", {LinkKind.SUPPLIES_WATER})


def test_trap_depots_west(town: Township) -> None:
    depots = [a for a in town.assets.values() if a.kind is AssetKind.DEPOT]
    assert len(depots) == 3
    assert all(a.x < 3000 for a in depots)

    bridge_edges = {
        e.id
        for e in town.roads.values()
        if e.host_asset and town.assets[e.host_asset].kind is AssetKind.BRIDGE
    }
    far_bank_assets = [
        a for a in town.assets.values() if a.id in {"S2", "P2", "X2", "H2", "T5"}
    ]
    for d in depots:
        for a in far_bank_assets:
            assert math.isfinite(town.shortest_path_hours(d.node, a.node))
            assert not math.isfinite(
                town.shortest_path_hours(d.node, a.node, blocked=bridge_edges)
            ), f"{d.id} -> {a.id} reachable without a bridge"


def test_served_population_monotone(town: Township) -> None:
    assert town.served_population("S2") > town.served_population("T5")


def test_roundtrip_json(town: Township, tmp_path) -> None:
    p = tmp_path / "t.json"
    save_township(town, p)
    back = load_township(p)
    assert back.to_dict() == town.to_dict()
    assert back == town


def test_road_graph_connected(town: Township) -> None:
    assert len(town.components()) == 1


def test_no_river_crossing_except_bridges(town: Township) -> None:
    bridge_hosted = [
        e
        for e in town.roads.values()
        if e.host_asset and town.assets[e.host_asset].kind is AssetKind.BRIDGE
    ]
    assert len(bridge_hosted) == 2
    for e in town.roads.values():
        ux, uy = town.nodes[e.u]
        vx, vy = town.nodes[e.v]
        crosses = (uy >= _river_y(town, ux)) != (vy >= _river_y(town, vx))
        if crosses:
            assert e.host_asset is not None
            assert town.assets[e.host_asset].kind is AssetKind.BRIDGE


def _river_y(town: Township, x: float) -> float:
    from civictwin.synth.geometry import build_terrain
    import numpy as np

    terrain = build_terrain(np.random.default_rng(town.seed), town.extent_m)
    return terrain.river_y(x)


def test_config_roundtrip() -> None:
    d = json.loads(json.dumps(DEFAULT.to_dict()))
    assert Config.from_dict(d) == DEFAULT


def test_validate_catches_dangling_link(town: Township) -> None:
    t = generate(42)
    before = len(t.validate())
    t.links.append(Link("S1", "NOPE", LinkKind.POWERS))
    t.rebuild_caches()
    after = t.validate()
    assert len(after) == before + 1
    assert any("NOPE" in p for p in after)


def test_generate_is_fast() -> None:
    start = time.perf_counter()
    generate(42)
    assert time.perf_counter() - start < 2.0


def test_upstream_all_assets_fast(town: Township) -> None:
    town.upstream("S1")  # warm nothing in particular
    t = generate(42)
    start = time.perf_counter()
    for aid in t.assets:
        t.upstream(aid)
    assert time.perf_counter() - start < 0.05


def test_provenance_is_recorded(town: Township) -> None:
    kinds = {a.kind: a.provenance for a in town.assets.values()}
    assert kinds[AssetKind.BRIDGE] is Provenance.OBSERVED
    assert kinds[AssetKind.TOWER] is Provenance.INFERRED
    assert kinds[AssetKind.FEEDER] is Provenance.SYNTHETIC
    assert all(z.provenance is Provenance.INFERRED for z in town.zones)
