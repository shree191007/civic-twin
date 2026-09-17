"""Adapter for providers that publish an ensemble as quantiles."""
from __future__ import annotations

from typing import Any

from gotham.hazard.normalization.normalizer import DEFAULT_UNCERTAINTY
from gotham.hazard.schemas.forecast import (
    HazardForecast,
    HazardType,
    SpatialFootprint,
    bands_from_quantiles,
)
from gotham.provenance import Provenance

#: Keys a provider might use for the quantile map, in order of preference.
QUANTILE_KEYS = ("quantiles", "percentiles", "ensemble_quantiles")


class EnsembleQuantileAdapter:
    """Reads `{"quantiles": {"0.1": 60, "0.5": 110, "0.9": 220}}` style payloads.

    This is the shape most operational rainfall and flood ensembles publish in.
    The quantiles become contiguous severity bands, so nothing is invented: the
    spread in the output is the spread the provider reported.
    """

    name = "ensemble_quantiles"

    def supports(self, payload: dict[str, Any]) -> bool:
        return any(key in payload for key in QUANTILE_KEYS)

    def parse(self, payload: dict[str, Any]) -> HazardForecast:
        raw: dict[str, Any] = {}
        for key in QUANTILE_KEYS:
            if key in payload:
                raw = payload[key]
                break
        if not raw:
            raise ValueError("no quantile map in payload")
        pairs = sorted((float(p), float(v)) for p, v in raw.items())
        if not pairs:
            raise ValueError("empty quantile map")
        unit = str(payload.get("unit", "mm"))
        hazard_type = HazardType(str(payload.get("hazard_type", HazardType.RIVER_FLOOD.value)))
        return HazardForecast(
            id=str(payload.get("id", f"{self.name}-forecast")),
            hazard_type=hazard_type,
            probability=float(payload.get("probability", 1.0)),
            start_time_h=float(payload.get("start_time_h", 0.0)),
            duration_h=float(payload.get("duration_h", 24.0)),
            severity_distribution=bands_from_quantiles(pairs, unit),
            spatial_footprint=SpatialFootprint.from_dict(
                payload.get("spatial_footprint", {})
            ),
            uncertainty=DEFAULT_UNCERTAINTY,
            source=str(payload.get("source", self.name)),
            provenance=Provenance.INFERRED,
            issued_at=payload.get("issued_at"),
            notes=payload.get("notes"),
        )
