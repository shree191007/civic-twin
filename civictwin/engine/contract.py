"""Shared types for the simulation engine: scenarios, overlays, state, results."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Protocol

import numpy as np

from civictwin.config import Config
from civictwin.engine.dependency import DependencyGate
from civictwin.ontology import (
    DamageState,
    OperatingState,
    Portfolio,
    Service,
    Township,
)


@dataclass(frozen=True, slots=True)
class HazardScenario:
    """One storm event: a scenario year in the Monte Carlo."""

    id: str
    seed: int
    rain_mm: float
    field_seed: int
    onset_hour: int
    asset_draws: dict[str, float]
    return_period_y: float | None = None
    label: str | None = None

    def summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "seed": self.seed,
            "rain_mm": round(self.rain_mm, 2),
            "onset_hour": self.onset_hour,
            "return_period_y": (
                None if self.return_period_y is None else round(self.return_period_y, 1)
            ),
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class Overlay:
    """A scenario branch: everything that differs from the baseline township."""

    interventions: tuple[str, ...] = ()
    forced_failures: tuple[str, ...] = ()
    invulnerable: tuple[str, ...] = ()
    param_overrides: Mapping[str, float] = field(default_factory=dict)
    flood_enabled: bool = True

    def hash(self) -> str:
        payload = json.dumps(
            {
                "interventions": sorted(self.interventions),
                "forced_failures": sorted(self.forced_failures),
                "invulnerable": sorted(self.invulnerable),
                "param_overrides": dict(sorted(self.param_overrides.items())),
                "flood_enabled": self.flood_enabled,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


@dataclass(slots=True)
class SharedState:
    """Mutable world state, passed through every layer once per time step."""

    t: float
    flood_depth: dict[str, float]
    road_depth: dict[str, float]
    damage: dict[str, DamageState]
    functionality: dict[str, float]
    power_available: dict[str, float]
    comms_available: dict[str, float]
    water_supply: dict[str, float]
    road_open: dict[str, bool]
    travel_time_h: dict[tuple[int, int], float]
    zone_service: dict[str, dict[Service, float]]
    demand_multiplier: dict[Service, float]
    backup_remaining_h: dict[str, float]
    fuel_remaining_h: dict[str, float]
    tank_level_h: dict[str, float]
    manual_until_h: dict[str, float]
    reason: dict[str, str] = field(default_factory=dict)
    water_storage_left_h: dict[str, float] = field(default_factory=dict)
    evacuated: dict[str, int] = field(default_factory=dict)
    drain_factor: dict[str, float] = field(default_factory=dict)
    #: How each asset is running now, as distinct from how damaged it is.
    operating_state: dict[str, OperatingState] = field(default_factory=dict)
    #: Assets currently living off a battery, generator or stored water.
    on_backup: set[str] = field(default_factory=set)
    #: Hours of reserve left, for whichever reserve the asset is drawing on.
    reserve_left_h: dict[str, float] = field(default_factory=dict)
    #: Applies each dependency's threshold, delay and reserve.
    gate: DependencyGate = field(default_factory=DependencyGate)


class LayerModel(Protocol):
    """One infrastructure portfolio's per-step behaviour."""

    portfolio: Portfolio

    def reset(
        self, township: Township, cfg: Config, rng: np.random.Generator
    ) -> None: ...

    def step(self, state: SharedState, cfg: Config) -> None:
        """Read inputs from `state`, write only this layer's outputs."""


@dataclass(slots=True)
class TimelineFrame:
    """One recorded step, exactly the payload the frontend consumes."""

    t: float
    flood: dict[str, float]
    func: dict[str, float]
    damage: dict[str, str]
    reason: dict[str, str]
    closed_roads: list[str]
    crews: list[dict[str, object]]
    zones: dict[str, dict[str, float]]
    totals: dict[str, int]
    #: asset id -> operating state, for assets not simply OPERATIONAL
    state: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "t": self.t,
            "flood": self.flood,
            "func": self.func,
            "damage": self.damage,
            "state": self.state,
            "reason": self.reason,
            "closed_roads": self.closed_roads,
            "crews": self.crews,
            "zones": self.zones,
            "totals": self.totals,
        }


@dataclass(slots=True)
class SimResult:
    """The outcome of one 72-hour simulation."""

    scenario_id: str
    overlay_hash: str
    weighted_loss_ph: float
    loss_by_service_ph: dict[Service, float]
    loss_by_zone_ph: dict[str, float]
    vulnerable_loss_ph: float
    peak_functionality_loss: dict[Portfolio, float]
    recovery_90_h: dict[Portfolio, float]
    damaged_assets: dict[str, DamageState]
    amplification_ratio: float
    timeline: list[TimelineFrame] | None = None

    def to_dict(self, include_timeline: bool = False) -> dict[str, object]:
        out: dict[str, object] = {
            "scenario_id": self.scenario_id,
            "overlay_hash": self.overlay_hash,
            "weighted_loss_ph": round(self.weighted_loss_ph, 4),
            "loss_by_service_ph": {
                k.value: round(v, 4) for k, v in self.loss_by_service_ph.items()
            },
            "loss_by_zone_ph": {k: round(v, 4) for k, v in self.loss_by_zone_ph.items()},
            "vulnerable_loss_ph": round(self.vulnerable_loss_ph, 4),
            "peak_functionality_loss": {
                k.value: round(v, 4) for k, v in self.peak_functionality_loss.items()
            },
            "recovery_90_h": {k.value: v for k, v in self.recovery_90_h.items()},
            "damaged_assets": {k: v.value for k, v in self.damaged_assets.items()},
            "amplification_ratio": round(self.amplification_ratio, 4),
        }
        if include_timeline and self.timeline is not None:
            out["timeline"] = [f.to_dict() for f in self.timeline]
        return out
