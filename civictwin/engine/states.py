"""Classifying an asset's operating state, the way an operator would report it."""
from __future__ import annotations

from civictwin.ontology import OperatingState

#: Below this an asset is barely delivering anything and is about to go.
CRITICAL_FUNCTIONALITY = 0.3
#: Below this it is no longer delivering full service.
DEGRADED_FUNCTIONALITY = 0.95
#: A reserve this thin means the asset is one step from falling over.
CRITICAL_RESERVE_H = 2.0


def classify(
    functionality: float,
    on_backup: bool = False,
    reserve_left_h: float = 0.0,
) -> OperatingState:
    """Map an asset's functionality and reserves onto an operating state.

    Args:
        functionality: fraction of normal service it is delivering.
        on_backup: whether it is running on a battery, generator or stored water
            rather than its normal supply.
        reserve_left_h: hours of that reserve remaining.
    """
    if functionality <= 0.0:
        return OperatingState.FAILED
    if on_backup and reserve_left_h <= CRITICAL_RESERVE_H:
        return OperatingState.CRITICAL
    if functionality < CRITICAL_FUNCTIONALITY:
        return OperatingState.CRITICAL
    if on_backup:
        return OperatingState.BACKUP
    if functionality < DEGRADED_FUNCTIONALITY:
        return OperatingState.DEGRADED
    return OperatingState.OPERATIONAL
