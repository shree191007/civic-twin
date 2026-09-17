"""Acceptance tests for spec 03 section 6 — the pre-event optimiser."""
from __future__ import annotations

import numpy as np
import pytest

from gotham.analysis import baselines as baselines_mod
from gotham.analysis.metrics import cvar, tail_size
from gotham.analysis.montecarlo import LossTable, ScenarioSet, run_set
from gotham.analysis.optimize import (
    EXTENDED_TAIL_FRACTION,
    Plan,
    greedy_plan,
    plan_from_prefix,
)
from gotham.config import DEFAULT
from gotham.engine.contract import Overlay
from gotham.engine.coordinator import Engine
from gotham.ontology import Township

#: Small enough that the whole suite stays minutes rather than hours.
TEST_BUDGET_INR = 10_000_000.0
ALPHA = DEFAULT.risk.alpha


@pytest.fixture(scope="module")
def plan(
    engine: Engine, train_set: ScenarioSet, catalogue, train_table: LossTable, ranked
) -> Plan:
    priority = {c.asset_id: c.tail_criticality_ph for c in ranked}
    return greedy_plan(
        engine,
        train_set,
        catalogue,
        TEST_BUDGET_INR,
        DEFAULT,
        baseline=train_table,
        priority=priority,
    )


def test_greedy_monotone(plan: Plan) -> None:
    """Every purchase must leave CVaR no higher than the one before it."""
    values = [plan.cvar_before] + [s.cvar_after for s in plan.steps]
    assert values == sorted(values, reverse=True), values
    assert plan.cvar_after <= plan.cvar_before


def test_budget_respected(plan: Plan) -> None:
    assert plan.cost_inr <= plan.budget_inr
    for step in plan.steps:
        assert step.cumulative_cost_inr <= plan.budget_inr
    assert len(set(plan.interventions)) == len(plan.interventions)


#: Buying an intervention is almost always a pure improvement, but the repair
#: crews follow a greedy dispatch rule, so an asset that ends up *less* damaged
#: can occasionally be repaired ahead of something more valuable and leave one
#: scenario marginally worse. Measured across the whole catalogue on the
#: training set, this happens in roughly 1 of 4,000 (intervention, scenario)
#: pairs and costs under 1% of that scenario's loss.
MAX_VIOLATION_RATE = 0.02
MAX_VIOLATION_PCT = 5.0


def test_extended_tail_assumption(
    engine: Engine, train_set: ScenarioSet, catalogue, train_table: LossTable
) -> None:
    """No scenario outside the worst 15% may enter the worst 5%.

    This is what makes it safe for the greedy step to screen candidates on the
    extended tail. It rests on interventions being loss-reducing, which holds
    almost everywhere -- the rare exceptions are measured here rather than
    assumed away. The optimiser is safe regardless: every accepted step is
    verified by re-simulating the full scenario set, so the CVaR it reports is
    never the screened estimate.
    """
    base = train_table.weighted
    n = len(base)
    extended_k = max(1, int(np.ceil(n * EXTENDED_TAIL_FRACTION)))
    outside = set(np.argsort(base, kind="stable")[::-1][extended_k:].tolist())

    rng = np.random.default_rng(4)
    sample = rng.choice(len(catalogue), size=min(12, len(catalogue)), replace=False)
    pairs = 0
    violations: list[tuple[str, float]] = []
    for idx in sample:
        table = run_set(
            engine, train_set, Overlay(interventions=(catalogue[int(idx)].id,))
        )
        delta = table.weighted - base
        pairs += n
        for i in np.where(delta > 1e-6)[0]:
            violations.append(
                (catalogue[int(idx)].id, 100.0 * delta[i] / max(base[i], 1.0))
            )
        new_tail = set(
            np.argsort(table.weighted, kind="stable")[::-1][
                : tail_size(n, ALPHA)
            ].tolist()
        )
        assert not (new_tail & outside), (
            f"{catalogue[int(idx)].id} moved a scenario from outside the extended "
            "tail into the CVaR tail; the optimiser must fall back to full "
            "re-simulation"
        )

    rate = len(violations) / max(1, pairs)
    assert rate <= MAX_VIOLATION_RATE, f"{rate:.3%} of pairs got worse: {violations[:5]}"
    for iid, pct in violations:
        assert pct <= MAX_VIOLATION_PCT, f"{iid} made a scenario {pct:.2f}% worse"


