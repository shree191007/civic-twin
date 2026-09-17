"""Reporting numbers with the precision they actually have.

An answer like `CVaR = ₹42,731,829.42` claims a precision the inputs cannot
support: the hazard is a forecast, the fragilities are assumed, the restoration
times are estimates. Every headline figure in this system is therefore an
`Estimate` — a range, a confidence level, and the named reasons it is uncertain
— rather than a point.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

import numpy as np

from civictwin.hazard.schemas.forecast import UncertaintySource


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: Relative half-width of the range, as a fraction of the central estimate.
#: Narrower than this and the answer is firm; wider and it is indicative.
HIGH_CONFIDENCE_SPREAD = 0.15
MEDIUM_CONFIDENCE_SPREAD = 0.40
#: The interval reported around every estimate.
DEFAULT_INTERVAL = 0.90


def confidence_for(low: float, central: float, high: float) -> Confidence:
    """Classify how firm an estimate is from the width of its own range."""
    if central <= 0.0:
        return Confidence.LOW if high > 0.0 else Confidence.HIGH
    spread = (high - low) / (2.0 * central)
    if spread <= HIGH_CONFIDENCE_SPREAD:
        return Confidence.HIGH
    if spread <= MEDIUM_CONFIDENCE_SPREAD:
        return Confidence.MEDIUM
    return Confidence.LOW


@dataclass(frozen=True, slots=True)
class Estimate:
    """A quantity with a range, a confidence, and its reasons for being uncertain."""

    low: float
    central: float
    high: float
    confidence: Confidence
    unit: str = "person-hours"
    drivers: tuple[UncertaintySource, ...] = ()
    interval: float = DEFAULT_INTERVAL
    n_samples: int = 0

    @property
    def relative_spread(self) -> float:
        if self.central <= 0.0:
            return 0.0
        return (self.high - self.low) / (2.0 * self.central)

    def to_dict(self) -> dict[str, Any]:
        return {
            "low": round(self.low, 2),
            "central": round(self.central, 2),
            "high": round(self.high, 2),
            "confidence": self.confidence.value,
            "unit": self.unit,
            "drivers": [d.value for d in self.drivers],
            "interval": self.interval,
            "n_samples": self.n_samples,
        }


def from_samples(
    values: Sequence[float] | np.ndarray,
    unit: str = "person-hours",
    drivers: Sequence[UncertaintySource] = (),
    interval: float = DEFAULT_INTERVAL,
    central: float | None = None,
) -> Estimate:
    """Build an estimate from a sample of simulated outcomes.

    Args:
        values: the sampled quantity, one value per scenario or member.
        unit: what the number counts.
        drivers: the uncertainty sources behind the spread, most important first.
        interval: the central interval to report, e.g. 0.9 for the 5th-95th.
        central: override the central value (use the statistic being reported,
            such as CVaR, rather than the mean of the sample).
    """
    array = np.asarray(list(values), dtype=float)
    if array.size == 0:
        return Estimate(0.0, 0.0, 0.0, Confidence.LOW, unit, tuple(drivers), interval, 0)
    tail = (1.0 - interval) / 2.0 * 100.0
    low = float(np.percentile(array, tail))
    high = float(np.percentile(array, 100.0 - tail))
    mid = float(np.mean(array)) if central is None else float(central)
    return Estimate(
        low=low,
        central=mid,
        high=high,
        confidence=confidence_for(low, mid, high),
        unit=unit,
        drivers=tuple(drivers),
        interval=interval,
        n_samples=int(array.size),
    )


def from_interval(
    low: float,
    central: float,
    high: float,
    unit: str = "person-hours",
    drivers: Sequence[UncertaintySource] = (),
    interval: float = DEFAULT_INTERVAL,
    n_samples: int = 0,
) -> Estimate:
    """Build an estimate from an interval that has already been computed."""
    return Estimate(
        low=low,
        central=central,
        high=high,
        confidence=confidence_for(low, central, high),
        unit=unit,
        drivers=tuple(drivers),
        interval=interval,
        n_samples=n_samples,
    )


def round_people(estimate: Estimate) -> tuple[int, int]:
    """A people range rounded to something a human would say out loud.

    "85,000-120,000", not "84,997-119,844". Rounding is outward, so the range
    never claims to be tighter than it is.
    """
    import math

    def step_for(value: float) -> int:
        if value >= 100_000:
            return 5_000
        if value >= 10_000:
            return 1_000
        if value >= 1_000:
            return 100
        return 10

    step = step_for(max(estimate.high, 1.0))
    return (
        int(math.floor(estimate.low / step) * step),
        int(math.ceil(estimate.high / step) * step),
    )
