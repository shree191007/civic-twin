"""Deterministic synthetic township generator.

Produces a 6 km x 6 km riverside town with four infrastructure portfolios and
six deliberate structural weaknesses (see specs/01-ONTOLOGY.md section 8.3).

Bank convention: the river runs roughly west-east across the middle of the
square, so the two banks are the *near* bank (+1, where the depots, the grid
supply and the ridge sit -- the spec calls this the "west bank") and the *far*
bank (-1, the low-lying side -- the spec calls this the "east bank").
"""
from __future__ import annotations

import logging
import math
from typing import Iterable, Sequence

import numpy as np

from civictwin.ontology import (
    Asset,
    AssetKind,
    Crew,
    DemandZone,
    Link,
    LinkKind,
    Portfolio,
    RoadEdge,
    Township,
)
from civictwin.provenance import Provenance
from civictwin.synth.geometry import (
    GRID_N,
    Terrain,
    build_terrain,
    grid_edge_pairs,
    grid_nodes,
    nearest_node,
)

logger = logging.getLogger(__name__)

EXTENT_M = 6000.0
CRS_EPSG = 32644
ORIGIN_LONLAT = (80.20, 13.05)

N_EDGE_DELETIONS = 18
BRIDGE_COLUMNS = (3, 9)  # node grid columns carrying the two bridges
UNDERPASS_HAND_M = 0.2
BRIDGE_DECK_HAND_M = 2.0
MIN_ASSET_SPACING_M = 220.0
#: A tank's capacity is expressed as hours of supply it can deliver unaided.
TANK_STORAGE_HOURS = 6.0
#: Dependency behaviour (spec patch section 10). A pump cannot lift water on a
#: brownout, holds a wet well, and takes time to lose downstream pressure.
PUMP_POWER_THRESHOLD = 0.6
PUMP_WET_WELL_H = 1.0
PUMP_PRESSURE_DELAY_H = 0.5
TREATMENT_POWER_THRESHOLD = 0.5
EXCHANGE_POWER_THRESHOLD = 0.4
HOSPITAL_TANK_H = 6.0
#: Tower capacity is in units of 1000 subscribers. Each tower is sized for its
#: share of the peak emergency demand of the zones it covers, so a healthy
#: network carries the surge -- and congestion appears only when a neighbouring
#: tower dies and its traffic hands over.
PEOPLE_PER_TOWER_CAPACITY_UNIT = 1000.0
EMERGENCY_DEMAND_FACTOR = 2.5

POP_MIN, POP_MAX = 1800, 9500
POP_TOTAL_TARGET = 79_000
VULN_MIN, VULN_MAX = 0.06, 0.22

LANES_DEFAULT = 2
FREE_FLOW_KPH = 40.0
CAPACITY_VPH_PER_LANE = 900.0

#: kind -> (fragility_median_m, backup_hours, fuel_hours, repair_hours_base)
FRAGILITY: dict[AssetKind, tuple[float, float, float, float]] = {
    AssetKind.GRID_SUPPLY: (3.0, 0.0, 0.0, 48.0),
    AssetKind.SUBSTATION: (0.9, 0.0, 0.0, 36.0),
    AssetKind.FEEDER: (1.5, 0.0, 0.0, 12.0),
    AssetKind.TRANSFORMER: (1.2, 0.0, 0.0, 8.0),
    AssetKind.INTAKE: (2.5, 0.0, 0.0, 24.0),
    AssetKind.TREATMENT: (1.1, 0.0, 24.0, 36.0),
    AssetKind.PUMP: (0.7, 0.0, 0.0, 18.0),
    AssetKind.TANK: (3.0, 0.0, 0.0, 24.0),
    AssetKind.STORMWATER_PUMP: (0.8, 0.0, 0.0, 12.0),
    AssetKind.EXCHANGE: (1.0, 8.0, 24.0, 24.0),
    AssetKind.TOWER: (1.3, 4.0, 0.0, 12.0),
    AssetKind.FIBRE: (99.0, 0.0, 0.0, 16.0),
    AssetKind.BRIDGE: (2.8, 0.0, 0.0, 72.0),
    AssetKind.UNDERPASS: (99.0, 0.0, 0.0, 4.0),
    AssetKind.HOSPITAL: (1.4, 0.0, 48.0, 48.0),
    AssetKind.CLINIC: (1.2, 0.0, 8.0, 24.0),
    AssetKind.FIRE_STATION: (1.2, 0.0, 24.0, 24.0),
    AssetKind.EOC: (1.5, 0.0, 72.0, 24.0),
    AssetKind.SHELTER: (2.0, 0.0, 0.0, 12.0),
    AssetKind.DEPOT: (1.5, 0.0, 24.0, 12.0),
    AssetKind.FUEL_STATION: (1.0, 0.0, 0.0, 12.0),
}

