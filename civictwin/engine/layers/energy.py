"""Energy layer: damage, operator de-energisation, source connectivity, transfer."""
from __future__ import annotations

import math

from civictwin.config import Config
from civictwin.engine.contract import SharedState
from civictwin.engine.layers._graph import reachable, topological_order
from civictwin.ontology import (
    AssetKind,
    Link,
    LinkKind,
    Portfolio,
    Service,
    Township,
    damage_index,
)

DEENERGISE_DEPTH_M = 0.15
FEEDER_ZONE_RADIUS_M = 1100.0


class EnergyLayer:
    """Fast radial-network energy model."""

    portfolio = Portfolio.ENERGY

    def __init__(self) -> None:
        self.township: Township | None = None

    def reset(self, township: Township, cfg: Config, rng: object = None) -> None:
        self.township = township
        self._energy_ids = [
            a.id for a in township.assets.values() if a.portfolio is Portfolio.ENERGY
        ]
        self._sources = [
            a.id
            for a in township.assets.values()
            if a.kind is AssetKind.GRID_SUPPLY
        ]
        self._feeders = [
            a.id for a in township.assets.values() if a.kind is AssetKind.FEEDER
        ]
        energy_set = set(self._energy_ids)

        feeder_set = {
            a.id for a in township.assets.values() if a.kind is AssetKind.FEEDER
        }
        # A feeder-to-feeder POWERS link is a normally-open tie: a standby route,
        # not a second supply the feeder needs. It must stay out of the radial
        # supply graph, or adding a tie would make both feeders depend on each
        # other and could only ever make things worse.
        self._ties: dict[str, list[str]] = {f: [] for f in self._feeders}
        self._powers_adj: dict[str, list[str]] = {}
        self._powers_providers: dict[str, list[str]] = {}
        self._powers_links: dict[str, list[Link]] = {}
        for ln in township.links:
            if ln.kind is not LinkKind.POWERS:
                continue
            if ln.source in feeder_set and ln.target in feeder_set:
                self._ties.setdefault(ln.target, []).append(ln.source)
                self._ties.setdefault(ln.source, []).append(ln.target)
                continue
            self._powers_adj.setdefault(ln.source, []).append(ln.target)
            self._powers_providers.setdefault(ln.target, []).append(ln.source)
            self._powers_links.setdefault(ln.target, []).append(ln)

        self._energy_adj = {
            s: [t for t in ts if t in energy_set]
            for s, ts in self._powers_adj.items()
            if s in energy_set
        }
        self._order = topological_order(self._powers_adj, township.assets)

        # feeder -> zones, and the road edges that sit inside those zones
        self._feeder_zones: dict[str, list[str]] = {f: [] for f in self._feeders}
        for z in township.zones:
            if z.feeder in self._feeder_zones:
                self._feeder_zones[z.feeder].append(z.id)
        zone_xy = {z.id: (z.x, z.y) for z in township.zones}
        self._feeder_edges: dict[str, list[str]] = {}
        for fid, zids in self._feeder_zones.items():
            pts = [zone_xy[z] for z in zids]
            eids: list[str] = []
            for eid, e in township.roads.items():
                ux, uy = township.nodes[e.u]
                vx, vy = township.nodes[e.v]
                mx, my = (ux + vx) / 2.0, (uy + vy) / 2.0
                if any(
                    math.hypot(mx - px, my - py) <= FEEDER_ZONE_RADIUS_M
                    for px, py in pts
                ):
                    eids.append(eid)
            self._feeder_edges[fid] = eids

        self._feeder_load = {
            f: sum(
                township.zone(z).population
                for z in self._feeder_zones[f]
            )
            / 1000.0
            for f in self._feeders
        }
        self._zone_transformer = {z.id: f"TR{z.id[1:]}" for z in township.zones}
        self._below_cache: dict[str, list[str]] = {}

    def _energy_order_below(self, feeder_id: str) -> list[str]:
        """Energy assets fed by this feeder, in supply order. Cached."""
        cached = self._below_cache.get(feeder_id)
        if cached is not None:
            return cached
        energy = set(self._energy_ids)
        below = {
            aid
            for aid in (self.township.downstream(feeder_id, {LinkKind.POWERS}) if self.township else set())
            if aid in energy
        }
        ordered = [aid for aid in self._order if aid in below]
        self._below_cache[feeder_id] = ordered
        return ordered

    def step(self, state: SharedState, cfg: Config) -> None:
        t = self.township
        assert t is not None
        resid = cfg.damage.residual_functionality

        base_f: dict[str, float] = {}
        for aid in self._energy_ids:
            base_f[aid] = resid[damage_index(state.damage[aid])]
            if base_f[aid] < 1.0:
                state.reason.setdefault(aid, "damaged")

        # 2. operator de-energisation for safety
        deenergised: set[str] = set()
        for fid in self._feeders:
            eids = self._feeder_edges[fid]
            if not eids:
                continue
            flooded = sum(
                1 for e in eids if state.road_depth.get(e, 0.0) > DEENERGISE_DEPTH_M
            )
            if flooded / len(eids) > cfg.energy.deenergise_flood_fraction:
                if base_f[fid] > 0.0:
                    state.reason[fid] = "deenergised"
                base_f[fid] = 0.0
                deenergised.add(fid)

        # 3. source connectivity, over the energy network only
        alive = {aid for aid in self._energy_ids if base_f[aid] > 0.0}
        energised = reachable(self._energy_adj, self._sources, alive)

        power: dict[str, float] = {}
        for aid in self._order:
            if aid not in self._energy_ids:
                continue
            if aid not in energised:
                power[aid] = 0.0
                if base_f[aid] > 0.0 and aid not in deenergised:
                    up = [
                        p
                        for p in self._powers_providers.get(aid, ())
                        if power.get(p, 0.0) <= 0.0
                    ]
                    state.reason[aid] = f"upstream:{up[0]}" if up else "upstream"
            else:
                provs = self._powers_providers.get(aid, ())
                upstream_p = (
                    min((power.get(p, 0.0) for p in provs), default=1.0)
                    if provs
                    else 1.0
                )
                power[aid] = base_f[aid] * upstream_p

        # 4. load transfer from a healthy tie feeder. This happens before the
        # supply is handed to anything outside the energy network, so that each
        # dependency is evaluated exactly once, against the final feeder power.
        # Evaluating a link twice in one step would drain its reserve twice.
        for fid in self._feeders:
            if power.get(fid, 0.0) > 0.0:
                continue
            load = self._feeder_load[fid]
            if load <= 0.0:
                continue
            best = 0.0
            for tie in self._ties[fid]:
                if power.get(tie, 0.0) <= 0.0:
                    continue
                spare = t.assets[tie].capacity * (
                    1.0 - cfg.energy.transfer_capacity_margin
                ) - self._feeder_load[tie]
                if spare <= 0.0:
                    continue
                best = max(best, min(load, spare) / load)
            if best > 0.0:
                power[fid] = best
                state.reason[fid] = "transfer_partial"
                # Push it down the energy network: the transformers below a
                # tied feeder are lit by it, and everything hanging off those
                # transformers is picked up by the pass that follows.
                for downstream in self._energy_order_below(fid):
                    provs = self._powers_providers.get(downstream, ())
                    upstream_p = min(
                        (power.get(p, 0.0) for p in provs), default=1.0
                    )
                    restored = base_f[downstream] * upstream_p
                    if restored > power.get(downstream, 0.0):
                        power[downstream] = restored
                        if state.reason.get(downstream, "").startswith("upstream"):
                            state.reason.pop(downstream, None)

        # 5. everything outside the energy network, seen through its own
        # dependency behaviour. One pass, one evaluation per link.
        for aid in self._order:
            if aid in self._energy_ids:
                continue
            links = self._powers_links.get(aid, ())
            if not links:
                power[aid] = 1.0
                continue
            # Every POWERS provider is required, and each one is seen through
            # its own threshold, delay and reserve.
            power[aid] = min(
                state.gate.availability(
                    ln, power.get(ln.source, 0.0), state.t, cfg.sim.dt_h
                )
                for ln in links
            )
            if power[aid] > 0.0 and any(
                state.gate.reserve_in_use(ln) for ln in links
            ):
                state.on_backup.add(aid)

        for aid in t.assets:
            state.power_available[aid] = power.get(aid, 1.0)
        for aid in self._energy_ids:
            state.functionality[aid] = power.get(aid, 0.0)

        for z in t.zones:
            tr = self._zone_transformer[z.id]
            state.zone_service[z.id][Service.ENERGY] = min(
                1.0, max(0.0, state.power_available.get(tr, 0.0))
            )
