"""The hazard ingestion entry point: any provider in, one forecast out."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

from gotham.hazard.adapters.base import HazardAdapter
from gotham.hazard.adapters.deterministic import DeterministicAdapter
from gotham.hazard.adapters.ensemble_quantiles import EnsembleQuantileAdapter
from gotham.hazard.adapters.native import NativeAdapter
from gotham.hazard.normalization.normalizer import normalize
from gotham.hazard.schemas.forecast import HazardForecast

logger = logging.getLogger(__name__)

#: Tried in order. The deterministic adapter is last because it is the
#: fallback: it accepts a bare value, which several richer shapes also carry.
DEFAULT_ADAPTERS: tuple[HazardAdapter, ...] = (
    NativeAdapter(),
    EnsembleQuantileAdapter(),
    DeterministicAdapter(),
)


class UnsupportedForecast(ValueError):
    """No registered adapter recognised the payload."""


def ingest(
    payload: dict[str, Any], adapters: Sequence[HazardAdapter] | None = None
) -> HazardForecast:
    """Normalise one provider payload into the internal schema.

    Raises:
        UnsupportedForecast: if nothing recognises the payload. Deliberately
            loud: silently guessing at a hazard feed is how a model ends up
            confidently simulating the wrong storm.
    """
    for adapter in adapters or DEFAULT_ADAPTERS:
        if not adapter.supports(payload):
            continue
        forecast = normalize(adapter.parse(payload))
        logger.info(
            "ingested %s via %s: %s, expected severity %.1f mm",
            forecast.id,
            adapter.name,
            forecast.hazard_type.value,
            forecast.expected_severity,
        )
        return forecast
    raise UnsupportedForecast(
        f"no adapter recognised a payload with keys {sorted(payload)}"
    )


def ingest_file(
    path: Path, adapters: Sequence[HazardAdapter] | None = None
) -> list[HazardForecast]:
    """Read one or many forecasts from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    payloads = data if isinstance(data, list) else [data]
    return [ingest(p, adapters) for p in payloads]
