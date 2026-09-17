"""Turning a normalised forecast into the scenarios the engine simulates.

The point of this module is that the forecast's uncertainty survives the
journey. A forecast that says "50% chance of 0.4-0.8 m" becomes a set of
scenario draws in that proportion, so the impact distribution the engine
produces reflects what the forecaster actually claimed rather than a single
representative number chosen on their behalf.
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

from gotham.config import Config
from gotham.engine.contract import HazardScenario
from gotham.hazard.schemas.forecast import (
    HazardForecast,
    HazardType,
    SeverityBand,
    UncertaintySource,
)
from gotham.engine.hazard import _derived_uniform, gumbel_cdf
from gotham.ontology import Township

logger = logging.getLogger(__name__)

#: Rain in millimetres is the engine's native severity unit.
NATIVE_UNIT = "mm"
#: Rough conversion for providers that publish depth rather than rainfall.
MM_PER_METRE_OF_DEPTH = 90.0


def normalize(forecast: HazardForecast) -> HazardForecast:
    """Convert a forecast's severity bands into the engine's native units.

    Providers publish depth in metres, rainfall in millimetres, or return
    periods. The engine speaks millimetres of rainfall, so everything is
    converted once, here, rather than in each adapter.
    """
    if all(b.unit == NATIVE_UNIT for b in forecast.severity_distribution):
        return forecast
    converted: list[SeverityBand] = []
    for band in forecast.severity_distribution:
        scale = MM_PER_METRE_OF_DEPTH if band.unit in ("m", "metre", "meters") else 1.0
        converted.append(
            SeverityBand(band.probability, band.low * scale, band.high * scale, NATIVE_UNIT)
        )
    logger.info(
        "normalised %s severity from %s to %s",
        forecast.id,
        forecast.severity_distribution[0].unit,
        NATIVE_UNIT,
    )
    return HazardForecast(
        id=forecast.id,
        hazard_type=forecast.hazard_type,
        probability=forecast.probability,
        start_time_h=forecast.start_time_h,
        duration_h=forecast.duration_h,
        severity_distribution=tuple(converted),
        spatial_footprint=forecast.spatial_footprint,
        uncertainty=forecast.uncertainty,
        source=forecast.source,
        provenance=forecast.provenance,
        issued_at=forecast.issued_at,
        notes=forecast.notes,
    )


def band_allocation(
    bands: Sequence[SeverityBand], n: int
) -> list[tuple[SeverityBand, int]]:
    """How many scenarios each band gets, respecting its probability.

    Largest-remainder allocation, so the counts always sum to `n` and no band
    with any probability at all is silently dropped.
    """
    total = sum(b.probability for b in bands)
    exact = [(b, n * b.probability / total) for b in bands]
    counts = [(b, int(value)) for b, value in exact]
    shortfall = n - sum(c for _b, c in counts)
    remainders = sorted(
        range(len(exact)), key=lambda i: (-(exact[i][1] % 1.0), i)
    )
    for i in remainders[:shortfall]:
        counts[i] = (counts[i][0], counts[i][1] + 1)
    return [(b, c) for b, c in counts if c > 0]


def to_scenarios(
    forecast: HazardForecast,
    township: Township,
    cfg: Config,
    n: int,
    seed: int,
) -> list[HazardScenario]:
    """Sample `n` scenarios from a forecast, in the proportions it states.

    Args:
        forecast: a normalised forecast.
        township: supplies the asset ids that need per-scenario draws.
        cfg: for the return-period calculation.
        n: how many scenario years to draw.
        seed: makes the draw reproducible.
    """
    forecast = normalize(forecast)
    rng = np.random.default_rng(seed)
    asset_ids = sorted(township.assets)
    out: list[HazardScenario] = []
    index = 0
    for band, count in band_allocation(forecast.severity_distribution, n):
        for _ in range(count):
            rain = float(rng.uniform(band.low, band.high))
            cdf = gumbel_cdf(
                rain, cfg.hazard.gumbel_loc_mm, cfg.hazard.gumbel_scale_mm
            )
            out.append(
                HazardScenario(
                    id=f"{forecast.id}-{index:05d}",
                    seed=seed * 1_000_003 + index,
                    rain_mm=rain,
                    field_seed=int(rng.integers(0, 2**31)),
                    onset_hour=int(forecast.start_time_h) % 24,
                    asset_draws={
                        aid: _derived_uniform(seed, f"{forecast.id}:{index}:{aid}")
                        for aid in asset_ids
                    },
                    return_period_y=(
                        None if cdf >= 1.0 else 1.0 / max(1e-12, 1.0 - cdf)
                    ),
                    label=f"{forecast.hazard_type.value} {band.low:.0f}-{band.high:.0f} mm",
                )
            )
            index += 1
    return out


#: The scenario families of spec-patch section 16, and what each one varies.
SCENARIO_FAMILIES: dict[HazardType, tuple[str, ...]] = {
    HazardType.RIVER_FLOOD: ("intensity", "duration", "spatial_extent"),
    HazardType.URBAN_FLOOD: ("rainfall_intensity", "drainage_capacity", "accumulation"),
    HazardType.COMPOUND_FLOOD: (
        "rainfall_intensity",
        "river_level",
        "concurrent_power_failure",
    ),
}

DEFAULT_UNCERTAINTY: tuple[UncertaintySource, ...] = (
    UncertaintySource.HAZARD_INTENSITY,
    UncertaintySource.ASSET_VULNERABILITY,
    UncertaintySource.RESTORATION_TIME,
)
