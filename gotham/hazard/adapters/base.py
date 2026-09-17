"""The adapter contract every hazard provider is wrapped in."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from gotham.hazard.schemas.forecast import HazardForecast


@runtime_checkable
class HazardAdapter(Protocol):
    """Wraps one external provider's output into the internal schema.

    An adapter never decides anything. It translates whatever the provider
    publishes into `HazardForecast` and records where it came from, so that a
    number in the dashboard can always be traced back to the model that
    produced it.
    """

    name: str

    def supports(self, payload: dict[str, Any]) -> bool:
        """Whether this adapter recognises the payload."""

    def parse(self, payload: dict[str, Any]) -> HazardForecast:
        """Translate the payload into a forecast. Raises ValueError if it cannot."""
