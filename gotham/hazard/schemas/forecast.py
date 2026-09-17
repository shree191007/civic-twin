"""The one internal hazard schema every external provider is normalised into."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from gotham.provenance import Provenance


class HazardType(str, Enum):
    """What kind of hazard the forecast describes."""

    RIVER_FLOOD = "river_flood"
    URBAN_FLOOD = "urban_flood"
    COMPOUND_FLOOD = "compound_flood"
    CYCLONE = "cyclone"
    WILDFIRE = "wildfire"


class UncertaintySource(str, Enum):
    """Where the spread in an answer comes from. Reported, never hidden."""

    HAZARD_INTENSITY = "hazard_intensity"
    ASSET_VULNERABILITY = "asset_vulnerability"
    RESTORATION_TIME = "restoration_time"
    DEPENDENCY_TOPOLOGY = "dependency_topology"
    OPERATOR_BEHAVIOUR = "operator_behaviour"
    POPULATION_EXPOSURE = "population_exposure"


@dataclass(frozen=True, slots=True)
class SeverityBand:
    """One band of a forecast's severity distribution.

    A forecast that says "1.3 m" is pretending to a precision it does not have.
    A forecast that says "50% chance of 0.4-0.8 m, 30% of 0.8-1.5 m, 20% above
    that" is saying what it actually knows.
    """

    probability: float
    low: float
    high: float
    unit: str = "mm"

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError(f"probability out of range: {self.probability}")
        if self.high < self.low:
            raise ValueError(f"band high {self.high} below low {self.low}")

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": self.probability,
            "low": self.low,
            "high": self.high,
            "unit": self.unit,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SeverityBand:
        return SeverityBand(
            probability=float(d["probability"]),
            low=float(d["low"]),
            high=float(d["high"]),
            unit=str(d.get("unit", "mm")),
        )


@dataclass(frozen=True, slots=True)
class SpatialFootprint:
    """Where the hazard falls.

    `centre_xy` and `radius_m` describe the weight of the event in projected
    metres; `polygon_lonlat` carries an observed extent when a provider has one,
    for validation against what actually flooded.
    """

    centre_xy: tuple[float, float] | None = None
    radius_m: float | None = None
    polygon_lonlat: tuple[tuple[float, float], ...] = ()
    covers_whole_extent: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "centre_xy": list(self.centre_xy) if self.centre_xy else None,
            "radius_m": self.radius_m,
            "polygon_lonlat": [list(p) for p in self.polygon_lonlat],
            "covers_whole_extent": self.covers_whole_extent,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SpatialFootprint:
        centre = d.get("centre_xy")
        return SpatialFootprint(
            centre_xy=(float(centre[0]), float(centre[1])) if centre else None,
            radius_m=None if d.get("radius_m") is None else float(d["radius_m"]),
            polygon_lonlat=tuple(
                (float(p[0]), float(p[1])) for p in d.get("polygon_lonlat", ())
            ),
            covers_whole_extent=bool(d.get("covers_whole_extent", True)),
        )


@dataclass(frozen=True, slots=True)
class HazardForecast:
    """A normalised hazard forecast, whatever model it came from."""

    id: str
    hazard_type: HazardType
    #: Probability the event happens at all, in [0, 1].
    probability: float
    #: Hours from now until it starts.
    start_time_h: float
    #: Hours it lasts.
    duration_h: float
    #: The severity distribution. Probabilities should sum to about 1.
    severity_distribution: tuple[SeverityBand, ...]
    spatial_footprint: SpatialFootprint = field(default_factory=SpatialFootprint)
    #: What the spread in this forecast is driven by, most important first.
    uncertainty: tuple[UncertaintySource, ...] = ()
    #: Which external model produced it.
    source: str = "unknown"
    provenance: Provenance = Provenance.INFERRED
    issued_at: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.severity_distribution:
            raise ValueError("a forecast needs at least one severity band")
        total = sum(b.probability for b in self.severity_distribution)
        if not math.isclose(total, 1.0, abs_tol=0.05):
            raise ValueError(f"severity probabilities sum to {total:.3f}, not 1")

    @property
    def expected_severity(self) -> float:
        return sum(b.probability * b.midpoint for b in self.severity_distribution)

    @property
    def severity_range(self) -> tuple[float, float]:
        return (
            min(b.low for b in self.severity_distribution),
            max(b.high for b in self.severity_distribution),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hazard_type": self.hazard_type.value,
            "probability": self.probability,
            "start_time_h": self.start_time_h,
            "duration_h": self.duration_h,
            "severity_distribution": [b.to_dict() for b in self.severity_distribution],
            "spatial_footprint": self.spatial_footprint.to_dict(),
            "uncertainty": [u.value for u in self.uncertainty],
            "source": self.source,
            "provenance": self.provenance.value,
            "issued_at": self.issued_at,
            "notes": self.notes,
            "expected_severity": round(self.expected_severity, 2),
            "severity_range": list(self.severity_range),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> HazardForecast:
        return HazardForecast(
            id=str(d["id"]),
            hazard_type=HazardType(str(d["hazard_type"])),
            probability=float(d["probability"]),
            start_time_h=float(d.get("start_time_h", 0.0)),
            duration_h=float(d.get("duration_h", 24.0)),
            severity_distribution=tuple(
                SeverityBand.from_dict(b) for b in d["severity_distribution"]
            ),
            spatial_footprint=SpatialFootprint.from_dict(
                d.get("spatial_footprint", {})
            ),
            uncertainty=tuple(
                UncertaintySource(u) for u in d.get("uncertainty", ())
            ),
            source=str(d.get("source", "unknown")),
            provenance=Provenance(str(d.get("provenance", "inferred"))),
            issued_at=d.get("issued_at"),
            notes=d.get("notes"),
        )


def bands_from_quantiles(
    quantiles: Sequence[tuple[float, float]], unit: str = "mm"
) -> tuple[SeverityBand, ...]:
    """Turn (cumulative probability, value) pairs into contiguous severity bands.

    Most providers publish an ensemble as quantiles — p10, p50, p90. This turns
    that into the band form the engine samples from, without inventing anything
    the provider did not say.
    """
    ordered = sorted(quantiles, key=lambda q: q[0])
    bands: list[SeverityBand] = []
    previous_p = 0.0
    previous_v = ordered[0][1]
    for cumulative, value in ordered:
        weight = cumulative - previous_p
        if weight > 0:
            bands.append(SeverityBand(weight, previous_v, value, unit))
        previous_p, previous_v = cumulative, value
    if previous_p < 1.0:
        span = ordered[-1][1] - ordered[0][1]
        bands.append(
            SeverityBand(1.0 - previous_p, previous_v, previous_v + max(span, 1.0) * 0.5, unit)
        )
    return tuple(bands)
