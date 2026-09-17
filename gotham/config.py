"""Every tunable constant in the system. No magic numbers live outside here."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LossWeights:
    energy: float = 1.0
    water: float = 1.0
    comms: float = 0.5
    health: float = 3.0
    mobility: float = 0.5


@dataclass(frozen=True, slots=True)
class HazardConfig:
    rain_threshold_mm: float = 60.0
    rain_scale_mm: float = 90.0
    field_bumps: int = 3
    field_sigma_frac: float = 0.35
    field_amplitude: float = 0.45
    recession_base_h: float = 12.0
    recession_per_w_h: float = 12.0
    rise_hours: float = 6.0
    gumbel_loc_mm: float = 85.0
    gumbel_scale_mm: float = 38.0
    pluvial_drain_capacity_mm_h: float = 18.0
    pluvial_pond_factor: float = 0.04


@dataclass(frozen=True, slots=True)
class RoadConfig:
    impassable_depth_m: float = 0.30
    speed_reduction_per_m: float = 2.5


@dataclass(frozen=True, slots=True)
class DamageConfig:
    state_multipliers: tuple[float, ...] = (0.6, 1.0, 1.5, 2.2)
    residual_functionality: tuple[float, ...] = (1.0, 0.9, 0.5, 0.1, 0.0)
    repair_multipliers: tuple[float, ...] = (0.0, 0.25, 1.0, 2.5, 5.0)


@dataclass(frozen=True, slots=True)
class EnergyConfig:
    deenergise_flood_fraction: float = 0.25
    transfer_capacity_margin: float = 0.15


@dataclass(frozen=True, slots=True)
class CommsConfig:
    handover_capacity_factor: float = 1.4
    demand_surge_factor: float = 2.5
    battery_load_exponent: float = 1.0


@dataclass(frozen=True, slots=True)
class ResponseConfig:
    site_dry_depth_m: float = 0.10
    crew_travel_speed_kph: float = 25.0
    fuel_truck_speed_kph: float = 20.0
    refuel_adds_hours: float = 48.0
    manual_operation_penalty_h: float = 4.0


@dataclass(frozen=True, slots=True)
class SimConfig:
    horizon_h: float = 72.0
    dt_h: float = 1.0
    health_access_minutes: float = 15.0
    mobility_access_minutes: float = 25.0


@dataclass(frozen=True, slots=True)
class RiskConfig:
    alpha: float = 0.95
    n_scenarios_train: int = 1000
    n_scenarios_test: int = 500
    seed_train: int = 1
    seed_test: int = 202


@dataclass(frozen=True, slots=True)
class Config:
    loss: LossWeights = LossWeights()
    hazard: HazardConfig = HazardConfig()
    road: RoadConfig = RoadConfig()
    damage: DamageConfig = DamageConfig()
    energy: EnergyConfig = EnergyConfig()
    comms: CommsConfig = CommsConfig()
    response: ResponseConfig = ResponseConfig()
    sim: SimConfig = SimConfig()
    risk: RiskConfig = RiskConfig()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Config:
        return _build(Config, d)


def _build(cls: type, d: dict[str, Any]) -> Any:
    """Recursively rebuild a nested frozen dataclass from plain JSON data."""
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in d:
            continue
        raw = d[f.name]
        if is_dataclass(f.type) if isinstance(f.type, type) else False:
            kwargs[f.name] = _build(f.type, raw)  # type: ignore[arg-type]
        elif isinstance(raw, dict):
            sub = _FIELD_TYPES[cls][f.name]
            kwargs[f.name] = _build(sub, raw)
        elif isinstance(raw, list):
            kwargs[f.name] = tuple(raw)
        else:
            kwargs[f.name] = raw
    return cls(**kwargs)


_FIELD_TYPES: dict[type, dict[str, type]] = {
    Config: {
        "loss": LossWeights,
        "hazard": HazardConfig,
        "road": RoadConfig,
        "damage": DamageConfig,
        "energy": EnergyConfig,
        "comms": CommsConfig,
        "response": ResponseConfig,
        "sim": SimConfig,
        "risk": RiskConfig,
    }
}

DEFAULT = Config()
