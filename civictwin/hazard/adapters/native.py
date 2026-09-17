"""Adapter for payloads already written in civic-twin's own hazard schema."""
from __future__ import annotations

from typing import Any

from civictwin.hazard.schemas.forecast import HazardForecast
from civictwin.provenance import Provenance


class NativeAdapter:
    """Passes through a payload that already speaks the internal schema."""

    name = "native"

    def supports(self, payload: dict[str, Any]) -> bool:
        return "severity_distribution" in payload and "hazard_type" in payload

    def parse(self, payload: dict[str, Any]) -> HazardForecast:
        forecast = HazardForecast.from_dict(payload)
        if forecast.source == "unknown":
            forecast = HazardForecast.from_dict(
                {**payload, "source": self.name, "provenance": Provenance.OBSERVED.value}
            )
        return forecast
