"""Services layer: hospitals and clinics, and each zone's access to health care."""
from __future__ import annotations

from civictwin.config import Config
from civictwin.engine.contract import SharedState
from civictwin.engine.layers.transport import TransportLayer
from civictwin.ontology import (
    AssetKind,
    Portfolio,
    Service,
    Township,
    damage_index,
)

CLINIC_WEIGHT = 0.4
COMMS_DEGRADED = 0.8
STAFF_ISOLATED = 0.6


class ServicesLayer:
    """Health-care capability and each zone's ability to reach it."""

    portfolio = Portfolio.SERVICES

    def __init__(self, transport: TransportLayer) -> None:
        self.township: Township | None = None
        self.transport = transport

    def reset(self, township: Township, cfg: Config, rng: object = None) -> None:
        self.township = township
        self._service_ids = [
            a.id for a in township.assets.values() if a.portfolio is Portfolio.SERVICES
        ]
        self._hospitals = [
            a.id for a in township.assets.values() if a.kind is AssetKind.HOSPITAL
        ]
        self._clinics = [
            a.id for a in township.assets.values() if a.kind is AssetKind.CLINIC
        ]
        self._shelter_nodes = [
            a.node for a in township.assets.values() if a.kind is AssetKind.SHELTER
        ]
        # Where the people who staff a facility actually come from: the
        # residential zones, weighted by nothing more than being inhabited.
        self._staff_nodes = sorted({z.node for z in township.zones})
        self._zone_nodes = {z.id: z.node for z in township.zones}
        self._access_h = cfg.sim.health_access_minutes / 60.0

    def step(self, state: SharedState, cfg: Config) -> None:
        t = self.township
        assert t is not None
        dt = cfg.sim.dt_h
        resid = cfg.damage.residual_functionality

        for aid in self._service_ids:
            state.functionality[aid] = resid[damage_index(state.damage[aid])]

        care: dict[str, float] = {}
        for aid in self._hospitals + self._clinics:
            asset = t.assets[aid]
            physical = resid[damage_index(state.damage[aid])]
            if state.power_available.get(aid, 0.0) > 0.0:
                power = 1.0
            elif state.fuel_remaining_h.get(aid, 0.0) > 0.0:
                state.fuel_remaining_h[aid] -= dt
                state.on_backup.add(aid)
                power = 1.0
            elif state.backup_remaining_h.get(aid, 0.0) > 0.0:
                # A theatre UPS is not a generator, but it is not nothing.
                state.backup_remaining_h[aid] -= dt
                state.on_backup.add(aid)
                power = 1.0
            else:
                power = 0.0
                state.reason[aid] = "fuel" if asset.fuel_hours > 0 else "power"
            water = 1.0 if state.water_supply.get(aid, 0.0) > 0.0 else 0.0
            if water == 0.0 and power > 0.0:
                state.reason[aid] = "water"
            comms = 1.0 if state.comms_available.get(aid, 0.0) > 0.0 else COMMS_DEGRADED
            # Staff live in the town, not in the evacuation shelters. Sourcing
            # them from shelters made a hospital look staffed whenever a shelter
            # happened to sit on its side of the river.
            reachable_staff = any(
                self.transport.same_component(n, asset.node)
                for n in self._staff_nodes
            )
            staff = 1.0 if reachable_staff else STAFF_ISOLATED
            if not reachable_staff:
                state.reason.setdefault(aid, "access")
            care[aid] = physical * min(power, water) * comms * staff
            state.functionality[aid] = care[aid]

        for z in t.zones:
            znode = self._zone_nodes[z.id]
            best = 0.0
            for aid in self._hospitals:
                if care[aid] <= 0.0:
                    continue
                tt = self.transport.access_time(znode, t.assets[aid].node)
                if tt <= self._access_h:
                    best = max(best, care[aid])
            for aid in self._clinics:
                if care[aid] <= 0.0:
                    continue
                tt = self.transport.access_time(znode, t.assets[aid].node)
                if tt <= self._access_h:
                    best = max(best, care[aid] * CLINIC_WEIGHT)
            state.zone_service[z.id][Service.HEALTH] = best
