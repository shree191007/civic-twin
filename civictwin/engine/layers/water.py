"""Water layer: power and SCADA gates, supply chain, tank and household storage."""
from __future__ import annotations

from civictwin.config import Config
from civictwin.engine.contract import SharedState
import logging

from civictwin.ontology import (
    AssetKind,
    LinkKind,
    Portfolio,
    Service,
    Township,
    damage_index,
)

logger = logging.getLogger(__name__)

POWERED_KINDS = {
    AssetKind.INTAKE,
    AssetKind.TREATMENT,
    AssetKind.PUMP,
    AssetKind.STORMWATER_PUMP,
}


class WaterLayer:
    """Fast water model: serial treatment chain feeding tanks feeding zones."""

    portfolio = Portfolio.WATER

    def __init__(self) -> None:
        self.township: Township | None = None

    def reset(self, township: Township, cfg: Config, rng: object = None) -> None:
        self.township = township
        self._water_ids = [
            a.id for a in township.assets.values() if a.portfolio is Portfolio.WATER
        ]
        self._tanks = [
            a.id for a in township.assets.values() if a.kind is AssetKind.TANK
        ]
        self._stormwater = [
            a.id
            for a in township.assets.values()
            if a.kind is AssetKind.STORMWATER_PUMP
        ]
        self._supply_providers: dict[str, list[str]] = {}
        for ln in township.links:
            if ln.kind is LinkKind.SUPPLIES_WATER:
                self._supply_providers.setdefault(ln.target, []).append(ln.source)
        self._zone_tank = {z.id: z.tank for z in township.zones}
        self._zone_storage = {z.id: z.water_storage_hours for z in township.zones}
        self._water_consumers = [
            a.id
            for a in township.assets.values()
            if a.portfolio is Portfolio.SERVICES
            and self._supply_providers.get(a.id)
        ]
        # Rebuilt here, not cached across resets: an intervention overlay can
        # add or remove a supply link, which changes the order.
        self._order_cache = self._topological_order()

    def step(self, state: SharedState, cfg: Config) -> None:
        t = self.township
        assert t is not None
        dt = cfg.sim.dt_h
        resid = cfg.damage.residual_functionality

        f: dict[str, float] = {}
        for aid in self._water_ids:
            f[aid] = resid[damage_index(state.damage[aid])]
            if f[aid] < 1.0:
                state.reason.setdefault(aid, "damaged")

        for aid in self._water_ids:
            asset = t.assets[aid]
            if asset.kind not in POWERED_KINDS:
                continue
            if state.power_available.get(aid, 0.0) > 0.0:
                continue
            if state.fuel_remaining_h.get(aid, 0.0) > 0.0:
                state.fuel_remaining_h[aid] -= dt
                state.on_backup.add(aid)
            else:
                f[aid] = 0.0
                state.reason[aid] = "power"

        for aid in self._water_ids:
            asset = t.assets[aid]
            if not asset.scada_controlled:
                continue
            if state.comms_available.get(aid, 1.0) > 0.0:
                continue
            if state.manual_until_h.get(aid, 0.0) > state.t:
                f[aid] = 0.0
                state.reason[aid] = "scada"

        # 4. serial supply chain: an asset can pass on no more than it receives
        eff: dict[str, float] = {}
        for aid in self._chain_order():
            provs = self._supply_providers.get(aid, ())
            upstream = min((eff.get(p, 0.0) for p in provs), default=1.0)
            eff[aid] = min(f.get(aid, 1.0), upstream)
            if eff[aid] <= 0.0 and f.get(aid, 1.0) > 0.0:
                state.reason.setdefault(aid, "water")

        # 5. tank dynamics
        outflow = state.demand_multiplier.get(Service.WATER, 1.0)
        for kid in self._tanks:
            capacity_h = t.assets[kid].capacity
            inflow = eff.get(kid, 0.0)
            level = state.tank_level_h.get(kid, capacity_h)
            level = min(capacity_h, max(0.0, level + (inflow - outflow) * dt))
            state.tank_level_h[kid] = level

        for aid in self._water_ids:
            state.functionality[aid] = eff.get(aid, 0.0)
            state.water_supply[aid] = eff.get(aid, 0.0)

        # 6. zone service with household storage countdown
        for z in t.zones:
            kid = self._zone_tank[z.id]
            level = state.tank_level_h.get(kid, 0.0)
            inflow = eff.get(kid, 0.0)
            supply = 1.0 if level > 0.0 else min(1.0, inflow)
            key = f"zone:{z.id}"
            if supply > 0.0:
                state.water_storage_left_h[key] = self._zone_storage[z.id]
                state.zone_service[z.id][Service.WATER] = supply
            else:
                left = state.water_storage_left_h.get(key, self._zone_storage[z.id])
                if left > 0.0:
                    state.water_storage_left_h[key] = left - dt
                    state.zone_service[z.id][Service.WATER] = 1.0
                else:
                    state.zone_service[z.id][Service.WATER] = 0.0

        # 7. hospitals and clinics draw from their own tanks, with own storage
        for aid in self._water_consumers:
            provs = self._supply_providers.get(aid, ())
            supply = 0.0
            for p in provs:
                if p in self._tanks:
                    supply = max(
                        supply,
                        1.0 if state.tank_level_h.get(p, 0.0) > 0.0 else eff.get(p, 0.0),
                    )
                else:
                    supply = max(supply, eff.get(p, 0.0))
            key = f"asset:{aid}"
            if supply > 0.0:
                state.water_storage_left_h[key] = t.assets[aid].backup_hours or 6.0
                state.water_supply[aid] = supply
            else:
                left = state.water_storage_left_h.get(key, 6.0)
                if left > 0.0:
                    state.water_storage_left_h[key] = left - dt
                    state.water_supply[aid] = 1.0
                else:
                    state.water_supply[aid] = 0.0

        # feed the stormwater drainage factor back to the hazard model
        for pid in self._stormwater:
            state.drain_factor[pid] = eff.get(pid, 0.0)

    def _topological_order(self) -> list[str]:
        """Water assets in supply order, taken from the links themselves.

        Ordering by asset kind assumes the chain always runs intake, treatment,
        pump, tank. A township that pumps into a tank that feeds another pump,
        or an overlay that reroutes a main, would be evaluated out of order and
        the downstream asset would read last step's supply.
        """
        water = set(self._water_ids)
        indegree = {aid: 0 for aid in self._water_ids}
        children: dict[str, list[str]] = {aid: [] for aid in self._water_ids}
        for aid in self._water_ids:
            for provider in self._supply_providers.get(aid, ()):
                if provider in water:
                    indegree[aid] += 1
                    children[provider].append(aid)
        queue = sorted(a for a, d in indegree.items() if d == 0)
        out: list[str] = []
        while queue:
            aid = queue.pop(0)
            out.append(aid)
            for child in sorted(children[aid]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(out) < len(self._water_ids):
            # A cycle in the water network: keep the remainder in a stable
            # order so the step is still deterministic, and say so.
            seen = set(out)
            remaining = sorted(a for a in self._water_ids if a not in seen)
            logger.warning(
                "cycle in the water supply graph; %d assets ordered by id: %s",
                len(remaining),
                ", ".join(remaining),
            )
            out.extend(remaining)
        return out

    def _chain_order(self) -> list[str]:
        """Water assets in supply order."""
        return self._order_cache