PROVENANCE_BY_KIND: dict[AssetKind, Provenance] = {
    AssetKind.BRIDGE: Provenance.OBSERVED,
    AssetKind.UNDERPASS: Provenance.OBSERVED,
    AssetKind.HOSPITAL: Provenance.OBSERVED,
    AssetKind.SUBSTATION: Provenance.OBSERVED,
    AssetKind.EXCHANGE: Provenance.OBSERVED,
    AssetKind.TREATMENT: Provenance.OBSERVED,
    AssetKind.INTAKE: Provenance.OBSERVED,
    AssetKind.FIRE_STATION: Provenance.OBSERVED,
    AssetKind.TOWER: Provenance.INFERRED,
    AssetKind.TANK: Provenance.INFERRED,
    AssetKind.PUMP: Provenance.INFERRED,
    AssetKind.DEPOT: Provenance.INFERRED,
    AssetKind.GRID_SUPPLY: Provenance.INFERRED,
    AssetKind.CLINIC: Provenance.INFERRED,
    AssetKind.EOC: Provenance.INFERRED,
    AssetKind.SHELTER: Provenance.INFERRED,
    AssetKind.FUEL_STATION: Provenance.INFERRED,
    AssetKind.FEEDER: Provenance.SYNTHETIC,
    AssetKind.TRANSFORMER: Provenance.SYNTHETIC,
    AssetKind.FIBRE: Provenance.SYNTHETIC,
    AssetKind.STORMWATER_PUMP: Provenance.SYNTHETIC,
}

PORTFOLIO_BY_KIND: dict[AssetKind, Portfolio] = {
    AssetKind.GRID_SUPPLY: Portfolio.ENERGY,
    AssetKind.SUBSTATION: Portfolio.ENERGY,
    AssetKind.FEEDER: Portfolio.ENERGY,
    AssetKind.TRANSFORMER: Portfolio.ENERGY,
    AssetKind.FUEL_STATION: Portfolio.ENERGY,
    AssetKind.INTAKE: Portfolio.WATER,
    AssetKind.TREATMENT: Portfolio.WATER,
    AssetKind.PUMP: Portfolio.WATER,
    AssetKind.TANK: Portfolio.WATER,
    AssetKind.STORMWATER_PUMP: Portfolio.WATER,
    AssetKind.EXCHANGE: Portfolio.COMMS,
    AssetKind.TOWER: Portfolio.COMMS,
    AssetKind.FIBRE: Portfolio.COMMS,
    AssetKind.BRIDGE: Portfolio.TRANSPORT,
    AssetKind.UNDERPASS: Portfolio.TRANSPORT,
    AssetKind.HOSPITAL: Portfolio.SERVICES,
    AssetKind.CLINIC: Portfolio.SERVICES,
    AssetKind.FIRE_STATION: Portfolio.SERVICES,
    AssetKind.EOC: Portfolio.SERVICES,
    AssetKind.SHELTER: Portfolio.SERVICES,
    AssetKind.DEPOT: Portfolio.SERVICES,
}


