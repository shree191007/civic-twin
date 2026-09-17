"""Demand multipliers and simple flood evacuation."""
from __future__ import annotations

import math

from gotham.config import Config
from gotham.engine.contract import SharedState
from gotham.ontology import AssetKind, Service, Township

SURGE_DECAY_H = 18.0
HEALTH_SURGE_FACTOR = 0.8
DIURNAL_MEAN = 0.975
DIURNAL_AMPLITUDE = 0.275
EVAC_DEPTH_M = 0.5
EVAC_MAX_FRACTION = 0.6
#: Depth at which evacuation reaches its ceiling. Without it the code compared
#: a depth in metres directly against a fraction, so 0.6 m read as 60% of the
#: zone and anything deeper pinned to the cap regardless of how much deeper.
EVAC_FULL_DEPTH_M = 2.0


def surge(t: float, cfg: Config) -> float:
    """Post-peak demand surge, decaying from 1.0 after the hydrograph peak."""
    peak_t = cfg.hazard.rise_hours
    return math.exp(-max(0.0, t - peak_t) / SURGE_DECAY_H)


def diurnal(t: float, onset_hour: int) -> float:
    """Energy demand shape over the day, in [0.7, 1.25]."""
    hour = (onset_hour + t) % 24.0
    return DIURNAL_MEAN + DIURNAL_AMPLITUDE * math.sin(
        2.0 * math.pi * (hour - 9.0) / 24.0
    )


class DemandModel:
    """Updates demand multipliers and moves people out of flooded zones."""

    def __init__(self, township: Township, onset_hour: int) -> None:
        self.township = township
        self.onset_hour = onset_hour
        self._shelters = [
            a for a in township.assets.values() if a.kind is AssetKind.SHELTER
        ]

    def step(self, state: SharedState, cfg: Config) -> None:
        s = surge(state.t, cfg)
        state.demand_multiplier[Service.COMMS] = 1.0 + (
            cfg.comms.demand_surge_factor - 1.0
        ) * s
        state.demand_multiplier[Service.HEALTH] = 1.0 + HEALTH_SURGE_FACTOR * s
        state.demand_multiplier[Service.WATER] = 1.0
        state.demand_multiplier[Service.ENERGY] = diurnal(state.t, self.onset_hour)
        state.demand_multiplier[Service.MOBILITY] = 1.0

        for z in self.township.zones:
            depth = self._zone_depth(state, z.id)
            if depth > EVAC_DEPTH_M:
                severity = (depth - EVAC_DEPTH_M) / (
                    EVAC_FULL_DEPTH_M - EVAC_DEPTH_M
                )
                frac = min(EVAC_MAX_FRACTION, EVAC_MAX_FRACTION * severity)
                state.evacuated[z.id] = int(z.population * frac)
            else:
                state.evacuated.setdefault(z.id, 0)

    def _zone_depth(self, state: SharedState, zone_id: str) -> float:
        """Flood depth at a zone, proxied by its transformer's depth."""
        return state.flood_depth.get(f"TR{zone_id[1:]}", 0.0)
