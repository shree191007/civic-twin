"""Forecast to impact: the path from a hazard forecast to a decision.

    FORECAST -> SIMULATE -> CASCADE -> QUANTIFY -> ACT

The output is deliberately a distribution, not a number. A forecast carries
uncertainty; collapsing it to a single impact figure would throw away the one
thing a planner most needs to know, which is how bad it could get.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from gotham.analysis.metrics import cvar, eal
from gotham.analysis.montecarlo import LossTable, ScenarioSet, run_set
from gotham.analysis.uncertainty import (
    Confidence,
    Estimate,
    from_samples,
    round_people,
)
from gotham.config import Config
from gotham.engine.contract import Overlay
from gotham.engine.coordinator import Engine
from gotham.hazard.normalization.normalizer import to_scenarios
from gotham.hazard.schemas.forecast import (
    HazardForecast,
    SeverityBand,
    UncertaintySource,
)
from gotham.ontology import Service

logger = logging.getLogger(__name__)

#: A service is "lost" for a person once it is below this for a whole hour.
SERVICE_LOST_BELOW = 0.5
#: The threshold the brief reports against: affected for more than this long.
SUSTAINED_HOURS = 12.0


@dataclass(slots=True)
class BandImpact:
    """What one severity band of the forecast implies."""

    probability: float
    severity_low: float
    severity_high: float
    scenarios: int
    person_hours: Estimate
    people_affected: Estimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": round(self.probability, 4),
            "severity_low_mm": round(self.severity_low, 1),
            "severity_high_mm": round(self.severity_high, 1),
            "scenarios": self.scenarios,
            "person_hours": self.person_hours.to_dict(),
            "people_affected": self.people_affected.to_dict(),
        }


@dataclass(slots=True)
class ForecastImpact:
    """The answer to 'given this forecast, what happens to the system?'"""

    forecast_id: str
    hazard_type: str
    source: str
    scenarios: int
    person_hours: Estimate
    people_affected: Estimate
    people_affected_range: tuple[int, int]
    tail_person_hours: Estimate
    confidence: Confidence
    primary_uncertainty: tuple[UncertaintySource, ...]
    bands: list[BandImpact] = field(default_factory=list)
    worst_zone: str | None = None
    most_likely_first_failure: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "forecast_id": self.forecast_id,
            "hazard_type": self.hazard_type,
            "source": self.source,
            "scenarios": self.scenarios,
            "person_hours": self.person_hours.to_dict(),
            "people_affected": self.people_affected.to_dict(),
            "people_affected_range": list(self.people_affected_range),
            "tail_person_hours": self.tail_person_hours.to_dict(),
            "confidence": self.confidence.value,
            "primary_uncertainty": [u.value for u in self.primary_uncertainty],
            "bands": [b.to_dict() for b in self.bands],
            "worst_zone": self.worst_zone,
            "most_likely_first_failure": list(self.most_likely_first_failure),
        }


def people_affected_in(result: Any, township: Any) -> float:
    """People who lose any essential service for at least an hour."""
    affected = 0.0
    for zone in township.zones:
        losses = result.loss_by_zone_ph.get(zone.id, 0.0)
        if losses > 0.0:
            affected += zone.population
    return affected


def sustained_people(
    engine: Engine, scenario: Any, overlay: Overlay, dt: float | None = None
) -> float:
    """People for whom a service stays lost longer than `SUSTAINED_HOURS`."""
    result = engine.simulate(scenario, overlay, record=True, _skip_amplification=True)
    township = engine.township
    # One frame is one step, which is not necessarily one hour.
    step = dt if dt is not None else engine.cfg.sim.dt_h
    hours: dict[str, float] = {z.id: 0.0 for z in township.zones}
    for frame in result.timeline or ():
        for zone_id, services in frame.zones.items():
            if min(
                services.get(s.value, 1.0)
                for s in (Service.ENERGY, Service.WATER, Service.HEALTH)
            ) < SERVICE_LOST_BELOW:
                hours[zone_id] += step
    return float(
        sum(
            township.zone(z).population
            for z, h in hours.items()
            if h >= SUSTAINED_HOURS
        )
    )


def impact_of(
    forecast: HazardForecast,
    engine: Engine,
    cfg: Config,
    n_scenarios: int = 200,
    seed: int = 11,
    overlay: Overlay = Overlay(),
) -> ForecastImpact:
    """Run a forecast through the engine and report the impact distribution.

    Args:
        forecast: a normalised hazard forecast.
        engine: the simulator, already bound to a township.
        cfg: configuration, for the risk level.
        n_scenarios: how many scenario years to draw across the bands.
        seed: makes the draw reproducible.
        overlay: an optional plan to evaluate the forecast against.
    """
    township = engine.township
    drivers = forecast.uncertainty or (
        UncertaintySource.HAZARD_INTENSITY,
        UncertaintySource.ASSET_VULNERABILITY,
        UncertaintySource.RESTORATION_TIME,
    )

    bands: list[BandImpact] = []
    all_losses: list[float] = []
    all_people: list[float] = []
    damaged_counts: dict[str, int] = {}
    zone_losses: dict[str, float] = {z.id: 0.0 for z in township.zones}

    from gotham.hazard.normalization.normalizer import band_allocation

    for band_index, (band, count) in enumerate(
        band_allocation(forecast.severity_distribution, n_scenarios)
    ):
        # The band is isolated at certainty: within this band, this is the
        # severity. Its share of the whole is carried by `count` instead.
        single = HazardForecast(
            id=f"{forecast.id}-b{int(band.low)}",
            hazard_type=forecast.hazard_type,
            probability=forecast.probability,
            start_time_h=forecast.start_time_h,
            duration_h=forecast.duration_h,
            severity_distribution=(SeverityBand(1.0, band.low, band.high, band.unit),),
            spatial_footprint=forecast.spatial_footprint,
            uncertainty=forecast.uncertainty,
            source=forecast.source,
            provenance=forecast.provenance,
        )
        # A distinct seed per band: sharing one would hand every band the same
        # fragility draws, so the bands would differ only by rainfall and the
        # spread between them would be understated.
        scenarios = to_scenarios(single, township, cfg, count, seed + band_index)
        results = engine.simulate_many(scenarios, overlay)
        losses = [r.weighted_loss_ph for r in results]
        people = [people_affected_in(r, township) for r in results]
        all_losses.extend(losses)
        all_people.extend(people)
        for r in results:
            for aid in r.damaged_assets:
                damaged_counts[aid] = damaged_counts.get(aid, 0) + 1
            for zone_id, value in r.loss_by_zone_ph.items():
                zone_losses[zone_id] = zone_losses.get(zone_id, 0.0) + value
        bands.append(
            BandImpact(
                probability=band.probability,
                severity_low=band.low,
                severity_high=band.high,
                scenarios=count,
                person_hours=from_samples(losses, "person-hours", drivers),
                people_affected=from_samples(people, "people", drivers),
            )
        )

    person_hours = from_samples(all_losses, "person-hours", drivers)
    people = from_samples(all_people, "people", drivers)
    tail = from_samples(
        all_losses,
        "person-hours",
        drivers,
        central=cvar(np.asarray(all_losses), cfg.risk.alpha),
    )
    first_failures = sorted(
        damaged_counts, key=lambda a: (-damaged_counts[a], a)
    )[:5]
    worst_zone = max(zone_losses, key=lambda z: zone_losses[z]) if zone_losses else None

    logger.info(
        "forecast %s: %s people affected (%s confidence), %.1f M person-hours",
        forecast.id,
        " to ".join(str(v) for v in round_people(people)),
        people.confidence.value,
        person_hours.central / 1e6,
    )
    return ForecastImpact(
        forecast_id=forecast.id,
        hazard_type=forecast.hazard_type.value,
        source=forecast.source,
        scenarios=len(all_losses),
        person_hours=person_hours,
        people_affected=people,
        people_affected_range=round_people(people),
        tail_person_hours=tail,
        confidence=people.confidence,
        primary_uncertainty=drivers,
        bands=bands,
        worst_zone=worst_zone,
        most_likely_first_failure=first_failures,
    )