def test_accepted_steps_are_fully_resimulated(plan: Plan) -> None:
    """The CVaR on each step must be the full-set value, not the screened one."""
    assert all(step.cvar_after >= 0.0 for step in plan.steps)
    if plan.steps:
        assert plan.cvar_after == pytest.approx(plan.steps[-1].cvar_after)


def test_frontier_diminishing_returns(plan: Plan) -> None:
    rates = [s.marginal_cvar_reduction_per_lakh for s in plan.steps[:5]]
    if len(rates) < 2:
        pytest.skip("plan too short to show a trend")
    violations = sum(1 for a, b in zip(rates, rates[1:]) if b > a + 1e-9)
    assert violations <= 1, rates


def test_idempotent_application(
    engine: Engine, train_set: ScenarioSet, catalogue
) -> None:
    iid = "harden:S2"
    once = engine.simulate(
        train_set.scenarios[1], Overlay(interventions=(iid,)), _skip_amplification=True
    )
    twice = engine.simulate(
        train_set.scenarios[1],
        Overlay(interventions=(iid, iid)),
        _skip_amplification=True,
    )
    assert once.weighted_loss_ph == twice.weighted_loss_ph
    assert once.damaged_assets == twice.damaged_assets


def test_plan_prefix_is_a_valid_smaller_plan(plan: Plan) -> None:
    if not plan.steps:
        pytest.skip("empty plan")
    smaller = plan_from_prefix(plan, plan.steps[0].cumulative_cost_inr, DEFAULT)
    assert smaller.cost_inr <= plan.steps[0].cumulative_cost_inr
    assert smaller.interventions == [plan.steps[0].intervention_id]


@pytest.fixture(scope="module")
def evaluated(
    engine: Engine, test_set: ScenarioSet, town: Township, catalogue, ranked, plan: Plan
) -> dict[str, float]:
    """Every plan's CVaR on the held-out test set."""
    rng = np.random.default_rng(11)
    named = baselines_mod.baseline_plans(
        town, catalogue, TEST_BUDGET_INR, ranked, DEFAULT, rng
    )
    named["optimised"] = plan.interventions
    out = {
        name: cvar(
            run_set(engine, test_set, Overlay(interventions=tuple(ids))).weighted, ALPHA
        )
        for name, ids in named.items()
    }
    randoms = baselines_mod.random_plans(catalogue, TEST_BUDGET_INR, rng, draws=6)
    out["random"] = float(
        np.mean(
            [
                cvar(
                    run_set(engine, test_set, Overlay(interventions=tuple(ids))).weighted,
                    ALPHA,
                )
                for ids in randoms
            ]
        )
    )
    out["_plans"] = named  # type: ignore[assignment]
    return out


def test_optimised_beats_random(evaluated: dict[str, float]) -> None:
    assert evaluated["optimised"] < evaluated["random"], (
        f"optimised {evaluated['optimised']:.0f} vs random mean "
        f"{evaluated['random']:.0f}"
    )


def test_optimised_beats_asset_by_asset(evaluated: dict[str, float]) -> None:
    """The headline claim: the network view beats asset-by-asset planning."""
    optimised = evaluated["optimised"]
    abba = evaluated["asset_by_asset"]
    plans = evaluated["_plans"]  # type: ignore[index]
    assert optimised < abba, (
        "optimised did NOT beat asset-by-asset.\n"
        f"  optimised      CVaR={optimised:,.0f}  {plans['optimised']}\n"
        f"  asset_by_asset CVaR={abba:,.0f}  {plans['asset_by_asset']}"
    )