class _Builder:
    """Accumulates assets and links while the township is being assembled."""

    def __init__(
        self,
        terrain: Terrain,
        nodes: dict[int, tuple[float, float]],
        scale: float = 1.0,
    ) -> None:
        self.terrain = terrain
        self.nodes = nodes
        self.scale = scale
        self.extent_m = EXTENT_M * scale
        self.assets: dict[str, Asset] = {}
        self.links: list[Link] = []

    def place(
        self,
        target_hand_m: float | None,
        anchor: tuple[float, float],
        bank: int | None = None,
        search_radius_m: float = 2200.0,
        max_x: float | None = None,
    ) -> tuple[float, float]:
        """Find a deterministic point near `anchor` with the wanted HAND value.

        Anchors are written in the coordinates of the nominal 6 km township and
        are scaled here, so `generate(scale=0.5)` produces the same layout in a
        3 km square.

        Args:
            target_hand_m: desired height above drainage; None keeps the anchor.
            anchor: preferred location in metres of the nominal township.
            bank: +1 near bank, -1 far bank, None either.
            search_radius_m: how far from the anchor to look.
            max_x: optional hard eastern bound on the result.
        Returns:
            (x, y) in metres.
        """
        ax, ay = anchor[0] * self.scale, anchor[1] * self.scale
        if target_hand_m is None:
            return (ax, ay)
        best: tuple[float, float] = (ax, ay)
        best_cost = math.inf
        step = 100.0 * self.scale
        radius = search_radius_m * self.scale
        bound = 50.0 * self.scale
        limit = None if max_x is None else max_x * self.scale
        rng_lo, rng_hi = -radius, radius + 1e-9
        yy = rng_lo
        while yy <= rng_hi:
            xx = rng_lo
            while xx <= rng_hi:
                x, y = ax + xx, ay + yy
                if not (
                    bound <= x <= self.extent_m - bound
                    and bound <= y <= self.extent_m - bound
                ):
                    xx += step
                    continue
                if limit is not None and x > limit:
                    xx += step
                    continue
                if bank is not None and self.terrain.bank(x, y) != bank:
                    xx += step
                    continue
                h = self.terrain.hand_m(x, y)
                dist = math.hypot(xx, yy)
                cost = (
                    abs(h - target_hand_m)
                    + dist / self.extent_m
                    + self._crowding(x, y)
                )
                if cost < best_cost:
                    best_cost, best = cost, (x, y)
                xx += step
            yy += step
        return best

    def _crowding(self, x: float, y: float) -> float:
        """Penalty that keeps placed assets from stacking on one another."""
        penalty = 0.0
        spacing = MIN_ASSET_SPACING_M * self.scale
        for a in self.assets.values():
            d = math.hypot(a.x - x, a.y - y)
            if d < spacing:
                penalty += (spacing - d) / spacing
        return penalty

    def add(
        self,
        aid: str,
        kind: AssetKind,
        name: str,
        xy: tuple[float, float],
        *,
        capacity: float = 1.0,
        scada: bool = False,
        meta: dict[str, object] | None = None,
    ) -> Asset:
        frag, backup, fuel, repair = FRAGILITY[kind]
        x, y = xy
        a = Asset(
            id=aid,
            kind=kind,
            portfolio=PORTFOLIO_BY_KIND[kind],
            name=name,
            x=round(x, 2),
            y=round(y, 2),
            node=nearest_node(self.nodes, x, y),
            hand_m=round(self.terrain.hand_m(x, y), 3),
            fragility_median_m=frag,
            provenance=PROVENANCE_BY_KIND[kind],
            fragility_beta=0.4,
            capacity=capacity,
            backup_hours=backup,
            fuel_hours=fuel,
            repair_hours_base=repair,
            scada_controlled=scada,
            meta=dict(meta or {}),
        )
        self.assets[aid] = a
        return a

    def link(
        self,
        source: str,
        target: str,
        kind: LinkKind,
        capacity: float = 1.0,
        provenance: Provenance = Provenance.SYNTHETIC,
        threshold: float = 0.0,
        backup_capacity_h: float = 0.0,
        delay_h: float = 0.0,
    ) -> None:
        self.links.append(
            Link(
                source,
                target,
                kind,
                capacity,
                provenance,
                threshold=threshold,
                backup_capacity_h=backup_capacity_h,
                delay_h=delay_h,
            )
        )


# --------------------------------------------------------------------- roads


def _build_roads(
    rng: np.random.Generator,
    terrain: Terrain,
    nodes: dict[int, tuple[float, float]],
) -> tuple[dict[str, RoadEdge], list[tuple[int, int]], list[tuple[int, int]]]:
    """Build the road network, returning (edges, bridge_pairs, underpass_pairs)."""
    pairs = grid_edge_pairs()

    def crosses(u: int, v: int) -> bool:
        ux, uy = nodes[u]
        vx, vy = nodes[v]
        return terrain.bank(ux, uy) != terrain.bank(vx, vy)

    crossing = [p for p in pairs if crosses(*p)]
    # Keep exactly two crossings: the vertical edges in the two bridge columns.
    bridge_pairs: list[tuple[int, int]] = []
    for col in BRIDGE_COLUMNS:
        candidates = [
            p
            for p in crossing
            if p[1] - p[0] == GRID_N and p[0] % GRID_N == col
        ]
        if not candidates:  # pragma: no cover - geometry guarantees one per column
            raise RuntimeError(f"no river crossing in column {col}")
        bridge_pairs.append(candidates[0])
    keep_crossing = set(bridge_pairs)
    pairs = [p for p in pairs if p not in set(crossing) or p in keep_crossing]

    adj_all = _adjacency(pairs)
    kept = set(pairs)
    removable = [p for p in pairs if p not in keep_crossing]
    order = rng.permutation(len(removable))
    deleted = 0
    for idx in order:
        if deleted >= N_EDGE_DELETIONS:
            break
        cand = removable[int(idx)]
        trial = kept - {cand}
        if _connected(trial, nodes):
            kept = trial
            deleted += 1
    if deleted < N_EDGE_DELETIONS:  # pragma: no cover
        logger.warning("only deleted %d of %d edges", deleted, N_EDGE_DELETIONS)
    del adj_all

    ordered = [p for p in pairs if p in kept]
    # Three deterministic low underpasses, away from the bridges.
    non_bridge = [p for p in ordered if p not in keep_crossing]
    up_idx = rng.choice(len(non_bridge), size=3, replace=False)
    underpass_pairs = [non_bridge[int(i)] for i in sorted(int(j) for j in up_idx)]

    edges: dict[str, RoadEdge] = {}
    for i, (u, v) in enumerate(ordered):
        ux, uy = nodes[u]
        vx, vy = nodes[v]
        length = math.hypot(vx - ux, vy - uy)
        if (u, v) in keep_crossing:
            hand = BRIDGE_DECK_HAND_M
        elif (u, v) in set(underpass_pairs):
            hand = UNDERPASS_HAND_M
        else:
            hand = min(
                terrain.hand_m(ux + (vx - ux) * f, uy + (vy - uy) * f)
                for f in (0.0, 0.25, 0.5, 0.75, 1.0)
            )
        edges[f"e{i}"] = RoadEdge(
            id=f"e{i}",
            u=u,
            v=v,
            length_m=round(length, 2),
            lanes=LANES_DEFAULT,
            free_flow_kph=FREE_FLOW_KPH,
            capacity_vph=LANES_DEFAULT * CAPACITY_VPH_PER_LANE,
            hand_m=round(hand, 3),
            host_asset=None,
            provenance=Provenance.OBSERVED,
        )
    return edges, bridge_pairs, underpass_pairs


