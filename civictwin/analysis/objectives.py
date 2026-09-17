"""What the optimiser is being asked to minimise.

Optimising economic loss alone answers one question well and several others
badly. A council protecting life will buy different things from one keeping the
lights on for business, and the honest response is to let them say which, not
to pick on their behalf and call it optimal.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from civictwin.config import Config, LossWeights
from civictwin.engine.loss import service_weight
from civictwin.ontology import Service

#: Scenarios that never recover inside the horizon are counted at this many
#: hours, so an unrecovered scenario is worse than a slow one rather than
#: infinite and unusable in an average.
UNRECOVERED_H = 96.0
#: How heavily "restore fast" weighs duration against the loss itself.
RECOVERY_WEIGHT_PH_PER_HOUR = 20_000.0


class ObjectiveMode(str, Enum):
    PROTECT_LIFE = "protect_life"
    MINIMISE_ECONOMIC_LOSS = "minimise_economic_loss"
    RESTORE_FAST = "restore_fast"
    BALANCED = "balanced"


@dataclass(frozen=True, slots=True)
class Objective:
    """One way of scoring an outcome, and the words to explain it."""

    mode: ObjectiveMode
    label: str
    description: str
    weights: LossWeights
    vulnerable_multiplier: float = 0.0
    recovery_weight: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "label": self.label,
            "description": self.description,
            "weights": {
                "energy": self.weights.energy,
                "water": self.weights.water,
                "comms": self.weights.comms,
                "health": self.weights.health,
                "mobility": self.weights.mobility,
            },
            "vulnerable_multiplier": self.vulnerable_multiplier,
            "recovery_weight": self.recovery_weight,
        }


OBJECTIVES: dict[ObjectiveMode, Objective] = {
    ObjectiveMode.PROTECT_LIFE: Objective(
        mode=ObjectiveMode.PROTECT_LIFE,
        label="Protect human life",
        description=(
            "Weighs access to hospitals and drinking water far above everything "
            "else, and counts loss to vulnerable residents twice."
        ),
        weights=LossWeights(energy=1.0, water=3.0, comms=0.5, health=8.0, mobility=1.0),
        vulnerable_multiplier=2.0,
    ),
    ObjectiveMode.MINIMISE_ECONOMIC_LOSS: Objective(
        mode=ObjectiveMode.MINIMISE_ECONOMIC_LOSS,
        label="Minimise economic loss",
        description=(
            "Weighs the services that stop work and trade -- power, "
            "communications and the road network."
        ),
        weights=LossWeights(energy=3.0, water=1.0, comms=2.0, health=1.0, mobility=2.0),
    ),
    ObjectiveMode.RESTORE_FAST: Objective(
        mode=ObjectiveMode.RESTORE_FAST,
        label="Restore services fast",
        description=(
            "Counts how long the town stays down, not only how far it falls, so "
            "measures that shorten recovery are preferred."
        ),
        weights=LossWeights(),
        recovery_weight=RECOVERY_WEIGHT_PH_PER_HOUR,
    ),
    ObjectiveMode.BALANCED: Objective(
        mode=ObjectiveMode.BALANCED,
        label="Balanced strategy",
        description=(
            "The default weighting: human services count for more than "
            "convenience, without overwhelming everything else."
        ),
        weights=LossWeights(),
    ),
}

DEFAULT_OBJECTIVE = OBJECTIVES[ObjectiveMode.BALANCED]


def objective_values(table: Any, objective: Objective, cfg: Config) -> np.ndarray:
    """Score every scenario in a loss table under one objective.

    Returns the per-scenario quantity the optimiser minimises, in person-hours
    so that the number stays interpretable whichever objective is chosen.
    """
    n = len(table.scenario_ids)
    total = np.zeros(n)
    for service, values in table.by_service.items():
        total = total + _weight(objective, service) * np.asarray(values)
    if objective.vulnerable_multiplier:
        total = total + objective.vulnerable_multiplier * np.asarray(table.vulnerable)
    if objective.recovery_weight and getattr(table, "recovery_h", None) is not None:
        recovery = np.asarray(table.recovery_h, dtype=float)
        recovery = np.where(np.isfinite(recovery), recovery, UNRECOVERED_H)
        total = total + objective.recovery_weight * recovery
    del cfg
    return total


def _weight(objective: Objective, service: Service) -> float:
    weights = objective.weights
    return {
        Service.ENERGY: weights.energy,
        Service.WATER: weights.water,
        Service.COMMS: weights.comms,
        Service.HEALTH: weights.health,
        Service.MOBILITY: weights.mobility,
    }[service]


def resolve(mode: str | ObjectiveMode | None) -> Objective:
    """Look up an objective by name, falling back to the balanced default."""
    if mode is None:
        return DEFAULT_OBJECTIVE
    try:
        return OBJECTIVES[ObjectiveMode(mode)]
    except (ValueError, KeyError):
        return DEFAULT_OBJECTIVE


def cfg_for(objective: Objective, cfg: Config) -> Config:
    """A config whose loss weights match the objective, for the engine itself."""
    from dataclasses import replace

    return replace(cfg, loss=objective.weights)


def service_weights_of(cfg: Config) -> dict[str, float]:
    return {s.value: service_weight(s, cfg) for s in Service}
