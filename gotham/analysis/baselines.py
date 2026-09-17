"""Planning baselines, including the asset-by-asset comparison that matters."""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

from gotham.analysis.criticality import AssetCriticality
from gotham.analysis.interventions import Intervention
from gotham.config import Config
from gotham.ontology import Township

logger = logging.getLogger(__name__)

RANDOM_DRAWS = 20


def _affordable_prefix(
    ordered: Sequence[Intervention], budget_inr: float
) -> list[str]:
    """Take interventions in the given order until the budget runs out."""
    chosen: list[str] = []
    left = budget_inr
    for iv in ordered:
        if iv.cost_inr <= left:
            chosen.append(iv.id)
            left -= iv.cost_inr
    return chosen


def baseline_plans(
    township: Township,
    catalogue: Sequence[Intervention],
    budget_inr: float,
    criticality: Sequence[AssetCriticality],
    cfg: Config,
    rng: np.random.Generator,
) -> dict[str, list[str]]:
    """Plans a planner would produce without a network view of the system."""
    del cfg
    by_asset = {c.asset_id: c for c in criticality}

    def served(iv: Intervention) -> int:
        return max(
            (
                township.served_population(t)
                for t in iv.targets
                if t in township.assets
            ),
            default=0,
        )

    def exposure(iv: Intervention) -> float:
        rows = [by_asset[t] for t in iv.targets if t in by_asset]
        return max((c.annual_failure_prob for c in rows), default=0.0)

    def asset_by_asset(iv: Intervention) -> float:
        rows = [by_asset[t] for t in iv.targets if t in by_asset]
        return max(
            (c.annual_failure_prob * c.standalone_loss_ph for c in rows), default=0.0
        )

    plans: dict[str, list[str]] = {
        "centrality": _affordable_prefix(
            sorted(catalogue, key=lambda i: (-served(i), i.id)), budget_inr
        ),
        "exposure": _affordable_prefix(
            sorted(catalogue, key=lambda i: (-exposure(i), i.id)), budget_inr
        ),
        "asset_by_asset": _affordable_prefix(
            sorted(catalogue, key=lambda i: (-asset_by_asset(i), i.id)), budget_inr
        ),
    }
    return plans


def random_plans(
    catalogue: Sequence[Intervention],
    budget_inr: float,
    rng: np.random.Generator,
    draws: int = RANDOM_DRAWS,
) -> list[list[str]]:
    """`draws` random affordable subsets, for the random baseline."""
    out: list[list[str]] = []
    for _ in range(draws):
        order = rng.permutation(len(catalogue))
        shuffled = [catalogue[int(i)] for i in order]
        out.append(_affordable_prefix(shuffled, budget_inr))
    return out
