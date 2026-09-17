"""Restoration sequencing: comparing three dispatch policies on one storm."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np

from civictwin.analysis.metrics import area_under_loss, recovery_time_h
from civictwin.config import Config
from civictwin.engine.contract import HazardScenario, Overlay
from civictwin.engine.coordinator import Engine
from civictwin.engine import response as response_module

logger = logging.getLogger(__name__)

Policy = Literal["greedy_population", "nearest", "optimised"]
LOCAL_SEARCH_ITERATIONS = 200


@dataclass(slots=True)
class RestorationPlan:
    scenario_id: str
    policy: Policy
    order: list[tuple[str, str]]
    area_under_loss_ph: float
    recovery_90_h: float
    priority: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "policy": self.policy,
            "order": [list(pair) for pair in self.order],
            "area_under_loss_ph": round(self.area_under_loss_ph, 2),
            "recovery_90_h": self.recovery_90_h,
            "priority": list(self.priority),
        }


def _run(
    engine: Engine,
    scenario: HazardScenario,
    policy: Policy,
    priority: Sequence[str] | None = None,
) -> tuple[float, float, list[tuple[str, str]]]:
    """Simulate one dispatch policy and return (loss, recovery, dispatch order)."""
    engine_policy = (
        response_module.POLICY_NEAREST
        if policy == "nearest"
        else response_module.POLICY_GREEDY
    )
    res = engine.simulate(
        scenario,
        Overlay(),
        record=True,
        dispatch=(engine_policy, list(priority) if priority else None),
        _skip_amplification=True,
    )
    return res.weighted_loss_ph, res.recovery_90_h, list(engine.last_dispatch_log)


def plan_for(
    engine: Engine,
    scenario: HazardScenario,
    cfg: Config,
    policy: Policy,
    seed: int = 0,
) -> RestorationPlan:
    """Build a restoration plan for one scenario under one policy."""
    if policy != "optimised":
        loss, recovery, order = _run(engine, scenario, policy)
        return RestorationPlan(
            scenario_id=scenario.id,
            policy=policy,
            order=order,
            area_under_loss_ph=loss,
            recovery_90_h=_worst_recovery(recovery),
            priority=[aid for _crew, aid in order],
        )

    base_loss, base_recovery, base_order = _run(engine, scenario, "greedy_population")
    priority = list(dict.fromkeys(aid for _crew, aid in base_order))
    best_loss, best_recovery, best_order = base_loss, base_recovery, base_order
    best_priority = list(priority)
    if len(priority) > 1:
        rng = np.random.default_rng(seed)
        for _ in range(LOCAL_SEARCH_ITERATIONS):
            trial = list(best_priority)
            i, j = rng.integers(0, len(trial), size=2)
            if i == j:
                continue
            if rng.random() < 0.5:
                trial[i], trial[j] = trial[j], trial[i]
            else:
                trial.insert(int(j), trial.pop(int(i)))
            loss, recovery, order = _run(engine, scenario, "greedy_population", trial)
            if loss < best_loss - 1e-9:
                best_loss, best_recovery, best_order = loss, recovery, order
                best_priority = trial
    return RestorationPlan(
        scenario_id=scenario.id,
        policy="optimised",
        order=best_order,
        area_under_loss_ph=best_loss,
        recovery_90_h=_worst_recovery(best_recovery),
        priority=best_priority,
    )


def _worst_recovery(recovery: dict) -> float:
    """Slowest portfolio to come back; infinite if any never does."""
    return max(recovery.values()) if recovery else float("inf")


def compare_policies(
    engine: Engine, scenario: HazardScenario, cfg: Config, seed: int = 0
) -> dict[str, RestorationPlan]:
    return {
        policy: plan_for(engine, scenario, cfg, policy, seed=seed)
        for policy in ("nearest", "greedy_population", "optimised")
    }
