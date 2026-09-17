"""Risk metrics: EAL, VaR, CVaR, risk contributions, and resilience curves."""
from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np

from civictwin.config import Config
from civictwin.engine.loss import service_weight
from civictwin.ontology import Portfolio, Service


def eal(losses: np.ndarray) -> float:
    """Expected annual loss: the mean across scenario years."""
    return float(np.mean(losses)) if len(losses) else 0.0


#: n * (1 - alpha) is rounded to this many decimals before the ceiling, so that
#: alpha=0.95 with n=100 gives 5 rather than 6 (1 - 0.95 is 0.050000000000000044).
TAIL_ROUNDING_DECIMALS = 9


def tail_size(n: int, alpha: float) -> int:
    """Number of scenarios in the tail: ceil(n * (1 - alpha)), at least one."""
    return max(1, math.ceil(round(n * (1.0 - alpha), TAIL_ROUNDING_DECIMALS)))


def cvar_indices(losses: np.ndarray, alpha: float) -> np.ndarray:
    """Indices of the `k` worst scenarios, sorted worst first."""
    k = tail_size(len(losses), alpha)
    order = np.argsort(losses, kind="stable")[::-1]
    return order[:k]


def tail_mask(losses: np.ndarray, alpha: float) -> np.ndarray:
    mask = np.zeros(len(losses), dtype=bool)
    mask[cvar_indices(losses, alpha)] = True
    return mask


def cvar(losses: np.ndarray, alpha: float) -> float:
    """Mean of the `k = ceil(n * (1 - alpha))` largest losses.

    This is the simple empirical conditional value at risk, not the
    interpolated Rockafellar-Uryasev form: it is exact enough here and makes
    the tests deterministic.
    """
    if not len(losses):
        return 0.0
    return float(np.mean(losses[cvar_indices(losses, alpha)]))


def var(losses: np.ndarray, alpha: float) -> float:
    """Empirical value at risk: the smallest loss inside the tail."""
    if not len(losses):
        return 0.0
    return float(np.min(losses[cvar_indices(losses, alpha)]))


def bootstrap_ci(
    losses: np.ndarray,
    stat_fn: Callable[[np.ndarray], float],
    n: int = 1000,
    seed: int = 0,
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for any statistic."""
    if not len(losses):
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    size = len(losses)
    stats = np.empty(n)
    for i in range(n):
        stats[i] = stat_fn(losses[rng.integers(0, size, size)])
    lo = (1.0 - level) / 2.0 * 100.0
    return (float(np.percentile(stats, lo)), float(np.percentile(stats, 100.0 - lo)))


def portfolio_contributions(table: object, alpha: float, cfg: Config) -> dict[Service, float]:
    """Each service's weighted mean loss inside the tail. Sums to CVaR."""
    weighted = getattr(table, "weighted")
    idx = cvar_indices(weighted, alpha)
    return {
        service: float(np.mean(values[idx])) * service_weight(service, cfg)
        for service, values in getattr(table, "by_service").items()
    }


def zone_contributions(table: object, alpha: float) -> dict[str, float]:
    """Each zone's mean weighted loss inside the tail."""
    weighted = getattr(table, "weighted")
    idx = cvar_indices(weighted, alpha)
    return {
        zone: float(np.mean(values[idx]))
        for zone, values in getattr(table, "by_zone").items()
    }


# ------------------------------------------------------- resilience curves


def _portfolio_mean_functionality(
    frame: object, asset_ids: Sequence[str]
) -> float:
    func = getattr(frame, "func")
    if not asset_ids:
        return 1.0
    return sum(func.get(a, 1.0) for a in asset_ids) / len(asset_ids)


def peak_functionality_loss(
    timeline: Sequence[object], asset_ids: Sequence[str]
) -> float:
    """Deepest dip in mean functionality across the event."""
    if not timeline:
        return 0.0
    return max(
        1.0 - _portfolio_mean_functionality(f, asset_ids) for f in timeline
    )


def recovery_time_h(
    timeline: Sequence[object], asset_ids: Sequence[str], threshold: float = 0.9
) -> float:
    """First time after which mean functionality stays at or above `threshold`."""
    recovered = math.inf
    for frame in timeline:
        if _portfolio_mean_functionality(frame, asset_ids) >= threshold:
            if math.isinf(recovered):
                recovered = float(getattr(frame, "t"))
        else:
            recovered = math.inf
    return recovered


def area_under_loss(
    timeline: Sequence[object], asset_ids: Sequence[str], dt: float | None = None
) -> float:
    """Integral of functionality loss over the event: the resilience drawdown.

    `dt` defaults to the spacing of the timeline itself rather than to one hour,
    so a run at a finer step does not report several times the drawdown.
    """
    frames = list(timeline)
    if not frames:
        return 0.0
    if dt is None:
        dt = (
            float(getattr(frames[1], "t")) - float(getattr(frames[0], "t"))
            if len(frames) > 1
            else 1.0
        )
    return sum(
        (1.0 - _portfolio_mean_functionality(f, asset_ids)) * dt for f in frames
    )


def portfolio_asset_ids(township: object) -> dict[Portfolio, list[str]]:
    return {
        p: [a.id for a in getattr(township, "assets").values() if a.portfolio is p]
        for p in Portfolio
    }
