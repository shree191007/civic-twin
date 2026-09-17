"""Service-loss accumulation in person-hours."""
from __future__ import annotations

from dataclasses import dataclass, field

from civictwin.config import Config
from civictwin.engine.contract import SharedState
from civictwin.ontology import Service, Township


#: Services consumed at home, which an evacuated household is not consuming.
DOMESTIC_SERVICES = frozenset({Service.ENERGY, Service.WATER})


def service_weight(service: Service, cfg: Config) -> float:
    return {
        Service.ENERGY: cfg.loss.energy,
        Service.WATER: cfg.loss.water,
        Service.COMMS: cfg.loss.comms,
        Service.HEALTH: cfg.loss.health,
        Service.MOBILITY: cfg.loss.mobility,
    }[service]


@dataclass(slots=True)
class LossAccumulator:
    """Running totals of unmet service, in person-hours."""

    by_service: dict[Service, float] = field(default_factory=dict)
    by_zone: dict[str, float] = field(default_factory=dict)
    vulnerable: float = 0.0

    def weighted_total(self, cfg: Config) -> float:
        return sum(
            service_weight(s, cfg) * v for s, v in self.by_service.items()
        )


_WEIGHT_CACHE: dict[int, tuple[tuple[Service, float], ...]] = {}


def _weights_for(cfg: Config) -> tuple[tuple[Service, float], ...]:
    key = id(cfg.loss)
    cached = _WEIGHT_CACHE.get(key)
    if cached is None:
        cached = tuple((s, service_weight(s, cfg)) for s in Service)
        _WEIGHT_CACHE[key] = cached
    return cached


def make_accumulator(township: Township) -> LossAccumulator:
    return LossAccumulator(
        by_service={s: 0.0 for s in Service},
        by_zone={z.id: 0.0 for z in township.zones},
        vulnerable=0.0,
    )


def accumulate(
    state: SharedState,
    dt: float,
    acc: LossAccumulator,
    township: Township,
    cfg: Config,
) -> None:
    """Add this step's unmet service to the running totals."""
    weights = _weights_for(cfg)
    by_service = acc.by_service
    by_zone = acc.by_zone
    vulnerable = 0.0
    for z in township.zones:
        services = state.zone_service[z.id]
        # People who have left do not go without piped water at home. Health
        # and mobility still count for them -- they are somewhere, needing a
        # hospital they can reach -- so only the domestic utilities are netted
        # off, and never below zero.
        evacuated = min(state.evacuated.get(z.id, 0), z.population)
        at_home = float(max(0, z.population - evacuated)) * dt
        pop = float(z.population) * dt
        zid = z.id
        vf = z.vulnerable_fraction
        zone_acc = 0.0
        for service, w in weights:
            level = services[service]
            if level >= 1.0:
                continue
            exposed = at_home if service in DOMESTIC_SERVICES else pop
            if exposed <= 0.0:
                continue
            unmet = exposed * (1.0 - level) if level > 0.0 else exposed
            by_service[service] += unmet
            zone_acc += unmet * w
        if zone_acc:
            by_zone[zid] += zone_acc
            vulnerable += zone_acc * vf
    acc.vulnerable += vulnerable
