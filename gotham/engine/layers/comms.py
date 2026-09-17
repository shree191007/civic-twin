"""Comms layer: backhaul connectivity, tower batteries, handover and congestion."""
from __future__ import annotations

from gotham.config import Config
from gotham.engine.contract import SharedState
from gotham.engine.layers._graph import reachable
from gotham.ontology import (
    AssetKind,
    LinkKind,
    Portfolio,
    Service,
    Township,
    damage_index,
)

LOAD_FACTOR_MIN = 0.2
LOAD_FACTOR_MAX = 2.0
PEOPLE_PER_CAPACITY_UNIT = 1000.0


class CommsLayer:
    """Fast comms model: backhaul reachability plus per-tower congestion."""

    portfolio = Portfolio.COMMS

    def __init__(self) -> None:
        self.township: Township | None = None

    def reset(self, township: Township, cfg: Config, rng: object = None) -> None:
        self.township = township
        self._comms_ids = [
            a.id for a in township.assets.values() if a.portfolio is Portfolio.COMMS
        ]
        self._towers = [
            a.id for a in township.assets.values() if a.kind is AssetKind.TOWER
        ]
        self._exchanges = [
            a.id for a in township.assets.values() if a.kind is AssetKind.EXCHANGE
        ]
        self._backhaul_adj: dict[str, list[str]] = {}
        for ln in township.links:
            if ln.kind is LinkKind.BACKHAULS:
                self._backhaul_adj.setdefault(ln.source, []).append(ln.target)
        # Only an exchange with no backhaul of its own is a core site; every
        # other exchange has to be reachable from one, which is what makes a
        # severed inter-exchange route take its whole district off the air.
        self._core_exchanges = [
            x
            for x in self._exchanges
            if not township.providers(x, {LinkKind.BACKHAULS})
        ]
        self._controls: dict[str, list[str]] = {}
        for ln in township.links:
            if ln.kind is LinkKind.CONTROLS:
                self._controls.setdefault(ln.target, []).append(ln.source)
        self._tower_zones: dict[str, list[str]] = {t: [] for t in self._towers}
        for z in township.zones:
            for t in z.towers:
                if t in self._tower_zones:
                    self._tower_zones[t].append(z.id)
        self._zone_pop = {z.id: z.population for z in township.zones}
        self._zone_towers = {z.id: list(z.towers) for z in township.zones}

    def step(self, state: SharedState, cfg: Config) -> None:
        t = self.township
        assert t is not None
        dt = cfg.sim.dt_h
        resid = cfg.damage.residual_functionality

        f: dict[str, float] = {}
        for aid in self._comms_ids:
            f[aid] = resid[damage_index(state.damage[aid])]
            if f[aid] < 1.0:
                state.reason.setdefault(aid, "damaged")

        # exchanges need power
        for xid in self._exchanges:
            if state.power_available.get(xid, 0.0) <= 0.0:
                if state.fuel_remaining_h.get(xid, 0.0) > 0.0:
                    state.fuel_remaining_h[xid] -= dt
                    state.on_backup.add(xid)
                elif state.backup_remaining_h.get(xid, 0.0) > 0.0:
                    state.backup_remaining_h[xid] -= dt
                    state.on_backup.add(xid)
                else:
                    f[xid] = 0.0
                    state.reason[xid] = "power"

        # 2. backhaul: towers need a live path back to a live exchange
        alive = {aid for aid in self._comms_ids if f[aid] > 0.0}
        live_sources = [x for x in self._core_exchanges if f[x] > 0.0]
        connected = reachable(self._backhaul_adj, live_sources, alive)
        for xid in self._exchanges:
            if xid in self._core_exchanges:
                continue
            if f[xid] > 0.0 and xid not in connected:
                f[xid] = 0.0
                state.reason[xid] = "backhaul"
                alive.discard(xid)
        connected = reachable(self._backhaul_adj, live_sources, alive)
        for tid in self._towers:
            if f[tid] > 0.0 and tid not in connected:
                f[tid] = 0.0
                state.reason[tid] = "backhaul"

        # 4. offered load per tower. A zone's traffic is split evenly between
        # the towers still covering it, so a dead neighbour hands its share over.
        surge = state.demand_multiplier.get(Service.COMMS, 1.0)
        offered: dict[str, float] = {tid: 0.0 for tid in self._towers}
        for zid, towers in self._zone_towers.items():
            live = [tid for tid in towers if f.get(tid, 0.0) > 0.0]
            carriers = live or towers
            share = self._zone_pop[zid] * surge / (
                len(carriers) * PEOPLE_PER_CAPACITY_UNIT
            )
            for tid in carriers:
                offered[tid] += share

        # 3. power and battery
        for tid in self._towers:
            if f[tid] <= 0.0:
                continue
            if state.power_available.get(tid, 0.0) > 0.0:
                continue
            cap = max(1e-6, t.assets[tid].capacity)
            load_factor = min(LOAD_FACTOR_MAX, max(LOAD_FACTOR_MIN, offered[tid] / cap))
            drain = dt * (load_factor**cfg.comms.battery_load_exponent)
            state.backup_remaining_h[tid] = state.backup_remaining_h.get(tid, 0.0) - drain
            if state.backup_remaining_h[tid] <= 0.0:
                f[tid] = 0.0
                state.reason[tid] = "battery"
            else:
                state.on_backup.add(tid)

        # 4b. congestion with handover
        tower_service: dict[str, float] = {}
        for tid in self._towers:
            if f[tid] <= 0.0:
                tower_service[tid] = 0.0
                continue
            cap_eff = (
                t.assets[tid].capacity * cfg.comms.handover_capacity_factor * f[tid]
            )
            load = offered[tid]
            tower_service[tid] = 1.0 if load <= 0.0 else min(1.0, cap_eff / load)

        for aid in self._comms_ids:
            state.functionality[aid] = f[aid]

        for z in t.zones:
            live = [tid for tid in z.towers if f.get(tid, 0.0) > 0.0]
            state.zone_service[z.id][Service.COMMS] = (
                0.0 if not live else max(tower_service[tid] for tid in live)
            )

        # 6. SCADA availability follows the controlling exchange
        for aid in t.assets:
            ctrl = self._controls.get(aid)
            if ctrl:
                state.comms_available[aid] = (
                    1.0 if any(f.get(c, 0.0) > 0.0 for c in ctrl) else 0.0
                )
            else:
                near = [f[x] for x in self._exchanges]
                state.comms_available[aid] = 1.0 if any(v > 0.0 for v in near) else 0.0