def _midpoint(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _adjacency(pairs: Iterable[tuple[int, int]]) -> dict[int, list[int]]:
    adj: dict[int, list[int]] = {}
    for u, v in pairs:
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)
    return adj


def _connected(pairs: Iterable[tuple[int, int]], nodes: dict[int, tuple[float, float]]) -> bool:
    adj = _adjacency(pairs)
    if len(adj) < len(nodes):
        return False
    start = next(iter(nodes))
    seen = {start}
    stack = [start]
    while stack:
        n = stack.pop()
        for nb in adj.get(n, ()):
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return len(seen) == len(nodes)


# --------------------------------------------------------------------- zones

ZONE_COLS = 4
ZONE_ROWS = 4
#: fixed y for row 0 (far north) and row 3 (far south); rows 1/2 hug the river
ROW0_Y = 5100.0
ROW3_Y = 800.0
ROW1_OFFSET_M = 700.0
ROW2_OFFSET_M = -420.0


def _zone_centroid(
    terrain: Terrain, row: int, col: int, scale: float = 1.0
) -> tuple[float, float]:
    """Zone centroid. Rows 1 and 2 hug the river; rows 0 and 3 sit at fixed y."""
    x = (col * 1500.0 + 750.0) * scale
    if row == 0:
        y = ROW0_Y * scale
    elif row == 1:
        y = terrain.river_y(x) + ROW1_OFFSET_M * scale
    elif row == 2:
        y = terrain.river_y(x) + ROW2_OFFSET_M * scale
    else:
        y = ROW3_Y * scale
    return x, y


def _populations(rng: np.random.Generator) -> list[int]:
    """16 zone populations in [POP_MIN, POP_MAX] summing near POP_TOTAL_TARGET."""
    raw = rng.uniform(POP_MIN, POP_MAX, size=ZONE_ROWS * ZONE_COLS)
    scaled = raw * (POP_TOTAL_TARGET / raw.sum())
    pops = [int(round(min(POP_MAX, max(POP_MIN, v)))) for v in scaled]
    # nudge the largest zones to land the total inside the required band
    total = sum(pops)
    i = 0
    while not 70_000 <= total <= 90_000 and i < 1000:
        j = i % len(pops)
        delta = 50 if total < 70_000 else -50
        new = min(POP_MAX, max(POP_MIN, pops[j] + delta))
        total += new - pops[j]
        pops[j] = new
        i += 1
    return pops


# ----------------------------------------------------------------- generator

