"""Adapter for providers that publish a single number.

A deterministic forecast is the hardest case to handle honestly: the provider
has given no spread, but the spread is real. Rather than pass the point value
through as though it were certain, this widens it into a band using a stated
default and records that the widening was an assumption of ours, not theirs.
"""
from __future__ import annotations

from typing import Any

from civictwin.hazard.normalization.normalizer import DEFAULT_UNCERTAINTY
from civictwin.hazard.schemas.forecast import (
    HazardForecast,
    HazardType,
    SeverityBand,
    SpatialFootprint,
)
from civictwin.provenance import Provenance

#: How far either side of a point forecast to spread the bands, as a fraction.
DEFAULT_SPREAD = 0.35
#: Weights for the low, central and high bands of a widened point forecast.
BAND_WEIGHTS = (0.25, 0.5, 0.25)
VALUE_KEYS = ("value", "rain_mm", "severity", "depth_m")


class DeterministicAdapter:
    """Widens a single published value into a three-band distribution."""

    name = "deterministic"

    def supports(self, payload: dict[str, Any]) -> bool:
        return any(key in payload for key in VALUE_KEYS)

    def parse(self, payload: dict[str, Any]) -> HazardForecast:
        value = next(
            float(payload[key]) for key in VALUE_KEYS if key in payload
        )
        unit = str(payload.get("unit", "m" if "depth_m" in payload else "mm"))
        spread = float(payload.get("spread", DEFAULT_SPREAD))
        low, high = value * (1 - spread), value * (1 + spread)
        mid_low, mid_high = value * (1 - spread / 3), value * (1 + spread / 3)
        return HazardForecast(
            id=str(payload.get("id", f"{self.name}-forecast")),
            hazard_type=HazardType(
                str(payload.get("hazard_type", HazardType.RIVER_FLOOD.value))
            ),
            probability=float(payload.get("probability", 1.0)),
            start_time_h=float(payload.get("start_time_h", 0.0)),
            duration_h=float(payload.get("duration_h", 24.0)),
            severity_distribution=(
                SeverityBand(BAND_WEIGHTS[0], low, mid_low, unit),
                SeverityBand(BAND_WEIGHTS[1], mid_low, mid_high, unit),
                SeverityBand(BAND_WEIGHTS[2], mid_high, high, unit),
            ),
            spatial_footprint=SpatialFootprint.from_dict(
                payload.get("spatial_footprint", {})
            ),
            uncertainty=DEFAULT_UNCERTAINTY,
            source=str(payload.get("source", self.name)),
            provenance=Provenance.INFERRED,
            issued_at=payload.get("issued_at"),
            notes=(
                "The provider published a single value with no spread. The "
                f"±{spread:.0%} band around it is our assumption, not theirs."
            ),
        )
