"""Acceptance tests for spec 03 section 2 — risk metrics."""
from __future__ import annotations

import numpy as np
import pytest

from civictwin.analysis.metrics import (
    bootstrap_ci,
    cvar,
    cvar_indices,
    eal,
    portfolio_contributions,
    tail_mask,
    tail_size,
    var,
    zone_contributions,
)
from civictwin.analysis.montecarlo import LossTable
from civictwin.config import DEFAULT
from civictwin.engine.loss import service_weight
from civictwin.ontology import Service


def test_cvar_definition() -> None:
    """CVaR is the plain mean of the k = ceil(n(1-alpha)) worst scenarios."""
    losses = np.arange(1.0, 101.0)  # 1..100
    assert tail_size(100, 0.95) == 5
    assert cvar(losses, 0.95) == pytest.approx(np.mean([96, 97, 98, 99, 100]))
    assert cvar(losses, 0.9) == pytest.approx(np.mean(np.arange(91, 101)))
    # unordered input must give the same answer
    shuffled = np.random.default_rng(0).permutation(losses)
    assert cvar(shuffled, 0.95) == pytest.approx(cvar(losses, 0.95))
    assert len(cvar_indices(losses, 0.95)) == 5
    assert tail_mask(losses, 0.95).sum() == 5


def test_cvar_ge_var_ge_eal() -> None:
    rng = np.random.default_rng(3)
    for _ in range(20):
        losses = rng.lognormal(10.0, 1.2, size=400)
        assert cvar(losses, 0.95) >= var(losses, 0.95) >= eal(losses)


def test_contributions_sum_to_cvar() -> None:
    rng = np.random.default_rng(5)
    n = 200
    by_service = {s: rng.lognormal(8.0, 0.6, size=n) for s in Service}
    weighted = sum(service_weight(s, DEFAULT) * v for s, v in by_service.items())
    zones = {f"Z{i}": rng.lognormal(7.0, 0.5, size=n) for i in range(1, 5)}
    table = LossTable(
        scenario_ids=[f"s{i}" for i in range(n)],
        weighted=np.asarray(weighted),
        by_service=by_service,
        by_zone=zones,
        vulnerable=np.zeros(n),
        damaged=[frozenset() for _ in range(n)],
    )
    contributions = portfolio_contributions(table, 0.95, DEFAULT)
    assert sum(contributions.values()) == pytest.approx(
        cvar(table.weighted, 0.95), rel=1e-9
    )
    assert set(zone_contributions(table, 0.95)) == set(zones)


def test_bootstrap_ci_brackets_point_estimate() -> None:
    rng = np.random.default_rng(11)
    losses = rng.lognormal(9.0, 0.8, size=500)
    lo, hi = bootstrap_ci(losses, eal, n=400, seed=2)
    assert lo <= eal(losses) <= hi
    lo_c, hi_c = bootstrap_ci(losses, lambda x: cvar(x, 0.95), n=400, seed=2)
    assert lo_c <= cvar(losses, 0.95) <= hi_c
    assert bootstrap_ci(losses, eal, n=400, seed=2) == bootstrap_ci(
        losses, eal, n=400, seed=2
    )


def test_empty_inputs_are_safe() -> None:
    empty = np.array([])
    assert eal(empty) == 0.0
    assert cvar(empty, 0.95) == 0.0
    assert var(empty, 0.95) == 0.0
    assert bootstrap_ci(empty, eal) == (0.0, 0.0)