#: zone -> substation, chosen so each substation serves a contiguous block
ZONE_SUBSTATION: dict[int, str] = {
    **{z: "S1" for z in (1, 2, 5, 6)},
    **{z: "S3" for z in (3, 4, 7, 8)},
    **{z: "S2" for z in (9, 10, 11, 12, 13, 14, 15, 16)},
}
#: feeder -> zones it serves
FEEDER_ZONES: dict[str, tuple[int, ...]] = {
    "F1": (1, 2),
    "F2": (5,),
    "F3": (6,),
    "F4": (9, 10),
    "F5": (11, 12),
    "F6": (13, 14, 15, 16),
    "F7": (3, 4),
    "F8": (7,),
    "F9": (8,),
}
FEEDER_SUBSTATION: dict[str, str] = {
    "F1": "S1", "F2": "S1", "F3": "S1",
    "F4": "S2", "F5": "S2", "F6": "S2",
    "F7": "S3", "F8": "S3", "F9": "S3",
}
#: tank -> zones it serves (K3 is the trap: only pump P2 refills it)
TANK_ZONES: dict[str, tuple[int, ...]] = {
    "K1": (1, 2, 5, 6),
    "K2": (3, 4, 7, 8),
    "K3": (9, 10, 11, 12),
    "K4": (13, 14, 15, 16),
}
#: tower -> zones it covers; every zone is covered by exactly two towers
TOWER_ZONES: dict[str, tuple[int, ...]] = {
    "T1": (1, 2, 3, 4),
    "T2": (1, 2, 3, 4),
    "T3": (5, 6, 7, 8),
    "T4": (5, 6, 7, 8),
    "T5": (9, 10, 11, 12),
    "T6": (9, 10, 11, 12),
    "T7": (13, 14, 15, 16),
    "T8": (13, 14, 15, 16),
}
#: tower -> powering substation. T5/T6 share S2: the fake-redundancy trap.
TOWER_SUBSTATION: dict[str, str] = {
    "T1": "S1", "T2": "S3", "T3": "S1", "T4": "S3",
    "T5": "S2", "T6": "S2", "T7": "S2", "T8": "S2",
}


