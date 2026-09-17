"""Pre-event budget allocation against tail risk."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from civictwin.analysis.interventions import Intervention
from civictwin.analysis.metrics import cvar, eal, tail_size
from civictwin.analysis.montecarlo import LossTable, ScenarioSet, run_set, run_set_subset
from civictwin.analysis.objectives import (
    DEFAULT_OBJECTIVE,
    Objective,
    objective_values,
)
from civictwin.config import Config
from civictwin.engine.contract import Overlay
from civictwin.engine.coordinator import Engine

logger = logging.getLogger(__name__)

#: The greedy step screens candidates on a wider tail than CVaR itself uses.
#: Interventions only ever reduce losses, so a scenario outside the worst 15%
#: cannot be pushed into the worst 5% by selecting one.
EXTENDED_TAIL_FRACTION = 0.15
SIMULATION_BUDGET_PER_STEP = 50_000
CANDIDATE_CAP = 40
LAKH = 100_000.0
#: Charged to a free intervention so value-per-rupee stays finite.
MINIMUM_COST_INR = 1.0
#: Consecutive rounds a candidate must gain nothing before it is set aside.
BARREN_ROUNDS_BEFORE_DROP = 3


@dataclass(slots=True)
class PlanStep:
    intervention_id: str
    cumulative_cost_inr: float
    cvar_after: float
    marginal_cvar_reduction_per_lakh: float
    label: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "intervention_id": self.intervention_id,
            "label": self.label,
            "cumulative_cost_inr": self.cumulative_cost_inr,
            "cvar_after": round(self.cvar_after, 2),
            "marginal_cvar_reduction_per_lakh": round(
                self.marginal_cvar_reduction_per_lakh, 4
            ),
        }


@dataclass(slots=True)
class Plan:
    budget_inr: float
    interventions: list[str]
    cost_inr: float
    cvar_before: float
    cvar_after: float
    eal_before: float
    eal_after: float
    worst_zone_cvar_after: float
    steps: list[PlanStep] = field(default_factory=list)
    candidate_subsampled: bool = False
    equity_constraint_bound: bool = False
    objective: Objective = DEFAULT_OBJECTIVE

    def to_dict(self) -> dict[str, object]:
        return {
            "budget_inr": self.budget_inr,
            "interventions": list(self.interventions),
            "cost_inr": self.cost_inr,
            "cvar_before": round(self.cvar_before, 2),
            "cvar_after": round(self.cvar_after, 2),
            "eal_before": round(self.eal_before, 2),
            "eal_after": round(self.eal_after, 2),
            "cvar_reduction_pct": (
                0.0
                if self.cvar_before <= 0
                else round(
                    100.0 * (self.cvar_before - self.cvar_after) / self.cvar_before, 2
                )
            ),
            "worst_zone_cvar_after": round(self.worst_zone_cvar_after, 2),
            "steps": [s.to_dict() for s in self.steps],
            "candidate_subsampled": self.candidate_subsampled,
            "equity_constraint_bound": self.equity_constraint_bound,
            "objective": self.objective.to_dict(),
        }


def _worst_zone_cvar(table: LossTable, alpha: float) -> float:
    if not table.by_zone:
        return 0.0
    return max(cvar(values, alpha) for values in table.by_zone.values())


def greedy_plan(
    engine: Engine,
    scenario_set: ScenarioSet,
    catalogue: Sequence[Intervention],
    budget_inr: float,
    cfg: Config,
    baseline: LossTable | None = None,
    priority: dict[str, float] | None = None,
    max_zone_cvar: float | None = None,
    n_jobs: int = 1,
    objective: Objective = DEFAULT_OBJECTIVE,
) -> Plan:
    """Buy interventions one at a time, always the best CVaR reduction per rupee.

    Args:
        priority: optional tail-criticality per asset id, used to sub-sample
            candidates when a greedy step would otherwise be too expensive.
        max_zone_cvar: optional equity constraint on the worst zone's CVaR.
        objective: what to minimise. Changing it changes what gets bought, not
            just how the result is reported.
    """
    alpha = cfg.risk.alpha
    table = baseline if baseline is not None else run_set(engine, scenario_set, n_jobs=n_jobs)
    losses = objective_values(table, objective, cfg)
    cvar_before = cvar(losses, alpha)
    eal_before = eal(losses)

    plan = Plan(
        budget_inr=budget_inr,
        interventions=[],
        cost_inr=0.0,
        cvar_before=cvar_before,
        cvar_after=cvar_before,
        eal_before=eal_before,
        eal_after=eal_before,
        worst_zone_cvar_after=_worst_zone_cvar(table, alpha),
        objective=objective,
    )

    by_id = {c.id: c for c in catalogue}
    barren: dict[str, int] = {}
    selected: list[str] = []
    budget_left = budget_inr
    current_table = table

    while True:
        affordable = [
            c for c in catalogue if c.id not in selected and c.cost_inr <= budget_left
        ]
        if not affordable:
            break
        tail_idx = _extended_tail(losses)
        affordable = _cap_candidates(affordable, tail_idx, priority, plan)

        best_id: str | None = None
        best_gain = 0.0
        best_score = 0.0
        current_cvar = cvar(losses, alpha)
        round_gain: dict[str, float] = {}

        # One batch for the whole candidate x tail-scenario cross product: the
        # pairs are independent, so this is the only place worth parallelising.
        jobs = [
            (
                scenario_set.scenarios[i],
                Overlay(interventions=tuple(selected + [candidate.id])),
            )
            for candidate in affordable
            for i in tail_idx
        ]
        flat = engine.simulate_batch(jobs, n_jobs=n_jobs, objective=objective)

        for c_index, candidate in enumerate(affordable):
            trial = losses.copy()
            offset = c_index * len(tail_idx)
            for j, i in enumerate(tail_idx):
                trial[i] = flat[offset + j]
            gain = current_cvar - cvar(trial, alpha)
            round_gain[candidate.id] = gain
            # An operational change can cost nothing, and dividing by zero
            # would either crash or rank it as infinitely good. Charging a
            # notional rupee keeps the ordering sane and still puts free
            # measures first, which is where they belong.
            score = gain / max(MINIMUM_COST_INR, candidate.cost_inr)
            if score > best_score + 1e-15:
                best_score, best_gain, best_id = score, gain, candidate.id

        # Candidates that do nothing on their own may still do something once
        # something else has been bought -- a tie is worthless until the feeder
        # it backs up has been hardened. Dropping them on the first sight of a
        # zero would hide exactly the combinations worth finding, so a candidate
        # has to come up empty several rounds running before it is set aside,
        # and only when the step is too big to afford otherwise.
        for cid, gain in round_gain.items():
            barren[cid] = 0 if gain > 0.0 else barren.get(cid, 0) + 1
        if len(round_gain) > CANDIDATE_CAP:
            dead = {
                cid
                for cid, misses in barren.items()
                if misses >= BARREN_ROUNDS_BEFORE_DROP
            }
            if dead:
                plan.candidate_subsampled = True
                catalogue = [c for c in catalogue if c.id not in dead]

        if best_id is None or best_gain <= 0.0:
            break

        trial_selected = selected + [best_id]
        verified = run_set(
            engine, scenario_set, Overlay(interventions=tuple(trial_selected)), n_jobs=n_jobs
        )
        verified_values = objective_values(verified, objective, cfg)
        if max_zone_cvar is not None and _worst_zone_cvar(verified, alpha) > max_zone_cvar:
            plan.equity_constraint_bound = True
            catalogue = [c for c in catalogue if c.id != best_id]
            continue

        previous_cvar = cvar(losses, alpha)
        losses = verified_values
        current_table = verified
        selected = trial_selected
        cost = by_id[best_id].cost_inr
        budget_left -= cost
        plan.cost_inr += cost
        new_cvar = cvar(losses, alpha)
        plan.steps.append(
            PlanStep(
                intervention_id=best_id,
                cumulative_cost_inr=plan.cost_inr,
                cvar_after=new_cvar,
                marginal_cvar_reduction_per_lakh=(previous_cvar - new_cvar)
                / (max(MINIMUM_COST_INR, cost) / LAKH),
                label=by_id[best_id].label,
            )
        )

    plan.interventions = selected
    plan.cvar_after = cvar(losses, alpha)
    plan.eal_after = eal(losses)
    plan.worst_zone_cvar_after = _worst_zone_cvar(current_table, alpha)
    return plan


def _extended_tail(losses: np.ndarray) -> list[int]:
    k = max(1, math.ceil(len(losses) * EXTENDED_TAIL_FRACTION))
    order = np.argsort(losses, kind="stable")[::-1]
    return sorted(int(i) for i in order[:k])


def _cap_candidates(
    candidates: Sequence[Intervention],
    tail_idx: Sequence[int],
    priority: dict[str, float] | None,
    plan: Plan,
) -> list[Intervention]:
    """Keep the greedy step inside its simulation budget."""
    if len(candidates) * len(tail_idx) <= SIMULATION_BUDGET_PER_STEP:
        return list(candidates)
    plan.candidate_subsampled = True
    logger.info(
        "greedy step sub-sampled from %d to %d candidates", len(candidates), CANDIDATE_CAP
    )
    ranked = sorted(
        candidates,
        key=lambda c: (-(priority or {}).get(c.target, 0.0), c.id),
    )
    return ranked[:CANDIDATE_CAP]


def frontier(
    engine: Engine,
    scenario_set: ScenarioSet,
    catalogue: Sequence[Intervention],
    max_budget_inr: float,
    cfg: Config,
    baseline: LossTable | None = None,
    priority: dict[str, float] | None = None,
    n_jobs: int = 1,
    objective: Objective = DEFAULT_OBJECTIVE,
) -> Plan:
    """One greedy run to the maximum budget; every prefix of `steps` is a plan."""
    return greedy_plan(
        engine,
        scenario_set,
        catalogue,
        max_budget_inr,
        cfg,
        baseline=baseline,
        priority=priority,
        n_jobs=n_jobs,
        objective=objective,
    )


def plan_from_prefix(frontier_plan: Plan, budget_inr: float, cfg: Config) -> Plan:
    """Cut a frontier down to the longest prefix that fits a smaller budget."""
    steps = [s for s in frontier_plan.steps if s.cumulative_cost_inr <= budget_inr]
    return Plan(
        budget_inr=budget_inr,
        interventions=[s.intervention_id for s in steps],
        cost_inr=steps[-1].cumulative_cost_inr if steps else 0.0,
        cvar_before=frontier_plan.cvar_before,
        cvar_after=steps[-1].cvar_after if steps else frontier_plan.cvar_before,
        eal_before=frontier_plan.eal_before,
        eal_after=frontier_plan.eal_after,
        worst_zone_cvar_after=frontier_plan.worst_zone_cvar_after,
        steps=steps,
        candidate_subsampled=frontier_plan.candidate_subsampled,
    )
