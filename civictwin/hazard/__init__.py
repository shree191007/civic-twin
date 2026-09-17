"""Hazard ingestion.

Civic-twin does not forecast weather. It starts one step later, at the
question: *given that this hazard is predicted or observed, what happens to the
infrastructure system?* External models supply the hazard; this package takes
whatever they produce, normalises it into one internal schema, and hands the
engine a set of scenarios that carry the forecast's uncertainty rather than
collapsing it to a single number.
"""
from __future__ import annotations

from civictwin.hazard.schemas.forecast import (
    HazardForecast,
    HazardType,
    SeverityBand,
    SpatialFootprint,
    UncertaintySource,
)
from civictwin.hazard.normalization.normalizer import normalize, to_scenarios

__all__ = [
    "HazardForecast",
    "HazardType",
    "SeverityBand",
    "SpatialFootprint",
    "UncertaintySource",
    "normalize",
    "to_scenarios",
]