def generate(seed: int = 42, scale: float = 1.0) -> Township:
    """Build the deterministic synthetic township.

    Args:
        seed: RNG seed; the same seed always yields byte-identical output.
        scale: multiplies the grid dimensions (1.0 = 6 km square).
    Returns:
        A fully linked, validating Township.
    """
    rng = np.random.default_rng(seed)
    extent = EXTENT_M * scale
    terrain = build_terrain(rng, extent)
    nodes = grid_nodes(extent)
    roads, bridge_pairs, underpass_pairs = _build_roads(rng, terrain, nodes)
    b = _Builder(terrain, nodes, scale)

    zone_xy: dict[int, tuple[float, float]] = {}
    for row in range(ZONE_ROWS):
        for col in range(ZONE_COLS):
            zid = row * ZONE_COLS + col + 1
            zone_xy[zid] = _zone_centroid(terrain, row, col, scale)

    # ----------------------------------------------------------- energy
    b.add("GS1", AssetKind.GRID_SUPPLY, "Regional grid supply",
          b.place(9.0, (900.0, 5200.0), bank=1), capacity=120.0)
    b.add("S1", AssetKind.SUBSTATION, "Westgate substation",
          b.place(7.0, (1500.0, 4800.0), bank=1), capacity=40.0)
    b.add("S2", AssetKind.SUBSTATION, "Riverside East substation",
          b.place(1.2, (4400.0, 2700.0), bank=-1), capacity=40.0)
    b.add("S3", AssetKind.SUBSTATION, "Northfield substation",
          b.place(4.0, (4200.0, 5100.0), bank=1), capacity=40.0)

    for fid, sub in FEEDER_SUBSTATION.items():
        zs = FEEDER_ZONES[fid]
        fx = sum(zone_xy[z][0] for z in zs) / len(zs)
        fy = sum(zone_xy[z][1] for z in zs) / len(zs)
        sa = b.assets[sub]
        b.add(fid, AssetKind.FEEDER, f"Feeder {fid}",
              _midpoint((fx, fy), (sa.x, sa.y)), capacity=12.0)
        b.link(sub, fid, LinkKind.POWERS, capacity=12.0)
        b.link("GS1", sub, LinkKind.POWERS, capacity=40.0) if False else None

    for sub in ("S1", "S2", "S3"):
        b.link("GS1", sub, LinkKind.POWERS, capacity=40.0)

    zone_feeder: dict[int, str] = {
        z: fid for fid, zs in FEEDER_ZONES.items() for z in zs
    }
    for zid in range(1, 17):
        fid = zone_feeder[zid]
        b.add(f"TR{zid}", AssetKind.TRANSFORMER, f"Transformer {zid}",
              zone_xy[zid], capacity=2.0)
        b.link(fid, f"TR{zid}", LinkKind.POWERS, capacity=2.0)

    b.add("G1", AssetKind.FUEL_STATION, "Westgate fuel depot",
          b.place(4.0, (2200.0, 4600.0), bank=1))
    b.add("G2", AssetKind.FUEL_STATION, "Eastbank fuel station",
          b.place(1.5, (4200.0, 2200.0), bank=-1))

    # ------------------------------------------------------------ water
    b.add("I1", AssetKind.INTAKE, "River intake",
          b.place(0.3, (2600.0, 2950.0)), capacity=900.0)
    b.add("W1", AssetKind.TREATMENT, "Central treatment works",
          b.place(1.0, (2600.0, 3700.0), bank=1), capacity=850.0)
    b.add("P1", AssetKind.PUMP, "West booster pump",
          b.place(3.0, (1600.0, 4300.0), bank=1), capacity=400.0)
    b.add("P2", AssetKind.PUMP, "East booster pump",
          b.place(0.9, (4600.0, 2900.0), bank=-1), capacity=400.0, scada=True)
    b.add("P3", AssetKind.PUMP, "North booster pump",
          b.place(2.5, (4300.0, 4600.0), bank=1), capacity=400.0, scada=True)

    tank_anchor = {
        "K1": (1400.0, 4500.0), "K2": (4600.0, 4800.0),
        "K3": (4700.0, 2300.0), "K4": (2200.0, 900.0),
    }
    tank_bank = {"K1": 1, "K2": 1, "K3": -1, "K4": -1}
    for kid, anchor in tank_anchor.items():
        b.add(kid, AssetKind.TANK, f"Service reservoir {kid}",
              b.place(5.5, anchor, bank=tank_bank[kid]), capacity=TANK_STORAGE_HOURS)

    b.add("SW1", AssetKind.STORMWATER_PUMP, "East basin stormwater pump",
          b.place(0.4, (3800.0, 2850.0), bank=-1), capacity=1.0)
    b.add("SW2", AssetKind.STORMWATER_PUMP, "Lower east stormwater pump",
          b.place(0.4, (5200.0, 3050.0), bank=-1), capacity=1.0)

    b.link("I1", "W1", LinkKind.SUPPLIES_WATER, capacity=850.0)
    for pid in ("P1", "P2", "P3"):
        b.link("W1", pid, LinkKind.SUPPLIES_WATER, capacity=400.0)
    b.link("P1", "K1", LinkKind.SUPPLIES_WATER, capacity=400.0)
    b.link("P1", "K4", LinkKind.SUPPLIES_WATER, capacity=400.0)
    b.link("P3", "K2", LinkKind.SUPPLIES_WATER, capacity=400.0)
    b.link("P2", "K3", LinkKind.SUPPLIES_WATER, capacity=400.0)  # trap 3

    # A pump needs most of its rated supply to lift water at all, carries a
    # wet-well reserve, and takes a little while to lose pressure downstream.
    for pump, substation in (("P1", "S1"), ("P2", "S2"), ("P3", "S3")):
        b.link(
            substation, pump, LinkKind.POWERS, capacity=3.0,
            threshold=PUMP_POWER_THRESHOLD,
            backup_capacity_h=PUMP_WET_WELL_H,
            delay_h=PUMP_PRESSURE_DELAY_H,
        )
    b.link("S1", "I1", LinkKind.POWERS, capacity=2.0, threshold=PUMP_POWER_THRESHOLD)
    b.link(
        "S1", "W1", LinkKind.POWERS, capacity=6.0,
        threshold=TREATMENT_POWER_THRESHOLD, delay_h=PUMP_PRESSURE_DELAY_H,
    )
    b.link("S2", "SW1", LinkKind.POWERS, capacity=2.0)  # trap 6
    b.link("S2", "SW2", LinkKind.POWERS, capacity=2.0)

    # ------------------------------------------------------------ comms
    b.add("X1", AssetKind.EXCHANGE, "West exchange",
          b.place(6.0, (1700.0, 4600.0), bank=1), capacity=100.0)
    b.add("X2", AssetKind.EXCHANGE, "East exchange",
          b.place(1.5, (4500.0, 2500.0), bank=-1), capacity=100.0)

    tower_anchor = {
        "T1": (1200.0, 5000.0), "T2": (4900.0, 5000.0),
        "T3": (1500.0, 3900.0), "T4": (4800.0, 4100.0),
        "T5": (2100.0, 2500.0), "T6": (5000.0, 2800.0),
        "T7": (1500.0, 1000.0), "T8": (4700.0, 1000.0),
    }
    tower_bank = {"T1": 1, "T2": 1, "T3": 1, "T4": 1,
                  "T5": -1, "T6": -1, "T7": -1, "T8": -1}
    for tid, anchor in tower_anchor.items():
        b.add(tid, AssetKind.TOWER, f"Cell tower {tid}",
              b.place(None, anchor), capacity=1.0)
        b.link(TOWER_SUBSTATION[tid], tid, LinkKind.POWERS, capacity=0.5)
    del tower_bank

    for i, name in enumerate(("FIBRE_1", "FIBRE_2", "FIBRE_3", "FIBRE_4"), start=1):
        anchor = {1: (2200.0, 3050.0), 2: (2250.0, 3100.0),
                  3: (2000.0, 4400.0), 4: (4600.0, 2300.0)}[i]
        b.add(
            name, AssetKind.FIBRE, f"Fibre route {i}",
            (anchor[0] * scale, anchor[1] * scale), capacity=100.0,
        )

    # trap 2: primary and backup east backhaul both ride bridge B1
    b.link("X1", "FIBRE_1", LinkKind.BACKHAULS, capacity=100.0)
    b.link("X1", "FIBRE_2", LinkKind.BACKHAULS, capacity=100.0)
    b.link("FIBRE_1", "X2", LinkKind.BACKHAULS, capacity=100.0)
    b.link("FIBRE_2", "X2", LinkKind.BACKHAULS, capacity=100.0)
    b.link("X1", "FIBRE_3", LinkKind.BACKHAULS, capacity=100.0)
    b.link("X2", "FIBRE_4", LinkKind.BACKHAULS, capacity=100.0)
    for tid in ("T1", "T2", "T3", "T4"):
        b.link("FIBRE_3", tid, LinkKind.BACKHAULS, capacity=25.0)
    for tid in ("T5", "T6", "T7", "T8"):
        b.link("FIBRE_4", tid, LinkKind.BACKHAULS, capacity=25.0)

    b.link("S1", "X1", LinkKind.POWERS, capacity=2.0, threshold=EXCHANGE_POWER_THRESHOLD)
    b.link("S2", "X2", LinkKind.POWERS, capacity=2.0, threshold=EXCHANGE_POWER_THRESHOLD)

    # trap 5: SCADA control of the two remote pumps rides the east exchange
    b.link("X2", "P2", LinkKind.CONTROLS)
    b.link("X2", "P3", LinkKind.CONTROLS)

    # -------------------------------------------------------- transport
    for i, (u, v) in enumerate(bridge_pairs, start=1):
        ux, uy = nodes[u]
        vx, vy = nodes[v]
        bid = f"B{i}"
        b.add(bid, AssetKind.BRIDGE, f"River bridge {bid}",
              ((ux + vx) / 2.0, (uy + vy) / 2.0))
        b.assets[bid].hand_m = BRIDGE_DECK_HAND_M
        for eid, e in roads.items():
            if (e.u, e.v) == (u, v):
                e.host_asset = bid
    for i, (u, v) in enumerate(underpass_pairs, start=1):
        ux, uy = nodes[u]
        vx, vy = nodes[v]
        uid = f"U{i}"
        b.add(uid, AssetKind.UNDERPASS, f"Underpass {uid}",
              ((ux + vx) / 2.0, (uy + vy) / 2.0))
        b.assets[uid].hand_m = UNDERPASS_HAND_M
        for eid, e in roads.items():
            if (e.u, e.v) == (u, v):
                e.host_asset = uid

    b.link("B1", "FIBRE_1", LinkKind.HOSTED_ON)  # trap 2
    b.link("B1", "FIBRE_2", LinkKind.HOSTED_ON)

    # --------------------------------------------------------- services
    b.add("H1", AssetKind.HOSPITAL, "St Anne's district hospital",
          b.place(6.0, (1800.0, 4900.0), bank=1), capacity=240.0)
    b.add("H2", AssetKind.HOSPITAL, "Eastbank general hospital",
          b.place(5.0, (4800.0, 1500.0), bank=-1), capacity=180.0)
    clinic_anchor = {"C1": (3000.0, 4900.0), "C2": (2200.0, 1400.0),
                     "C3": (5300.0, 3900.0)}
    for cid, anchor in clinic_anchor.items():
        b.add(cid, AssetKind.CLINIC, f"Community clinic {cid}",
              b.place(3.5, anchor), capacity=40.0)
    fs_xy = b.place(5.0, (1600.0, 4400.0), bank=1)
    b.add("FS1", AssetKind.FIRE_STATION, "Central fire station", fs_xy)
    b.add("E1", AssetKind.EOC, "Emergency operations centre",
          (fs_xy[0] + 60.0 * scale, fs_xy[1] + 60.0 * scale))
    shelter_anchor = {"SH1": (1200.0, 5300.0), "SH2": (5000.0, 5300.0),
                      "SH3": (1300.0, 800.0), "SH4": (5100.0, 800.0)}
    for sid, anchor in shelter_anchor.items():
        b.add(sid, AssetKind.SHELTER, f"Evacuation shelter {sid}",
              b.place(6.0, anchor), capacity=2500.0)

    # trap 4: every depot sits on the near bank, west of the midline
    depot_spec = {
        "D1": ("Power repair depot", Portfolio.ENERGY, (1900.0, 4200.0)),
        "D2": ("Water repair depot", Portfolio.WATER, (2400.0, 4500.0)),
        "D3": ("Comms repair depot", Portfolio.COMMS, (1500.0, 3800.0)),
    }
    for did, (dname, pf, anchor) in depot_spec.items():
        a = b.add(
            did, AssetKind.DEPOT, dname,
            b.place(3.5, anchor, bank=1, search_radius_m=900.0, max_x=2800.0),
        )
        a.portfolio = pf

    b.link("S1", "H1", LinkKind.POWERS, capacity=4.0)
    b.link("S3", "H2", LinkKind.POWERS, capacity=4.0)
    # Each hospital holds its own roof tank on top of whatever the mains give it.
    b.link(
        "K1", "H1", LinkKind.SUPPLIES_WATER, capacity=30.0,
        backup_capacity_h=HOSPITAL_TANK_H,
    )
    b.link(  # trap 3
        "K3", "H2", LinkKind.SUPPLIES_WATER, capacity=30.0,
        backup_capacity_h=HOSPITAL_TANK_H,
    )
    for cid, sub in (("C1", "S3"), ("C2", "S2"), ("C3", "S3")):
        b.link(sub, cid, LinkKind.POWERS, capacity=1.0)
    for cid, tank in (("C1", "K1"), ("C2", "K4"), ("C3", "K2")):
        b.link(tank, cid, LinkKind.SUPPLIES_WATER, capacity=5.0)
    b.link("S1", "FS1", LinkKind.POWERS, capacity=1.0)
    b.link("S1", "E1", LinkKind.POWERS, capacity=1.0)
    for sid, sub in (("SH1", "S1"), ("SH2", "S3"), ("SH3", "S2"), ("SH4", "S2")):
        b.link(sub, sid, LinkKind.POWERS, capacity=1.0)
    for did, sub in (("D1", "S1"), ("D2", "S1"), ("D3", "S1")):
        b.link(sub, did, LinkKind.POWERS, capacity=1.0)
    b.link("S1", "G1", LinkKind.POWERS, capacity=1.0)
    b.link("S2", "G2", LinkKind.POWERS, capacity=1.0)

    for target in ("H1", "W1", "X1", "FS1", "E1", "D1", "D2", "D3"):
        b.link("G1", target, LinkKind.FUELS)
    for target in ("H2", "X2", "C2"):
        b.link("G2", target, LinkKind.FUELS)

    # ------------------------------------------------------------ zones
    zone_tank = {z: kid for kid, zs in TANK_ZONES.items() for z in zs}
    zone_towers: dict[int, list[str]] = {z: [] for z in range(1, 17)}
    for tid, zs in TOWER_ZONES.items():
        for z in zs:
            zone_towers[z].append(tid)

    pops = _populations(rng)
    vulns = rng.uniform(VULN_MIN, VULN_MAX, size=16)
    zones: list[DemandZone] = []
    for zid in range(1, 17):
        x, y = zone_xy[zid]
        zones.append(
            DemandZone(
                id=f"Z{zid}",
                x=round(x, 2),
                y=round(y, 2),
                node=nearest_node(nodes, x, y),
                hand_m=round(terrain.hand_m(x, y), 3),
                population=pops[zid - 1],
                vulnerable_fraction=round(float(vulns[zid - 1]), 4),
                substation=ZONE_SUBSTATION[zid],
                feeder=zone_feeder[zid],
                tank=zone_tank[zid],
                towers=sorted(zone_towers[zid]),
                provenance=Provenance.INFERRED,
                water_storage_hours=6.0,
            )
        )

    for tid, zs in TOWER_ZONES.items():
        share = sum(pops[z - 1] / len(zone_towers[z]) for z in zs)
        b.assets[tid].capacity = round(
            share * EMERGENCY_DEMAND_FACTOR / PEOPLE_PER_TOWER_CAPACITY_UNIT, 4
        )

    crews = [
        Crew("CP1", Portfolio.ENERGY, "D1"),
        Crew("CP2", Portfolio.ENERGY, "D1"),
        Crew("CW1", Portfolio.WATER, "D2"),
        Crew("CW2", Portfolio.WATER, "D2"),
        Crew("CC1", Portfolio.COMMS, "D3"),
        Crew("CC2", Portfolio.COMMS, "D3"),
    ]

    return Township(
        name="Ellorai Township",
        crs_epsg=CRS_EPSG,
        origin_lonlat=ORIGIN_LONLAT,
        assets=b.assets,
        links=b.links,
        zones=zones,
        roads=roads,
        nodes=nodes,
        crews=crews,
        extent_m=extent,
        seed=seed,
    )
